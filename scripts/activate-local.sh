#!/usr/bin/env bash
# Local environment for this project. Use it with `source`, so the variables stay in your shell:
#   source scripts/activate-local.sh
# Passwords and the test admin are NOT in this file. They are read from a private file outside the repository:
#   ~/mda-local-data/secrets.env   (create it yourself; never commit it)
# Local only. Nothing here deploys or exposes anything.

_MDA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MDA_LOCAL_DATA="${MDA_LOCAL_DATA:-$HOME/mda-local-data}"
export MDA_VENV="$MDA_LOCAL_DATA/venv"
export PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@14/bin}"

# Databases (PostgreSQL on port 5433). Development: mda. Tests: mda_test. Disposable end-to-end: mda_e2e.
export MDA_DATABASE_URL="postgresql+psycopg://mda@127.0.0.1:5433/mda"
export MDA_TEST_DATABASE_URL="postgresql+psycopg://mda@127.0.0.1:5433/mda_test"
export MDA_E2E_DATABASE_URL="postgresql+psycopg://mda@127.0.0.1:5433/mda_e2e"

# Servers: API 8000 (development), web 5173 (development); e2e API 8001, web 5174.
export MDA_API_URL="http://127.0.0.1:8000"
export E2E_BASE_URL="http://localhost:5174"

# Web Push (VAPID): the private key stays in the local data folder.
export MDA_VAPID_PRIVATE_KEY_PATH="$MDA_LOCAL_DATA/vapid_private.pem"
export MDA_VAPID_PUBLIC_KEY="$(cat "$MDA_LOCAL_DATA/vapid_public.txt" 2>/dev/null || true)"
export MDA_VAPID_SUBJECT="${MDA_VAPID_SUBJECT:-mailto:admin@example.com}"

# Secrets (passwords, test admin): loaded from the private file if it exists.
if [ -f "$MDA_LOCAL_DATA/secrets.env" ]; then
  set -a
  . "$MDA_LOCAL_DATA/secrets.env"
  set +a
else
  echo "note: $MDA_LOCAL_DATA/secrets.env not found; passwords are not set" >&2
fi

# Handy commands
alias mda-py="$MDA_VENV/bin/python"
echo "MDA local environment ready (project: $_MDA_ROOT)"
unset _MDA_ROOT
