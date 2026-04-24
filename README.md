# dmx-control

A password-gated web console for driving DMX fixtures over Art-Net. It ships
with a FastAPI backend (SQLite + direct Art-Net UDP output), a responsive
React + Tailwind SPA, a curated library of stage-lighting color palettes, and
a Caddy + systemd deployment that fronts everything at
[https://dmx.50day.io](https://dmx.50day.io) with automatic HTTPS.

## Features

- Add, edit, and delete **Art-Net controllers** (IP, port, net/subnet/universe).
- Define **light models** as ordered lists of channel roles
  (`r`, `g`, `b`, `w`, `a`, `uv`, `dimmer`, `strobe`, `macro`, `speed`, `pan`,
  `tilt`, `other`), so any fixture from a 3-channel RGB par to a 7-channel
  RGBWA+UV+dimmer bar is a first-class citizen.
- Place **lights** on controllers at a specific DMX start address, with a
  "create N in a row" helper that auto-spaces by the model's channel count.
- Maintain a library of **color palettes** — 15 built-ins (Cyberpunk Neon,
  Synthwave Sunset, Vaporwave, Aurora Borealis, Deep Ocean, Forest Canopy,
  Ember and Ash, Candlelight, Ice and Fire, Blood Moon, Pastel Dream,
  Halloween, Bioluminescence, Desert Sunset, Rainbow Spectrum) plus unlimited
  user-defined palettes.
- Apply a palette to a selection of lights in **cycle**, **gradient**, or
  **random** mode.
- Turn lights on/off, set individual colors, and bulk-blackout a controller.
- Run a real-time **effect engine** (static, fade, cycle, chase, pulse,
  rainbow, strobe, sparkle, wave) with per-effect fade-in/out, spread
  (across lights / across fixture / across zones), and six curated
  built-in effects. Effects are non-destructive: stopping one cleanly
  restores whatever base color was in place.
- Save and recall named **scenes** — snapshots of every light's current
  color/dimmer/on state, scoped per-controller or spanning the whole rig.
  Each controller's header on the Lights page gets a `Restore scene`
  dropdown (with a built-in `Blackout` entry), an `Apply` button, and a
  `Save scene` button; a dedicated `/scenes` page covers rename,
  re-capture, delete, and cross-controller management. Applying a scene
  stops any running effect that covers the affected lights so the
  restored state actually sticks.
- Save and recall named **rig states** — always rig-wide snapshots
  covering every light on every controller. The Lights page toolbar has
  a dedicated `Rig state` picker (with a built-in `Blackout all` entry)
  plus an inline `Save` button to capture the whole rig in one click,
  and the `/scenes` page has a sibling section for rename, re-capture,
  and delete.
- The **Lights page toolbar** is organized into three labeled groups
  — `Selection` (select all / clear / counter / set color / apply
  palette), `Rig state` (right-floated picker + apply/save), and
  `Effect controls` (the Effects dialog trigger, with any currently
  running effects shown inline on the same row). Each controller's
  header also has a `Select all` button for grabbing every light on
  that one controller.
- Single shared password ("secretsauce" by default) via a signed session
  cookie.
- Everything persists to SQLite and is restored to the physical rig on
  service restart.

## Quick deploy on a fresh Debian/Ubuntu host

```bash
git clone https://github.com/amcchord/dmx-control.git /opt/dmx-control
sudo /opt/dmx-control/deploy/install.sh
```

The installer is idempotent — rerun it any time after pulling new changes.
It will:

1. Install Python 3, Node.js, and (if missing) Caddy from the official
   Cloudsmith repository.
2. Create a `dmx` system user and `/var/lib/dmx-control` data directory.
3. Build the backend venv and the frontend bundle.
4. Enable `dmx-control.service` (uvicorn on `127.0.0.1:8000`).
5. Drop `deploy/Caddyfile` at `/etc/caddy/Caddyfile` and reload Caddy so
   `https://dmx.50day.io` terminates TLS and reverse-proxies the backend.

Override the login password by editing `DMX_PASSWORD=` in
`deploy/dmx-control.service` before running the installer (or
`systemctl edit dmx-control.service` after).

## Local development

```bash
# Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DMX_PASSWORD=secretsauce .venv/bin/uvicorn app.main:app --reload --port 8000

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev   # http://localhost:5173, proxies /api to :8000
```

## REST API

All endpoints live under `/api`. Auth is a single shared password:

```
POST /api/auth/login     { "password": "..." }      -> sets dmx_session cookie
POST /api/auth/logout
GET  /api/auth/status
```

Every other endpoint requires the session cookie.

### Controllers (`/api/controllers`)

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/controllers` | — | `Controller[]` |
| POST | `/api/controllers` | `ControllerIn` | `Controller` |
| PATCH | `/api/controllers/{id}` | `ControllerIn` | `Controller` |
| DELETE | `/api/controllers/{id}` | — | 204 |
| POST | `/api/controllers/{id}/blackout` | — | `{ok: true}` |

```json
// ControllerIn
{
  "name": "Stage Left",
  "ip": "192.168.1.100",
  "port": 6454,
  "net": 0,
  "subnet": 0,
  "universe": 0,
  "enabled": true
}
```

### Light models (`/api/models`)

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/models` | — | `LightModel[]` |
| POST | `/api/models` | `{name, channels[]}` | `LightModel` |
| PATCH | `/api/models/{id}` | `{name, channels[]}` | `LightModel` |
| DELETE | `/api/models/{id}` | — | 204 |
| POST | `/api/models/{id}/clone` | — | `LightModel` |

Channel roles: `r, g, b, w, a, uv, dimmer, strobe, macro, speed, pan, tilt, other`.

### Lights (`/api/lights`)

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/lights` | — | `Light[]` |
| POST | `/api/lights` | `LightIn` | `Light` |
| PATCH | `/api/lights/{id}` | `LightIn` | `Light` |
| DELETE | `/api/lights/{id}` | — | 204 |
| POST | `/api/lights/{id}/color` | `ColorRequest` | `Light` |
| POST | `/api/lights/bulk-color` | `BulkColorRequest` | `{updated: n}` |

```json
// ColorRequest
{ "r": 255, "g": 128, "b": 0, "w": null, "a": null, "uv": null, "dimmer": 255, "on": true }

// BulkColorRequest
{ "light_ids": [1,2,3], "r": 255, "g": 0, "b": 0 }
```

### Palettes (`/api/palettes`)

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/palettes` | — | `Palette[]` |
| POST | `/api/palettes` | `{name, colors[]}` | `Palette` |
| PATCH | `/api/palettes/{id}` | `{name, colors[]}` | `Palette` |
| DELETE | `/api/palettes/{id}` | — | 204 |
| POST | `/api/palettes/{id}/clone` | — | `Palette` |
| POST | `/api/palettes/{id}/apply` | `{light_ids[], mode}` | `{updated: n}` |

`mode` is one of `"cycle"`, `"gradient"`, or `"random"`. Colors are `#RRGGBB`.

### Effects (`/api/effects`)

Saved animated presets (cycle/fade/rainbow/etc) plus a transient "live"
playback path used by the Effects dialog on the Lights page.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/effects` | — | `Effect[]` |
| POST | `/api/effects` | `EffectIn` | `Effect` |
| PATCH | `/api/effects/{id}` | `EffectIn` | `Effect` |
| DELETE | `/api/effects/{id}` | — | 204 |
| POST | `/api/effects/{id}/clone` | — | `Effect` |
| POST | `/api/effects/{id}/play` | — | `{ok, handle}` |
| POST | `/api/effects/{id}/stop` | — | `{ok, stopped}` |
| POST | `/api/effects/stop-all` | — | `{ok, stopped}` |
| GET | `/api/effects/active` | — | `ActiveEffect[]` |
| POST | `/api/effects/live` | `LiveEffectIn` | `{ok, handle, name}` |
| POST | `/api/effects/live/{handle}/stop` | — | `{ok}` |
| POST | `/api/effects/live/{handle}/save` | `{name}` | `Effect` |

### Scenes (`/api/scenes`)

State snapshots you can save once and replay later. Each scene belongs
to a primary `controller_id`; set `cross_controller=true` to cover every
light on the rig. A virtual `Blackout` entry is synthesized per
controller in `GET /api/scenes` so the UI can render one uniform list.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/scenes?controller_id=<int>` | — | `Scene[]` |
| POST | `/api/scenes` | `SceneCreate` | `Scene` |
| PATCH | `/api/scenes/{id}` | `SceneUpdate` | `Scene` |
| DELETE | `/api/scenes/{id}` | — | 204 |
| POST | `/api/scenes/{id}/apply` | — | `{ok, applied}` |
| POST | `/api/scenes/blackout/{cid}/apply` | — | `{ok, applied}` |

```json
// SceneCreate
{
  "name": "Evening wash",
  "controller_id": 1,
  "cross_controller": false,
  "light_ids": [1, 2, 3],
  "from_rendered": false
}

// SceneUpdate (all fields optional)
{ "name": "Renamed", "recapture": true, "from_rendered": false }
```

`from_rendered=true` captures the live DMX output (useful for freezing a
running effect) instead of the DB base state.

### Rig states (`/api/states`)

Rig-wide snapshots covering every light on every controller. Unlike
Scenes, States have no primary `controller_id` — applying one always
touches the whole rig. A virtual `Blackout all` entry is synthesized in
`GET /api/states` so the UI can render one uniform list.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/states` | — | `State[]` |
| POST | `/api/states` | `StateCreate` | `State` |
| PATCH | `/api/states/{id}` | `StateUpdate` | `State` |
| DELETE | `/api/states/{id}` | — | 204 |
| POST | `/api/states/{id}/apply` | — | `{ok, applied}` |
| POST | `/api/states/blackout/apply` | — | `{ok, applied}` |

```json
// StateCreate
{ "name": "Showtime", "from_rendered": false }

// StateUpdate (all fields optional)
{ "name": "Renamed", "recapture": true, "from_rendered": false }
```

### State (`/api/state`)

`GET /api/state` returns the current in-memory DMX buffers and the per-light
state. `POST /api/state/resend` pushes every controller's buffer again.

## Architecture

```
Browser --HTTPS--> Caddy --127.0.0.1:8000--> FastAPI (uvicorn)
                                              ├─ SQLite (SQLModel)
                                              └─ ArtNetManager --UDP 6454--> Fixtures
```

- `backend/app/artnet.py` speaks Art-Net ArtDmx directly over UDP. One 512-byte
  buffer is kept per controller in memory; every color change patches the
  buffer in place and re-sends.
- CRUD operations call `rebuild_manager_sync()` which re-reads controllers,
  lights, and models from SQLite and rebuilds the in-memory universes,
  restoring the last-known color state for every light.
- Sessions are signed with `itsdangerous`; the key is persisted at
  `$DMX_DATA_DIR/session.key` so restarts do not log everybody out.

## Built-in palettes

| Name | Colors |
| --- | --- |
| Cyberpunk Neon | `#FF2DAA #00E5FF #7C4DFF #2D1B69 #C9D1D9` |
| Synthwave Sunset | `#FF3B7F #FF7A59 #FFB36B #7C4DFF #2D1B69` |
| Vaporwave | `#F62E97 #94167F #E93479 #F9AC53 #153CB4` |
| Aurora Borealis | `#00FF9F #00B8FF #7C4DFF #2EF9B6 #001A33` |
| Deep Ocean | `#011F4B #03396C #005B96 #6497B1 #B3CDE0` |
| Forest Canopy | `#0B3D0B #1B5E20 #2E7D32 #7CB342 #C5E1A5` |
| Ember and Ash | `#1A0A00 #4A1500 #B23A00 #FF6B1A #FFD199` |
| Candlelight | `#2B1400 #7A3C00 #FF8A3D #FFB26B #FFD19A` |
| Ice and Fire | `#E8F6FF #66D3FA #0077B6 #FF5B1F #FFB36B` |
| Blood Moon | `#2B0A0A #6E0F0F #B01E1E #FF3B30 #FFB36B` |
| Pastel Dream | `#FFB5E8 #B28DFF #AFCBFF #BFFCC6 #FFC9DE #FFFFD1` |
| Halloween | `#FF6A00 #8A2BE2 #1B1B1B #39FF14 #FFD300` |
| Bioluminescence | `#001018 #003049 #00B4D8 #90E0EF #CAFFBF` |
| Desert Sunset | `#2E0F0A #7A1F0F #C1440E #E57B3A #F6C28B` |
| Rainbow Spectrum | 12-stop hue sweep |

## Built-in light models

| Name | Channels |
| --- | --- |
| RGB 3ch | `r, g, b` |
| RGBW 4ch | `r, g, b, w` |
| RGBWA 5ch | `r, g, b, w, a` |
| RGBWA+UV 6ch | `r, g, b, w, a, uv` |
| Par 7ch | `dimmer, r, g, b, strobe, macro, speed` |

## License

MIT
