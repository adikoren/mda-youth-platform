# MDA youth volunteers — scheduling platform (prototype)

> מערכת שיבוץ למתנדבי נוער, מד״א ראשון לציון. אב־טיפוס עם **נתונים סינתטיים בלבד**.
> Milestones A (accounts, access, all-day calendar), B (registration, draft/publish scheduling) and
> C (ride journal, mentoring, offline drafts, personal notes) are implemented. Real phone notifications (D) are not.

Hebrew-only, RTL, mobile-first web app. FastAPI + synchronous SQLAlchemy 2 + PostgreSQL + Alembic, React/TypeScript (Vite), with a PostgreSQL outbox and a Python worker. Modular monolith.

**Deployment:** prepared for DigitalOcean App Platform but not deployed — see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and [`.do/app.yaml`](.do/app.yaml).

**Read first:** [`DESIGN_REVIEW.md`](DESIGN_REVIEW.md) (approved design, 2026-10-12), [`CLAUDE_START.md`](CLAUDE_START.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md) (implementation decisions). The older handoff files (`PRODUCT_REQUIREMENTS.md`, `ARCHITECTURE.md`, …) remain for background; the revised design wins where they conflict.

## Layout

```text
backend/
  app/
    access/         central authorization: context, policies, SQL visibility queries, clock, FastAPI deps
    accounts/       signup/login/sessions, levels, home days, authority roles
    days/           seven weekday templates, shift templates, activity days
    registration/   per-shift availability, invitations, attention flags
    scheduling/     draft/published versions, slots, assignments, pools, publication
    mentoring/      ride journal, per-case assessments, shift summaries, notes, progress, extensions
    notifications/  outbox + worker (reminders, delivery state)
    audit/          minimized audit log
    admin/          admin interface API, youth-manager appointments, calendar config
    cli.py          private setup commands (first admin, seed, dates)
  migrations/       Alembic (0001 schema + immutability trigger, 0002 journal/mentoring, 0003 ride cancellation, 0004 morning registration, 0005 registration requests)
  tests/            pytest against real PostgreSQL
frontend/           React + TS, Hebrew RTL screens; public/sw.js (offline shell), src/merge.ts (conflict merge), tests/
docs/DECISIONS.md
```

## Run locally

Requirements: Python 3.11+, Node 20+, and PostgreSQL 16 (Docker is the easiest way to get it).

```bash
# 1. Database (local only, bound to 127.0.0.1)
cp .env.example .env                 # set MDA_DB_PASSWORD
docker compose up -d db              # creates databases mda and mda_test

# 2. Backend
cd backend
cp .env.example .env                 # use the same password in both URLs
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.cli init                                   # station + 7 registration templates
python -m app.cli create-admin --email you@example.com --name "שם מלא"   # password is prompted, never stored in files
python -m app.cli generate-days --start 2026-10-11 --end 2026-11-30
uvicorn app.main:app --reload --port 8000

# optional: synthetic demo personas (prints a generated password once)
python -m app.cli seed-demo

# 3. Worker (separate terminal; reminders + outbox processing)
python -m app.notifications.worker          # or --once for a single pass

# 4. Frontend (separate terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api to :8000)

# To try offline reopening, use the production build (the service worker is not used in dev):
cd frontend && npm run build && npm run preview   # http://localhost:4173 (proxies /api to :8000)
```

Demo personas (after `seed-demo`): `admin@example.test`, `youth.manager@example.test`, `<day>.manager|mentor|youth|trainee1..3@example.test` for `sun mon tue wed thu fri`, `pending@example.test`. Saturday is managed by `fri.mentor@example.test`.

## Tests

```bash
cd backend && pytest          # uses MDA_TEST_DATABASE_URL (must end with _test; it is wiped each run)
cd frontend && npm run build  # type-check + production build
cd frontend && npm test       # conflict-merge unit tests (Node 23+ built-in runner)
```

121 backend tests run against real PostgreSQL, grouped by area:

- **Calendar and time:** the seven registration calendars, and the 24–25 Oct DST night.
- **Accounts and access:** sessions, CSRF, rate limiting, pending/admin/youth-manager/day-manager scopes.
- **Scheduling:** registration rules, draft/publish isolation, hard blocks and capacity warnings, guest windows and list visibility.
- **Journal (milestone C):**
  - the mentor window boundaries (13:29/13:30/09:59/10:00);
  - that only formal mentors of the same ambulance get access, morning shifts give none, and a draft grants nothing;
  - historical per-case records being visible to mentors, while previous shift summaries are not;
  - idempotent ride creation, concurrent retries and unique sequences;
  - two mentors sharing access, with stale writes returning a conflict;
  - marker validation, completeness rules, and reminders at shift end and 09:00;
  - window expiry on sync, and reopening by the home-day manager or admin;
  - progress and audited corrections, and personal-note privacy and sharing.
- **Publication notifications:** assigned and waiting recipients, one message per person per publication across shifts and retries, and no notifications from draft edits (including edits that cancel each other out).
- **Duplicate rides:** who may cancel, audit, progress and completeness, refused edits, offline retries that never revive a cancelled ride, restore, DB constraints, and the mentor access-end hint for offline copies.
- **Worker:** reminders, retries, truthful delivery state, and the removal of the closing-soon reminder.

A browser run at 360px also covered both milestones: the scheduling flow, then an offline ride draft (not counted), a sync after reconnecting (counted once), mentor assessment and summary, and the notifications-off notice.

A second browser run at 360px (production build, 2026-10-12) covered the new work:
- **Offline reopening:** with both servers stopped, the app reopened from the service worker and the mentor's device copy, and an assessment draft was saved locally.
- **Auto-merge:** after the restart it merged with another mentor's concurrent edit of a different marker, with nothing lost.
- **Real conflict:** a same-field conflict showed the per-field chooser, and the choice was saved.
- **Duplicate ride:** a manager cancelled a duplicate ride through the UI.

## Honest status

- Phone notifications are **not connected**. Messages are stored and shown in-app as "מוצג באפליקציה בלבד" (status `simulated`); none is claimed as delivered.
- No real personal data, no deployment. `MDA_COOKIE_SECURE=true` is required for any HTTPS deployment.
- Offline journal drafts are kept in IndexedDB and synced on reconnect/open. With a production build the app can be **reopened** without a connection (service worker + bounded device copy, D-44). Tested in a desktop browser only, not yet on an iPhone/Android device. There is no closed-app background sync.
- The notifications-off notice always shows for now, because no phone channel exists. Milestone D (PWA/Web Push device trial, password recovery, backups, deployment) is not started.
- Schedule-change notifications go to the assigned **and** waiting volunteers of each affected shift, one message per person per publication, only after publication (decided 2026-10-12, D-27).
