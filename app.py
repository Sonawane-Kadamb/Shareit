import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import JSON, ForeignKey, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./shareit.db")
CHECKIN_EARLY = timedelta(minutes=int(os.getenv("CHECKIN_EARLY_MIN", "30")))
NO_SHOW_AFTER = timedelta(minutes=int(os.getenv("NO_SHOW_MIN", "30")))
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE_BOOKINGS", "3"))
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DB_URL.startswith("postgresql://"):
    DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)
if DB_URL.startswith("sqlite"):
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
else:  # serverless + pgbouncer (transaction mode): no client pool, no prepared statements
    from sqlalchemy.pool import NullPool
    engine = create_engine(DB_URL, poolclass=NullPool, connect_args={"prepare_threshold": None})
SessionLocal = sessionmaker(engine, expire_on_commit=False)
ACTIVE = ("approved", "checked_in", "overdue")
STAFF = ("owner", "manager", "admin")
RATING = ["good", "fair", "damaged"]


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def naive(d: datetime):
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


class Base(DeclarativeBase):
    HIDE: set = set()

    def d(self):
        out = {}
        for c in self.__table__.columns:
            if c.name in self.HIDE:
                continue
            v = getattr(self, c.name)
            out[c.name] = v.isoformat() + "Z" if isinstance(v, datetime) else v
        return out


class User(Base):
    __tablename__ = "users"
    HIDE = {"pw", "token"}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str] = mapped_column(unique=True)
    pw: Mapped[str]
    role: Mapped[str] = mapped_column(default="user")
    verified: Mapped[bool] = mapped_column(default=False)
    token: Mapped[Optional[str]] = mapped_column(default=None)


class Cred(Base):
    __tablename__ = "creds"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    evidence: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="pending")  # pending|verified|rejected
    reviewed_by: Mapped[Optional[str]] = mapped_column(default=None)
    note: Mapped[str] = mapped_column(default="")


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    category: Mapped[str]
    location: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    rules: Mapped[str] = mapped_column(default="")
    requirements: Mapped[list] = mapped_column(JSON, default=list)
    max_hours: Mapped[float] = mapped_column(default=4)
    weekly_cap_hours: Mapped[float] = mapped_column(default=8)
    requires_approval: Mapped[bool] = mapped_column(default=True)
    deposit: Mapped[int] = mapped_column(default=0)
    available_from: Mapped[Optional[datetime]] = mapped_column(default=None)
    available_to: Mapped[Optional[datetime]] = mapped_column(default=None)


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    start: Mapped[datetime]
    end: Mapped[datetime]
    purpose: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="pending")
    reason: Mapped[str] = mapped_column(default="")
    decided_by: Mapped[Optional[str]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=now)
    checkin_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    checkin_rating: Mapped[Optional[str]] = mapped_column(default=None)
    checkin_notes: Mapped[str] = mapped_column(default="")
    returned_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    return_rating: Mapped[Optional[str]] = mapped_column(default=None)
    return_notes: Mapped[str] = mapped_column(default="")
    reminded: Mapped[bool] = mapped_column(default=False)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"))
    reporter: Mapped[str]
    kind: Mapped[str]  # damage|delay|conflict|other
    description: Mapped[str]
    status: Mapped[str] = mapped_column(default="open")
    resolution: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=now)
    actor: Mapped[str]
    action: Mapped[str]
    booking_id: Mapped[Optional[int]] = mapped_column(default=None)
    resource_id: Mapped[Optional[int]] = mapped_column(default=None)
    detail: Mapped[str] = mapped_column(default="")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message: Mapped[str]
    ts: Mapped[datetime] = mapped_column(default=now)
    read: Mapped[bool] = mapped_column(default=False)


Base.metadata.create_all(engine)
app = FastAPI(title="ShareIt")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hashpw(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 100_000).hex()


def checkpw(p, stored):
    return hmac.compare_digest(hashpw(p, stored.split("$")[0]), stored)


def auth(authorization: str = Header(""), db=Depends(get_db)):
    t = authorization.removeprefix("Bearer ").strip()
    u = db.scalar(select(User).where(User.token == hashlib.sha256(t.encode()).hexdigest())) if t else None
    if not u:
        raise HTTPException(401, "Not signed in")
    return u


def can_manage(u, r):
    return u.role in ("manager", "admin") or (u.role == "owner" and r.owner_id == u.id)


def get_or_404(db, model, id):
    o = db.get(model, id)
    if not o:
        raise HTTPException(404, f"{model.__name__} not found")
    return o


def audit(db, actor, action, b=None, r=None, detail=""):
    rid = r.id if r else (b.resource_id if b else None)
    db.add(Audit(actor=actor.name if actor else "system", action=action, booking_id=b.id if b else None,
                 resource_id=rid, detail=detail))


def notify(db, uid, msg):
    db.add(Notification(user_id=uid, message=msg))


def hours(b):
    return (b.end - b.start).total_seconds() / 3600


def overlap(db, rid, s, e, statuses, exclude=None):
    q = select(Booking).where(Booking.resource_id == rid, Booking.status.in_(statuses), Booking.start < e, Booking.end > s)
    if exclude:
        q = q.where(Booking.id != exclude)
    return db.scalars(q).all()


def usage30(db, uid):
    since = now() - timedelta(days=30)
    bs = db.scalars(select(Booking).where(Booking.user_id == uid, Booking.start >= since,
                                          Booking.status.in_(ACTIVE + ("returned",)))).all()
    return round(sum(hours(b) for b in bs), 1)


def eligibility(db, u, r):
    ok = {c.name for c in db.scalars(select(Cred).where(Cred.user_id == u.id, Cred.status == "verified"))}
    missing = [q for q in r.requirements if q not in ok]
    return {"eligible": u.verified and not missing, "missing": missing, "account_verified": u.verified}


def promote(db, rid):
    """Fair waitlist: least recent usage first, then earliest request. Promotes to owner review."""
    ws = db.scalars(select(Booking).where(Booking.resource_id == rid, Booking.status == "waitlisted",
                                          Booking.end > now())).all()
    ws.sort(key=lambda b: (usage30(db, b.user_id), b.created_at))
    taken = []
    for b in ws:
        if overlap(db, rid, b.start, b.end, ACTIVE) or any(b.start < e and b.end > s for s, e in taken):
            continue
        b.status, b.reason = "pending", "Promoted from waitlist: slot became free. Awaiting owner review."
        taken.append((b.start, b.end))
        audit(db, None, "promoted_from_waitlist", b, detail=f"usage30={usage30(db, b.user_id)}h")
        notify(db, b.user_id, f"Booking #{b.id} moved from waitlist to pending review.")


def sweep(db):
    """Lazy scheduler: reminders, overdue, no-shows."""
    t = now()
    for b in db.scalars(select(Booking).where(Booking.status.in_(("approved", "checked_in")))).all():
        r = db.get(Resource, b.resource_id)
        if b.status == "checked_in" and b.end < t:
            b.status = "overdue"
            audit(db, None, "overdue_flagged", b)
            db.add(Incident(booking_id=b.id, resource_id=r.id, reporter="system", kind="delay",
                            description=f"Not returned by scheduled end ({b.end.isoformat()}Z)."))
            notify(db, b.user_id, f"Booking #{b.id} is overdue. Please return {r.name}.")
            notify(db, r.owner_id, f"Booking #{b.id} for {r.name} is overdue.")
        elif b.status == "approved" and b.start + NO_SHOW_AFTER < t:
            b.status = "no_show"
            audit(db, None, "no_show", b)
            notify(db, b.user_id, f"Booking #{b.id} marked no-show (not checked in).")
            promote(db, r.id)
        elif b.status == "approved" and not b.reminded and b.start - t <= timedelta(hours=1):
            b.reminded = True
            notify(db, b.user_id, f"Reminder: {r.name} booking #{b.id} starts at {b.start.isoformat()}Z.")
    db.commit()


def bout(db, b, viewer):
    r = db.get(Resource, b.resource_id)
    u = db.get(User, b.user_id)
    staff = can_manage(viewer, r)
    out = b.d() | {"resource_name": r.name, "location": r.location, "user_name": u.name, "staff": staff,
                   "is_mine": b.user_id == viewer.id}
    if staff:
        out |= {"user_email": u.email, "usage30": usage30(db, u.id)}
    return out


def booking_for(db, id, u):
    b = get_or_404(db, Booking, id)
    r = db.get(Resource, b.resource_id)
    if b.user_id != u.id and not can_manage(u, r):
        raise HTTPException(403, "Not your booking")
    return b, r


# ---------- auth ----------
class RegisterIn(BaseModel):
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/register")
def register(i: RegisterIn, db=Depends(get_db)):
    if len(i.password) < 6:
        raise HTTPException(422, "Password must be at least 6 characters")
    if db.scalar(select(User).where(User.email == i.email.lower())):
        raise HTTPException(409, "Email already registered")
    db.add(User(name=i.name, email=i.email.lower(), pw=hashpw(i.password)))
    db.commit()
    return {"ok": True, "message": "Registered. An administrator must verify your account before you can book."}


@app.post("/api/login")
def login(i: LoginIn, db=Depends(get_db)):
    u = db.scalar(select(User).where(User.email == i.email.lower()))
    if not u or not checkpw(i.password, u.pw):
        raise HTTPException(401, "Invalid credentials")
    t = secrets.token_urlsafe(24)
    u.token = hashlib.sha256(t.encode()).hexdigest()
    db.commit()
    return {"token": t, "user": u.d()}


@app.get("/api/me")
def me(u=Depends(auth)):
    return u.d()


# ---------- resources ----------
class ResourceIn(BaseModel):
    name: str
    category: str
    location: str
    description: str = ""
    rules: str = ""
    requirements: list[str] = []
    max_hours: float = 4
    weekly_cap_hours: float = 8
    requires_approval: bool = True
    deposit: int = 0
    available_from: Optional[datetime] = None
    available_to: Optional[datetime] = None


@app.post("/api/resources")
def add_resource(i: ResourceIn, u=Depends(auth), db=Depends(get_db)):
    if u.role not in STAFF or not u.verified:
        raise HTTPException(403, "Only verified owners/managers/admins can list resources")
    data = i.model_dump()
    for k in ("available_from", "available_to"):
        data[k] = naive(data[k]) if data[k] else None
    data["requirements"] = [q.strip() for q in data["requirements"] if q.strip()]
    r = Resource(owner_id=u.id, **data)
    db.add(r)
    db.flush()
    audit(db, u, "resource_listed", r=r, detail=r.name)
    db.commit()
    return r.d()


@app.get("/api/resources")
def list_resources(q: str = "", category: str = "", eligible: bool = False, free_from: Optional[datetime] = None,
                   free_to: Optional[datetime] = None, u=Depends(auth), db=Depends(get_db)):
    out = []
    for r in db.scalars(select(Resource)).all():
        text = f"{r.name} {r.category} {r.location} {r.description}".lower()
        if q and q.lower() not in text or category and r.category != category:
            continue
        if free_from and free_to and overlap(db, r.id, naive(free_from), naive(free_to), ACTIVE):
            continue
        el = eligibility(db, u, r)
        if eligible and not el["eligible"]:
            continue
        out.append(r.d() | {"eligibility": el})
    return out


@app.get("/api/resources/{id}")
def get_resource(id: int, u=Depends(auth), db=Depends(get_db)):
    r = get_or_404(db, Resource, id)
    busy = [{"start": b.start.isoformat() + "Z", "end": b.end.isoformat() + "Z", "status": b.status}
            for b in db.scalars(select(Booking).where(Booking.resource_id == id, Booking.status.in_(ACTIVE),
                                                      Booking.end > now())).all()]
    wl = len(db.scalars(select(Booking).where(Booking.resource_id == id, Booking.status == "waitlisted")).all())
    owner = db.get(User, r.owner_id)
    return r.d() | {"eligibility": eligibility(db, u, r), "busy": busy, "waitlist_size": wl, "owner_name": owner.name}


# ---------- credentials ----------
class CredIn(BaseModel):
    name: str
    evidence: str = ""


@app.post("/api/credentials")
def submit_cred(i: CredIn, u=Depends(auth), db=Depends(get_db)):
    c = Cred(user_id=u.id, name=i.name.strip(), evidence=i.evidence)
    db.add(c)
    audit(db, u, "credential_submitted", detail=c.name)
    db.commit()
    return c.d()


@app.get("/api/credentials")
def list_creds(scope: str = "mine", u=Depends(auth), db=Depends(get_db)):
    if scope == "review":
        if u.role not in STAFF:
            raise HTTPException(403, "Staff only")
        cs = db.scalars(select(Cred).where(Cred.status == "pending")).all()
    else:
        cs = db.scalars(select(Cred).where(Cred.user_id == u.id)).all()
    return [c.d() | {"user_name": db.get(User, c.user_id).name} for c in cs]


class ReviewIn(BaseModel):
    approve: bool
    note: str = ""


@app.post("/api/credentials/{id}/review")
def review_cred(id: int, i: ReviewIn, u=Depends(auth), db=Depends(get_db)):
    if u.role not in STAFF:
        raise HTTPException(403, "Staff only")
    c = get_or_404(db, Cred, id)
    c.status, c.reviewed_by, c.note = ("verified" if i.approve else "rejected"), u.name, i.note
    audit(db, u, f"credential_{c.status}", detail=f"{c.name} for user #{c.user_id}")
    notify(db, c.user_id, f"Your credential '{c.name}' was {c.status}." + (f" Note: {i.note}" if i.note else ""))
    db.commit()
    return c.d()


# ---------- bookings ----------
class BookingIn(BaseModel):
    resource_id: int
    start: datetime
    end: datetime
    purpose: str = ""


@app.post("/api/bookings")
def create_booking(i: BookingIn, u=Depends(auth), db=Depends(get_db)):
    r = get_or_404(db, Resource, i.resource_id)
    s, e = naive(i.start), naive(i.end)
    if not u.verified:
        raise HTTPException(403, "Your account is not verified yet. Ask an administrator to verify it.")
    if e <= s or s < now() - timedelta(minutes=5):
        raise HTTPException(422, "Invalid time range (must end after it starts and not be in the past)")
    if not i.purpose.strip():
        raise HTTPException(422, "Please state the purpose of use")
    h = (e - s).total_seconds() / 3600
    if h > r.max_hours:
        raise HTTPException(422, f"Maximum {r.max_hours}h per booking for this resource")
    if (r.available_from and s < r.available_from) or (r.available_to and e > r.available_to):
        raise HTTPException(422, "Outside the resource's listed availability window")
    el = eligibility(db, u, r)
    if el["missing"]:
        raise HTTPException(422, "Not eligible. Missing verified credentials: " + ", ".join(el["missing"]))
    open_inc = db.scalars(select(Incident).join(Booking, Incident.booking_id == Booking.id)
                          .where(Booking.user_id == u.id, Incident.status == "open",
                                 Incident.kind.in_(("damage", "delay")))).first()
    if open_inc:
        raise HTTPException(403, f"Booking paused: unresolved {open_inc.kind} incident #{open_inc.id}. "
                                 "An owner must review it first.")
    active = db.scalars(select(Booking).where(Booking.user_id == u.id, Booking.end > now(),
                                              Booking.status.in_(("pending", "approved", "waitlisted")))).all()
    if len(active) >= MAX_ACTIVE:
        raise HTTPException(429, f"Fair-use limit: at most {MAX_ACTIVE} open upcoming requests per user")
    wk = s - timedelta(days=s.weekday(), hours=s.hour, minutes=s.minute, seconds=s.second, microseconds=s.microsecond)
    used = sum(hours(b) for b in db.scalars(select(Booking).where(
        Booking.user_id == u.id, Booking.resource_id == r.id, Booking.start >= wk, Booking.start < wk + timedelta(days=7),
        Booking.status.in_(("pending", "approved", "checked_in", "overdue", "returned")))).all())
    if used + h > r.weekly_cap_hours:
        raise HTTPException(422, f"Weekly fair-use cap: {r.weekly_cap_hours}h per user per week on this resource "
                                 f"({used:.1f}h already requested/used that week)")
    if overlap(db, r.id, s, e, ACTIVE):
        status = "waitlisted"
        reason = (f"Slot already booked. Waitlisted; ranking is least recent usage first "
                  f"({usage30(db, u.id)}h used in last 30 days), then request time.")
    elif not r.requires_approval:
        status, reason = "approved", "Auto-approved: requirements verified and slot free."
    else:
        status, reason = "pending", "Eligibility verified. Awaiting owner review."
        if overlap(db, r.id, s, e, ("pending",)):
            reason += " Other requests compete for this slot; the owner decides."
    b = Booking(resource_id=r.id, user_id=u.id, start=s, end=e, purpose=i.purpose, status=status, reason=reason,
                decided_by="system" if status == "approved" else None)
    db.add(b)
    db.flush()
    audit(db, u, f"booking_requested:{status}", b, detail=reason)
    if status == "pending":
        notify(db, r.owner_id, f"New request #{b.id} for {r.name} from {u.name}.")
    db.commit()
    return bout(db, b, u)


@app.get("/api/bookings")
def list_bookings(scope: str = "mine", u=Depends(auth), db=Depends(get_db)):
    sweep(db)
    bs = db.scalars(select(Booking).order_by(Booking.start.desc())).all()
    out = []
    for b in bs:
        r = db.get(Resource, b.resource_id)
        if scope == "manage" and can_manage(u, r) or scope != "manage" and b.user_id == u.id:
            out.append(bout(db, b, u))
    if scope == "manage":  # fairness order for review: least recent usage first, then request time
        out.sort(key=lambda x: (x["status"] != "pending", x["usage30"], x["created_at"]))
    return out


@app.get("/api/bookings/{id}")
def booking_detail(id: int, u=Depends(auth), db=Depends(get_db)):
    sweep(db)
    b, r = booking_for(db, id, u)
    hist = db.scalars(select(Audit).where(Audit.booking_id == id).order_by(Audit.id)).all()
    inc = db.scalars(select(Incident).where(Incident.booking_id == id)).all()
    return bout(db, b, u) | {"history": [a.d() for a in hist], "incidents": [x.d() for x in inc]}


class DecisionIn(BaseModel):
    decision: str  # approve|reject|waitlist
    reason: str = ""


@app.post("/api/bookings/{id}/decision")
def decide(id: int, i: DecisionIn, u=Depends(auth), db=Depends(get_db)):
    b, r = booking_for(db, id, u)
    if not can_manage(u, r):
        raise HTTPException(403, "Only the owner or a manager can decide")
    if b.status not in ("pending", "waitlisted"):
        raise HTTPException(409, f"Cannot decide a booking that is {b.status}")
    if i.decision == "approve":
        if overlap(db, r.id, b.start, b.end, ACTIVE, exclude=b.id):
            raise HTTPException(409, "Conflicts with an already approved booking")
        b.status, b.reason = "approved", i.reason or f"Approved by {u.name}."
        for o in overlap(db, r.id, b.start, b.end, ("pending",), exclude=b.id):
            o.status, o.reason = "waitlisted", f"Another request (#{b.id}) was approved for this slot; you are on the waitlist."
            audit(db, u, "auto_waitlisted", o, detail=o.reason)
            notify(db, o.user_id, f"Booking #{o.id}: {o.reason}")
    elif i.decision == "reject":
        if not i.reason.strip():
            raise HTTPException(422, "A reason is required when rejecting")
        b.status, b.reason = "rejected", i.reason
    elif i.decision == "waitlist":
        if b.status != "pending":
            raise HTTPException(409, "Only pending requests can be waitlisted")
        b.status, b.reason = "waitlisted", i.reason or f"Placed on waitlist by {u.name}."
    else:
        raise HTTPException(422, "decision must be approve, reject or waitlist")
    b.decided_by = u.name
    audit(db, u, f"decision:{b.status}", b, detail=b.reason)
    notify(db, b.user_id, f"Booking #{b.id} for {r.name} is {b.status}. Reason: {b.reason}")
    if b.status == "rejected":
        promote(db, r.id)
    db.commit()
    return bout(db, b, u)


class ReasonIn(BaseModel):
    reason: str = ""


@app.post("/api/bookings/{id}/cancel")
def cancel(id: int, i: ReasonIn, u=Depends(auth), db=Depends(get_db)):
    b, r = booking_for(db, id, u)
    if b.status not in ("pending", "approved", "waitlisted"):
        raise HTTPException(409, f"Cannot cancel a booking that is {b.status}")
    by_owner = b.user_id != u.id
    b.status, b.reason = "cancelled", f"Cancelled by {u.name}. {i.reason}".strip()
    audit(db, u, "cancelled", b, detail=i.reason)
    notify(db, b.user_id if by_owner else r.owner_id, f"Booking #{b.id} for {r.name} was cancelled by {u.name}.")
    promote(db, r.id)
    db.commit()
    return bout(db, b, u)


class ExtendIn(BaseModel):
    new_end: datetime


@app.post("/api/bookings/{id}/extend")
def extend(id: int, i: ExtendIn, u=Depends(auth), db=Depends(get_db)):
    b, r = booking_for(db, id, u)
    if b.user_id != u.id:
        raise HTTPException(403, "Only the borrower can request an extension")
    ne = naive(i.new_end)
    if b.status not in ("approved", "checked_in") or ne <= b.end:
        raise HTTPException(409, "Only approved or in-use bookings can be extended to a later end time")
    if (ne - b.start).total_seconds() / 3600 > r.max_hours:
        raise HTTPException(422, f"Extension exceeds the {r.max_hours}h per-booking limit")
    if overlap(db, r.id, b.end, ne, ACTIVE + ("pending",), exclude=b.id):
        raise HTTPException(409, "Extension conflicts with another booking")
    old, b.end = b.end, ne
    audit(db, u, "extended", b, detail=f"{old.isoformat()}Z -> {ne.isoformat()}Z")
    notify(db, r.owner_id, f"Booking #{b.id} for {r.name} extended by {u.name}.")
    db.commit()
    return bout(db, b, u)


class ConditionIn(BaseModel):
    rating: str  # good|fair|damaged
    notes: str = ""


@app.post("/api/bookings/{id}/checkin")
def checkin(id: int, i: ConditionIn, u=Depends(auth), db=Depends(get_db)):
    b, r = booking_for(db, id, u)
    if b.user_id != u.id:
        raise HTTPException(403, "Only the borrower checks in")
    if b.status != "approved":
        raise HTTPException(409, f"Cannot check in a booking that is {b.status}")
    if i.rating not in RATING:
        raise HTTPException(422, f"rating must be one of {RATING}")
    if now() < b.start - CHECKIN_EARLY:
        raise HTTPException(409, f"Check-in opens {int(CHECKIN_EARLY.total_seconds() // 60)} min before start")
    b.status, b.checkin_at, b.checkin_rating, b.checkin_notes = "checked_in", now(), i.rating, i.notes
    audit(db, u, "checked_in", b, detail=f"condition={i.rating}. {i.notes}")
    notify(db, r.owner_id, f"{u.name} checked in to {r.name} (condition: {i.rating}).")
    db.commit()
    return bout(db, b, u)


@app.post("/api/bookings/{id}/return")
def return_(id: int, i: ConditionIn, u=Depends(auth), db=Depends(get_db)):
    b, r = booking_for(db, id, u)
    if b.user_id != u.id and not can_manage(u, r):
        raise HTTPException(403, "Not allowed")
    if b.status not in ("checked_in", "overdue"):
        raise HTTPException(409, f"Cannot return a booking that is {b.status}")
    if i.rating not in RATING:
        raise HTTPException(422, f"rating must be one of {RATING}")
    late = b.status == "overdue"
    b.status, b.returned_at, b.return_rating, b.return_notes = "returned", now(), i.rating, i.notes
    audit(db, u, "returned", b, detail=f"condition={i.rating}{' (late)' if late else ''}. {i.notes}")
    if RATING.index(i.rating) > RATING.index(b.checkin_rating or "good"):
        db.add(Incident(booking_id=b.id, resource_id=r.id, reporter="system", kind="damage",
                        description=f"Condition worsened: {b.checkin_rating} -> {i.rating}. {i.notes}"))
        audit(db, None, "auto_incident:damage", b, detail="condition worsened at return")
        notify(db, r.owner_id, f"Condition of {r.name} worsened on return (booking #{b.id}). Review required.")
    notify(db, r.owner_id, f"{r.name} returned (booking #{b.id}, condition: {i.rating}).")
    promote(db, r.id)
    db.commit()
    return bout(db, b, u)


# ---------- incidents ----------
class IncidentIn(BaseModel):
    kind: str
    description: str


@app.post("/api/bookings/{id}/incidents")
def report_incident(id: int, i: IncidentIn, u=Depends(auth), db=Depends(get_db)):
    b, r = booking_for(db, id, u)
    if i.kind not in ("damage", "delay", "conflict", "other"):
        raise HTTPException(422, "kind must be damage, delay, conflict or other")
    inc = Incident(booking_id=b.id, resource_id=r.id, reporter=u.name, kind=i.kind, description=i.description)
    db.add(inc)
    audit(db, u, f"incident_reported:{i.kind}", b, detail=i.description)
    notify(db, r.owner_id if u.id == b.user_id else b.user_id, f"Incident ({i.kind}) reported on booking #{b.id}.")
    db.commit()
    return inc.d()


@app.post("/api/incidents/{id}/resolve")
def resolve_incident(id: int, i: ReasonIn, u=Depends(auth), db=Depends(get_db)):
    inc = get_or_404(db, Incident, id)
    b = db.get(Booking, inc.booking_id)
    r = db.get(Resource, inc.resource_id)
    if not can_manage(u, r):
        raise HTTPException(403, "Only the owner or a manager can resolve incidents")
    if not i.reason.strip():
        raise HTTPException(422, "A resolution note is required")
    inc.status, inc.resolution = "resolved", i.reason
    audit(db, u, "incident_resolved", b, detail=i.reason)
    notify(db, b.user_id, f"Incident #{inc.id} on booking #{b.id} was resolved: {i.reason}")
    db.commit()
    return inc.d()


# ---------- notifications / admin ----------
@app.get("/api/notifications")
def notifications(u=Depends(auth), db=Depends(get_db)):
    sweep(db)
    ns = db.scalars(select(Notification).where(Notification.user_id == u.id).order_by(Notification.id.desc()).limit(50)).all()
    out = [n.d() for n in ns]
    for n in ns:
        n.read = True
    db.commit()
    return out


@app.get("/api/notifications/unread")
def unread(u=Depends(auth), db=Depends(get_db)):
    return {"count": len(db.scalars(select(Notification).where(Notification.user_id == u.id, Notification.read == False)).all())}  # noqa: E712


def admin_only(u):
    if u.role != "admin":
        raise HTTPException(403, "Administrators only")


@app.get("/api/admin/users")
def admin_users(u=Depends(auth), db=Depends(get_db)):
    admin_only(u)
    return [x.d() for x in db.scalars(select(User)).all()]


class UserPatch(BaseModel):
    verified: Optional[bool] = None
    role: Optional[str] = None


@app.post("/api/admin/users/{id}")
def admin_patch(id: int, i: UserPatch, u=Depends(auth), db=Depends(get_db)):
    admin_only(u)
    t = get_or_404(db, User, id)
    if i.role and i.role not in ("user",) + STAFF:
        raise HTTPException(422, "Invalid role")
    if i.verified is not None:
        t.verified = i.verified
    if i.role:
        t.role = i.role
    audit(db, u, "user_updated", detail=f"{t.email}: verified={t.verified} role={t.role}")
    notify(db, t.id, f"Your account was updated: verified={t.verified}, role={t.role}.")
    db.commit()
    return t.d()


@app.get("/api/admin/audit")
def admin_audit(u=Depends(auth), db=Depends(get_db)):
    admin_only(u)
    return [a.d() for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(300)).all()]


@app.on_event("startup")
def _seed():
    from seed import seed
    seed()


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
