"""Pure-math layer for the effect engine.

This module contains no asyncio, no DB, and no sockets. It takes in a scene
definition + current `t` + the materialized light/mode snapshot and returns
a per-light color overlay that the engine merges with each fixture's base
state before rendering to DMX.

Every effect is a special case of one formula:

    phase_i = fract(t * speed_hz + i * offset)
    color_i = samplePalette(palette, phase_i)      # or HSV for rainbow
    bri_i   = envelope(phase_i, size, softness)    # brightness shaping
    out_i   = mix(base_i, color_i * bri_i, intensity)

The ``i`` axis comes from the scene's ``spread``:

* ``across_lights`` - one index per fixture
* ``across_fixture`` - per-fixture, indices = zones of that fixture
* ``across_zones`` - flatten every zone across the selection into one list
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Iterable, Optional

from .models import Light, LightModelMode, Palette


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------
def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _fract(v: float) -> float:
    return v - math.floor(v)


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    """h, s, v in [0, 1]. Returns 0-255 ints."""
    h = _fract(h)
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return (
        max(0, min(255, int(round(r * 255)))),
        max(0, min(255, int(round(g * 255)))),
        max(0, min(255, int(round(b * 255)))),
    )


# ---------------------------------------------------------------------------
# Palette sampling
# ---------------------------------------------------------------------------
def sample_palette_smooth(
    colors: list[str], phase: float
) -> tuple[int, int, int]:
    """Smooth (wrapping) interpolation across the palette at ``phase`` in
    [0, 1). The last stop blends back into the first so cycles are seamless."""
    if not colors:
        return (0, 0, 0)
    if len(colors) == 1:
        return hex_to_rgb(colors[0])
    phase = _fract(phase)
    n = len(colors)
    pos = phase * n
    lo = int(pos) % n
    hi = (lo + 1) % n
    frac = pos - int(pos)
    r1, g1, b1 = hex_to_rgb(colors[lo])
    r2, g2, b2 = hex_to_rgb(colors[hi])
    return (
        int(round(r1 + (r2 - r1) * frac)),
        int(round(g1 + (g2 - g1) * frac)),
        int(round(b1 + (b2 - b1) * frac)),
    )


def sample_palette_step(
    colors: list[str], phase: float
) -> tuple[int, int, int]:
    """Stepped (no interpolation) sample at ``phase``."""
    if not colors:
        return (0, 0, 0)
    phase = _fract(phase)
    idx = int(phase * len(colors)) % len(colors)
    return hex_to_rgb(colors[idx])


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------
def _apply_direction(phase: float, direction: str, cycles_done: float) -> float:
    phase = _fract(phase)
    if direction == "reverse":
        return _fract(1.0 - phase)
    if direction == "pingpong":
        # Triangle wave on cycles_done's integer part: alternate direction.
        if int(cycles_done) % 2 == 1:
            return _fract(1.0 - phase)
    return phase


def envelope_chase(phase: float, size: float, softness: float) -> float:
    """A moving window of width ~size/N lit, tapered by ``softness``.

    Because phase already incorporates the per-index offset, the "lit"
    window is centered where ``phase`` is near 0 (equivalently 1.0)."""
    # Distance to the nearest wrap of 0 in [0, 1).
    d = min(phase, 1.0 - phase)
    width = max(0.001, 0.5 * size)
    if d >= width:
        return 0.0
    t = 1.0 - (d / width)
    if softness <= 0.0:
        return 1.0 if t > 0.0 else 0.0
    edge = softness
    if t >= 1.0 - edge:
        return 1.0
    return t / max(0.001, 1.0 - edge)


def envelope_pulse(phase: float) -> float:
    """Full cosine pulse: bright at phase 0/1, dark at 0.5."""
    return 0.5 + 0.5 * math.cos(2.0 * math.pi * phase)


def envelope_strobe(phase: float, duty: float) -> float:
    duty = _clamp(duty, 0.02, 0.98)
    return 1.0 if phase < duty else 0.0


def envelope_wave(phase: float) -> float:
    """Smooth sine wave in [0, 1]."""
    return 0.5 + 0.5 * math.sin(2.0 * math.pi * phase)


# ---------------------------------------------------------------------------
# Scene expansion: spread -> list of (light_id, zone_id | None) tuples
# ---------------------------------------------------------------------------
@dataclass
class TargetSlot:
    light_id: int
    zone_id: Optional[str]  # None = whole fixture


def zone_ids_for_light(
    light: Light, modes_by_id: dict[int, LightModelMode]
) -> list[str]:
    """Ordered zone ids for a light, honoring (row, col) if present."""
    mode = modes_by_id.get(light.mode_id) if light.mode_id else None
    if mode is None:
        return []
    layout = mode.layout if isinstance(mode.layout, dict) else None
    if not layout:
        return []
    zones = layout.get("zones") or []
    ordered = sorted(
        enumerate(zones),
        key=lambda p: (
            p[1].get("row", 0) or 0,
            p[1].get("col", 0) or 0,
            p[0],
        ),
    )
    return [z.get("id") for _, z in ordered if isinstance(z.get("id"), str)]


def expand_slots(
    spread: str,
    light_ids: list[int],
    targets: list[dict],
    lights_by_id: dict[int, Light],
    modes_by_id: dict[int, LightModelMode],
) -> list[list[TargetSlot]]:
    """Return a list of "groups" of TargetSlots.

    Each group is animated against its own index axis (so an effect runs
    independently inside each fixture when spread=across_fixture).

    * ``across_lights``: one group containing one slot per selected light.
      Each slot targets the whole fixture (zone_id=None).
    * ``across_fixture``: one group per fixture, slots = that fixture's
      zones (or a single whole-fixture slot for simple pars).
    * ``across_zones``: one group containing every zone across the whole
      selection flattened into a single index axis.

    ``targets`` entries override which zones participate when provided;
    if empty the fixture's full zone list is used.
    """
    # Per-light, collect the set of zones explicitly requested (if any).
    per_light_zones: dict[int, list[Optional[str]]] = {}
    for lid in light_ids:
        per_light_zones.setdefault(lid, [None])  # whole fixture
    for t in targets or []:
        lid = t.get("light_id")
        zid = t.get("zone_id")
        if lid is None:
            continue
        arr = per_light_zones.setdefault(lid, [])
        if None in arr and zid is not None:
            # Swap "whole" for explicit zones if both are mentioned.
            arr.remove(None)
        arr.append(zid)

    ordered_lids = list(per_light_zones.keys())

    if spread == "across_lights":
        group: list[TargetSlot] = []
        for lid in ordered_lids:
            group.append(TargetSlot(light_id=lid, zone_id=None))
        return [group]

    if spread == "across_fixture":
        groups: list[list[TargetSlot]] = []
        for lid in ordered_lids:
            light = lights_by_id.get(lid)
            if light is None:
                continue
            zone_list = zone_ids_for_light(light, modes_by_id)
            explicit = [z for z in per_light_zones[lid] if z is not None]
            if explicit:
                picks: list[Optional[str]] = list(explicit)
            elif zone_list:
                picks = list(zone_list)  # type: ignore[list-item]
            else:
                picks = [None]
            groups.append(
                [TargetSlot(light_id=lid, zone_id=z) for z in picks]
            )
        return groups

    # across_zones: flatten everything into one index axis.
    flat: list[TargetSlot] = []
    for lid in ordered_lids:
        light = lights_by_id.get(lid)
        if light is None:
            continue
        zone_list = zone_ids_for_light(light, modes_by_id)
        explicit = [z for z in per_light_zones[lid] if z is not None]
        if explicit:
            for zid in explicit:
                flat.append(TargetSlot(light_id=lid, zone_id=zid))
        elif zone_list:
            for zid in zone_list:
                flat.append(TargetSlot(light_id=lid, zone_id=zid))
        else:
            flat.append(TargetSlot(light_id=lid, zone_id=None))
    return [flat]


# ---------------------------------------------------------------------------
# Per-index color computation
# ---------------------------------------------------------------------------
@dataclass
class IndexOutput:
    """Result of computing an effect at one index slot.

    ``rgb`` is the effect's desired color (pre-intensity).
    ``brightness`` is the envelope value in [0, 1].
    ``active`` means the effect wants to drive this slot at all; when False
    the engine leaves the slot's base state alone (useful for sparkle /
    chase gaps so the base color shows through cleanly).
    """

    rgb: tuple[int, int, int]
    brightness: float = 1.0
    active: bool = True


def _palette_colors(
    palette: Optional[Palette], fallback: Optional[list[str]] = None
) -> list[str]:
    if palette is not None and palette.colors:
        return list(palette.colors)
    return list(fallback or ["#FFFFFF"])


def _sparkle_on(
    scene_id_seed: int, slot_key: tuple[int, Optional[str]], t: float, rate_hz: float
) -> tuple[bool, float]:
    """Deterministic-ish per-slot sparkle gating.

    Uses a hashed seed so concurrent scenes don't all flash at the same
    moments. Returns (is_on, phase_within_flash) - the phase is used to
    shape a small decay envelope."""
    bucket = int(t * max(0.1, rate_hz))
    key = f"{scene_id_seed}:{slot_key[0]}:{slot_key[1]}:{bucket}"
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    on = (h & 0xFF) < 96  # ~38% chance per bucket
    phase_in_bucket = _fract(t * max(0.1, rate_hz))
    return on, phase_in_bucket


def compute_effect_outputs(
    effect_type: str,
    palette_colors: list[str],
    params: dict,
    group: list[TargetSlot],
    t: float,
    scene_seed: int,
) -> list[IndexOutput]:
    """Return one IndexOutput per slot in ``group``."""
    speed_hz = float(params.get("speed_hz", 0.5))
    direction = str(params.get("direction", "forward"))
    offset = float(params.get("offset", 0.0))
    size = float(params.get("size", 1.0))
    softness = float(params.get("softness", 0.5))

    n = len(group)
    if n == 0:
        return []

    # Offset slider maps [0, 1] -> per-index phase step. A user-friendly
    # "perfect chase" lands at offset == 1/n, so we scale so that 1.0 maps
    # to one full cycle per N indices.
    per_index = offset / max(1, n) if offset <= 1.0 else offset
    # Global time contribution.
    cycles_done = t * speed_hz

    out: list[IndexOutput] = []
    for i, slot in enumerate(group):
        raw_phase = cycles_done + i * per_index
        phase = _apply_direction(raw_phase, direction, cycles_done)

        if effect_type == "static":
            # Distribute palette across indices at t=0; no time-evolution.
            if n <= 1:
                pick_phase = 0.0
            else:
                pick_phase = i / n
            rgb = sample_palette_smooth(palette_colors, pick_phase)
            out.append(IndexOutput(rgb=rgb, brightness=1.0))
            continue

        if effect_type == "fade":
            rgb = sample_palette_smooth(palette_colors, phase)
            out.append(IndexOutput(rgb=rgb, brightness=1.0))
            continue

        if effect_type == "cycle":
            rgb = sample_palette_step(palette_colors, phase)
            out.append(IndexOutput(rgb=rgb, brightness=1.0))
            continue

        if effect_type == "chase":
            bri = envelope_chase(phase, max(0.05, size / max(1, n) * 2.0), softness)
            if bri <= 0.0:
                out.append(IndexOutput(rgb=(0, 0, 0), brightness=0.0, active=False))
                continue
            # Color follows palette at cycles_done so every lit slot shares
            # the currently-advancing palette hue.
            rgb = sample_palette_smooth(palette_colors, cycles_done * 0.5)
            out.append(IndexOutput(rgb=rgb, brightness=bri))
            continue

        if effect_type == "pulse":
            bri = envelope_pulse(phase)
            # Pick a palette color per slot that evolves over time (slow).
            rgb_phase = cycles_done * 0.25 + i / max(1, n)
            rgb = sample_palette_smooth(palette_colors, rgb_phase)
            out.append(IndexOutput(rgb=rgb, brightness=bri))
            continue

        if effect_type == "rainbow":
            rgb = hsv_to_rgb(phase, 1.0, 1.0)
            out.append(IndexOutput(rgb=rgb, brightness=1.0))
            continue

        if effect_type == "strobe":
            duty = _clamp(size, 0.02, 0.98)
            bri = envelope_strobe(phase, duty)
            rgb = sample_palette_smooth(palette_colors, cycles_done * 0.1)
            out.append(IndexOutput(rgb=rgb, brightness=bri, active=bri > 0.0))
            continue

        if effect_type == "sparkle":
            on, flash_phase = _sparkle_on(
                scene_seed, (slot.light_id, slot.zone_id), t, max(0.5, speed_hz * 4.0)
            )
            if not on:
                out.append(IndexOutput(rgb=(0, 0, 0), brightness=0.0, active=False))
                continue
            # Quick decay envelope inside each sparkle bucket.
            decay = max(0.0, 1.0 - flash_phase)
            # Random-ish palette pick per bucket.
            bucket = int(t * max(0.5, speed_hz * 4.0))
            rr = random.Random(f"{scene_seed}:{slot.light_id}:{slot.zone_id}:{bucket}")
            if palette_colors:
                rgb = hex_to_rgb(palette_colors[rr.randrange(len(palette_colors))])
            else:
                rgb = (255, 255, 255)
            out.append(IndexOutput(rgb=rgb, brightness=decay))
            continue

        if effect_type == "wave":
            bri = envelope_wave(phase)
            rgb_phase = cycles_done * 0.25
            rgb = sample_palette_smooth(palette_colors, rgb_phase)
            out.append(IndexOutput(rgb=rgb, brightness=bri))
            continue

        # Unknown effect: no-op.
        out.append(IndexOutput(rgb=(0, 0, 0), brightness=0.0, active=False))

    return out


# ---------------------------------------------------------------------------
# Overlay assembly: scene -> {light_id: overlay}
# ---------------------------------------------------------------------------
@dataclass
class LightOverlay:
    """Per-light effect contribution ready for merging with the base state.

    ``flat`` is the whole-fixture desired RGB+intensity (None means the
    effect is not writing the flat fallback for this fixture).
    ``zones`` maps zone_id -> desired RGB+intensity for that zone.
    ``intensity`` is the effect's overall blend weight (0..1) multiplied
    by any fade-in/out envelope; the engine mixes with base accordingly."""

    flat: Optional[tuple[int, int, int, float]] = None  # r, g, b, bri
    zones: dict[str, tuple[int, int, int, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.zones is None:
            self.zones = {}


def compute_scene_overlays(
    scene_id: int,
    effect_type: str,
    palette_colors: list[str],
    params: dict,
    spread: str,
    light_ids: list[int],
    targets: list[dict],
    t: float,
    lights_by_id: dict[int, Light],
    modes_by_id: dict[int, LightModelMode],
) -> dict[int, LightOverlay]:
    """Compute per-light overlays for a single scene at time ``t``."""
    groups = expand_slots(
        spread, light_ids, targets, lights_by_id, modes_by_id
    )
    overlays: dict[int, LightOverlay] = {}

    intensity = float(params.get("intensity", 1.0))

    for group in groups:
        outs = compute_effect_outputs(
            effect_type, palette_colors, params, group, t, scene_id
        )
        for slot, result in zip(group, outs):
            if not result.active:
                # Still create an overlay entry so the merger can mix base
                # at 0 effect intensity (keeps base visible cleanly).
                ov = overlays.setdefault(slot.light_id, LightOverlay())
                eff_bri = 0.0
            else:
                ov = overlays.setdefault(slot.light_id, LightOverlay())
                eff_bri = max(0.0, min(1.0, result.brightness)) * intensity

            r, g, b = result.rgb
            if slot.zone_id is None:
                ov.flat = (r, g, b, eff_bri)
            else:
                ov.zones[slot.zone_id] = (r, g, b, eff_bri)

    return overlays


def merge_overlay_into_state(
    base_state: dict,
    overlay: LightOverlay,
    zone_ids: Iterable[str],
    fade_weight: float,
) -> dict:
    """Produce a rendered state dict for one light.

    ``base_state`` is the light's current DB-backed state (flat r/g/b/w/a/uv
    + zone_state + motion_state). ``fade_weight`` is the scene's current
    fade-in/out envelope in [0, 1]. The result has the same shape the
    ArtNet renderer expects."""
    out = dict(base_state)
    zone_state = dict(base_state.get("zone_state") or {})

    def _mix(a: int, b: int, w: float) -> int:
        w = max(0.0, min(1.0, w))
        return max(0, min(255, int(round(a * (1.0 - w) + b * w))))

    # Whole-fixture overlay: blend into flat fields and into every zone
    # that does not have an explicit zone overlay.
    flat_zone_ids = set(zone_ids)
    if overlay.flat is not None:
        r, g, b, eff = overlay.flat
        eff *= fade_weight
        out["r"] = _mix(int(base_state.get("r", 0)), int(r * 1.0), eff)
        out["g"] = _mix(int(base_state.get("g", 0)), int(g * 1.0), eff)
        out["b"] = _mix(int(base_state.get("b", 0)), int(b * 1.0), eff)
        # Derive w/a if not explicitly held by base.
        if base_state.get("w") is not None:
            out["w"] = _mix(int(base_state.get("w", 0)), min(r, g, b), eff)
        if base_state.get("a") is not None:
            out["a"] = _mix(int(base_state.get("a", 0)), min(r, g) // 2, eff)
        # Also propagate to every zone that isn't overridden by a zone
        # overlay below - this makes "across_lights" actually colour every
        # pixel of a compound fixture.
        for zid in flat_zone_ids:
            if zid in overlay.zones:
                continue
            zs = dict(zone_state.get(zid) or {})
            zr = int(zs.get("r", base_state.get("r", 0)))
            zg = int(zs.get("g", base_state.get("g", 0)))
            zb = int(zs.get("b", base_state.get("b", 0)))
            zs["r"] = _mix(zr, r, eff)
            zs["g"] = _mix(zg, g, eff)
            zs["b"] = _mix(zb, b, eff)
            zs["on"] = True
            zone_state[zid] = zs

    # Per-zone overlays win over flat for their specific zones.
    for zid, (r, g, b, eff) in overlay.zones.items():
        eff *= fade_weight
        zs = dict(zone_state.get(zid) or {})
        zr = int(zs.get("r", base_state.get("r", 0)))
        zg = int(zs.get("g", base_state.get("g", 0)))
        zb = int(zs.get("b", base_state.get("b", 0)))
        zs["r"] = _mix(zr, r, eff)
        zs["g"] = _mix(zg, g, eff)
        zs["b"] = _mix(zb, b, eff)
        zs["on"] = True
        zone_state[zid] = zs

    out["zone_state"] = zone_state
    return out
