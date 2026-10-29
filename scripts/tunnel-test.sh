#!/usr/bin/env bash
# PUBLIC test tunnel for the TEST stack only (database mda_e2e, synthetic accounts). Never the development database.
#   scripts/tunnel-test.sh start   build the app, start the test stack, open a public HTTPS address
#   scripts/tunnel-test.sh stop    close the tunnel and the preview server (the test stack keeps running)
# Needs E2E_PASSWORD, E2E_DATASET_PASSWORD, MDA_ADMIN_EMAIL, MDA_ADMIN_PASSWORD (scripts/activate-local.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${MDA_LOCAL_DATA:-$HOME/mda-local-data}"
PY="$DATA/venv/bin"
PIDS="$DATA/test-tunnel.pids"
LOG="$DATA/test-tunnel.log"
PREVIEW_PORT=4174

case "${1:-}" in
  start)
    : "${E2E_PASSWORD:?run: source scripts/activate-local.sh}"
    "$ROOT/scripts/e2e-stack.sh" up
    (cd "$ROOT/frontend" && npm run build >/dev/null)
    : > "$LOG"
    nohup cloudflared tunnel --no-autoupdate --url "http://localhost:$PREVIEW_PORT" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDS"
    HOST=""
    for _ in $(seq 1 30); do
      HOST="$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$LOG" | head -1 | sed 's#https://##' || true)"
      [ -n "$HOST" ] && break; sleep 2
    done
    [ -n "$HOST" ] || { echo "tunnel address not found; see $LOG" >&2; exit 1; }
    # Preview of the built app, same origin for /api, proxied to the TEST API (8001), only this tunnel host allowed.
    (cd "$ROOT/frontend" && MDA_API_URL="http://127.0.0.1:8001" MDA_PREVIEW_HOST="$HOST" \
      nohup npx vite preview --port "$PREVIEW_PORT" --strictPort > "$DATA/test-preview.log" 2>&1 < /dev/null & echo $! >> "$PIDS")
    echo "public test address: https://$HOST"
    echo "database: mda_e2e (test accounts only)"
    ;;
  stop)
    if [ -f "$PIDS" ]; then xargs kill < "$PIDS" 2>/dev/null || true; rm -f "$PIDS"; fi
    lsof -tiTCP:$PREVIEW_PORT -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
    echo "public test tunnel stopped"
    ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
