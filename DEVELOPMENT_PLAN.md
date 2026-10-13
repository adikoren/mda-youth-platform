# Development plan and acceptance checks

Status: proposed sequence, not a finalized release commitment. Scheduling and mentoring are both core product flows. Final prioritization of summaries, learning tools and notification work needs agreement.

## Phase 0 — design review

Review all files, explain architecture tradeoffs, resolve contradictions, and document provisional choices. Produce an entity diagram, access matrix and one detailed Thursday flow. Confirm the first implementation slice. Do not spend the initial phase choosing a production provider before the product model is understood.

## Phase 1 — foundation and accounts

Create frontend/backend structure, local PostgreSQL setup, migrations and synthetic seed users. Include trainee, youth, mentor, overlapping manager/mentor and guest accounts. Implement scoped authorization and pending-account behavior once agreed.

Acceptance: persistent accounts after restart; multiple roles work; unauthorized direct requests fail; home-day transfer preserves history and moves manager access.

## Phase 2 — one complete scheduling cycle

Build activity dates, configurable registration times, evening responses, available slots, manual crew assignment, waiting, manual publication and read-only volunteer schedule. Include conditional invitation cards, guest access expiry, late-edit notification records, slot addition and cancellation.

Acceptance: drafts stay hidden; time alone never publishes; registration is unique; cancellation returns users to waiting atomically; guest access ends correctly; home-day published access remains; concurrent assignment does not exceed capacity. No auto crew recommendations.

## Phase 3 — cumulative mentoring journal

Implement role-specific ride entry, mentor history access, current assessment editing and manager extensions. Add sequence allocation, automatic progress and explicit manager corrections. Implement conflict handling for two mentors.

Acceptance: one ride is counted once despite retries/concurrent edits; youth cannot assess; trainee can enter permitted descriptions; history access obeys window; previous managers lose access after transfer; a shift without rides does not change the last ride date.

## Phase 4 — notifications and scheduled work

Build persistent outbox and retryable jobs. Then connect the chosen phone channel and test real iPhone/Android behavior. A demo adapter must be labeled simulated. Integrate the chosen holiday calendar with explicit override behavior if agreed.

Acceptance: worker restart does not lose due work; retries do not duplicate logical notifications; delivery statuses are truthful; late edits notify the manager; schedule changes target the whole defined shift. Incomplete-journal reminders depend on a resolved completion rule.

## Phase 5 — secondary flows and pilot readiness

Personal summaries with revocable sharing, profile/contact features and a learning-tool entry button, according to the agreed scope. Before real data: recovery/authentication flow, privacy/retention decisions, backups and restore test, monitoring, mobile usability and load checks. Choose pilot participants with the station.

Acceptance: a private summary and its existence remain private; revoked sharing denies access on subsequent requests; core pages work in RTL on a narrow screen; demonstrated recovery and tested access controls.

## Test strategy

Prioritize backend policy tests, transaction/concurrency integration tests with PostgreSQL, time-boundary tests with controlled clocks, and a small number of end-to-end scheduling/journal flows. Cover Asia/Jerusalem daylight-saving boundaries and next-day 10:00 expiry. Avoid tests that only repeat code structure.

## Suggested initial repository layout

```text
backend/
  app/
    accounts/
    access/
    registration/
    scheduling/
    mentoring/
    notifications/
    audit/
  migrations/
  tests/
frontend/
  src/
docs/
```

This is a proposal to adapt to the repository, not mandatory scaffolding. Keep domain logic in services/policies rather than duplicating it across routes and components.
