from sqlalchemy import select

from app import Cred, Resource, SessionLocal, User, hashpw


def seed():
    with SessionLocal() as db:
        if db.scalar(select(User)):
            return
        pw = hashpw("demo123")
        users = {
            "admin": User(name="Ada Admin", email="admin@demo.edu", pw=pw, role="admin", verified=True),
            "owner": User(name="Dr. Rao (Lab Owner)", email="owner@demo.edu", pw=pw, role="owner", verified=True),
            "manager": User(name="Mia Manager", email="manager@demo.edu", pw=pw, role="manager", verified=True),
            "asha": User(name="Asha (Student)", email="asha@demo.edu", pw=pw, verified=True),
            "ben": User(name="Ben (Student)", email="ben@demo.edu", pw=pw, verified=True),
            "chen": User(name="Chen (Unverified)", email="chen@demo.edu", pw=pw, verified=False),
        }
        db.add_all(users.values())
        db.flush()
        db.add(Cred(user_id=users["asha"].id, name="Lab Safety Training", status="verified", reviewed_by="Dr. Rao"))
        o = users["owner"].id
        db.add_all([
            Resource(owner_id=o, name="Prusa MK4 3D Printer", category="Fabrication", location="Makerspace, Block C",
                     description="FDM printer, 250x210x220 mm build volume.",
                     rules="Staff-approved filament only. Never leave a running print unattended.",
                     requirements=["3D Printing Orientation"], max_hours=3, weekly_cap_hours=4, deposit=500),
            Resource(owner_id=o, name="Oscilloscope Bench (Lab 2)", category="Electronics", location="Electronics Lab 2",
                     description="4-channel 200 MHz scope, bench PSU, soldering station.",
                     rules="ESD wrist strap mandatory. Report any probe damage.",
                     requirements=["Lab Safety Training"], max_hours=4, weekly_cap_hours=8),
            Resource(owner_id=o, name="Seminar Room B-204", category="Space", location="Block B, 2nd floor",
                     description="30-seat room with projector.", rules="Leave the room as found. No food.",
                     requirements=[], max_hours=4, weekly_cap_hours=8, requires_approval=False),
            Resource(owner_id=o, name="DSLR Camera Kit", category="Media", location="Media Cell",
                     description="Camera body, 2 lenses, tripod, 2 batteries.",
                     rules="Return with all accessories. Memory card is yours to clear.",
                     requirements=[], max_hours=4, weekly_cap_hours=6, deposit=1000),
        ])
        db.commit()


if __name__ == "__main__":
    seed()
    print("Seeded. All demo accounts use password: demo123")
