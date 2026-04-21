#!/usr/bin/env bash
# Idempotent installer for dmx-control.
#
# Run as root (or via sudo) from anywhere:
#   sudo ./deploy/install.sh
#
# Re-running is safe: it upgrades deps, rebuilds the frontend, and reloads
# services only when their generated state differs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
DEPLOY_DIR="$REPO_ROOT/deploy"

DMX_USER="dmx"
DMX_DATA_DIR="/var/lib/dmx-control"
SERVICE_UNIT="/etc/systemd/system/dmx-control.service"
CADDYFILE="/etc/caddy/Caddyfile"

log() { printf "\033[1;36m==>\033[0m %s\n" "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (try: sudo $0)" >&2
    exit 1
fi

log "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    nodejs npm \
    debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates

if ! command -v caddy >/dev/null 2>&1; then
    log "Installing Caddy from the official repository..."
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y caddy
fi

log "Ensuring service user '$DMX_USER' exists..."
if ! id -u "$DMX_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$DMX_DATA_DIR" --shell /usr/sbin/nologin "$DMX_USER"
fi

log "Creating data directory $DMX_DATA_DIR..."
install -d -m 750 -o "$DMX_USER" -g "$DMX_USER" "$DMX_DATA_DIR"

log "Ensuring repo is readable by $DMX_USER..."
# The service user only needs to read the repo (it writes to $DMX_DATA_DIR).
# We chgrp the repo to $DMX_USER so the group can read without changing owner.
chgrp -R "$DMX_USER" "$REPO_ROOT"
chmod -R g+rX "$REPO_ROOT"

# Give dmx write access on build-output directories so reinstalls can refresh
# node_modules and the venv without upsetting the invoking user.
install -d -o "$DMX_USER" -g "$DMX_USER" -m 775 "$BACKEND_DIR/.venv" 2>/dev/null || true
install -d -o "$DMX_USER" -g "$DMX_USER" -m 775 "$FRONTEND_DIR/node_modules" 2>/dev/null || true
install -d -o "$DMX_USER" -g "$DMX_USER" -m 775 "$FRONTEND_DIR/dist" 2>/dev/null || true

log "Setting up Python virtualenv..."
if [[ ! -e "$BACKEND_DIR/.venv/bin/python" ]]; then
    sudo -u "$DMX_USER" python3 -m venv "$BACKEND_DIR/.venv"
fi
sudo -u "$DMX_USER" "$BACKEND_DIR/.venv/bin/pip" install --upgrade pip --quiet
sudo -u "$DMX_USER" "$BACKEND_DIR/.venv/bin/pip" install -r "$BACKEND_DIR/requirements.txt" --quiet

log "Installing frontend dependencies..."
cd "$FRONTEND_DIR"
if [[ -f package-lock.json ]]; then
    sudo -u "$DMX_USER" npm ci --no-audit --no-fund
else
    sudo -u "$DMX_USER" npm install --no-audit --no-fund
fi

log "Building frontend..."
sudo -u "$DMX_USER" npm run build

log "Installing systemd unit..."
install -m 644 "$DEPLOY_DIR/dmx-control.service" "$SERVICE_UNIT"
systemctl daemon-reload
systemctl enable dmx-control.service >/dev/null
systemctl restart dmx-control.service

log "Installing Caddyfile..."
install -d -m 755 /etc/caddy
if [[ ! -f "$CADDYFILE" ]] || ! cmp -s "$DEPLOY_DIR/Caddyfile" "$CADDYFILE"; then
    install -m 644 "$DEPLOY_DIR/Caddyfile" "$CADDYFILE"
    systemctl enable caddy >/dev/null
    systemctl restart caddy
else
    systemctl enable caddy >/dev/null
    systemctl reload-or-restart caddy
fi

log "Waiting for backend to come up..."
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fs http://127.0.0.1:8000/api/health >/dev/null; then
        log "Backend is up."
        break
    fi
    sleep 1
done

log "Done."
echo
echo "  Local:  http://127.0.0.1:8000"
echo "  Public: https://dmx.50day.io"
echo "  Login password (DMX_PASSWORD env on service): secretsauce"
echo
