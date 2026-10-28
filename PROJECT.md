# ShareIt — Project Document

**Problem statement:** Unlock Underused Equipment and Spaces

## 1. Selected use case
A single college (labs, makerspace, media cell, seminar rooms) sharing underused equipment and spaces with its own students and researchers, under owner control. Scope is deliberately narrow so the whole lifecycle works reliably.

## 2. Target users
| Role | Needs |
|---|---|
| Student / researcher / creator (user) | Find resources they are allowed to use, understand rules, book, know why a request was decided |
| Resource owner (lab in-charge) | List resources with conditions, control approvals, know who uses what and in what condition |
| Manager (staff) | Review requests and credentials, resolve incidents across resources |
| Administrator | Verify accounts, assign roles, inspect the global audit log |

## 3. Why the problem matters
- Institutional equipment sits idle while students cannot afford to buy or rent it. Plain booking tools show availability but not trust: who may use it, in what condition it came back, who is responsible for damage.
- Without controls, institutions face misuse, damage, conflicting bookings and unfair allocation (a few users hold scarce slots).
- *Evidence to add before submission (team to fill):* idle-hours figures or a short survey from your own college labs.

## 4. Proposed solution
A resource-sharing platform where every access decision is rule-checked, explained to the user, reviewable by a human owner, and written to an audit trail.

## 5. Complete product workflow
1. Owner lists a resource: location, rules, required credentials, max hours per booking, weekly cap, approval mode, deposit.
2. User searches/filters (text, category, free time window, "only what I'm eligible for") and sees an eligibility verdict with the exact missing credentials.
3. User submits a booking (time, purpose).
4. System checks: account verified, credentials verified, limits, open incidents, conflicts.
5. Outcome is *auto-approved*, *pending owner review*, or *waitlisted*, each with a stored plain-language reason.
6. Owner/manager approves, rejects (reason required) or waitlists. Approving a slot auto-waitlists competing pending requests, with notifications.
7. User checks in and records initial condition (check-in opens 30 min before start).
8. User returns and records final condition. A worse condition automatically opens a damage incident.
9. Delay (overdue), no-show, damage and conflict incidents are recorded and resolved by an owner with a note. An open damage/delay incident pauses new bookings by that user until a human resolves it.
10. Both parties see the booking's full history; admins see the global audit log.

## 6. Features developed during the hackathon
Resource listings with rules and requirements; search and filters; account verification; credential submission and review; booking with approval, rejection, waitlist; competing-request handling; extensions; cancellations with waitlist promotion; check-in/return with before/after condition; incident reporting and resolution; in-app notifications (approvals, reminders, cancellations, overdue); fair-usage limits; role-based access; per-booking and global audit history; privacy controls (below); automated tests.

## 7. Technology stack and architecture
- **Backend:** Python, FastAPI, SQLAlchemy 2. One module (`app.py`) with REST endpoints under `/api`.
- **Database:** SQLite for local runs; PostgreSQL via `DATABASE_URL` for deployment.
- **Frontend:** React 18 + htm loaded from esm.sh, single `static/index.html`, no build step. Served by the same FastAPI app.
- **Auth:** email + password (PBKDF2-SHA256, salted, 100k iterations); random bearer token stored only as a SHA-256 hash.
- **Scheduling:** lazy sweep on API reads computes reminders, overdue and no-shows.
- **Tests:** `pytest` end-to-end suite (`test_flow.py`).

Data model: users, creds, resources, bookings, incidents, audit, notifications.

## 8. AI tools, APIs and external services
No AI or external APIs are used. All decisions are deterministic rules, so every outcome is explainable and reviewable. Considered for future work: eligibility-document review and incident summarisation, with human sign-off.

## 9. Privacy, security and fairness
- **Privacy:** borrowers' emails and usage history are visible only to the owner/manager of that resource. Other users see anonymised busy slots only. Bookings, incidents and history are visible only to the two parties and staff.
- **Security:** hashed passwords and tokens; role checks on every endpoint; admin-only user management and global audit; unverified accounts cannot book.
- **Fairness:** per-booking hour limit; weekly per-user cap per resource; max 3 open requests per user; waitlist and review order by least recent usage (last 30 days) then request time; every decision stores a visible reason.
- **Human oversight:** approvals, rejections, credential verification and incident resolution are always made by an authorised person. The system never applies penalties on its own; it only pauses new bookings while an incident awaits human review.

## 10. Known limitations
Single institution; no payments (deposit is informational); one active session per user; no login rate limiting or email verification; no email/push delivery; overdue/no-show checks run on API reads, not a scheduler; no file upload for credential evidence; no calendar view.

## 11. Future development
Multi-institution tenancy; credit-based allocation; document upload with assisted review; email/push; calendar UI; anomaly detection for suspicious usage; SSO.

## 12. Testing instructions
See `README.md`. Demo accounts (password `demo123`): asha@demo.edu (eligible student), ben@demo.edu (no credentials), chen@demo.edu (unverified), owner@demo.edu, manager@demo.edu, admin@demo.edu.
Judge path: asha books Oscilloscope Bench → owner approves → asha checks in → returns as "damaged" → owner resolves incident → open Details to see the full history.

## 13. Team and contributions
*To fill:* names, roles and contribution of each member.

## 14. Disclosure of templates, libraries and pre-existing code
No templates or pre-existing project code. Open-source libraries: FastAPI, Uvicorn, SQLAlchemy, Pydantic, psycopg, pytest, httpx, React, htm (CDN). AI coding assistance (Claude) was used to generate code under the team's direction. *Team to confirm this disclosure is accurate.*
