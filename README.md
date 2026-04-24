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
- Maintain a library of **color palettes** — 17 built-ins (Cyberpunk Neon,
  Synthwave Sunset, Vaporwave, Aurora Borealis, Deep Ocean, Forest Canopy,
  Ember and Ash, Candlelight, Ice and Fire, Blood Moon, Pastel Dream,
  Halloween, Bioluminescence, Desert Sunset, Rainbow Spectrum, UV Blacklight,
  Warm Amber Wash) plus unlimited user-defined palettes. Each palette entry
  carries RGB **plus optional explicit W / A / UV (also called "V") values**,
  so a palette can kick the amber LED or UV strip independently of the
  fixture's RGB derivation.
- **Generate palettes with Claude** from a free-text prompt
  (`POST /api/palettes/generate`) — the returned draft is loaded into the
  editor where the user can tweak and save.
- Apply a palette to a selection of lights in **cycle**, **gradient**, or
  **random** mode.
- Turn lights on/off, set individual colors, and bulk-blackout a controller.
- Run a real-time **effect engine** (static, fade, cycle, chase, pulse,
  rainbow, strobe, sparkle, wave) with per-effect fade-in/out, spread
  (across lights / across fixture / across zones), and nine curated
  built-in effects. Effects are non-destructive: stopping one cleanly
  restores whatever base color was in place.
- Every effect carries a **`target_channels`** list that decides which
  logical channels the overlay animates: `rgb` (default) blends into the
  fixture's color, `w` / `a` / `uv` drive a scalar brightness on the
  white / amber / UV LEDs **without touching RGB**, and `dimmer` /
  `strobe` animate the master dimmer or strobe faders. This is how
  "keep the wash red but chase a white pulse across the bar" works.
- **Dedicated `/effects` page** — full-page editor with a simulated
  preview grid (JS port of the effect math, no DMX required), a
  "push live" toggle that drives `/api/effects/live` against the
  currently selected lights, a target-channel chip selector, saved
  presets with one-click load, and an **inline Claude chat** that
  iteratively refines the effect (say "faster", "tighter window",
  "chase only the white channel" and Claude returns a fresh
  `EffectIn` draft each turn).
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
- **Designer tab** — a multi-turn chat with Claude Opus that turns a
  natural-language prompt ("warm amber ballad wash", "four-part DJ set:
  build, drop, breakdown, outro") into structured rig designs. Claude
  picks from three tools per turn: `propose_rig_design` (rig-wide
  `State` or per-controller `Scene`), `propose_palette` (a new palette
  draft), or `propose_effect` (an animated effect ready to play on the
  current selection or save as a preset). Responses stream
  token-by-token over Server-Sent Events, with per-light color swatches
  and one-click `Apply` / `Save` buttons on every proposal card. The
  link auto-appears in the nav when an Anthropic API key is configured.
- **Context notes** on every controller and every light — free-text
  descriptions ("stage-left wash bar, front-of-house" / "lead vocalist
  key light") that the Designer reads as part of the system prompt, so
  Claude knows the purpose and layout of each fixture when composing
  looks. Notes edit inline on the Controllers modal and the per-light
  edit form.
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
| POST | `/api/palettes` | `PaletteIn` | `Palette` |
| PATCH | `/api/palettes/{id}` | `PaletteIn` | `Palette` |
| DELETE | `/api/palettes/{id}` | — | 204 |
| POST | `/api/palettes/{id}/clone` | — | `Palette` |
| POST | `/api/palettes/{id}/apply` | `{light_ids[], mode, spread?}` | `{updated: n}` |
| POST | `/api/palettes/generate` | `{prompt, num_colors?, include_aux?}` | `PaletteGenerateResponse` |

`mode` is one of `"cycle"`, `"gradient"`, or `"random"`. `spread` is one
of `"across_lights"` (default), `"across_fixture"`, or `"across_zones"`.

`PaletteIn` accepts either the legacy `colors: string[]` hex list **or**
the richer `entries: PaletteEntry[]` shape:

```json
{
  "name": "UV Blacklight",
  "entries": [
    { "r": 0,   "g": 0,  "b": 0,  "uv": 255 },
    { "r": 48,  "g": 0,  "b": 96, "uv": 220 },
    { "r": 124, "g": 77, "b": 255 }
  ]
}
```

`w`, `a`, `uv` are optional 0-255 integers. When set, they bypass the
RGB-derivation fallback and write directly (subject to the mode's
`color_policy`). `uv` is also labelled "V" (violet) in some fixture
docs — they refer to the same channel role.

`POST /api/palettes/generate` asks Claude for a draft palette from a
free-text prompt and returns `{ name, entries: PaletteEntry[], summary? }`
without persisting anything. The UI loads the draft into the editor.
Requires `ANTHROPIC_API_KEY`.

### Effects (`/api/effects`)

Saved animated presets (cycle/fade/rainbow/etc) plus a transient "live"
playback path used by the `/effects` page and the Effects dialog.

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

`EffectIn` carries a `target_channels` list that selects which logical
channel groups the overlay animates:

| Value | Effect on the overlay |
| --- | --- |
| `rgb` (default) | Blends the overlay color into fixture RGB; W/A are also derived under `mix` policy. |
| `w` | Writes a scalar brightness (max of overlay RGB × envelope × intensity × fade) onto the white channel; leaves base RGB alone. |
| `a` | Same scalar onto the amber channel. |
| `uv` | Same scalar onto the UV / V channel. |
| `dimmer` | Animates the master dimmer fader. |
| `strobe` | Animates the fixture's strobe-rate channel. |

Multiple values may be combined (e.g. `["rgb", "w"]`). `EffectParams`
ranges are: `speed_hz` 0-25, `offset`/`intensity`/`softness` 0-1,
`size` 0-16 (strobe duty is separately clamped 0.02-0.98), and
`fade_in_s`/`fade_out_s` 0-30 seconds.

### Effect chat (`/api/effect-chat`)

Multi-turn Claude chat for iteratively refining one effect per
conversation. Mirrors the Designer's SSE contract.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/effect-chat/status` | — | `{enabled, model}` |
| GET | `/api/effect-chat/conversations` | — | `EffectConversationSummary[]` |
| POST | `/api/effect-chat/conversations` | `{name?}` | `EffectConversation` |
| GET | `/api/effect-chat/conversations/{cid}` | — | `EffectConversation` |
| PATCH | `/api/effect-chat/conversations/{cid}` | `{name}` | `EffectConversation` |
| DELETE | `/api/effect-chat/conversations/{cid}` | — | 204 |
| POST | `/api/effect-chat/conversations/{cid}/message` | `{message}` | `text/event-stream` |
| POST | `/api/effect-chat/conversations/{cid}/apply` | `{proposal_id, light_ids[]}` | `{ok, handle, name}` |
| POST | `/api/effect-chat/conversations/{cid}/save` | `{proposal_id, name?}` | `{ok, id, name}` |

Each assistant turn emits at most one `EffectProposal` (name, effect
type, palette id, spread, params, target channels). The `apply` endpoint
plays it live on the supplied light ids; `save` persists it as an
`Effect` row.

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

### Designer (`/api/designer`)

Multi-turn chat with Claude Opus that produces structured rig designs.
Each turn is persisted as a `DesignerConversation`, and each response
streams from the server as Server-Sent Events so the UI can render
tokens as they arrive.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/designer/status` | — | `{enabled, model}` |
| GET | `/api/designer/conversations` | — | `DesignerConversationSummary[]` |
| POST | `/api/designer/conversations` | `{name?}` | `DesignerConversation` |
| GET | `/api/designer/conversations/{cid}` | — | `DesignerConversation` |
| PATCH | `/api/designer/conversations/{cid}` | `{name}` | `DesignerConversation` |
| DELETE | `/api/designer/conversations/{cid}` | — | 204 |
| POST | `/api/designer/conversations/{cid}/message` | `{message}` | `text/event-stream` |
| POST | `/api/designer/conversations/{cid}/apply` | `{proposal_id}` | `{ok, applied}` |
| POST | `/api/designer/conversations/{cid}/save` | `{proposal_id, name?}` | `{ok, kind, id, name}` |

The message endpoint streams these SSE events:

- `start` — `{conversation_id}`
- `text` — `{delta}` incremental prose tokens
- `tool_start` — `{tool}` Claude has begun constructing the proposal
- `tool_delta` — `{partial_json}` raw partial JSON for the tool input
- `proposal` — `DesignerProposal[]` final sanitized proposals
- `done` — `{conversation}` full persisted `DesignerConversation`
- `error` — `{message}` on API / parse errors

A `DesignerProposal` has `kind` ∈ `state | scene | palette | effect` and
one of the following payload shapes:

```json
// kind: "state" or "scene"
{
  "proposal_id": "p1",
  "kind": "state",
  "name": "Sunset wash",
  "controller_id": 1,              // required when kind="scene"
  "notes": "Warm, low-saturation.",
  "lights": [
    {
      "light_id": 12,
      "on": true,
      "dimmer": 220,
      "r": 255, "g": 120, "b": 40,
      "w": null, "a": null, "uv": null,
      "zone_state": { "p0": { "r": 255, "g": 0, "b": 0, "on": true } },
      "motion_state": { "pan": 0.5, "tilt": 0.25 }
    }
  ]
}

// kind: "palette"
{
  "proposal_id": "p1",
  "kind": "palette",
  "name": "Ember accents",
  "palette_entries": [
    { "r": 40,  "g": 10, "b": 0,  "a": 180 },
    { "r": 200, "g": 80, "b": 20, "a": 220 }
  ]
}

// kind: "effect"
{
  "proposal_id": "p1",
  "kind": "effect",
  "name": "White pulse",
  "effect": {
    "effect_type": "pulse",
    "palette_id": null,
    "spread": "across_lights",
    "params": { "speed_hz": 1.2, "intensity": 1.0, "size": 1.0 },
    "target_channels": ["w"],
    "light_ids": [],
    "targets": []
  }
}
```

Applying a `state` / `scene` proposal reuses the same pipeline as
`POST /api/scenes/{sid}/apply` (stops overlapping effects, rewrites the
light state, pushes to Art-Net). Applying a `palette` proposal saves it
as a new `Palette` row; applying an `effect` proposal starts it live on
the engine. Saving a proposal inserts the appropriate `State` / `Scene`
/ `Palette` / `Effect` row so the proposal outlives the conversation.

Per-controller and per-light `notes` fields feed the Designer's system
prompt. They live on `POST /api/controllers` / `POST /api/lights` (plus
the matching `PATCH` endpoints) as an optional `notes: string | null`.

Requires `ANTHROPIC_API_KEY` (env var or `claudeKey.env` at the repo
root); the model defaults to `claude-opus-4-7` and is overridable via
`ANTHROPIC_MODEL`.

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
- The Designer tab streams Claude via `anthropic.Anthropic().messages.stream()`
  from a worker thread that feeds an `asyncio.Queue`. The FastAPI
  handler drains the queue as Server-Sent Events; the full assistant
  message (prose + tool_use) is sanitized and persisted in a single
  commit at the stream's `done` boundary, so a disconnect mid-stream
  cleanly drops the turn.

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
| UV Blacklight | Black base + explicit UV channel drive (e.g. `uv=255`) for fixtures with a UV LED. |
| Warm Amber Wash | Tungsten-style palette with explicit amber (and one white) value per entry. |

## Built-in effects

| Name | Type | Target channels | Notes |
| --- | --- | --- | --- |
| Rainbow Wash | rainbow | `rgb` | Slow full-hue sweep; ignores palette. |
| Breathing Amber | pulse | `rgb` | Candlelight palette breathing. |
| Cyberpunk Chase | chase | `rgb` | Neon chase across the rig. |
| Aurora Fade | fade | `rgb` | Across-fixture aurora crossfade. |
| Halloween Strobe | strobe | `rgb` | 6 Hz flash on the Halloween palette. |
| Pastel Sparkle | sparkle | `rgb` | Random pastel flashes per zone. |
| White LED Chase | chase | `w` | Chases the white LED without touching RGB. |
| Strobe Pulse (Strobe Channel) | pulse | `strobe` | Pulses the fixture's strobe-rate fader. |
| UV Accent Wave | wave | `uv` | Slow UV brightness wave. |

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
