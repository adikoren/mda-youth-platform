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
