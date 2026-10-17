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

case "${1:-start}" in
  start)
    if ! "$PG_BIN/pg_ctl" -D "$DATA/pgdata" status >/dev/null 2>&1; then
      "$PG_BIN/pg_ctl" -D "$DATA/pgdata" -l "$DATA/postgres.log" -w start
    fi
    (cd "$ROOT/backend" && "$PY/alembic" upgrade head >/dev/null)
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
