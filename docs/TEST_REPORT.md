# Test report

Date: 2026-10-24. Environment: local (PostgreSQL 14 on port 5433, API on 8000, web on 5173, synthetic dataset). No external services.

| Layer | Command | Result |
|---|---|---|
| Backend: unit, service, API (pytest) | `python -m pytest -q tests` | PASS — 259 of 259 |
| Frontend unit (node --test) | `npm test` | PASS — 20 of 20 |
| Frontend type-check | `npx tsc --noEmit -p .` | PASS |
| End-to-end (Playwright Test, disposable stack) | `npm run e2e:fresh` | PASS — 28 passed, 8 skipped (shared-state scenarios run once, on phone) |

Backend: 259 tests collected and passed (259 of 259, no failures or errors).

E2E tests:
- auth: wrong password refused; sign-in reaches home; no horizontal scroll on home (phone and desktop)
- manager: hub → signup approval → back; signup list shows only the manager's weekday; trainee search, empty state, back restores search
- trainee page: header totals and add action, one ride at a time, summary last; assessments are dropdowns with neutral placeholder

Requirements §12 scenarios now covered end to end: invitation of another-day volunteer and confirmation when the window opens; late request approved by the Day Manager; ambulance cancellation returns crew to waiting; private note hidden from the manager; youth manager sees progress but not content; phone layout of registration, schedule, board and journal screens.

Open items: the HTML e2e report is generated at `frontend/e2e-report/index.html`; component tests and the countdown/approval e2e journeys are not yet written (see `docs/TEST_STRATEGY.md` section 7).
