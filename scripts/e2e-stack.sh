#!/usr/bin/env bash
# Disposable end-to-end stack. Never touches the development database (mda) or the test database (mda_test).
#   scripts/e2e-stack.sh up     recreate database mda_e2e, migrate, seed, start API (8001) and web (5174)
#   scripts/e2e-stack.sh down   stop the e2e API and web server
# Requires E2E_PASSWORD (the staff demo password, shared by seed-demo personas) and E2E_DATASET_PASSWORD
# (the dataset volunteers' password). Both are read from the environment; nothing is written to the repository.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${MDA_LOCAL_DATA:-$HOME/mda-local-data}"
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@14/bin}"
PY="$DATA/venv/bin"
DB="mda_e2e"
DB_URL="postgresql+psycopg://mda@127.0.0.1:5433/$DB"
PIDS="$DATA/e2e-stack.pids"

export MDA_DATABASE_URL="$DB_URL"
export MDA_VAPID_PRIVATE_KEY_PATH="$DATA/vapid_private.pem"
export MDA_VAPID_PUBLIC_KEY="$(cat "$DATA/vapid_public.txt" 2>/dev/null || true)"
export MDA_VAPID_SUBJECT="${MDA_VAPID_SUBJECT:-mailto:admin@example.com}"

stop() {
  # Stop what this script started, and anything still listening on its ports (a stale API keeps the database busy).
  if [ -f "$PIDS" ]; then xargs kill < "$PIDS" 2>/dev/null || true; rm -f "$PIDS"; fi
  for port in 8001 5174; do
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  done
  sleep 1
}

case "${1:-up}" in
  up)
    : "${E2E_PASSWORD:?set E2E_PASSWORD}"
    : "${E2E_DATASET_PASSWORD:?set E2E_DATASET_PASSWORD}"
    stop
    "$PG_BIN/pg_ctl" -D "$DATA/pgdata" status >/dev/null 2>&1 || "$PG_BIN/pg_ctl" -D "$DATA/pgdata" -l "$DATA/postgres.log" -w start
    # Recreate the disposable database. The name is fixed above; no other database is dropped.
    psql "postgresql://mda@127.0.0.1:5433/mda" -q -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND pid <> pg_backend_pid()" >/dev/null
    psql "postgresql://mda@127.0.0.1:5433/mda" -q -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
    (cd "$ROOT/backend" && "$PY/alembic" upgrade head >/dev/null)
    (cd "$ROOT/backend" && MDA_DEMO_PASSWORD="$E2E_PASSWORD" "$PY/python" -m app.cli seed-demo --days 21 >/dev/null)
    (cd "$ROOT/backend" && MDA_DEMO_PASSWORD="$E2E_DATASET_PASSWORD" "$PY/python" -m app.cli seed-dataset >/dev/null)
    # Test admin from the environment (never in the repository): MDA_ADMIN_EMAIL and MDA_ADMIN_PASSWORD.
    if [ -n "${MDA_ADMIN_EMAIL:-}" ] && [ -n "${MDA_ADMIN_PASSWORD:-}" ]; then
      (cd "$ROOT/backend" && MDA_ADMIN_NAME="Test Admin" "$PY/python" -m app.cli bootstrap-admin >/dev/null)
      echo "test admin ready: $MDA_ADMIN_EMAIL"
    fi
    (cd "$ROOT/backend" && nohup "$PY/uvicorn" app.main:app --port 8001 > "$DATA/e2e-api.log" 2>&1 & echo $! >> "$PIDS")
    (cd "$ROOT/frontend" && MDA_API_URL="http://127.0.0.1:8001" nohup npx vite --port 5174 --strictPort > "$DATA/e2e-web.log" 2>&1 & echo $! >> "$PIDS")
    echo "e2e stack: database $DB, api http://127.0.0.1:8001, web http://localhost:5174"
    ;;
  down)
    stop
    echo "e2e stack stopped (database $DB kept for inspection)"
    ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
