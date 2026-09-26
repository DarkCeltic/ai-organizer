#!/bin/sh
set -eu

if [ -n "${HP_SHARED_KEY:-}" ]; then
    SOCKET=/tmp/exapp.sock
    rm -f "$SOCKET"
    echo "Starting AI Organizer on HaRP Unix socket $SOCKET"
    exec uvicorn exapp.main:app \
        --uds "$SOCKET" \
        --proxy-headers \
        --forwarded-allow-ips='*'
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-23000}"
echo "Starting AI Organizer on http://${HOST}:${PORT}"
exec uvicorn exapp.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --proxy-headers \
    --forwarded-allow-ips='*'
