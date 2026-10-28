import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db")

from fastapi.testclient import TestClient  # noqa: E402

import app as A  # noqa: E402
from seed import seed  # noqa: E402

seed()
c = TestClient(A.app)


def login(who):
    r = c.post("/api/login", json={"email": f"{who}@demo.edu", "password": "demo123"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def iso(mins):
    return (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()


H = {k: login(k) for k in ("admin", "owner", "asha", "ben", "chen")}
rid = {r["name"]: r["id"] for r in c.get("/api/resources", headers=H["asha"]).json()}
SCOPE = rid["Oscilloscope Bench (Lab 2)"]


def book(who, res, s=1, e=61, purpose="lab work"):
    return c.post("/api/bookings", headers=H[who], json={"resource_id": res, "start": iso(s), "end": iso(e), "purpose": purpose})


def test_full_lifecycle():
    r = book("asha", SCOPE)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    bid = r.json()["id"]
    assert c.post(f"/api/bookings/{bid}/decision", headers=H["asha"], json={"decision": "approve"}).status_code == 403
    d = c.post(f"/api/bookings/{bid}/decision", headers=H["owner"], json={"decision": "approve", "reason": "ok"}).json()
    assert d["status"] == "approved"
    assert c.post(f"/api/bookings/{bid}/checkin", headers=H["asha"], json={"rating": "good", "notes": "clean"}).json()["status"] == "checked_in"
    assert c.post(f"/api/bookings/{bid}/return", headers=H["asha"], json={"rating": "damaged", "notes": "probe cracked"}).json()["status"] == "returned"
    det = c.get(f"/api/bookings/{bid}", headers=H["asha"]).json()
    actions = [a["action"] for a in det["history"]]
    assert "checked_in" in actions and "returned" in actions and "auto_incident:damage" in actions
    inc = det["incidents"][0]
    assert inc["kind"] == "damage" and inc["status"] == "open"
    # open damage incident blocks new bookings until human resolves it
    assert book("asha", SCOPE, 200, 260).status_code == 403
    assert c.post(f"/api/incidents/{inc['id']}/resolve", headers=H["owner"], json={"reason": "Probe replaced"}).status_code == 200
    assert book("asha", SCOPE, 200, 260).status_code == 200


def test_eligibility_and_verification():
    assert book("ben", SCOPE).status_code == 422  # missing credential
    assert book("chen", rid["Seminar Room B-204"]).status_code == 403  # unverified account
    cid = c.post("/api/credentials", headers=H["ben"], json={"name": "Lab Safety Training"}).json()["id"]
    assert book("ben", SCOPE, 300, 360).status_code == 422  # still pending review
    c.post(f"/api/credentials/{cid}/review", headers=H["owner"], json={"approve": True})
    assert book("ben", SCOPE, 300, 360).status_code == 200


def test_conflict_waitlist_and_fairness():
    room = rid["Seminar Room B-204"]
    a = book("asha", room, 500, 560).json()
    assert a["status"] == "approved"  # auto-approved
    b = book("ben", room, 520, 580).json()
    assert b["status"] == "waitlisted"
    c.post(f"/api/bookings/{a['id']}/cancel", headers=H["asha"], json={"reason": "plan changed"})
    assert c.get(f"/api/bookings/{b['id']}", headers=H["ben"]).json()["status"] == "pending"  # promoted
    # weekly cap
    assert book("asha", room, 1000, 1000 + 60 * 5).status_code == 422  # > max hours
    # reject needs reason
    assert c.post(f"/api/bookings/{b['id']}/decision", headers=H["owner"], json={"decision": "reject"}).status_code == 422


def test_privacy_and_audit_access():
    bid = book("ben", rid["DSLR Camera Kit"], 700, 760).json()["id"]
    assert c.get(f"/api/bookings/{bid}", headers=H["asha"]).status_code == 403
    assert c.get("/api/admin/audit", headers=H["asha"]).status_code == 403
    assert len(c.get("/api/admin/audit", headers=H["admin"]).json()) > 0
