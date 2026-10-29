# Open questions and decision register

These decisions are unresolved. Do not treat assistant suggestions as user approval. Resolve each before implementing the dependent behavior; unrelated work can continue.

## Early design decisions

| Question | Why it matters | Provisional direction |
| --- | --- | --- |
| Is FastAPI + PostgreSQL + React acceptable? | Stack and development setup. | Recommended modular monolith; user confirms. |
| Full-name login or unique email? | Duplicate names and recovery. | Email identity; show full name. Confirm login UX. |
| What can an unassigned new user see? Who grants privileged roles? | Prevent self-granted access. | Pending users see only their own onboarding/profile; bootstrap manager separately. |
| Which weekdays have home-day membership, and how does Saturday work? | Earlier source says Saturday open across days; latest discussion only confirms inclusion. | Separate Saturday eligibility from home-day permissions; confirm. |
| Exact registration schedules for each day? | Jobs and local dates. | Only Thursday example known; make templates configurable. |
| Does manager enter available seats directly? | Capacity model. | Integer volunteer capacity; do not infer crew occupants. |
| Can a trainee serve reinforcement then evening on one date? | Assignment uniqueness and journal context. | No blanket per-date assignment limit until resolved. |

## Scheduling and access

- Whether an invitation is a request, requires acceptance, and expires before or after schedule publication.
- How a late availability change or withdrawal affects an existing published assignment. Notification is confirmed; automatic reassignment is not.
- Mentor window starts one hour before arrival or shift start; do not hardcode one interpretation.
- Whether mentor cancellation ends journal access and how shared historical authorship is preserved.
- Whether whole-shift notification recipients include only assigned volunteers or all registered/waiting volunteers. Crew-only targeting is not the confirmed requirement.
- Manual versus automatic escalation when a new ambulance needs people: manual is a proposal.
- Youth oversight and day-manager scope, particularly managing guests.

## Journal and privacy

- Exact definitions of ✓, ✗, חסר and ויקס, and which fields trainees can edit.
- What constitutes incomplete mentoring for reminders when there is no submit action.
- Who may correct previously written assessments and for how long.
- Manager progress correction semantics: baseline historical count, current count override, last-date correction and audit.
- Display sequence after deletion/import; retention and archival rules.
- Contact/photo visibility and handling account deletion without silently destroying history.
- Clinical description form is deferred; use fictional non-identifying cases only.

## Nonfunctional decisions

- Live entry during a shift versus end-of-shift entry; offline needs remain unanswered.
- Authentication/recovery mechanism and initial account verification.
- Phone notification channel and mobile installation requirements.
- Acceptable downtime/data loss, recovery targets and backup frequency.
- Hosting budget, provider, environments and expected peak concurrency.
- Calendar provider, holiday rules and who may override date classification.

## Scope decisions

Both scheduling and mentoring are core. Deferring summaries and learning content was suggested, not approved. Learning content can initially be a clearly labeled entry button. Internal announcements, MICU, community security, course modules, attendance tracking and crew recommendations are deferred.

## Decision log template

For every resolved item record: decision, confirmed by whom/date, rationale, affected requirements/schema, and whether it is a demo-only assumption. Do not mark an unresolved item accepted merely because implementation chose a default.

## Status register (2026-10-24)

Resolved means a recorded decision in `docs/DECISIONS.md` (or the product constraints in `CLAUDE.md`) answers the question. Open means no decision is recorded; the recommended default is a proposal until you confirm it.

| Question | Status | Source |
| --- | --- | --- |
| Stack: FastAPI + PostgreSQL + React | Resolved (in use) | ARCHITECTURE; implemented |
| Login by email or full name | Resolved: email identity | D-10 |
| What an unassigned user sees | Resolved: pending users see only their own profile | D-11 |
| Who grants privileged roles | Resolved: admin-only approval; day managers approve only their weekday (no roles); temporary Day Managers by Admin or Youth Manager | D-14, D-73; signup approval (2026-10-24, not yet logged in D-log) |
| Registration schedules per day | Resolved: configurable templates, one current row per weekday | D-04 |
| Capacity model | Resolved: integer capacity, 0–3 | D-48 |
| Invitation expiry | Resolved: expires at local midnight after the date | D-24 |
| Late availability change or withdrawal of a published assignment | Resolved: manager attention, no automatic reassignment | D-23, D-61 |
| Republication notification recipients | Resolved | D-27, D-62 |
| Scheduling by hand vs automatic | Resolved: manual (product constraint) | CLAUDE.md, PRD |
| Mentor window | Recorded as 13:30 to 10:00 next day; open question says do not hardcode | D-34 — confirm |
| Reminders for incomplete mentoring | Resolved: completeness defined, reminder timing set | D-37, D-38 |
| Manager progress correction | Resolved: baseline count, corrections, last-date rule, audit | D-39 |
| Personal notes author | Resolved: the trainee | D-40 |
| Phone visibility | Resolved: shown to managers of that day only | D-13 |
| Notification channel | Resolved: Web Push | D-71 |
| Sessions and login limits | Resolved | D-07, D-09, D-52 |
| Offline entry | Resolved for drafts; live-entry rules open | D-41, D-44, D-45 |

Open, needs your decision (recommended default in brackets):

1. Saturday eligibility: may every active volunteer register and see Saturday, regardless of home day? [yes, as implemented].
2. Mentor window start: keep 13:30 on the shift date, or one hour before arrival? [keep 13:30 as recorded].
3. Mentor cancellation: does it end the mentor's journal access, and how is authorship of shared records kept? [end access, keep authorship].
4. Same trainee in reinforcement then evening on one date: allow or block? [allow; no blanket limit].
5. Meaning of ✓, ✗, חסר and ויקס, and which fields trainees may edit. [use the labels as written; trainee edits only description].
6. Who may correct a written assessment, and for how long. [the mentor who wrote it, for 24 hours; managers any time, audited].
7. Youth oversight scope, including whether it covers guests. [progress only, no content; guests included in progress].
8. Photos, and what happens to history when an account is deleted. [no photos now; deactivate, never delete history].
9. Invitation: is a manager's invitation a request needing acceptance, or an assignment? [invitation; answering availability confirms it, as recorded in D-24].
10. Authentication recovery: how a user regains access without a password reset page. [manager-initiated reset; not built yet].
11. Calendar: holiday source and who may override a day's kind. [manual classification by the manager/admin, as D-05].
12. Operations: hosting, budget, backup frequency, downtime and recovery targets, and expected concurrency. [not decided; needs your input].
13. Scope deferrals: summaries and learning content. [defer summaries; keep the learning entry button only].

Decision log for each resolved answer: keep it in `docs/DECISIONS.md`; add a new entry when an open item is confirmed.

### Answers recorded (2026-10-24, D-81)

1 yes; 2 one hour before arrival, per shift (implementation pending); 3 yes; 4 allow; 5 default labels, ויקס drawn as ✓ and ✗ together; 6 default; 7 progress only; 8 photos later; 9 yes; 10 temporary credential by email (email delivery not built yet); 11 default; 12 still open, see below; 13 yes.

Item 12 needs: hosting provider and budget; backup frequency; acceptable downtime and data loss; expected number of concurrent users at peak.

Update: backups every day at midnight, kept 30 days (D-82). Still open for item 12: hosting provider and budget, acceptable downtime and data loss, expected peak load.

Update: hosting chosen, lowest cost: Hetzner server + Backblaze B2 backups (D-83). Open: Hetzner plan confirmation and server location (data residency), and approval to create the paid resources.
