# MDA youth platform — approved design and implementation handoff

Revision: 2026-10-12 (updated the same day with the milestone C decisions in §10). Owner: Adi Koren. Status: reviewed product decisions. Implementation status is tracked in README.md and docs/DECISIONS.md, not in this document.

This revision replaces the earlier Phase-0 proposals. Where this document conflicts with the original CLAUDE.md, PRODUCT_REQUIREMENTS.md, ARCHITECTURE.md, DATA_MODEL.md, OPEN_QUESTIONS.md or DEVELOPMENT_PLAN.md, use this revision. Explicit future instructions from Adi take precedence. Technical details labeled proposed are implementation guidance, not new business requirements.

## 1. Product scope and approved decisions

Build a Hebrew-only, RTL, mobile-first web application for MDA Rishon LeZion, about 240 volunteers. Support all seven days from the beginning; Thursday is only an example, never a hardcoded product restriction. Begin with synthetic data and a real persistent database. Do not import real volunteer or clinical data or deploy a real-data pilot merely because the design mentions one.

Roles: trainee (חניך), youth volunteer (נוער), mentor (חונך), day manager (אחראי יום), youth manager (אחראי נוער), admin (אדמין). One account per person, unique email, one current volunteer level, stacking authority roles, one home-day membership where applicable, and historical transitions. Every day manager is also a mentor. Drivers are not users; the ambulance slot is a schedule position, not a fleet vehicle.

Core scope: signup/approval, schedules and availability for all days, manual scheduling and publication, conditional personal invitations, personal shift history, ride journal and mentoring, offline journal drafts, private/shared personal summaries, notifications and reminders. Implement in milestones without restricting the underlying product to Thursday.

Deferred: MICU, community security duties, attendance tracking, automatic crew recommendations, course modules, internal announcements, and detailed learning content. Provide a clearly labeled learning entry point later; do not invent medical educational content. Photos, file storage and holiday-provider connection can follow the core model. The detailed ride form is a later specification task; journal capability itself remains in scope.

## 2. Resolved business rules and registration calendars

### Registration and availability

- Weekday ordinary self-registration is evening availability, yes/no with optional late-arrival selection. No early-leave field or ambulance selection. Reinforcement is manager-assigned from eligible trainee availability, not an independent signup option.
- Friday, Saturday, holiday and holiday eve: morning and evening buttons, multiple selection, full shifts only. A person may select both. No late-arrival field for these shifts. No reinforcement.
- Availability belongs to the user and individual selectable shift. Assigning evening never clears morning availability. Waiting is calculated separately per shift, including manager-added participants.
- Saturday is open to all active station volunteers without personal invitation. All active volunteers can view the published Saturday schedule, assigned or not. Saturday is an exception to ordinary cross-day access.
- Other-day participation is through a personal invitation, or a direct addition by the authorized manager. Directly added guests receive a request to confirm; confirmation is preferred, not a prerequisite. No refusal-of-assignment feature was requested.
- Invitations appear only when an active request exists; no permanent invitations area or open cross-day signup.
- Registration close is a planning cutoff, not an edit lock. First answers and edits after closure are allowed and MUST notify the relevant manager. They never automatically modify a published assignment.
- When an assigned person withdraws availability, preserve the published assignment until manager action. Show a persistent manager warning: "ביטל זמינות — נדרש טיפול" in addition to notifying. Track resolution; do not clear it merely because the push was sent.
- A manager may add a non-registrant. Preserve their provenance and do not pretend they self-confirmed availability. Manager-added participants remain eligible for the appropriate waiting list after published cancellation.

### Approved default timing templates

All times are local Asia/Jerusalem. Open 5 calendar days before, close 2 days before, target publication 1 day before, with the Wednesday opening exception below. Relative days refer to the days before the actual activity date, including across week boundaries.

| Activity weekday | Opens | Closes | Target publication |
|---|---|---|---|
| Sunday | preceding Tuesday 20:00 | Friday 18:00 | Saturday 20:00 |
| Monday | preceding Wednesday 20:00 | Saturday 18:00 | Sunday 20:00 |
| Tuesday | preceding Thursday 20:00 | Sunday 18:00 | Monday 20:00 |
| Wednesday | preceding Friday **14:00** | Monday 18:00 | Tuesday 20:00 |
| Thursday | preceding Saturday 20:00 | Tuesday 18:00 | Wednesday 20:00 |
| Friday | Sunday 20:00 | Wednesday 18:00 | Thursday 20:00 |
| Saturday | Monday 20:00 | Thursday 18:00 | Friday 20:00 |

Initialize all seven templates, not just Thursday. Admin can edit templates and date-specific timing; the relevant day manager may adjust their day's timing as previously specified. Store effective times on each activity date so template edits do not silently change already-created dates. Holiday/eve defaults use 5/2/1 calendar-day offsets, with editable overrides. The Wednesday 14:00 exception applies when opening is on the preceding Friday; holiday behavior can be made explicit in the calendar configuration.

Opening/closing eligibility is evaluated from timestamps even if the worker is delayed. Target publication creates a reminder, NEVER automatic publication. There is no "registration closing soon" reminder (removed by decision, §10.6).

### Shift templates

| Day kind | Shift | Operating time | Ordinary arrival | Slots |
|---|---|---|---|---|
| Ordinary | Morning | 07:00–15:00 | 06:30 | source baseline: 3 |
| Ordinary | Reinforcement | 12:00–20:00 | 14:30; trainee joining may begin at 12:00 | source baseline: 1 |
| Ordinary | Evening | 15:00–23:00 | 14:30 | source baseline: at least 2 |
| Friday/Saturday/holiday/eve | Morning | 07:00–15:00 | 06:30 | 2 |
| Friday/Saturday/holiday/eve | Evening | 15:00–23:00 | 14:30 | 2 |

Slot counts and volunteer capacities are entered/adjusted by management. Baselines are editable, not staffing prohibitions. Ordinary morning population is managed explicitly; do not infer morning availability from evening registration.

### Scheduling, versions and constraints

- All assignment and boarding/waiting order decisions are manual. Ride count and last ride date are context, not an automatic priority formula. Preserve a manager-editable order per shift.
- Keep a last published schedule plus a separately autosaved working draft. Publish the entire date schedule/version together. Only manual Publish/Publish changes exposes it.
- Published schedule relationships determine volunteer/mentor permissions. Adding/removing a person in an unpublished draft must NEVER change their access.
- Cancellation edited in a draft does not remove live assignments or revoke access until publication. On publication, apply cancellation, waiting state and permission changes atomically and create change notifications.
- Warn before navigating away with unpublished edits. Browser exit detection is best effort. Send a manager reminder after 10 minutes without editing activity while dirty unpublished changes remain. Use persisted draft activity and deduplication to avoid reminder floods. Reminder cadence after the first notification is an implementation detail to document.
- HARD BLOCK: one active assignment per person per shift within one schedule version. A person cannot occupy two ambulance slots in the same shift. Moving a person replaces their draft assignment.
- HARD BLOCK: a trainee cannot be assigned to both reinforcement and evening on the same date/version.
- Capacity is a SOFT WARNING: "הוגדרו 2 מקומות, אך משובצים 3 מתנדבים". Managers may keep the over-capacity crew and publish. Remove old slot_full rejection and last-seat-only-one-wins tests.
- Do not block crew role composition, a trainee without a formal mentor, or legitimate morning/evening selections. Do not impose a blanket one-assignment-per-date constraint.

## 3. Approved stack and architecture rationale

| Component | Choice | Rationale/tradeoff |
|---|---|---|
| Backend | Python + FastAPI | Python is required; FastAPI supports explicit API/service structure. Authentication and admin UX need deliberate implementation; no speculative time estimate. |
| Database | PostgreSQL | Shared persistence and row-level concurrency controls for assignments and versioned writes; more setup than SQLite. |
| DB access | SQLAlchemy 2.x, synchronous | Straightforward transactions for the initial scale. Keep blocking DB calls off the async event loop using appropriate FastAPI execution paths. |
| Migrations | Alembic | Version and review database schema changes alongside code. |
| Frontend | React + TypeScript | Interactive RTL screens and local offline drafts; separate frontend maintenance. Vite is a proposed build tool. |
| Login | Server-side sessions + persistent secure cookie | Remain logged in across normal app closures; revoke sessions independently of access decisions. |
| Background work | PostgreSQL outbox + Python worker | Persist data change and notification intent atomically, deliver after commit, retry failures. |
| Authorization | Central Python access module | One consistent implementation for resource, action, field and query visibility. No external policy engine/RLS initially. |
| Progress | Derived from saved rides, with audited manager corrections | No drifting browser counters; everyone begins at zero, no legacy import baseline. |
| Waiting | Derived per shift plus persisted manual order | Availability is independent from assignment; include manager-added participants. |

### Why a modular monolith, not microservices

Approved by Adi on 2026-10-12. One Python codebase and PostgreSQL database, with API and worker processes. A separate worker process does not imply a business microservice. React remains a separate presentation layer.

The project has one station and no independent service teams/release requirements. Registration, assignment, access and mentoring share strongly related business state. In a monolith, publishing cancellation and outbox intent can commit in one transaction instead of requiring distributed coordination. There is no measured need for independent domain scaling. Local development, tests, deployment, monitoring and recovery are simpler with fewer independently operated components.

Tradeoffs: backend modules share deployment and database resources; failures may affect multiple flows. Maintain explicit module/service interfaces for accounts, access, registration/scheduling, mentoring, notifications and audit. Avoid arbitrary cross-module writes.

Reconsider extraction if independent teams/releases, measured load, isolation requirements or reuse across products justify it. Notifications could be extracted first, but would require event/API contracts, data ownership, retry and observability design. User count alone never decides the architecture.

A policy engine such as OpenFGA can support relational and time-conditioned access; our reason to defer it is avoiding another model/integration now, not lack of capability. PostgreSQL RLS is optional future defence-in-depth; it is not enabled as an assumed guarantee.

## 4. Authentication and access policies

### Signup, approval and sessions

Share an application link. Signup creates a pending account. Pending users access only their own onboarding/profile; no schedules or others' data. Initial admin approves, assigns day/level and activates accounts. Later, youth/day managers approve within their assigned authority. Public signup cannot self-grant privileged roles.

Admin is an ongoing full-control role, not bootstrap-only, with a separate administration interface. Bootstrap the first admin through a documented private setup command/environment procedure, never a public admin signup or committed password. This setup mechanism is an implementation proposal.

Email/password authentication; display full name. Persistent HttpOnly Secure cookies in HTTPS deployments, CSRF protection and login rate limiting. Store session-token hashes rather than exposed credentials. Session inactivity expiry: 30 days since meaningful user activity, with logout, browser data clearing and server revocation ending access earlier. Background polling/worker activity must not keep an idle user signed in indefinitely. App closure alone does not log out. Password recovery is required before a real-data pilot.

### Role authority and privacy

- Admin can view, edit and change ALL application data, including unshared personal summaries, roles, dates, drafts, schedules and mentoring. This is an audited override of role scopes/privacy/time windows, not permission to corrupt structural data or bypass the approved scheduling invariants.
- Admin-only operations/audit may include private summaries. User-facing privacy text must state that unshared notes are accessible to the trainee and admins. Do not claim trainee-only privacy.
- Youth manager reads published schedules across all days (main focus), registrations and progress summary (count/last ride date). No drafts, journal content or schedule edits. Their authority action is appointing/replacing/removing day managers. Registration approval in the later rollout must respect this explicit delegated onboarding authority.
- Day managers manage their scoped days, change youth/mentor levels within scope and read the mentoring history of their current home-day trainees. Home-day transfer moves this access: previous manager loses it. A guest-day manager receives only progress summary for a guest trainee.
- Only the trainee's current home-day manager (or admin override) may reopen mentor journal access, even for another day's shift.
- Private personal summaries remain hidden, including existence in feeds/counts, from non-admin managers until shared with the current home-day manager. Revocation removes subsequent access through sharing.

### Access module contract

Suggested files: context.py (trusted actor data), policies.py (named action/resource/field rules), queries.py (SQL visibility predicates), dependencies.py (FastAPI integration), clock.py (injectable time). All protected endpoints, lists/counts/searches and authorized files use the module. Client-supplied roles are not trusted. Deny by default; action permissions are distinct from visibility. Check current state within mutation transactions where races matter.

Role grants compose by relevant context: a manager who is also a mentor gets each legitimate permission. No broad role label grants unrestricted journal access. A resource concealed from the actor may return 404; an action denied on a visible resource returns 403. Logged-in status and data access are separate.

### Schedule/journal/history visibility

| Resource | Volunteer / trainee | Mentor | Day manager | Youth manager | Admin |
|---|---|---|---|---|---|
| Schedule draft | No | No from mentor role | Own managed day, edit/publish | No | All, edit/publish |
| Published home-day schedule | Permanent for current home-day members | Same membership rule | Own managed days | All days, read-only | All |
| Published Saturday schedule | All active volunteers | All active volunteers | Read; manage only with Saturday scope | Read | Full |
| Ordinary guest full date schedule | Assigned guest: publication through 23:59 of activity date | Same guest rule, distinct from journal window | Managed date | Read | Full |
| Own shift history | Permanent personal history from published, non-cancelled participation | Same | Same plus scoped management | No expanded journal rights | Full |
| Registration | Own response | Own response | Managed dates, edit | Read across days | Full |
| Progress count/last date | Self | Crew trainee within window | Home-day trainees and guest progress as specified | Read summary | Full |
| Journal content | Own journal | Crew trainees (evening): per-case history read, current-shift assessments and shift summary edit within window; no previous shift summaries | Current home-day trainees | No | Full |
| Unshared personal summary | Author only among volunteers | No | No | No | Full |
| Shared personal summary | Author | No from mentor role | Current home-day manager while shared | No | Full |

Guest access ends on publication of cancellation or at the end of the date. Implementation interpretation: "through 23:59" includes that minute; use exclusive next-day local midnight boundary, not shift end at 23:00. Saturday/home-day permanent rules are explicit exceptions. This boundary convention must be documented and tested.

Mentor evening journal access begins at 13:30 and ends next local day at 10:00, including guest mentors. Only evening mentoring is implemented; morning and reinforcement mentoring access is intentionally undefined and not implemented (§10.4). Morning/reinforcement registration and scheduling remain unchanged. Use server time, UTC storage and Asia/Jerusalem local boundary computation, with DST tests. A history page never extends mentor access to trainee journals or grants an expired guest the full date schedule.

All volunteers get a personal history button/feed using published assignments that were not cancelled. It represents planned participation; actual attendance is not tracked. Preserve history when moving a volunteer between ambulance slots in the same shift. Historical detail data beyond personal shift context is not a grant to the full station schedule.

## 5. Data model — revised implementation direction

UUID identities; UTC timestamptz for instants, local date for activity dates; validate enums and fields server-side. Keep history and optimistic version fields. Naming/layout below is proposed; preserve requirements even if code uses different names.

### Identity

- users: id, normalized unique email, full_name, phone, password_hash, pending/active/inactive status, approval actor/time, created_at.
- sessions: token_hash, user_id, created_at, last_activity_at, revoked_at, expiry metadata.
- volunteer_level_history: user_id, trainee/youth/mentor, valid_from/to, changed_by.
- authority_roles: user_id, admin/youth_manager/day_manager, validity, granted_by.
- day_memberships: user_id, home weekday, validity. One current membership; do not automatically make Saturday an exclusive home day.
- manager_day_scopes: user_id, weekday, validity. Saturday management separate from universal volunteer eligibility.

### Calendar, registration and schedule versions

- registration_templates: seven weekday rules, local opening/closing/target times and day offsets, validity and creator.
- activity_days: station/date unique, kind, effective registration times, target publication, latest_published_version_id.
- shifts: activity_day, morning/reinforcement/evening, operating and arrival timestamps; unique day/kind.
- registrations: one user/day response container, source, revision and actor/time.
- registration_choices: registration_id, shift_id, available, optional arrival_time where allowed; unique registration/shift. Separate shift choices preserve multiple selections.
- manager_added_participants: or equivalent explicit participant/source state tied to user/shift. Manager addition does not silently rewrite self-declared availability.
- schedule_versions: activity_day, revision, draft/published/superseded, base_published_version_id, last_edit_at, published_by/at. One current working draft; retain publication history.
- ambulance_slots: version_id, shift_id, logical slot identity/ordinal, capacity, active/cancelled. Capacity and status belong to the version; never mutate the published version through a draft edit.
- assignments: version_id, user, shift, slot, state, audit metadata. Unique active user/shift/version. Ensure slot/shift/version references agree, via composite keys or equivalent validated integrity.
- manual_order: user/shift and editable rank; availability persists independently. Choose durable storage so cancellation/republication does not reset the chosen order accidentally.
- invitations/confirmation_requests: user/date or shift, inviter, invited or manager-added source, pending/confirmed/expired state. No refusal feature. Invitation expiry must not revoke a published assignment by itself.
- availability_attention: published assignment/shift, availability revision, pending/resolved, resolution actor/time.

Day/version lock plus uniqueness protects concurrent edits across two different slots. Capacity count is informational with warning, not a blocking constraint. A trainee's reinforcement/evening conflict spans shifts; serialize/check on the date/version, not just one ambulance lock.

### Journal and assessments

All trainees start from zero. No legacy-count setup or synthetic historical rides. Counts and last date come from saved canonical rides with explicit audited corrections by the home-day manager/admin. Only trainees have ride counts. Boarding order is independent.

- rides: client-generated UUID, trainee, shift, occurrence date, sequence number, description, creator, version.
- ride_assessments: ride_id primary/foreign key, markers jsonb, summary, improvement/preservation fields, version.
- ride_contributors: participant mentors plus actual editor/creator tracking. Two mentors normally fill jointly from one phone; no live collaborative editor required.
- progress_corrections: trainee, correction scope/value, reason, actor/time. Do not invent legacy baseline semantics; document reversible correction implementation and validate before exposing complex overrides.
- mentor_access_extensions: mentor, trainee, shift, grantor, time interval, revoked_at.
- personal_summaries: author/trainee, optional shift, text, shared flag, version; admin access explicit.

Trainee edits case/ride description; mentor edits assessment. Link both tables by ride UUID and update only authorized fields. Either trainee or authorized crew mentor may create the ride for the trainee. Mentor-created ride appears to the trainee as awaiting their description after server sync. On create/list, show existing shift rides to reduce duplicate creation. A stable ID prevents retries of one draft from duplicating records, but does not automatically detect two independently created records of the same case.

Allocate sequence atomically on first server save under a trainee lock, with unique trainee/sequence. Retry checks permissions and returns the already saved authorized record, not a silent success for arbitrary ID collision. Sequence is separate from UUID; if offline drafts arrive out of occurrence order, do not claim synchronization sequence is guaranteed chronological. Define display semantics explicitly.

Marker choices: success (✓), failure (✗), not performed (חסר), partial success (ויקס). "חסר" means the action was not performed in this case and is a valid completed answer. Null/unselected ("not selected yet") is distinct from it. Assessed actions: blood pressure, pulse, breathing, forms. Buttons expose labels; jsonb is storage, not UI. Validate allowed keys/values.

Completion: all applicable required fields must be filled (§10.3). Saving partial data is allowed and visible when synchronized; no submission action. Remind each participant about their own missing part, at shift end and at 09:00 the next morning, only while incomplete. Without a formal mentor, no mentor assessment, no shift mentoring summary and no mentor reminders are required.

### Offline journal drafts

Persistent local storage (IndexedDB is proposed), scoped by authenticated account. Stable ride IDs; clear "טיוטה במכשיר — ממתינה לסנכרון" versus server-saved states. Preserve drafts across app closure. Synchronize on reconnection/open while app execution is available; do not promise closed-app background sync on every phone. Server rechecks authentication, roles, field rights and time windows. Expired authorization keeps draft locally and requires reopen/reauth as appropriate. Counts/visibility update only after server confirmation. Separate versions for description and assessment prevent cross-part overwrites; stale writes must preserve input and surface conflict rather than silently overwrite. Switching accounts must not expose another person's local drafts.

### Operations

notification_outbox: event/revision/recipient, payload reference, dedup_key, queued/accepted/failed state, attempts/next_retry. Include schedule version in publication dedup keys so subsequent publications generate legitimate new messages. delivered only with actual provider evidence.

audit_events: actor/action/object/time/minimized metadata. Admin edits audited. Personal-summary audit visible to authorized admins, not leaked into manager activity feeds. Avoid patient text, raw passwords or tokens in logs.

## 6. Transactions, hard blocks and soft warnings

| Operation | Atomic scope / behavior |
|---|---|
| Signup approval | Check approver authority; activate and grant scoped day/level together; audit. Public payload cannot grant admin. |
| Assign/move in draft | Check actor; lock date/version for cross-slot consistency; enforce user/shift/version uniqueness and trainee reinforcement/evening conflict; save assignment/order/audit. Exceeding capacity warns but succeeds. |
| Publish / publish changes | Check authority and expected draft version; validate hard invariants; atomically publish immutable snapshot, switch date pointer, retain previous publication/history, resolve applicable assignment changes and insert outbox events. Stale writes conflict; repeated request produces no duplicate publication. |
| Cancel ambulance | Edit only draft. On publication, cancellation ends applicable live assignments, returns affected users to per-shift waiting, updates derived guest access and produces audit/outbox together. |
| Register/edit | Atomic day response/shift choices. Before opening blocked; at/after closing allowed and flagged late with manager notification intent. Dedup retries; preserve other shift choices. Published assignments unchanged. |
| Withdraw availability | Save availability, persistent attention flag and manager notification intent. No automatic unassignment. |
| Save ride/assessment | Actor/field authorization, idempotent create, atomic sequence allocation and versioned part update; audit; progress consistent. Preserve client draft on failures. |
| Transfer day/revoke sharing | Change current relationship and audit atomically; next request uses updated policy; no session re-login necessary. |

HARD: authorization boundaries (admin override defined above), manual publication, draft isolation, same-person double ambulance assignment in one shift, trainee reinforcement+evening, unique identities/ride sequences and field validation. Full-shift signup on Friday/Saturday/holiday/eve.

SOFT: capacity excess, role composition, trainee without mentor, late registration, guest awaiting confirmation. No algorithmic prioritization. Locks protect consistency, not a slot_full rule; remove unsupported claims about microsecond latency or guaranteed delivery/performance.

## 7. Example cycle — Thursday, not a scope restriction

Thursday 15 October 2026 in Asia/Jerusalem:

| Local time | Event |
|---|---|
| Sat 10 Oct 20:00 | Registration opens automatically. |
| Tue 13 Oct 18:00 | Planning cutoff. Late answers/edits remain possible and notify manager. |
| Before publication | Manager builds private draft, manually orders waiting/boarding and handles capacity warnings. |
| Wed 14 Oct 20:00 | Publication target reminder only. |
| Manual Publish | Home-day schedule visible; assigned guests receive full schedule access; outbox queued. |
| Post-publish edits | Separate autosaved draft, unchanged published access; warning/reminder for dirty draft after 10 minutes idle. |
| Thu 13:30 | Evening crew mentor journal window opens based on published assignment. |
| Thu 14:30 / 15:00 | Arrival / evening operations begin. |
| During shift | Trainee/mentor enter separate ride parts; offline drafts remain local until sync. |
| Thu 23:00 | Evening operations end; guest full schedule remains available for the rest of the date. |
| Thu through 23:59 | Guest full schedule access, unless cancelled in a published update. |
| Fri 00:00 | Ordinary guest full schedule access expires; own history remains. |
| Fri 10:00 | Mentor journal access expires unless authorized extension exists. |

Home-day and Saturday access exceptions remain distinct. Daylight-saving tests construct local next-day midnight/10:00 and then convert to UTC.

## 8. Implementation milestones and acceptance criteria

Start building now; do not reopen approved architecture choices. Incremental delivery is approved, but the first core implementation must support all weekdays and special-day morning/evening choices, with admin approval/UI. Thursday can be a test fixture only.

### Milestone A — foundation, accounts and all-day configuration

Python/API modules, React/TS frontend, local PostgreSQL configuration, Alembic migrations, environment example without secrets, synthetic seed personas, private admin bootstrap. Public signup, pending account state, persistent sessions/30-day inactivity, admin approval/day/level assignment, separate admin interface. Central access module with youth/day/admin scopes. Seed all seven registration templates, approved times, special-day shift templates, editable date kind and timing; no pretend holiday-provider integration.

### Milestone B — complete scheduling flow

Registration buttons, late edits/notifications persisted, per-shift choices, manager-added participants and guest confirmation requests, manual order, versioned slots/assignments, capacity warnings, hard conflict blocks, draft autosave/manual publication, private draft reminders, published views for each role, history. Persist outbox from the start; basic worker handles due reminders/retries. Unconnected phone delivery must be labeled pending/simulated, not delivered.

### Milestone C — journal and offline

Linked rides/assessments, either-person creation, markers, zero-based progress, manager corrections, relevant mentor time windows/extensions, local offline drafts/idempotent sync, shared-phone mentors/contributor tracking, personal notes with admin visibility and revocable day-manager sharing. Resolve detailed form fields and non-evening window anchors before those tests/UI are finalized.

### Milestone D — device integration and pilot readiness

PWA setup and real Web Push trial on iPhone/Android before provider selection. Explain install/permission steps and show notification activation state. Select holiday data source, add approved calendar/overrides, photos/storage as required. Recovery flow, backup and restore test, monitoring, mobile QA and user pilot review. Do not start real-data rollout automatically.

### Meaningful checks

- PostgreSQL persistence across restart; migrations and synthetic seed repeatability.
- Pending users cannot view others' data; no self-elevation. Admin all-data access, including notes, audited. Youth manager sees published schedules/registrations, no drafts/journal/edits; can manage day-manager authority.
- Session persists after app closure; meaningful inactivity expiry after 30 days, logout/revocation; updated roles applied without re-login.
- Seven opening/closing/target templates, Wednesday Friday 14:00 exception, local DST boundaries. Target does not publish.
- Before-open signup blocked; late first signup/edit saved and notification queued. Saturday multiple selections and universal published viewing.
- Evening assignment leaves morning choice/waiting unchanged. Manager-added users return to proper waiting on published cancellation; manual rank retained.
- Concurrent same-user assignment across two slots: at most one succeeds per shift/version. Trainee reinforcement/evening conflict blocked. Distinct users may exceed capacity with warning; publication allowed.
- Draft add/remove/cancel never affects published access, schedule/history or sends change notifications before publication. Atomic pointer switch and retry dedup; republishing another version generates new legitimate notifications.
- Guest has full schedule through 23:59, not just until 23:00; loses access at local midnight/published cancellation. Personal history remains distinct.
- Offline stable-ID retries count one ride; authorized mentor can create first; separate fields do not overwrite; stale/conflicting input preserved; expiry on sync enforced.
- Mentor evening boundaries 13:30 and next-day 10:00; transfer/reopen rules; private-note sharing revoked for day manager while admin retains access.
- Required-part reminders exclude absent mentors, scheduled jobs survive worker restart and notification statuses are truthful.
- RTL pages usable at 360px; tests run against real PostgreSQL where concurrency semantics matter.

## 9. Remaining decisions — scoped, not foundation blockers

Do not claim every detail is resolved. Continue work that is independent of these items and ask only before implementing the dependent behavior:

1. ~~Journal field dictionary~~ — resolved 2026-10-12, see §10.1–10.3.
2. Morning/reinforcement mentoring access: deliberately not defined and not implemented (§10.4). Evening 13:30 → next-day 10:00 is implemented.
3. Trainee eligibility stage for reinforcement remains open; do not create an invented promotion threshold.
4. Notification provider and device acceptance trial. (The closing-soon reminder was removed, §10.6. Schedule-change recipients were decided in §10.7.)
5. Holiday data source and date classification precedence/override rules; initial manual classification is allowed as an implementation step, not completion of automatic holiday support.
6. Backup/recovery targets, deployment provider/budget, retention, file visibility and real-data pilot participants. Resolve before real-data rollout.
7. Invitation expiry/default acceptance timing and Saturday permanent history retention are implementation details to document; do not add refusal or hidden access restrictions.

No additional approval is needed to begin the approved local development milestones. Surface only genuine dependent questions and preserve reversible defaults with clear labels.

## 10. Decisions confirmed for milestone C (Adi, 2026-10-12)

These override any conflicting text above.

### 10.1 Per-ride form
- The trainee enters only a short case description (e.g. "סחרחורת"). There is no trainee field for actions performed.
- The mentor assesses blood pressure, pulse, breathing and forms. Each has four choices: success, failure, not performed (חסר) and partial success (ויקס).
- "חסר" = not performed in this case; it is a valid completed answer. "Not selected yet" stays distinct.
- The mentor writes a separate summary for each case.

### 10.2 Shift-level mentoring summary
- Strengths (שימור) and areas for improvement (שיפור) are recorded once per trainee per shift, not per ride.
- Each field accepts either text or an explicit "אין הערות" (no comments).
- It is stored separately from per-case assessments and is distinct from the trainee's personal notes.

### 10.3 Completion and reminders
- Documentation counts as complete only when all applicable required fields are filled. Partial content can be saved; there is no submission button.
- Reminders go out at shift end and at 09:00 the following morning, only while content is incomplete. Each participant is reminded about their own missing content.
- Without a formal mentor: no mentor assessments, no shift mentoring summary and no mentor reminders are required.

### 10.4 Mentoring access
- Evening only: from 13:30 to 10:00 the next morning, Asia/Jerusalem. Morning and reinforcement mentoring access are not defined or implemented; their registration and scheduling remain.
- During the window, mentors may read historical per-case records of their assigned trainees, but never previous shift-level summaries. They may complete the current shift's assessments and mentoring summary.
- The trainee's current home-day manager can reopen mentor access. The admin override remains.

### 10.5 Ride creation and offline synchronization
- Either the trainee or an authorized mentor may create the ride first.
- Two mentors usually complete the assessment together on one phone, but both have access.
- Trainee and mentor records stay separate, linked by the same ride id.
- Offline drafts persist locally and sync when connectivity and app execution allow. Permissions are rechecked on sync, and drafts are preserved when authorization has expired or a conflict occurs.
- A synchronized ride counts once. Local drafts never change server progress.

### 10.6 Notification corrections
- The "registration closing soon" reminder is removed, including its temporary 24-hour default.
- The mentoring reminders and the unpublished-draft reminder are kept.
- When phone notifications are disabled, a persistent Hebrew notice is shown: "ההתראות כבויות — יש להפעיל כדי לקבל עדכוני משמרות". It comes with activation instructions and never blocks registration.
- In-app-only notifications stay honestly labeled until real phone delivery is implemented and tested.

### 10.7 Schedule-change notification recipients (decided by Adi, 2026-10-12)
- A schedule-change notification reaches both the **assigned** and the **waiting** volunteers of each affected shift. Waiting = the same derived per-shift list the manager sees (pool − assigned in the new publication), including manager-added participants. People who withdrew availability are not waiting.
- Recipients are deduplicated: one message per person per publication, even when the person is affected in several shifts. A retried publish request adds nothing.
- Notifications are sent only after changes are published, never for draft edits, draft cancellations or idle drafts.
- The earlier provisional rule (assigned volunteers only) is removed. Implementation: DECISIONS D-27.
