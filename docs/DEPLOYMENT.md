# Deployment plan — DigitalOcean App Platform (not deployed)

Status: configuration prepared on 2026-10-13. **Nothing has been created or deployed.** Creating the database cluster or the app starts billing; that needs Adi's explicit go-ahead.

Spec: [`.do/app.yaml`](../.do/app.yaml). Region: Frankfurt (`fra` / `fra1`), the closest App Platform region to Israel and where the account's existing apps already run.

## Proposed resources and monthly cost

Prices come from DigitalOcean's pricing docs, read 2026-10-13 (App Platform page updated 2026-07-13, PostgreSQL page updated 2026-09-30). Prices are USD per month, excluding VAT.

| Component | What it runs | Size | Price / month |
|---|---|---|---|
| Managed PostgreSQL 16 `mda-youth-db` | The database (daily backups and point-in-time recovery included) | 1 node, `db-s-1vcpu-1gb` (1 vCPU, 1 GiB RAM, 10 GiB disk) | **$15.00** |
| Service `api` | FastAPI on uvicorn, `/api/*` | `apps-s-1vcpu-1gb-fixed` (1 shared vCPU, 1 GiB), 1 instance | **$10.00** |
| Worker `worker` | Reminders and the notification outbox, a pass every 30 s | `apps-s-1vcpu-0.5gb` (512 MiB), 1 instance | **$5.00** |
| Static site `web` | The built React app (CDN), `/` | — | **$0** (see note) |
| Pre-deploy job `migrate` | `alembic upgrade head` before every release | `apps-s-1vcpu-0.5gb`, only while running | ≈ $0.01 per deploy |
| **Total** | | | **≈ $30 / month** (up to $33) |

**Notes**
- **Static site:** App Platform's free tier covers static-site-only apps. For an app that also has services, DigitalOcean's page does not state the static component's price clearly; at worst it is $3/month. Check the price shown at app creation.
- **Bandwidth:** each container plan includes outbound transfer (150 GiB in total here, pooled per team); extra costs $0.02/GiB. Expected usage is far below the allowance. Database traffic is free.
- **Billing:** App Platform bills per second, up to 28 days a month per component. Databases bill hourly.
- **Existing apps:** the account's 4 existing Frankfurt apps (`sea-lion-app`, `monkfish-app`, `starfish-app`, `lionfish-app`) are not touched and are billed separately.

**Alternatives**
- **Cheaper, ≈ $25:** run the API on `apps-s-1vcpu-0.5gb` ($5). This is probably enough for a pilot of a few dozen users, but there is less memory headroom for password hashing.
- **Cheapest, ≈ $17:** an App Platform *development* database ($7) instead of a managed cluster. It is not recommended once real data is involved, because it has fewer guarantees than a managed cluster.
- **More resilient, ≈ $75:** a high-availability PostgreSQL cluster (primary plus standby, $30 + $30).

## Why this shape

- **One origin:** the frontend and API share a domain (ingress `/api` → api, `/` → web). The HttpOnly session cookie and the CSRF cookie stay first-party, so no CORS is needed. `MDA_COOKIE_SECURE=true` is set for the API.
- **API instance count:** exactly 1, because the login rate limit lives in process memory (D-09). Scaling out first needs a shared store for it.
- **Worker:** a separate component, so reminders and outbox delivery keep running independently of web traffic. A duplicate run would still be safe, because the outbox dedup keys are unique in the database.
- **Migrations:** run as a pre-deploy job. A failed migration stops the release, and the previous version keeps serving.
- **Real client IP:** uvicorn runs with `--proxy-headers`, so the rate limit sees the client IP rather than DigitalOcean's proxy.
- **Database URL:** App Platform hands out `postgresql://…?sslmode=require`; `Settings` converts it to `postgresql+psycopg://` automatically, with a test. TLS stays required.
- **Builds:** the Python buildpack reads `backend/requirements.txt` (pinned to the tested versions) and `backend/.python-version` (3.12). The frontend builds with `npm ci && npm run build`.
- **Releases:** deploys come from `main`, with `deploy_on_push: false`, so every release is a manual decision.

## Steps when Adi approves (in order)

1. **Merge `develop` into `main` on GitHub.** The spec deploys `main`, which is currently behind `develop`.
2. **GitHub access:** in DigitalOcean, connect GitHub and grant the DigitalOcean app access to `adikoren/mda-youth-platform`.
3. **Database cluster (starts billing $15/month):** create managed PostgreSQL 16, `db-s-1vcpu-1gb`, 1 node, region `fra1`, named `mda-youth-db`, with database `mda` and user `mda`.
4. **App (starts billing ≈ $15/month):** create it from `.do/app.yaml`. Then restrict the cluster's *trusted sources* to this app only.
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
