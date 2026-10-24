#!/usr/bin/env bash
# Local development: persistent PostgreSQL, API and web app. Local only (not a deployment).
#   scripts/local-dev.sh start   start database, migrations, API (port 8000) and web (port 5173)
#   scripts/local-dev.sh stop    stop the API and web server (the database keeps running)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${MDA_LOCAL_DATA:-$HOME/mda-local-data}"          # persistent: outside /tmp
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@14/bin}"
PY="$DATA/venv/bin"

export MDA_DATABASE_URL="postgresql+psycopg://mda@127.0.0.1:5433/mda"
export MDA_TEST_DATABASE_URL="postgresql+psycopg://mda@127.0.0.1:5433/mda_test"
# Web Push (VAPID): the private key stays in the local data folder (never in Git); the public key is shown to browsers.
export MDA_VAPID_PRIVATE_KEY_PATH="$DATA/vapid_private.pem"
export MDA_VAPID_PUBLIC_KEY="$(cat "$DATA/vapid_public.txt" 2>/dev/null || true)"
export MDA_VAPID_SUBJECT="${MDA_VAPID_SUBJECT:-mailto:admin@example.com}"

case "${1:-start}" in
  start)
    if ! "$PG_BIN/pg_ctl" -D "$DATA/pgdata" status >/dev/null 2>&1; then
      "$PG_BIN/pg_ctl" -D "$DATA/pgdata" -l "$DATA/postgres.log" -w start
    fi
    (cd "$ROOT/backend" && "$PY/alembic" upgrade head >/dev/null)
    # Admin from the environment: loads ~/mda-local-data/secrets.env (outside the repository) if present.
    if [ -f "$DATA/secrets.env" ]; then set -a; . "$DATA/secrets.env"; set +a; fi
    if [ -n "${MDA_ADMIN_EMAIL:-}" ] && [ -n "${MDA_ADMIN_PASSWORD:-}" ]; then
      (cd "$ROOT/backend" && "$PY/python" -m app.cli bootstrap-admin)
    fi
    (cd "$ROOT/backend" && nohup "$PY/uvicorn" app.main:app --port 8000 > "$DATA/api.log" 2>&1 &)
    (cd "$ROOT/frontend" && nohup npx vite --port 5173 --strictPort > "$DATA/web.log" 2>&1 &)
    echo "database: 127.0.0.1:5433 (data in $DATA/pgdata)"
    echo "api:      http://localhost:8000/api/health"
    echo "web:      http://localhost:5173"
    ;;
  stop)
    pkill -f "uvicorn app.main:app --port 8000" || true
    pkill -f "vite --port 5173" || true
    echo "api and web stopped; database still running"
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
