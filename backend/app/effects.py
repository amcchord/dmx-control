"""Effect overlay assembly + per-layer compositing.

The math that was once a giant ``compute_effect_outputs`` switch over
``effect_type`` strings has moved into Lua scripts (see
``backend/app/lua/``). This module now only handles:

* :func:`expand_slots` - turn ``spread`` + ``light_ids`` + ``targets``
  into one or more per-group lists of ``TargetSlot``.
* :func:`compute_lua_overlays` - call the spec's :class:`LuaScript` for
  every slot, building a per-light :class:`LightOverlay`.
* :func:`merge_overlay_into_state` - composite a per-light overlay onto
  the running state per channel using the layer's ``blend_mode`` and
  ``opacity`` (the engine seeds the running state from the fixture's
  base DB row and then walks the layer stack bottom-to-top).
* :class:`LightOverlay` / :class:`TargetSlot` - shared dataclasses.

Blend modes (:data:`BLEND_MODES`):

  ``normal``   - linear cross-fade (matches legacy single-effect merge)
  ``add``      - additive: ``out = clamp(below + overlay * opacity)``
  ``multiply`` - ``out = below * mix(1, overlay, opacity)`` (darken)
  ``screen``   - inverted multiply (lighten)
  ``max``      - per-channel max (brightest wins)
  ``min``      - per-channel min (darkest wins)
  ``replace``  - overwrite (ignore below; ``opacity`` still scales the
                 transition between below and overlay)

Each blend operates per channel, so RGB/W/A/UV/dimmer/strobe all combine
correctly under the same compositor; aux channels honor the mode's
``color_policy`` so a "direct" white fader is never derived from RGB
when an effect only writes RGB above it."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional

from .lua import LuaScript, ScriptError
from .models import Light, LightModelMode

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from .engine import EffectSpec

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Color utilities (kept around: palettes etc. still use them)
# ---------------------------------------------------------------------------
def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# ---------------------------------------------------------------------------
# Slot expansion
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

    See module docstring for the spread semantics."""
    if not light_ids and not targets:
        light_ids = list(lights_by_id.keys())

    per_light_zones: dict[int, list[Optional[str]]] = {}
    for lid in light_ids:
        per_light_zones.setdefault(lid, [None])
    for t in targets or []:
        lid = t.get("light_id")
        zid = t.get("zone_id")
        if lid is None:
            continue
        arr = per_light_zones.setdefault(lid, [])
        if None in arr and zid is not None:
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

    # across_zones
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
# Lua script -> overlay
# ---------------------------------------------------------------------------
@dataclass
class LightOverlay:
    """Per-light effect contribution ready for merging with the base state.

    ``flat`` is the whole-fixture desired RGB+intensity (None means the
    effect is not writing the flat fallback for this fixture).
    ``zones`` maps zone_id -> desired RGB+intensity for that zone."""

    flat: Optional[tuple[int, int, int, float]] = None  # r, g, b, bri
    zones: dict[str, tuple[int, int, int, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.zones is None:
            self.zones = {}


def _palette_triples(colors: list[str]) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for hx in colors or []:
        s = hx.strip().lstrip("#")
        if len(s) != 6:
            continue
        try:
            out.append((int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)))
        except ValueError:
            continue
    if not out:
        out.append((255, 255, 255))
    return out


def compute_lua_overlays(
    *,
    spec: "EffectSpec",
    t: float,
    frame: int,
    lights_by_id: dict[int, Light],
    modes_by_id: dict[int, LightModelMode],
) -> dict[int, LightOverlay]:
    """Run the spec's Lua script across every expanded slot."""
    groups = expand_slots(
        spec.spread,
        list(spec.light_ids or []),
        list(spec.targets or []),
        lights_by_id,
        modes_by_id,
    )
    overlays: dict[int, LightOverlay] = {}
    script = spec.script
    pal_obj = script.make_palette(_palette_triples(spec.palette_colors))

    seed = (
        spec.effect_id
        if isinstance(spec.effect_id, int)
        else (hash(spec.handle) & 0x7FFFFFFF)
    )

    for group in groups:
        n = len(group)
        if n == 0:
            continue
        for i, slot in enumerate(group):
            ctx = script.new_table()
            ctx["t"] = float(t)
            ctx["i"] = i
            ctx["n"] = n
            ctx["frame"] = frame
            ctx["seed"] = seed
            ctx["palette"] = pal_obj
            params_tbl = script.new_table()
            for k, v in (spec.params or {}).items():
                params_tbl[k] = v
            ctx["params"] = params_tbl
            slot_tbl = script.new_table()
            slot_tbl["light_id"] = slot.light_id
            slot_tbl["zone_id"] = slot.zone_id
            ctx["slot"] = slot_tbl

            try:
                result = script.render_slot(ctx)
            except ScriptError:
                raise

            ov = overlays.setdefault(slot.light_id, LightOverlay())
            if not result.get("active", False):
                eff_bri = 0.0
                r = g = b = 0
            else:
                eff_bri = float(result.get("brightness", 0.0))
                if eff_bri < 0.0:
                    eff_bri = 0.0
                elif eff_bri > 1.0:
                    eff_bri = 1.0
                r = int(result.get("r", 0))
                g = int(result.get("g", 0))
                b = int(result.get("b", 0))

            if slot.zone_id is None:
                ov.flat = (r, g, b, eff_bri)
            else:
                ov.zones[slot.zone_id] = (r, g, b, eff_bri)

    return overlays


# ---------------------------------------------------------------------------
# Blend modes
# ---------------------------------------------------------------------------
BLEND_MODES = ("normal", "add", "multiply", "screen", "max", "min", "replace")


def _clamp_byte(v: float) -> int:
    if v <= 0.0:
        return 0
    if v >= 255.0:
        return 255
    return int(round(v))


def _blend_byte(below: int, overlay: int, mode: str, opacity: float) -> int:
    """Composite a single 0-255 channel value.

    The ``opacity`` (combined effect intensity * fade envelope * layer
    opacity) controls how much of the ``mode`` operation contributes to
    the final value; at opacity 0 we always return ``below``, at opacity
    1 we return the pure ``mode`` result. This keeps fade in/out smooth
    regardless of which blend mode the layer uses."""
    o = max(0.0, min(1.0, float(opacity)))
    if o <= 0.0:
        return int(below)
    a = float(below)
    b = float(overlay)
    if mode == "normal":
        out = a * (1.0 - o) + b * o
    elif mode == "replace":
        out = a * (1.0 - o) + b * o
    elif mode == "add":
        out = a + b * o
    elif mode == "multiply":
        # `b/255` blended with 1.0 by opacity, then multiplies `a`.
        m = (1.0 - o) + o * (b / 255.0)
        out = a * m
    elif mode == "screen":
        # 1 - (1-a)(1-b) projected through opacity.
        screened = 255.0 - (255.0 - a) * (255.0 - b) / 255.0
        out = a * (1.0 - o) + screened * o
    elif mode == "max":
        out = a * (1.0 - o) + max(a, b) * o
    elif mode == "min":
        out = a * (1.0 - o) + min(a, b) * o
    else:
        out = a * (1.0 - o) + b * o
    return _clamp_byte(out)


def _scalar_from_rgb(r: int, g: int, b: int) -> int:
    """Collapse an RGB triple to a 0-255 scalar for aux-channel overlays."""
    return max(0, min(255, max(int(r), int(g), int(b))))


def merge_overlay_into_state(
    base_state: dict,
    overlay: LightOverlay,
    zone_ids: Iterable[str],
    fade_weight: float,
    color_policy: dict | None = None,
    target_channels: Optional[list[str]] = None,
    blend_mode: str = "normal",
    layer_opacity: float = 1.0,
) -> dict:
    """Composite ``overlay`` onto ``base_state`` for one light.

    Walks each writable channel and applies :func:`_blend_byte` using the
    layer's ``blend_mode``. Effective opacity per channel is the product
    of the overlay's per-slot brightness, ``fade_weight`` (engine fade
    envelope), and ``layer_opacity`` (Photoshop-style mixer slider). The
    result has the same shape the ArtNet renderer expects so it can flow
    straight back into the running tick state for the next layer."""
    out = dict(base_state)
    zone_state = dict(base_state.get("zone_state") or {})
    policy = color_policy or {}
    tc = {c for c in (target_channels or ["rgb"]) if isinstance(c, str)}
    if not tc:
        tc = {"rgb"}
    touches_rgb = "rgb" in tc
    mode = blend_mode if blend_mode in BLEND_MODES else "normal"
    lop = max(0.0, min(1.0, float(layer_opacity)))

    # When a layer contributes any output, force the fixture-level
    # ``on`` flag on for this frame. Otherwise the Art-Net renderer
    # short-circuits to all-zero in ``_compute_*_values`` whenever
    # ``light.on`` is False — and operators expect a freshly-pushed
    # effect to win against a stale blackout. The base DB ``on`` flag
    # is unchanged; only the rendered state for this tick is forced.
    has_contribution = (
        overlay.flat is not None
        and (lop * fade_weight) > 0.0
    ) or any(
        (lop * fade_weight) > 0.0 for _ in overlay.zones.values()
    )
    if has_contribution:
        out["on"] = True

    flat_zone_ids = set(zone_ids)
    if overlay.flat is not None:
        r, g, b, eff = overlay.flat
        eff = max(0.0, min(1.0, eff * fade_weight)) * lop
        scalar = _scalar_from_rgb(r, g, b)
        if touches_rgb:
            out["r"] = _blend_byte(int(base_state.get("r", 0)), int(r), mode, eff)
            out["g"] = _blend_byte(int(base_state.get("g", 0)), int(g), mode, eff)
            out["b"] = _blend_byte(int(base_state.get("b", 0)), int(b), mode, eff)
            if base_state.get("w") is not None and policy.get("w") != "direct":
                out["w"] = _blend_byte(
                    int(base_state.get("w", 0)), int(min(r, g, b)), mode, eff
                )
            if base_state.get("a") is not None and policy.get("a") != "direct":
                out["a"] = _blend_byte(
                    int(base_state.get("a", 0)),
                    int(min(r, g) // 2),
                    mode,
                    eff,
                )
            for zid in flat_zone_ids:
                if zid in overlay.zones:
                    continue
                zs = dict(zone_state.get(zid) or {})
                zr = int(zs.get("r", base_state.get("r", 0)))
                zg = int(zs.get("g", base_state.get("g", 0)))
                zb = int(zs.get("b", base_state.get("b", 0)))
                zs["r"] = _blend_byte(zr, r, mode, eff)
                zs["g"] = _blend_byte(zg, g, mode, eff)
                zs["b"] = _blend_byte(zb, b, mode, eff)
                zs["on"] = True
                zone_state[zid] = zs
        for aux in ("w", "a", "uv"):
            if aux not in tc:
                continue
            base_aux = int(base_state.get(aux) or 0)
            out[aux] = _blend_byte(base_aux, scalar, mode, eff)
        if "dimmer" in tc:
            base_dim = int(base_state.get("dimmer") or 0)
            out["dimmer"] = _blend_byte(base_dim, scalar, mode, eff)
        if "strobe" in tc:
            base_strobe = int(base_state.get("strobe") or 0)
            out["strobe"] = _blend_byte(base_strobe, scalar, mode, eff)

    for zid, (r, g, b, eff) in overlay.zones.items():
        eff = max(0.0, min(1.0, eff * fade_weight)) * lop
        if not touches_rgb:
            continue
        zs = dict(zone_state.get(zid) or {})
        zr = int(zs.get("r", base_state.get("r", 0)))
        zg = int(zs.get("g", base_state.get("g", 0)))
        zb = int(zs.get("b", base_state.get("b", 0)))
        zs["r"] = _blend_byte(zr, r, mode, eff)
        zs["g"] = _blend_byte(zg, g, mode, eff)
        zs["b"] = _blend_byte(zb, b, mode, eff)
        zs["on"] = True
        zone_state[zid] = zs

    out["zone_state"] = zone_state
    return out
