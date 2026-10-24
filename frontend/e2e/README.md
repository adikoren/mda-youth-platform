# End-to-end tests

Run against the local stack (see the project README), with the synthetic dataset loaded:

```bash
cd frontend
E2E_PASSWORD='<local demo password>' npm run e2e
```

Projects: `phone` (Pixel 7 viewport, the primary target) and `desktop`.
Reports: `frontend/e2e-report/index.html`. Traces are kept for failures.

Rules for these tests:
- Only synthetic accounts (`@example.test`) and the local database. Never a production URL.
- Credentials come from environment variables only.
- Assertions check invariants (structure, rights, wording) rather than fixed ids, so they survive a re-seed.
