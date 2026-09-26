#!/bin/sh
set -eu

if [ -n "${HP_SHARED_KEY:-}" ]; then
    test -S /tmp/exapp.sock
    curl --fail --silent --show-error --max-time 5 \
        --unix-socket /tmp/exapp.sock \
        http://localhost/heartbeat >/dev/null
else
    curl --fail --silent --show-error --max-time 5 \
        "http://127.0.0.1:${APP_PORT:-23000}/heartbeat" >/dev/null
fi
