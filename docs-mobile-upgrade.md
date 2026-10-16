# Phone operator upgrade — October 16, 2026

The operator console now supports phone use for fixture selection, colors,
effect previews, explicitly scoped launches, look capture and recall, favorites,
fades, and restoring a previous look. Configure controllers, fixture labels and
models from Me. Effects and Scene Composer no longer require a desktop.

## Access

The shared default password is retired. Every session belongs to a named account:
admin (configure and manage access), operator (run lights and author looks), or
viewer (read only). Sessions use Secure, HttpOnly, SameSite cookies. Disabling an
account or changing its password/role revokes existing sessions. Sign-ins are
rate limited by both account and client address.

Create the first admin from an SSH terminal; there is no public setup endpoint:

```sh
cd /opt/dmx-control/backend
sudo -u dmx env DMX_DATA_DIR=/var/lib/dmx-control .venv/bin/python -m app.accounts austin Austin
```

The command prompts for a password of at least 12 characters. In production the
initial generated credential is stored in AustinLand's `dmx-control-admin` entry.
Local HTTP development needs `DMX_COOKIE_SECURE=false`; production defaults to
secure cookies. For a demo or tests, use a fresh `DMX_DATA_DIR` and
`DMX_OUTPUT_ENABLED=false` so no hardware packets leave the process.

## Output and look recovery

Controller health describes successful UDP sends, send failures, disabled output,
or simulation. It does not assert that a fixture received a packet. Invalid legacy
addresses remain readable so admins can repair them, but writes reject leading
zeros and invalid IPv4. Failing destinations back off; warnings are rate limited.

Live changes keep up to 12 durable checkpoints containing base state and the
active persisted layer stack. Restore is rig-wide and asks before changing live
output. Slider updates within a short drag coalesce into one history entry.
Scenes can save running effects with their target masks and parameter values.
Fades are cancellable by the next live change; blackout remains immediate.

## AI

Defaults are `claude-opus-5-5` for Designer/effect chat and `claude-sonnet-5-5` for
manual parsing, palette generation, verification, and Lua repair. Override using
`ANTHROPIC_MODEL` and `ANTHROPIC_FAST_MODEL`. The Anthropic SDK is pinned to 1.15.0.
Tool selection is automatic, request effort is explicit, and signed thinking
blocks are preserved through tool-result round trips.

Verified model availability through this account's Models API and the provider's
[current catalog](https://platform.claude.com/docs/en/models/overview),
[Opus migration guide](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide),
and [Sonnet migration guide](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide).

## Operations

`deploy/backup-state.sh` creates root-only archives containing a consistent SQLite
snapshot, uploaded data, service configuration, and Caddy ACME/certificate storage.
`dmx-backup.timer` runs nightly at approximately 04:15 server local time. Backups
are kept under `/var/backups/dmx-control`; check available disk and copy archives
off host according to the site's retention policy. The script never deletes older
backups.

`python3 deploy/check-public.py` checks the public homepage, backend and verified
TLS certificate. It exits unsuccessfully when TLS expires in fewer than 14 days.
Run this from a host outside the DMX VM. It needs no credentials.

Before deploying, run a backup and retain the current source commit and frontend
bundle. Migrations add tables/columns without deleting existing scene/fixture
records. For rollback, redeploy the previous source and frontend, retain the
production data, and keep a strong shared password override for the older build;
rolling back to its default password would reopen access. Restore database backup
only if necessary, since that also reverts live edits made after the backup.

## Verification commands

```sh
npm ci --prefix frontend
npm run build --prefix frontend
DMX_DATA_DIR="$PWD/output/test-data" DMX_OUTPUT_ENABLED=false PYTHONPATH=backend \
  backend/.venv312/bin/pytest backend/tests -q
bash -n deploy/backup-state.sh
python3 deploy/check-public.py
```

Tests use a fresh initialized SQLite directory. Browser QA uses a synthetic rig
with output disabled. Production verification must not change real lights or
activate the smoke-machine controller.
