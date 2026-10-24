#!/usr/bin/env bash
# TEMPORARY public HTTPS test tunnel (free quick tunnel, no account). Local testing only. Not a deployment.
#   scripts/tunnel.sh start   production build on port 4173 (API proxied on the same origin), secure cookies, tunnel
#   scripts/tunnel.sh stop    stop the tunnel and the preview server; the API returns to normal local mode
# Exposes only the app (preview). PostgreSQL and the development server (port 5173) are never exposed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${MDA_LOCAL_DATA:-$HOME/mda-local-data}"
PY="$DATA/venv/bin"
case "${1:-}" in
  start)
    cd "$ROOT/frontend" && npm run build >/dev/null
    : > "$DATA/tunnel.log"
    nohup cloudflared tunnel --no-autoupdate --url http://localhost:4173 > "$DATA/tunnel.log" 2>&1 < /dev/null &
    HOST=""
    for _ in $(seq 1 30); do
      HOST="$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$DATA/tunnel.log" | head -1 | sed 's#https://##' || true)"
      [ -n "$HOST" ] && break; sleep 2
    done
    [ -n "$HOST" ] || { echo "tunnel address not found; see $DATA/tunnel.log" >&2; exit 1; }
    pkill -f "uvicorn app.main:app --port 8000" || true; sleep 1
    (cd "$ROOT/backend" && MDA_COOKIE_SECURE=true MDA_DATABASE_URL="postgresql+psycopg://mda@127.0.0.1:5433/mda" \
      MDA_VAPID_PRIVATE_KEY_PATH="$DATA/vapid_private.pem" MDA_VAPID_PUBLIC_KEY="$(cat "$DATA/vapid_public.txt")" \
      nohup "$PY/uvicorn" app.main:app --port 8000 --proxy-headers > "$DATA/api.log" 2>&1 < /dev/null &)
    pkill -f "vite preview --port 4173" || true; sleep 1
    (cd "$ROOT/frontend" && MDA_PREVIEW_HOST="$HOST" nohup npx vite preview --port 4173 --strictPort > "$DATA/preview.log" 2>&1 < /dev/null &)
    echo "HTTPS test address: https://$HOST"
    ;;
  stop)
    pkill -f "cloudflared tunnel" || true
    pkill -f "vite preview --port 4173" || true
    pkill -f "uvicorn app.main:app --port 8000" || true; sleep 1
    (cd "$ROOT" && ./scripts/local-dev.sh start >/dev/null 2>&1 </dev/null &)
    echo "tunnel and preview stopped; local API restored (secure cookies off, localhost mode)"
    ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
