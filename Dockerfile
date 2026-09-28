FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# curl is used by the runtime healthcheck and to obtain Nextcloud's FRP client.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ghostscript qpdf tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# HaRP FRP client. Match the version currently shipped by Nextcloud's HaRP
# ExApp examples and support Docker's common amd64/arm64 targets.
RUN set -eux; \
    ARCH="$(uname -m)"; \
    case "$ARCH" in \
      x86_64|amd64) FRP_ARCH="amd64" ;; \
      aarch64|arm64) FRP_ARCH="arm64" ;; \
      *) echo "Unsupported architecture for FRP: $ARCH" >&2; exit 1 ;; \
    esac; \
    FRP_VERSION="0.61.1"; \
    FRP_URL="https://raw.githubusercontent.com/nextcloud/HaRP/main/exapps_dev/frp_${FRP_VERSION}_linux_${FRP_ARCH}.tar.gz"; \
    curl --fail --location --retry 3 "$FRP_URL" -o /tmp/frp.tar.gz; \
    tar -xzf /tmp/frp.tar.gz -C /tmp; \
    cp "/tmp/frp_${FRP_VERSION}_linux_${FRP_ARCH}/frpc" /usr/local/bin/frpc; \
    chmod 0755 /usr/local/bin/frpc; \
    rm -rf /tmp/frp.tar.gz "/tmp/frp_${FRP_VERSION}_linux_${FRP_ARCH}"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY exapp /app/exapp
COPY python_organizer_local_llm /app/python_organizer_local_llm
COPY appinfo /app/appinfo
COPY --chmod=0755 start.sh /start.sh
COPY --chmod=0755 run.sh /run.sh
COPY --chmod=0755 healthcheck.sh /healthcheck.sh

# Manual/Compose deployments use this conventional port. AppAPI/HaRP assigns
# APP_PORT dynamically and reaches the application through /tmp/exapp.sock.
EXPOSE 23000

ENTRYPOINT ["/start.sh", "/run.sh"]
HEALTHCHECK --interval=30s --timeout=8s --retries=3 --start-period=20s CMD ["/healthcheck.sh"]
