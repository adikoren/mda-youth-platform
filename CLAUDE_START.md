# Claude Code — start implementation

Adi reviewed the design on 2026-10-12 and authorized starting development. Read the attached revised DESIGN_REVIEW.md first. It supersedes conflicting statements in the earlier handoff. Keep the project Hebrew-only/RTL/mobile-first; explain progress to Adi in Hebrew.

## Your task

Inspect the existing repository and its instructions. If code exists, adapt it without overwriting unrelated work. If no project exists, initialize the approved modular monolith. Build milestone A, then the complete scheduling flow in milestone B; continue incrementally toward C/D. Deliver working database-backed flows, not only a design response or mocked screens.

Approved stack: Python/FastAPI, synchronous SQLAlchemy 2.x, PostgreSQL, Alembic, React/TypeScript, persistent server sessions with 30-day inactivity expiry, central Python access module, PostgreSQL outbox + Python worker. No microservices or policy engine initially.

## Critical corrections to the original design

1. ALL DAYS from the beginning. Seed the seven exact calendars in section 2, including Wednesday opening Friday at 14:00. Thursday is a fixture, not an implementation boundary.
2. Separate admin interface and full application-data read/write, including unshared personal notes. Signup stays pending until initial admin approval. No public admin signup. Later delegate approval per scope. Youth manager reads published schedules/registration/progress, no draft/journal or operational edit access; can appoint/replace/remove day managers. Day managers are mentors.
3. Availability is PER SHIFT; Saturday/Friday/holiday/eve have morning/evening multiple-select buttons, full shifts only. Assigning one never clears the other. Waiting derived per shift, manager-added participants included, manual ordering persists.
4. Saturday published schedules visible to ALL active volunteers. Ordinary guests see the full schedule through 23:59 of the date, with local midnight as exclusive end. Permanent personal history is distinct from the full date schedule and mentoring access.
5. Published immutable snapshot + separate autosaved working draft. Manual Publish changes is mandatory. Only published relationships control access. Draft cancellation has no live effect. Dirty draft gets a reminder after 10 minutes of editing inactivity; navigation warning best effort.
6. Capacity excess WARNS, does not block assignment/publication. HARD blocks: same user in two slots of one shift/version and trainee reinforcement+evening on one date/version. Remove old slot_full tests.
7. First late registration and all late edits allowed, notify manager. Withdrawing availability preserves assignment and shows persistent "ביטל זמינות — נדרש טיפול". No refusal-of-assignment UI. Direct guest addition allowed; confirmation preferred, not required.
8. Trainee rides and mentor assessments linked but separately editable. Either may create first. Stable client UUID supports retry; return existing record safely. Two mentors normally write together on one phone, with both having access. No live collaborative editor. Everyone starts from zero; preserve correction audit. Markers: success/failure/missing/partial plus unselected.
9. Offline journal drafts persist locally, sync when execution/network available, count only after server confirmation. Keep drafts on denied/conflicting sync. Evening mentor window is 13:30 through next-day 10:00; current trainee home-day manager can reopen, admin can override.
10. "Private" notes are visible to author and admins; managers see only shared notes. Privacy text/audit visibility must reflect this.

## Implementation delivery

- Establish schema/version and access boundaries before screens. Use migrations and meaningful PostgreSQL policy/concurrency tests.
- Build signup/login/pending/admin approval, roles/scopes, all-day calendars, then draft/published scheduling views and personal history.
- Use deterministic clocks for time tests. Verify list/count/search authorization, not just detail requests.
- Persist outbox intent during transactions; send afterward. Schedule-version-aware deduplication must allow later publication notifications. Do not promise exactly-once external delivery.
- Keep credentials private; provide safe .env.example and local startup instructions. First-admin creation uses a private setup command, no hardcoded shared password.
- Record short implementation decisions for reversible technical details. Do not reopen approved decisions. Ask only for relevant blockers in section 9 when the dependent feature is reached.
- Run appropriate checks and report working features, validation and remaining limitations. Do not claim push/offline/device flows work before testing them. Do not deploy publicly, contact volunteers or import real personal data without an independently authorized rollout.

The revised design is ready to build. Begin implementation rather than another Phase-0 review.
