# Test strategy

Status: in place (2026-10-24). Covers the MDA youth platform: FastAPI backend, React web app, PostgreSQL.

## 1. Layers

| Layer | What it proves | Where | Tooling | Runs against |
|---|---|---|---|---|
| Unit (pure) | Rules that take values in and give values out: time cutoffs, window state, rights matrix, countdown text, lifecycle labels, merge of drafts, push helpers | `backend/tests/` (pure functions), `frontend/tests/*.test.ts` | pytest, Node test runner (`node --test`) | nothing (no DB, no network) |
| Service / integration | Business transactions on real PostgreSQL: publication, cancellation, retention purge, temporary appointment overlap lock, outbox, triggers that freeze published rows | `backend/tests/test_*.py` that use the `world` fixture and `db` | pytest + SQLAlchemy, `mda_test` database | scratch database `mda_test` (wiped by tests) |
| API | HTTP contract and authorization: status codes, error codes, scope (404 vs 403), CSRF, validation (422), cross-day and expired access | `backend/tests/test_*.py` through `fastapi.testclient` (`Client(email)`) | pytest | `mda_test`, real routes and middleware |
| End-to-end | The real app in a real browser: journeys and §12 acceptance scenarios, phone layout, back navigation | `frontend/e2e/` | Playwright Test (`npm run e2e:fresh`) | disposable stack `scripts/e2e-stack.sh` (database `mda_e2e`, API 8001, web 5174, synthetic dataset, recreated on every run) |
| Exploratory / device | Things automation cannot judge: Web Push on a real iPhone, the look of new screens, Hebrew copy | manual | Playwright MCP for guided browser sessions, real device | local stack or the tunnel |

Counts at the time of writing: backend 259 test functions in 40 files; frontend unit tests 20; end-to-end tests 8, each run on the phone and desktop projects (16 runs, all passing).

## 2. Rules for what goes where

- A rule that can be tested without a database belongs in the unit layer. Example: `self_service_rights` is a pure function of the day, the shift and "now".
- Anything that changes persistent state, or depends on a DB constraint or trigger, belongs in the service layer.
- Authorization is tested through the API layer, never by calling a service directly. The HTTP status and the error code are the contract.
- The end-to-end layer checks behaviour a user would notice: a control is present or absent, a label, the order of sections, a navigation result, no horizontal scroll on a phone. It does not repeat every rule. Rules stay in the layers above.
- Each bug fix adds a regression test in the lowest layer that can catch it, plus an end-to-end test only when the bug was a journey problem.

## 3. Test data

- Backend tests build their own world with `tests/conftest.py::world` on `mda_test`, with a `FixedClock` (`clock.set(...)`). Tests never depend on the real date.
- The end-to-end layer uses the synthetic dataset on the local development database (`seed-dataset`, see `docs/DECISIONS.md` D-79 for the rules). Accounts use `@example.test`. The e2e assertions check invariants (structure, rights, wording), not fixed ids or dates, so a re-seed does not break them.
- No real volunteer or patient data in any test. No production URL in any test.
- Credentials come from environment variables (`E2E_PASSWORD`, `E2E_DATASET_PASSWORD`). They are not stored in the repository.

## 4. Commands

```bash
# backend: unit, service and API layers (scratch database)
cd backend && MDA_TEST_DATABASE_URL=postgresql+psycopg://mda@127.0.0.1:5433/mda_test python -m pytest -q tests

# frontend unit tests
cd frontend && npm test

# end-to-end (local stack must be running)
cd frontend && E2E_PASSWORD=... E2E_DATASET_PASSWORD=... npm run e2e

# everything that is automated
cd frontend && E2E_PASSWORD=... E2E_DATASET_PASSWORD=... npm run test:all   # frontend unit + e2e
```

Before a change is called done: the backend suite, `npm test`, `npx tsc --noEmit -p .` and, for UI changes, the e2e suite must pass. A failing test is never skipped to get green; a test that encodes an old rule is updated to the new rule and the decision is recorded.

## 5. Coverage by feature (current)

| Feature | Unit | Service | API | E2E |
|---|---|---|---|---|
| Registration rights (publication, shift start, window) | `self_service_rights` via API tests | yes | `test_self_registration_rules.py` (9 + 2) | planned |
| Late requests after publication | rights matrix | yes | `test_self_registration_rules.py`, `test_registration_requests.py` | planned |
| Countdown and lifecycle labels | `countdownParts`, `shiftLifecycle`, `dayLifecycle` (frontend) | - | - | planned |
| Publication, retention, history | - | `test_retention.py`, `test_publication_*` | `test_schedule_history.py` | - |
| Cancellation (volunteer) | - | `test_cancellation.py` | `test_boarding_and_operators.py` | - |
| Day-scoped access, admin scope | - | - | `test_trainee_day_scope.py`, `test_admin_authorization.py` | manager spec |
| Trainee list search | - | - | `test_trainee_search.py` | manager spec |
| Trainee page (one ride, dropdown, summary last) | - | - | `test_journal*.py` | trainee-page spec |
| Signup approval scope (day-scoped, temporary expiry) | policy | - | `test_signup_approval_scope.py` | manager spec |
| Temporary Day Managers | - | overlap lock | `test_temporary_day_managers.py` | - |
| Web Push | - | - | `test_push.py` | device (manual) |
| Layout and copy on the phone | - | - | - | auth spec (no horizontal scroll) |

## 6. End-to-end suite

`frontend/e2e/`, configured by `frontend/playwright.config.ts`.

- Projects: `phone` (Pixel 7 emulation, 390 px wide, the primary target) and `desktop` (1280 px).
- One worker, sequential: the local database is shared.
- Each test starts from an empty browser context and logs in through the form.
- Traces and screenshots are kept for failures; the HTML report is in `frontend/e2e-report/`.
- The Chromium build comes from the local Playwright cache; `E2E_CHROMIUM` overrides it.
- Specs: `auth.spec.ts` (wrong password, sign-in, no horizontal scroll), `manager.spec.ts` (hub entry to signup approval and back, day-scoped signup list, trainee search and back navigation), `trainee-page.spec.ts` (totals, one ride, summary last, dropdown placeholders).

Playwright MCP is used for exploratory and debugging sessions: drive the page, read the accessibility tree, capture the failing state, then turn it into a spec.

## 7. Gaps and next steps (in priority order)

Done since the first version: §12 scenarios for invitation confirmation, late-request approval, ambulance cancellation, privacy of personal notes, youth-manager access, and the phone layout. Still open from §12: a guest losing access on cancellation or at the end of the shift; a manager who is also a mentor keeping management rights; two mentors recording the same trainee without overwriting each other; history windows for mentors. Some of these need a clock that the browser cannot move; they are candidates for the API layer or for scripted time changes on the e2e database.


1. Frontend component tests for the registration page and the trainee page (Vitest with Testing Library). They would catch label and control regressions without a browser.
2. Extend the e2e suite to the remaining journeys: volunteer registration before and after publication, the countdown deadline (with a clock set near the deadline), the trainee dropdown on a multi-shift trainee, and the approval action itself (approve, then the applicant appears as active).
3. Contract checks on API responses that the frontend depends on (keys such as `direct`, `request`, `schedule_visible`, `temporary_until`).
4. Accessibility scan (axe) on the main screens in the e2e suite.
5. Push delivery: keep manual. Automated tests cover the subscription and the payload; delivery needs a real device.
6. Flake policy: a flaky e2e test is quarantined with an issue, not retried silently. `retries` stays at 0 until a real flake is seen.
