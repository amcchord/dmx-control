"""Effect overlay assembly + base/overlay merging.

The math that was once a giant ``compute_effect_outputs`` switch over
``effect_type`` strings has moved into Lua scripts (see
``backend/app/lua/``). This module now only handles:

* :func:`expand_slots` - turn ``spread`` + ``light_ids`` + ``targets``
  into one or more per-group lists of ``TargetSlot``.
* :func:`compute_lua_overlays` - call the spec's :class:`LuaScript` for
  every slot, building a per-light :class:`LightOverlay`.
* :func:`merge_overlay_into_state` - blend a per-light overlay onto the
  fixture's base DB state (RGB / W / A / UV / dimmer / strobe).
* :class:`LightOverlay` / :class:`TargetSlot` - shared dataclasses.
"""

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
# Overlay merge
# ---------------------------------------------------------------------------
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
) -> dict:
    """Produce a rendered state dict for one light.

    ``base_state`` is the light's current DB-backed state (flat r/g/b/w/a/uv
    + zone_state + motion_state). ``fade_weight`` is the effect's current
    fade-in/out envelope in [0, 1]. The result has the same shape the
    ArtNet renderer expects."""
    out = dict(base_state)
    zone_state = dict(base_state.get("zone_state") or {})
    policy = color_policy or {}
    tc = {c for c in (target_channels or ["rgb"]) if isinstance(c, str)}
    if not tc:
        tc = {"rgb"}
    touches_rgb = "rgb" in tc

    def _mix(a: int, b: int, w: float) -> int:
        w = max(0.0, min(1.0, w))
        return max(0, min(255, int(round(a * (1.0 - w) + b * w))))

    flat_zone_ids = set(zone_ids)
    if overlay.flat is not None:
        r, g, b, eff = overlay.flat
        eff *= fade_weight
        scalar = _scalar_from_rgb(r, g, b)
        if touches_rgb:
            out["r"] = _mix(int(base_state.get("r", 0)), int(r), eff)
            out["g"] = _mix(int(base_state.get("g", 0)), int(g), eff)
            out["b"] = _mix(int(base_state.get("b", 0)), int(b), eff)
            if base_state.get("w") is not None and policy.get("w") != "direct":
                out["w"] = _mix(int(base_state.get("w", 0)), min(r, g, b), eff)
            if base_state.get("a") is not None and policy.get("a") != "direct":
                out["a"] = _mix(int(base_state.get("a", 0)), min(r, g) // 2, eff)
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
        for aux in ("w", "a", "uv"):
            if aux not in tc:
                continue
            base_aux = int(base_state.get(aux) or 0)
            out[aux] = _mix(base_aux, scalar, eff)
        if "dimmer" in tc:
            base_dim = int(base_state.get("dimmer") or 0)
            out["dimmer"] = _mix(base_dim, scalar, eff)
        if "strobe" in tc:
            base_strobe = int(base_state.get("strobe") or 0)
            out["strobe"] = _mix(base_strobe, scalar, eff)

    for zid, (r, g, b, eff) in overlay.zones.items():
        eff *= fade_weight
        if not touches_rgb:
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

    out["zone_state"] = zone_state
    return out
