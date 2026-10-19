#!/usr/bin/env bash
# Root-only backup of the database, uploaded assets, session signing key,
# deployment config and Caddy's ACME account/certificate storage.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
umask 077
backup_root=/var/backups/dmx-control
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_stage=$(mktemp -d "$backup_root-stage.XXXXXX")
trap 'rm -rf "$backup_stage"' EXIT
install -d -m 700 "$backup_root"
python3 - "$backup_stage/dmx.db" <<'PY'
import sqlite3,sys
with sqlite3.connect('file:/var/lib/dmx-control/dmx.db?mode=ro',uri=True) as source:
    with sqlite3.connect(sys.argv[1]) as destination: source.backup(destination)
PY
cp -a /var/lib/dmx-control "$backup_stage/data"
# Replace the live copy with SQLite's consistent online backup.
mv "$backup_stage/dmx.db" "$backup_stage/data/dmx.db"
rm -f "$backup_stage/data/dmx.db-wal" "$backup_stage/data/dmx.db-shm"
cp -a /etc/caddy "$backup_stage/caddy-config"
cp -a /var/lib/caddy/.local/share/caddy "$backup_stage/caddy-storage"
cp -a /etc/systemd/system/dmx-control.service "$backup_stage/"
if [[ -d /etc/systemd/system/dmx-control.service.d ]]; then cp -a /etc/systemd/system/dmx-control.service.d "$backup_stage/"; fi
if [[ -f /opt/dmx-control/claudeKey.env ]]; then cp -a /opt/dmx-control/claudeKey.env "$backup_stage/"; fi
git -c safe.directory=/opt/dmx-control -C /opt/dmx-control rev-parse HEAD > "$backup_stage/source-commit.txt"
backup_archive="$backup_root/state-$backup_stamp.tar.gz"
tar -czf "$backup_archive" -C "$backup_stage" .
chmod 600 "$backup_archive"
sha256sum "$backup_archive" > "$backup_archive.sha256"
echo "Backup: $backup_archive"
