# ShareIt — Unlock Underused Equipment and Spaces

Single-institution (college labs/equipment) sharing platform: listing, eligibility, booking, approval/waitlist, check-in/return with condition reports, incidents, notifications, fair usage, audit trail, role-based access.

## Run
```
pip install -r requirements.txt
uvicorn app:app --port 8000      # open http://localhost:8000 (auto-seeds demo data)
pytest -q test_flow.py           # end-to-end tests
```
Postgres: set `DATABASE_URL=postgresql+psycopg://user:pass@host/db`.
Env knobs: `CHECKIN_EARLY_MIN` (30), `NO_SHOW_MIN` (30), `MAX_ACTIVE_BOOKINGS` (3).

## Demo accounts (password `demo123`)
asha@demo.edu (student, eligible) · ben@demo.edu (student, no credentials) · chen@demo.edu (unverified) · owner@demo.edu · manager@demo.edu · admin@demo.edu

## Judge walkthrough
1. Sign in as **owner** → Resources → "+ List resource" (or use seeded ones).
2. Sign in as **asha** → open "Oscilloscope Bench" → book (default start is +2 min) → status *pending*.
3. **owner** → Bookings & requests → Approve (reason shown to the user).
4. **asha** → Check in (condition: good) → Return (condition: damaged) → auto damage incident.
5. **owner** → Details → Resolve incident. Both parties see the full activity history; **admin** sees the global audit log.

Also try: **ben** (blocked: missing credential → submit under Credentials → owner verifies), two users on one slot (waitlist → auto-promote on cancel), weekly cap and max-hours limits.

## Known limitations
Single institution; no payments (deposit is informational); one active session per user; no login rate limiting; no AI features (all decisions are rule-based and human-reviewable); overdue/no-show/reminders are computed lazily on API reads, not by a scheduler; in-app notifications only (no email/push).

## Deploy (Docker, SQLite on a persistent volume)
The image stores data at `/data/shareit.db`; mount a persistent volume at `/data`.
- **Render:** push to GitHub → New → Blueprint → select repo (uses `render.yaml`; disks need the paid Starter plan).
- **Railway / Fly.io:** deploy the Dockerfile, attach a volume at `/data`.
Without a volume the database resets on each redeploy (fine for a quick demo, since demo data re-seeds).
