# dmx-control

A password-gated web console for driving DMX fixtures over Art-Net. It ships
with a FastAPI backend (SQLite + direct Art-Net UDP output), a responsive
React + Tailwind SPA, a curated library of stage-lighting color palettes, and
a Caddy + systemd deployment that fronts everything at
[https://dmx.50day.io](https://dmx.50day.io) with automatic HTTPS.

## Features

- Add, edit, and delete **Art-Net controllers** (IP, port, net/subnet/universe).
- Define **light models** as ordered lists of channel roles
  (`r`, `g`, `b`, `w`, `a`, `uv`, `dimmer`, `strobe`, `macro`, `speed`,
  `color`, `pan`, `tilt`, `other`), so any fixture from a 3-channel RGB par
  to a 7-channel RGBWA+UV+dimmer bar is a first-class citizen.
- **Indexed-color fixtures as fake-RGB lights.** Many older or smaller
  fixtures (e.g. Blizzard StormChaser Supercell 20CH mode) expose a single
  DMX byte that selects from a fixed palette of preset colors instead of
  separate R/G/B sliders. Tag those slots with the `color` role and attach
  a per-mode **color table** (a list of `{lo, hi, name, r, g, b}` entries
  documenting the manufacturer's byte ranges + representative RGB). The
  Art-Net renderer projects each frame's logical RGB onto the closest
  preset and emits the matching byte, so palettes, effects, scenes, and
  the color picker all "just work" on these fixtures — every consumer
  treats the light as ordinary RGB. Compound fixtures with one indexed
  byte per cell (StormChaser-style 16 cells × 1 byte) become 16-zone
  fixtures sharing one mode-level table. The Claude PDF manual parser
  auto-extracts the table and pre-fills the editor for review.
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
- Run a real-time **effect engine** powered by sandboxed **Lua scripts**.
  Every effect — built-in or user-authored — is a Lua source file the
  engine ticks at 30 Hz inside a locked-down `lupa` runtime (no `io`,
  `os`, `require`, `package`, `debug`, `load`, or `dofile`; per-call
  instruction budget). Each script declares its own `PARAMS` table, so
  the UI auto-generates exactly the right knobs (speed, size, warmth,
  etc.) per effect. Effects are non-destructive: stopping one cleanly
  restores whatever base color was in place.
- Every effect carries a **`target_channels`** list that decides which
  logical channels the overlay animates: `rgb` (default) blends into the
  fixture's color, `w` / `a` / `uv` drive a scalar brightness on the
  white / amber / UV LEDs **without touching RGB**, and `dimmer` /
  `strobe` animate the master dimmer or strobe faders. This is how
  "keep the wash red but chase a white pulse across the bar" works.
- **Dedicated `/effects` page** — top-down stack of presets dropdown →
  multi-strip live preview (one strip per active target channel, fed by
  a 30 Hz websocket from the real engine) → Claude chat (left) and
  auto-generated script controls (right) → collapsed Lua source editor
  (CodeMirror 6 with Lua syntax highlighting, lint markers, and
  `Ctrl+Z` history) → light selection + Push live. Spread, channels,
  and palette live behind a small "routing" disclosure since they're
  usually best described to Claude in plain English.
- **Iterative chat with Claude** that writes and revises Lua. Every
  proposal is **smoke-tested server-side** (compile + dry-run across
  several slots × timesteps) before reaching the user; runtime errors
  are pumped back to Claude as a `tool_result` with targeted hints
  (e.g. "`ctx.palette:smooth` returns three numbers, not a table") and
  Claude retries up to 3 times automatically. Streaming SSE events show
  the in-progress text, partial Lua draft (with a friendly "Drafting
  effect (1.2 KB) · Theater Chase" status), retry attempts, and a
  final `script_error` if the script still fails so the user can see
  what was tried and ask for a fix.
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
- **Photoshop-style layered effects engine.** Multiple effects can run
  on the rig at the same time as an explicit, z-ordered stack of
  layers. Each layer carries a `blend_mode` (`normal`, `add`,
  `multiply`, `screen`, `max`, `min`, `replace`), an `opacity` slider,
  an optional fixture mask, mute / solo toggles, and per-layer
  telemetry (last error, error count, last tick ms). The compositor
  walks layers bottom-up and blends per channel under the same
  `target_channels` and `color_policy` rules used for static color, so
  W/A/UV "direct" channels still survive an add/multiply layer above
  them. When a layer's Lua throws repeatedly, the engine
  **auto-mutes** it instead of taking the show down with it. Live
  layer state streams over the `/api/layers/ws` WebSocket so the UI
  can react instantly without polling.
- **Responsive SPA with two distinct shells.** Mobile gets a focused
  operator console — `Now Playing` (live rig hero, master fader, the
  layer stack with mute/solo/opacity per row, recent scenes/states
  chips), `Lights` (fixtures grouped per controller; tap to multi-
  select; bottom action sheet for color, palette, blackout), `Quick
  FX` (curated grid of presets — short-tap launches a layer, long
  press configures blend + opacity), `Scenes`, and a `Me` tab with
  engine telemetry. Desktop adds a persistent side nav (`Operate /
  Author / Configure`) and a permanent right-hand `Live` rail showing
  the running layers + master, with the **Effects Composer** as the
  hero — library / live preview / per-layer inspector / running stack
  — and a matching **Scene Composer** that captures the current rig
  state plus the running layer stack into a single applyable scene.
- **Designer that auto-repairs custom Lua effects.** Claude can call
  multiple proposal tools in one turn (`propose_rig_design` +
  `propose_effect`, `propose_palette` + `propose_effect`, etc.), and
  any custom Lua source the model writes runs through a server-side
  smoke test before the user sees Apply. When the smoke test fails
  (or the script doesn't even compile — common when an LLM emits a
  module-style `local function render` + `return { ... }` block), a
  focused **Lua refiner sub-agent** kicks off a brief Anthropic call
  with the broken source plus a targeted diagnostic ("ctx.t not
  ctx.time_s", "r/g/b are 0..255 ints not 0..1 floats", etc.) and
  loops up to 3 attempts. Healthy scripts skip the refiner entirely
  — only failing ones pay the latency cost.
- **Atomic scene apply with saved layers.** Scenes can carry an
  ordered layer stack alongside their base snapshot. Applying a
  scene clears every running layer that touches affected lights,
  pushes the base state, then starts each saved layer in order — a
  whole-show recall in one click.
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

Channel roles: `r, g, b, w, w2, w3, a, a2, uv, uv2, dimmer, strobe, macro,
speed, color, pan, pan_fine, tilt, tilt_fine, zoom, focus, other`. The
`color` role marks an indexed-color slot driven by the mode's
`color_table` (see below).

Each `LightModelMode` may carry an optional `color_table`:

```json
{
  "entries": [
    { "lo": 0,  "hi": 15, "name": "Off",  "r": 0,   "g": 0, "b": 0 },
    { "lo": 16, "hi": 31, "name": "Red",  "r": 255, "g": 0, "b": 0 },
    { "lo": 32, "hi": 47, "name": "Green","r": 0,   "g": 255,"b": 0 }
  ],
  "off_below": 0
}
```

The renderer maps each frame's logical `(r, g, b)` to the nearest entry
by Euclidean RGB distance and emits the midpoint of that entry's range
on every `color`-tagged slot in the mode (per-cell or fixture-wide via
`layout.globals.color`). Multi-cell fixtures share a single mode-level
table — model the StormChaser-style "16 cells, one palette" shape with
one table plus 16 zones whose `colors.color` points at each cell's
offset. `off_below` (0–255, default 0) forces the off-marked entry on
dimmerless fixtures whose requested RGB has `max(r,g,b)` below the
threshold so dim colors actually go dark.

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

Saved Lua-scripted animated presets plus a transient "live" playback
path used by the `/effects` page.

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
| POST | `/api/effects/lint` | `{source}` | `EffectLintResponse` |
| POST | `/api/effects/live` | `LiveEffectIn` | `{ok, handle, name}` |
| POST | `/api/effects/live/{handle}/stop` | — | `{ok}` |
| POST | `/api/effects/live/{handle}/save` | `{name}` | `Effect` |
| WS | `/api/effects/preview/ws` | `{source, params, palette, …}` | streams `PreviewFrame`s |

```json
// EffectIn
{
  "name": "Theater Chase",
  "source": "PARAMS = { { id='speed_hz', type='number', min=0, max=25, default=2.0, suffix='Hz' } }\nfunction render(ctx) ... end",
  "palette_id": 4,
  "light_ids": [],
  "targets": [],
  "spread": "across_lights",
  "params": { "speed_hz": 2.0 },
  "controls": { "intensity": 1.0, "fade_in_s": 0.25, "fade_out_s": 0.25 },
  "target_channels": ["rgb"]
}
```

`source` is the Lua script. `params` is a free-form dict whose keys
match `id`s declared in the script's top-level `PARAMS` table; values
are clamped against the schema at save time. `controls` are the
engine-applied envelope (intensity multiplier + fade in/out). The
script's `param_schema` is parsed once and cached on the row so the UI
doesn't need to re-compile on every paint.

The `target_channels` list selects which logical channel groups the
overlay animates:

| Value | Effect on the overlay |
| --- | --- |
| `rgb` (default) | Blends the overlay color into fixture RGB; W/A are also derived under `mix` policy. |
| `w` | Writes a scalar brightness (max of overlay RGB × envelope × intensity × fade) onto the white channel; leaves base RGB alone. |
| `a` | Same scalar onto the amber channel. |
| `uv` | Same scalar onto the UV / V channel. |
| `dimmer` | Animates the master dimmer fader. |
| `strobe` | Animates the fixture's strobe-rate channel. |

Multiple values may be combined (e.g. `["rgb", "w"]`).

#### Lint (`POST /api/effects/lint`)

Compile a Lua source string in the sandbox and return its parsed
metadata or an error with a line number. Used by the editor to drive
syntax highlighting + auto-generate the params form. Cheap (~ms);
called on every keystroke (debounced) and never persists anything.

```json
// EffectLintResponse
{
  "ok": true,
  "name": "Theater Chase",
  "description": "Hard-edged chase, every Nth slot lit.",
  "param_schema": [{ "id": "speed_hz", "type": "number", "min": 0, "max": 25, "default": 2.0 }],
  "has_render": true,
  "has_tick": false,
  "error": null
}
```

#### Preview WS (`/api/effects/preview/ws`)

Auth-gated WebSocket. Send the script + params + palette once; the
server compiles it inside the sandbox and streams `PreviewFrame`s at
30 Hz:

```json
{
  "frame": 273,
  "t": 9.1,
  "strips": [
    { "target": "rgb", "cells": [{ "active": true, "r": 255, "g": 60, "b": 0, "brightness": 1.0 }, …] },
    { "target": "w",   "cells": [{ "active": true, "brightness": 0.42 }, …] }
  ]
}
```

Send `{ patch: { source?, params?, palette?, target_channels?, intensity? } }`
to hot-update without dropping the connection. Compile or runtime errors
arrive as `{ "error": { "message": "...", "line": 12 } }` so the editor
can underline the failing line.

#### Lua effect API

Every script runs in a sandbox with these globals (and only these
globals — `io`, `os`, `package`, `require`, `debug`, `load`, `dofile`,
`loadfile`, and `loadstring` are intentionally absent):

```lua
-- Optional metadata + auto-generated UI knobs
NAME = "My Effect"
DESCRIPTION = "Free-text description shown next to the script controls."
PARAMS = {
  { id="speed_hz", label="Speed",  type="number", min=0, max=25, default=1.0, suffix="Hz" },
  { id="warmth",   label="Warmth", type="number", min=0, max=1,  default=0.5 },
  { id="mode",     label="Mode",   type="choice", options={"smooth","step"}, default="smooth" },
}

-- Per-slot pure function (default contract).
function render(ctx)
  local p = ctx.params
  local r, g, b = ctx.palette:smooth(ctx.t * (p.speed_hz or 1) + ctx.i / ctx.n)
  return { r = r, g = g, b = b, brightness = 1.0 }
end

-- Or, opt into a whole-frame entry point for stateful / cross-slot effects:
-- function tick(ctx) ... end
```

`ctx` carries `t` (seconds since the effect started), `i` / `n` (this
slot's 0-indexed position and the group size), `frame` (monotonic tick
counter), `seed` (deterministic per-effect seed), `palette`,
`params`, and `slot = { light_id, zone_id }`. `render` must return
`{ r=, g=, b=, brightness= }` with **named** keys (positional values
fall back gracefully but named is correct), or `{ active = false }` to
let the base color show through this slot.

Stdlib helpers loaded into every script's env (see
[backend/app/lua/stdlib.lua](backend/app/lua/stdlib.lua) for the full
source):

| Helper | Returns |
| --- | --- |
| `ctx.palette:smooth(p)` / `:step(p)` / `:get(i)` | three numbers `(r, g, b)` (not a table) |
| `ctx.palette:size()` | number of palette entries |
| `color.hsv(h, s, v)` / `color.hex("#RRGGBB")` / `color.mix(...)` | three numbers |
| `envelope.pulse(p)` / `.wave(p)` / `.chase(p, size, soft)` / `.strobe(p, duty)` | scalar 0..1 |
| `direction.apply(phase, "forward"\|"reverse"\|"pingpong", cycles_done)` | wrapped phase |
| `per_index_offset(slider, n)` | per-index step (1.0 = perfect chase) |
| `noise.hash(...)` / `noise.simplex(x, y)` | deterministic 0..1 |
| `easing.linear/quad_in/quad_out/quad_inout/cosine` | scalar |
| `random(seed)` | stateful PRNG with `:next()` and `:int(lo, hi)` |

### Layers (`/api/layers`)

The Photoshop-style compositor. Each entry is one running effect on
the rig. The engine walks layers in deterministic `(z_index,
layer_id, handle)` order every tick, blends each layer's overlay
into the running state per channel, and emits the result to Art-Net.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/layers` | — | `EffectLayer[]` |
| POST | `/api/layers` | `LayerCreate` | `EffectLayer` |
| PATCH | `/api/layers/{id}` | `LayerPatch` | `EffectLayer` |
| POST | `/api/layers/reorder` | `{order: [{layer_id, z_index}, ...]}` | `EffectLayer[]` |
| DELETE | `/api/layers/{id}` | — | 204 |
| POST | `/api/layers/clear` | — | `{ok, stopped}` |
| WS | `/api/layers/ws` | — | streams `{type: "layers", layers, health}` |

```json
// LayerCreate
{
  "effect_id": 7,
  "name": null,                        // optional display override
  "z_index": null,                     // null = top of stack
  "blend_mode": "normal",              // normal|add|multiply|screen|max|min|replace
  "opacity": 1.0,                      // 0..1
  "intensity": 1.0,                    // 0..1
  "fade_in_s": 0.25,
  "fade_out_s": 0.25,
  "mute": false,
  "solo": false,
  "mask_light_ids": [12, 13, 14],      // optional fixture restriction
  "target_channels": null,             // inherit from effect when null
  "spread": null,                      // inherit from effect when null
  "light_ids": null,
  "targets": null,
  "palette_id": null,
  "params_override": { "speed_hz": 1.5 }
}

// LayerPatch (every field optional)
{ "opacity": 0.6, "blend_mode": "add", "mute": true }

// EffectLayer (response shape; matches the WS push)
{
  "handle": "9b8a...c7",
  "layer_id": 12,
  "effect_id": 7,
  "name": "Warm Pulse",
  "runtime_s": 14.6,
  "z_index": 200,
  "blend_mode": "add",
  "opacity": 0.6,
  "intensity": 1.0,
  "target_channels": ["rgb"],
  "mute": false,
  "solo": false,
  "auto_muted": false,                  // engine flipped this on after N Lua errors
  "stopping": false,
  "error": null,
  "error_count": 0,
  "last_tick_ms": 0.42,
  "mask_light_ids": []
}
```

`POST /api/effects/{eid}/play` is a backwards-compatible shim that
stops any existing layer for that effect and creates a fresh layer on
top — old clients keep working unchanged. Pass an explicit `z_index`
to `POST /api/layers` to insert mid-stack rather than at the top.

`POST /api/layers/clear` is the **panic stop**: every running layer is
hard-stopped (no fade) and every persisted layer row deleted. Wired
to the panic button in both the mobile and desktop chrome.

The WS frame includes a `health` sub-object identical to
`GET /api/health` (engine tick rate, dropped frames, last tick ms,
active layer count) so the desktop Live rail can show telemetry
without a separate poll.

### Health (`/api/health`)

`GET /api/health` returns the engine's live telemetry alongside the
`{ok: true}` heartbeat:

```json
{
  "ok": true,
  "tick_count": 9213,
  "dropped_frames": 0,
  "last_tick_ms": 0.66,
  "active_layers": 2,
  "tick_hz": 30.0
}
```

The Designer / Effects Composer / Me tab all read from this; the WS
layer store also embeds a copy in every push so most surfaces never
need to poll.

### Effect chat (`/api/effect-chat`)

Multi-turn Claude chat for iteratively refining one Lua-scripted effect
per conversation.

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

Each assistant turn forces a single `propose_effect` tool call. Claude
either references one of the seeded `builtin` scripts or emits raw
`source` Lua. The router:

1. Sanitizes + compiles the proposal in the sandbox.
2. Runs `smoke_test_source(...)` (compile + dry-run across 8 slots × 5
   timesteps) to catch runtime errors that hide behind a clean compile
   (e.g. indexing the return of `palette:smooth` as if it were a table).
3. On failure, builds a `tool_result` with a targeted hint and re-runs
   the model — up to 3 attempts per chat turn.
4. Stores the final assistant turn (with the `tool_use` block rewritten
   to the sanitized payload) so subsequent turns replay correctly.
   Synthesizes `tool_result` placeholders for any prior `tool_use`
   blocks when replaying history, since Anthropic rejects multi-turn
   flows otherwise.

The message endpoint streams these SSE events:

- `start` — `{conversation_id}`
- `text` — `{delta}` incremental prose tokens
- `tool_start` — `{tool}` Claude has begun constructing the proposal
- `tool_delta` — `{partial_json}` raw partial JSON for the tool input
- `retry` — `{attempt, max_attempts, reason}` smoke test failed; retrying
- `script_error` — `{message, line, attempts}` final attempt still broken
- `proposal` — `EffectProposal` final sanitized proposal (loaded into
  the editor; the script is included even if smoke-testing failed so
  the user can see what Claude tried)
- `done` — `{conversation}` full persisted `EffectConversation`
- `error` — `{message}` on API / parse errors

`apply` plays the proposal live on the supplied light ids; `save`
persists it as an `Effect` row.

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

Claude can call **multiple tools in a single turn** — the persist
phase walks every `tool_use` block, sanitizes each independently,
and unions the cleaned proposals into `last_proposal` so prompts
like "design a cyberpunk theme with a flicker" produce both a rig
state and an effect on the same turn, each surfaced as its own card
with independent Apply / Save buttons.

Custom Lua effects ride a **refiner sub-agent** between sanitize
and persist: every effect proposal's `source` runs through
`smoke_test_source` (compile + dry-run across multiple slots and
timesteps; rejects "always-zero" output that LLMs commonly produce
when they get the ctx field names wrong). On failure, the refiner
opens a brief Anthropic call dedicated to that one script, feeds
the diagnostic back as a `tool_result`, and loops up to 3 attempts
before giving up. Healthy scripts skip Claude entirely. Proposals
that can't be rescued are dropped and a `refine_dropped` SSE event
fires so the UI knows to suppress the card. Setting up the refiner
requires no extra config — same `ANTHROPIC_API_KEY` the designer
itself uses.

The message endpoint streams these SSE events:

- `start` — `{conversation_id}`
- `text` — `{delta}` incremental prose tokens
- `tool_start` — `{tool}` Claude has begun constructing the proposal
- `tool_delta` — `{partial_json}` raw partial JSON for the tool input
- `proposal` — `DesignerProposal[]` final sanitized proposals
- `refine_dropped` — `{proposal_id, name}` an effect proposal failed
  smoke-testing + refinement and was excluded
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
    "source": "PARAMS = { ... }\nfunction render(ctx) ... end",
    "description": "Slow white-LED breathing.",
    "param_schema": [{ "id": "speed_hz", "type": "number", "min": 0, "max": 25, "default": 1.2 }],
    "palette_id": null,
    "spread": "across_lights",
    "params": { "speed_hz": 1.2 },
    "controls": { "intensity": 1.0, "fade_in_s": 0.25, "fade_out_s": 0.25 },
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
- `backend/app/lua/` is the sandboxed effect runtime: each `LuaScript`
  wraps its own `lupa.lua54.LuaRuntime`, loads `stdlib.lua` into a
  restricted env (no `io`/`os`/`require`/`package`/`debug`/`load`/
  `dofile`), and gates every `render(ctx)` call with a debug-hook
  instruction budget so a runaway loop can't peg a CPU. The seeded
  builtins live as plain `.lua` files under `backend/app/lua/builtins/`
  — edit them on disk, restart, and the seeder upserts the new source
  into the matching `Effect` row on next boot.
- The Designer and effect-chat tabs stream Claude via
  `anthropic.Anthropic().messages.stream()` from a worker thread that
  feeds an `asyncio.Queue`. The FastAPI handler drains the queue as
  Server-Sent Events. The effect-chat orchestrator additionally runs
  every proposal through `smoke_test_source` and re-streams up to 3
  attempts per turn before persisting; the full assistant turn is
  saved in a single commit at the stream's `done` boundary, so a
  disconnect mid-stream cleanly drops the turn.
- `backend/app/lua_refiner.py` is the small "fix-this-one-script"
  sub-agent invoked from the designer's persist phase. It only fires
  when a custom Lua source fails the smoke test, makes a single
  `propose_effect` Anthropic call seeded with the broken source plus
  a targeted diagnostic, and loops up to 3 attempts. Healthy scripts
  skip the refiner entirely.
- `backend/app/engine.py` owns the layered compositor. Each
  `EffectLayer` row spawns one `EffectSpec` in `_active`; the tick
  loop sorts by `(z_index, layer_id, handle)`, walks bottom-up,
  computes each layer's per-light overlay, and composites it via
  `merge_overlay_into_state` using the layer's `blend_mode` and
  `opacity`. WebSocket subscribers on `/api/layers/ws` get pushed a
  fresh snapshot whenever the active set changes, with the engine
  health embedded in every frame.
- The frontend is one SPA with a viewport-aware shell
  (`frontend/src/components/shell/AppShell.tsx`) that swaps mobile
  bottom-tab chrome and desktop side-nav-plus-Live-rail chrome at the
  Tailwind `md` breakpoint. A single `LayerStoreProvider`
  (`frontend/src/state/layers.tsx`) opens one reconnecting WebSocket
  to `/api/layers/ws` and broadcasts to every screen that shows
  layer state — mobile Now Playing, desktop Live rail, Effects
  Composer — so the UI never disagrees with itself.

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

Each is a seeded Lua script under
[backend/app/lua/builtins/](backend/app/lua/builtins/). Read-only in the
UI; clone any of them to make an editable copy.

| Name | Script | Target channels | Notes |
| --- | --- | --- | --- |
| Rainbow Wash | `rainbow.lua` | `rgb` | Slow full-hue HSV sweep; ignores palette. |
| Breathing Amber | `pulse.lua` | `rgb` | Candlelight palette breathing. |
| Cyberpunk Chase | `chase.lua` | `rgb` | Neon chase across the rig. |
| Aurora Fade | `fade.lua` | `rgb` | Across-fixture aurora crossfade. |
| Halloween Strobe | `strobe.lua` | `rgb` | 6 Hz flash on the Halloween palette. |
| Pastel Sparkle | `sparkle.lua` | `rgb` | Random pastel flashes per zone. |
| White LED Chase | `chase.lua` | `w` | Chases the white LED without touching RGB. |
| Strobe Pulse (Strobe Channel) | `pulse.lua` | `strobe` | Pulses the fixture's strobe-rate fader. |
| UV Accent Wave | `wave.lua` | `uv` | Slow UV brightness wave. |

The remaining canonical scripts (`cycle.lua`, `static.lua`) are also
shipped and available via Clone → edit; the table above lists only the
ones the seeder upserts as named presets.

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
