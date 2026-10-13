# Deployment plan — DigitalOcean App Platform (not deployed)

Status: configuration prepared on 2026-10-13. **Nothing has been created or deployed.** Creating the database cluster or the app starts billing; that needs Adi's explicit go-ahead.

Spec: [`.do/app.yaml`](../.do/app.yaml). Region: Frankfurt (`fra` / `fra1`), the closest App Platform region to Israel and where the account's existing apps already run.

## Proposed resources and monthly cost (lower-cost plan, 2026-10-13)

Prices come from DigitalOcean's pricing docs, read 2026-10-13 (App Platform page updated 2026-07-13, PostgreSQL page updated 2026-09-30). Prices are USD per month, excluding VAT.

| Component | What it runs | Size | Price / month |
|---|---|---|---|
| Managed PostgreSQL 16 `mda-youth-db` | The database (daily backups and point-in-time recovery included) | 1 node, `db-s-1vcpu-1gb` (1 vCPU, 1 GiB RAM, 10 GiB disk) | **$15.00** |
| Service `api` | FastAPI on uvicorn, `/api/*` | `apps-s-1vcpu-0.5gb` (1 shared vCPU, 512 MiB), 1 instance | **$5.00** |
| Worker `worker` | Reminders and the notification outbox, a pass every 30 s | `apps-s-1vcpu-0.5gb` (512 MiB), 1 instance | **$5.00** |
| Static site `web` | The built React app (CDN), `/` | — | **$0**, at worst $3 (see notes) |
| Pre-deploy job `migrate` | `alembic upgrade head` before every release | `apps-s-1vcpu-0.5gb`, only while running | ≈ $0.01 per deploy |
| **Fixed monthly total** | | | **$25.00** (+ $3.00 if the static site is billed — confirm at creation) |

**Usage-based charges (on top of the fixed total; not capped)**

These depend on usage, so no monthly figure is a hard maximum.
- **Outbound bandwidth:** $0.02/GiB beyond the team allowance. The two $5 containers include 50 GiB each, pooled per team, so about 100 GiB. The app transfers little (the JS bundle is about 76 KB gzipped), so normal use should stay well inside the allowance. Database traffic is free.
- **Database storage** beyond the included 10 GiB: $0.215/GiB/month. Not expected at this data volume.
- **Pre-deploy migration job:** billed per second at the $5 rate while it runs. Each deploy costs a fraction of a cent.
- **Taxes:** VAT/sales tax as applicable.
- **Not used, so no charge:** dedicated egress IP, standby or read-only database nodes, extra API instances.

**Billing rules:** App Platform bills per second, up to 28 days a month per component; renaming a component restarts its 28-day count. Databases bill hourly. The account's 4 existing Frankfurt apps (`sea-lion-app`, `monkfish-app`, `starfish-app`, `lionfish-app`) are not touched and are billed separately.

**Alternatives**
- **≈ $30:** the API on `apps-s-1vcpu-1gb-fixed` ($10). It is needed only if measured memory grows past about 400 MiB; see below.
- **≈ $75:** a high-availability PostgreSQL cluster (primary plus standby, $30 + $30).

## Memory measurement behind the 512 MiB choice (2026-10-13)

The API was run exactly as deployed: one uvicorn process with `--proxy-headers`, on PostgreSQL. Its resident memory (RSS) was sampled every 100 ms. Script: a scripted load test run in the working session (not committed). Measured on macOS (Apple M3), not inside a Linux container, so the numbers indicate scale rather than exact container values.

| Flow | Before the fix | After the fix (D-52) |
|---|---|---|
| Idle after start | 147 MiB | 147 MiB |
| Home screen; registration (5 concurrent); manager board (10 concurrent, 100 loads); publish; published schedule views (10 concurrent) | up to 414 MiB* | up to 225 MiB |
| Journal: 60 rides with assessments (4 concurrent), then 100 journal views (10 concurrent) | 424 MiB | 236 MiB |
| Signups (5 concurrent) | 414 MiB | 222 MiB |
| Login burst, 10 at once | **733 MiB** | 236 MiB |
| Login burst, 40 at once | **808 MiB** | **237 MiB (peak)** |
| Worker (3 passes, 44 reminders, 54 messages) | 80 MiB peak | 80 MiB peak |

\*The "before" figures in the first row include memory already held after the 5 concurrent signups.

**Why it was unsafe before:** every password hash or check (argon2id defaults) allocates 64 MiB. Sync endpoints run in a thread pool, so N simultaneous logins needed N × 64 MiB. About 6 at once would exceed a 512 MiB container, and DigitalOcean would restart it mid-request. A 1 GiB container only moves that point to about 13 at once.

**The fix (D-52):** at most 2 password operations run at a time (`MDA_PASSWORD_HASH_CONCURRENCY=2`); the rest queue. This does not weaken the password parameters. On the plan's single shared vCPU, hashing is CPU-bound, so extra parallelism would not have made logins faster anyway; the 40-login burst took 2.5 s locally both before and after.

**Result:**
- **API:** peak 237 MiB out of 512 MiB, more than 50% headroom. The scheduling and journal flows themselves add only about 10–20 MiB.
- **Worker:** 80 MiB out of 512 MiB.
- **Linux setting:** `MALLOC_ARENA_MAX=2` is set to keep glibc from multiplying malloc arenas per thread.

**Expected login latency on a 1-vCPU container:** about 30 ms per check was measured on the M3 with 4 lanes, so expect around 300 ms on one shared vCPU. If 20 people log in in the same second, the last waits roughly 6 s. Sessions last 30 days, so logins are rare. The $10 plan would not change this: it has the same single shared vCPU.

**Re-check after the first deploy:** read the `api` memory graph in App Platform Insights. Move to the $10 plan if the steady peak goes above about 400 MiB.

## Linux container test, 512 MiB and 1 CPU (2026-10-13)

The API was built from `backend/` into a `python:3.12-slim` image that approximates the buildpack: the same pinned `requirements.txt`, the same uvicorn command with `--workers 1`, and `MALLOC_ARENA_MAX=2`.
- **Limits:** `--memory=512m --memory-swap=512m --cpus=1`, matching the `apps-s-1vcpu-0.5gb` plan.
- **Database:** PostgreSQL 16 in a second container, reached with a plain `postgresql://` URL as App Platform provides.
- **Measurement:** memory from cgroup v2 (`memory.current`, `memory.stat` anon, and the kernel's `memory.peak` and `oom_kill` counters). Latency was measured by the load client.

Results with the production setting, `MDA_PASSWORD_HASH_CONCURRENCY=2`:

| Load (all requests at once) | OK | p50 | p95 | max | Container memory peak |
|---|---|---|---|---|---|
| 10 signups | 10/10 | 1.9 s | 2.8 s | 2.8 s | 202 MiB |
| 20 signups | 20/20 | 2.9 s | 4.9 s | 4.9 s | 203 MiB |
| 40 signups | 40/40 | 5.6 s | 10.2 s | 10.2 s | 205 MiB |
| 10 logins | 10/10 | 1.7 s | 2.6 s | 2.6 s | 205 MiB |
| 20 logins | 20/20 | 3.1 s | 4.7 s | 4.7 s | 205 MiB |
| 40 logins | 40/40 | 5.4 s | 9.5 s | 9.6 s | 206 MiB |
| 80 logins | 80/80 | 10.1 s | 19.0 s | 19.5 s | 207 MiB |
| 60 mixed signups and logins | 60/60 | 8.8 s | 14.0 s | 14.3 s | 207 MiB |
| 100 manager-board loads (10 concurrent) | 100/100 | 84 ms | 110 ms | 144 ms | 78 MiB |
| 60 rides + assessments (4 concurrent) | 60/60 | 42 ms | 109 ms | 122 ms | 80 MiB |
| 100 journal views (10 concurrent) | 100/100 | 194 ms | 252 ms | 303 ms | 87 MiB |

**Summary:**
- **Memory:** idle 63 MiB; the kernel `memory.peak` was **208 MiB of 512 MiB**.
- **OOM:** `oom_kill` events **0**; the container was never restarted. Memory returned to about 80 MiB after the bursts.
- **Login and signup speed:** each password operation takes about 250 ms of CPU on 1 vCPU, and the 2-slot limit queues the rest. A burst of N simultaneous logins therefore finishes in roughly N × 0.25 s, the same as without the limit, because the CPU is the bottleneck. With 30-day sessions, real login bursts should be far smaller than these tests.
- **Other flows:** schedule and journal requests stay well under 0.3 s.

**Control run (limit raised to 40):** the same container was OOM-killed by the kernel during the signup and login bursts (exit 137, `OOMKilled=true`). The limit is what makes 512 MiB safe.

**Other Linux checks:**
- The pre-deploy command `alembic upgrade head` reached `0004 (head)`.
- The worker completed a pass (44 reminders, 54 messages).
- A clean `npm ci && npm run build` on `node:20-slim` produced `dist/`, including `sw.js` and the logo.

## Why this shape

- **One origin:** the frontend and API share a domain (ingress `/api` → api, `/` → web). The HttpOnly session cookie and the CSRF cookie stay first-party, so no CORS is needed. `MDA_COOKIE_SECURE=true` is set for the API.
- **API instance count:** exactly 1 instance and exactly 1 process (`--workers 1` is explicit, so a platform `WEB_CONCURRENCY` cannot start more). The login rate limit (D-09) and the password-hashing limit (D-52) both live in process memory. Scaling out first needs a shared store for the rate limit.
- **Worker:** a separate component, so reminders and outbox delivery keep running independently of web traffic. A duplicate run would still be safe, because the outbox dedup keys are unique in the database.
- **Migrations:** run as a pre-deploy job. A failed migration stops the release, and the previous version keeps serving.
- **Real client IP:** uvicorn runs with `--proxy-headers`, so the rate limit sees the client IP rather than DigitalOcean's proxy.
- **Database URL:** App Platform hands out `postgresql://…?sslmode=require`; `Settings` converts it to `postgresql+psycopg://` automatically, with a test. TLS stays required.
- **Builds:** the Python buildpack reads `backend/requirements.txt` (pinned to the tested versions) and `backend/runtime.txt` (`python-3.12.13`, the documented way to choose the version; the default would be 3.13). The frontend builds with `npm ci && npm run build`.
- **Releases:** deploys come from `main`, with `deploy_on_push: false`, so every release is a manual decision.

## Steps when Adi approves (in order)

1. **Merge `develop` into `main` on GitHub.** The spec deploys `main`, which is currently behind `develop`.
2. **GitHub access:** in DigitalOcean, connect GitHub and grant the DigitalOcean app access to `adikoren/mda-youth-platform`.
3. **Database cluster (starts billing $15/month):** create managed PostgreSQL 16, `db-s-1vcpu-1gb`, 1 node, region `fra1`, named `mda-youth-db`, with database `mda` and user `mda`.
4. **App (starts billing ≈ $10/month):** create it from `.do/app.yaml`. Then restrict the cluster's *trusted sources* to this app only.
5. **First setup**, from the App Platform console of the `api` component:
   ```bash
   python -m app.cli init
   python -m app.cli generate-days --start <today> --end <today+60>
   python -m app.cli create-admin --email <admin email> --name "<name>"   # password is prompted, never stored in files
   ```
   Do **not** run `seed-demo` there unless the deployment is a demo with synthetic data only.
6. **Smoke test:** open `https://<app>.ondigitalocean.app/api/health`, then log in, register, and publish a test schedule.

## Not covered yet (before any real-data pilot)

- **Custom domain** and email for password recovery (password recovery is not built yet).
- **Phone notifications:** no push provider, so messages remain in-app only and are labelled "simulated".
- **Backups:** the restore drill, retention policy and who may access backups.
- **Monitoring and alerts:** App Platform alerts on failed deploys and on the worker restarting.
- **Real-data authorization:** importing real volunteer data, or inviting real users, needs a separately authorized rollout (CLAUDE.md).
