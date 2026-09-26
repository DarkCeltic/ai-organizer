#!/bin/bash
# HaRP bootstrap based on Nextcloud's exapps_dev/start.sh.
set -e

if [ -n "${HP_SHARED_KEY:-}" ]; then
    : "${HP_FRP_ADDRESS:?HP_FRP_ADDRESS is required when HP_SHARED_KEY is set}"
    : "${HP_FRP_PORT:?HP_FRP_PORT is required when HP_SHARED_KEY is set}"
    : "${APP_PORT:?APP_PORT is required when HP_SHARED_KEY is set}"
    : "${APP_ID:?APP_ID is required when HP_SHARED_KEY is set}"

    echo "HP_SHARED_KEY is set; configuring HaRP FRP tunnel."

    if [ -d "/certs/frp" ]; then
        cat > /frpc.toml <<EOF
serverAddr = "$HP_FRP_ADDRESS"
serverPort = $HP_FRP_PORT
loginFailExit = false
transport.tls.enable = true
transport.tls.certFile = "/certs/frp/client.crt"
transport.tls.keyFile = "/certs/frp/client.key"
transport.tls.trustedCaFile = "/certs/frp/ca.crt"
transport.tls.serverName = "harp.nc"
metadatas.token = "$HP_SHARED_KEY"

[[proxies]]
remotePort = $APP_PORT
type = "tcp"
name = "$APP_ID"
[proxies.plugin]
type = "unix_domain_socket"
unixPath = "/tmp/exapp.sock"
EOF
    else
        cat > /frpc.toml <<EOF
serverAddr = "$HP_FRP_ADDRESS"
serverPort = $HP_FRP_PORT
loginFailExit = false
transport.tls.enable = false
metadatas.token = "$HP_SHARED_KEY"

[[proxies]]
remotePort = $APP_PORT
type = "tcp"
name = "$APP_ID"
[proxies.plugin]
type = "unix_domain_socket"
unixPath = "/tmp/exapp.sock"
EOF
    fi

    echo "Starting frpc in the background..."
    frpc -c /frpc.toml &
else
    echo "HP_SHARED_KEY is not set; starting without HaRP tunnel."
fi

exec "$@"
