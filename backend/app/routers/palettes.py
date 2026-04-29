"""Palette CRUD, apply, and one-shot Claude generation.

Palettes are named color lists. Each entry carries RGB plus optional
W/A/UV values so the paint logic can drive auxiliary LED channels
directly when the fixture's policy is ``direct`` and the palette has
opinionated aux values. The legacy ``colors: string[]`` shape is kept in
sync for backward compatibility with consumers that only care about
RGB (e.g. the Designer rig snapshot fallback, the simulated preview).
"""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager
from ..auth import AuthDep
from ..base_state_log import log as base_state_log
from .. import config as _config
from ..db import get_session
from ..models import Light, LightModelMode, Palette
from ..schemas import (
    ApplyPaletteRequest,
    PaletteEntry,
    PaletteGenerateRequest,
    PaletteGenerateResponse,
    PaletteIn,
    PaletteOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/palettes", tags=["palettes"], dependencies=[AuthDep])


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return (r, g, b)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _entries_for_palette(p: Palette) -> list[PaletteEntry]:
    """Return :class:`PaletteEntry` list for a palette row.

    Prefers the structured ``entries`` column; falls back to parsing the
    legacy ``colors`` hex list for rows that haven't been migrated yet."""
    raw = p.entries or []
    out: list[PaletteEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(PaletteEntry(**item))
        except Exception:
            continue
    if out:
        return out
    for hex_color in list(p.colors or []):
        try:
            r, g, b = _hex_to_rgb(hex_color)
        except Exception:
            continue
        out.append(PaletteEntry(r=r, g=g, b=b))
    return out


def _entry_to_dict(e: PaletteEntry) -> dict:
    d: dict[str, int] = {"r": e.r, "g": e.g, "b": e.b}
    if e.w is not None:
        d["w"] = int(e.w)
    if e.a is not None:
        d["a"] = int(e.a)
    if e.uv is not None:
        d["uv"] = int(e.uv)
    return d


def _to_out(p: Palette) -> PaletteOut:
    entries = _entries_for_palette(p)
    colors = list(p.colors or [])
    if not colors and entries:
        colors = [_rgb_to_hex(e.r, e.g, e.b) for e in entries]
    return PaletteOut(
        id=p.id,
        name=p.name,
        colors=colors,
        entries=entries,
        builtin=p.builtin,
    )


def _persist_payload(p: Palette, payload: PaletteIn) -> None:
    """Copy name + (colors, entries) from a validated payload onto a row."""
    p.name = payload.name
    p.entries = [_entry_to_dict(e) for e in (payload.entries or [])]
    p.colors = list(payload.colors or [])


@router.get("")
def list_palettes(sess: Session = Depends(get_session)) -> list[PaletteOut]:
    rows = sess.exec(select(Palette).order_by(Palette.builtin.desc(), Palette.name)).all()
    return [_to_out(p) for p in rows]


@router.post("", status_code=201)
def create_palette(payload: PaletteIn, sess: Session = Depends(get_session)) -> PaletteOut:
    p = Palette(name=payload.name, builtin=False)
    _persist_payload(p, payload)
    sess.add(p)
    sess.commit()
    sess.refresh(p)
    return _to_out(p)


@router.patch("/{pid}")
def update_palette(
    pid: int, payload: PaletteIn, sess: Session = Depends(get_session)
) -> PaletteOut:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    if p.builtin:
        raise HTTPException(400, "builtin palettes are read-only; clone to edit")
    _persist_payload(p, payload)
    sess.add(p)
    sess.commit()
    sess.refresh(p)
    return _to_out(p)


@router.delete("/{pid}", status_code=204, response_model=None)
def delete_palette(pid: int, sess: Session = Depends(get_session)) -> None:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    if p.builtin:
        raise HTTPException(400, "builtin palettes cannot be deleted")
    sess.delete(p)
    sess.commit()


@router.post("/{pid}/clone", status_code=201)
def clone_palette(pid: int, sess: Session = Depends(get_session)) -> PaletteOut:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    clone = Palette(
        name=f"{p.name} (copy)",
        colors=list(p.colors or []),
        entries=[dict(e) for e in (p.entries or [])],
        builtin=False,
    )
    sess.add(clone)
    sess.commit()
    sess.refresh(clone)
    return _to_out(clone)


def _pick_entries(
    entries: list[PaletteEntry], n: int, mode: str
) -> list[PaletteEntry]:
    """Return ``n`` palette entries drawn from ``entries`` according to mode."""
    if n <= 0 or not entries:
        return []
    if mode == "random":
        return [random.choice(entries) for _ in range(n)]
    if mode == "gradient":
        if len(entries) == 1 or n == 1:
            return [entries[0]] * n
        picks: list[PaletteEntry] = []
        for i in range(n):
            t = i / (n - 1)
            pos = t * (len(entries) - 1)
            lo = int(pos)
            hi = min(lo + 1, len(entries) - 1)
            frac = pos - lo
            a = entries[lo]
            b = entries[hi]

            def _blend(x: Optional[int], y: Optional[int]) -> Optional[int]:
                if x is None and y is None:
                    return None
                xv = 0 if x is None else int(x)
                yv = 0 if y is None else int(y)
                return max(0, min(255, int(round(xv + (yv - xv) * frac))))

            picks.append(
                PaletteEntry(
                    r=_blend(a.r, b.r) or 0,
                    g=_blend(a.g, b.g) or 0,
                    b=_blend(a.b, b.b) or 0,
                    w=_blend(a.w, b.w),
                    a=_blend(a.a, b.a),
                    uv=_blend(a.uv, b.uv),
                )
            )
        return picks
    # cycle (default)
    return [entries[i % len(entries)] for i in range(n)]


def _policy_for(mode: LightModelMode | None) -> dict:
    """Return the mode's color_policy dict, or {} when unset."""
    if mode is None:
        return {}
    if isinstance(mode.color_policy, dict):
        return dict(mode.color_policy)
    return {}


def _apply_entry_flat(
    light: Light, entry: PaletteEntry, policy: dict | None = None
) -> None:
    """Write a single palette entry onto a fixture's flat color state.

    Honors the mode's W/A/UV ``color_policy``:
      * ``mix`` (default) - if the entry has the aux value, it wins; else
        derive from RGB as before (W = min(R,G,B), A = min(R,G)//2, UV = 0
        unless an explicit value was supplied).
      * ``direct`` - only overwrite the aux fader when the entry supplies
        an explicit value; otherwise leave the user's fader alone."""
    policy = policy or {}
    light.r = int(entry.r)
    light.g = int(entry.g)
    light.b = int(entry.b)

    def _aux(role: str, explicit: Optional[int], derived: int) -> None:
        if explicit is not None:
            setattr(light, role, int(explicit))
            return
        if policy.get(role) == "direct":
            return
        setattr(light, role, derived)

    _aux("w", entry.w, min(entry.r, entry.g, entry.b))
    _aux("a", entry.a, min(entry.r, entry.g) // 2)
    # Palette apply now also touches UV when the entry supplies one (fixes
    # the historical gap where UV was never written by palette paint).
    if entry.uv is not None:
        light.uv = int(entry.uv)
    elif policy.get("uv") != "direct":
        # "mix" policy with no explicit UV value: zero it out so the
        # resulting look is deterministic. The renderer's default was
        # already 0 when state omitted UV; making that explicit here
        # keeps the base-state dict consistent for subsequent renders.
        light.uv = 0

    light.on = True
    light.zone_state = {}


def _apply_entry_zone(
    zone_state_map: dict,
    zone_id: str,
    entry: PaletteEntry,
    policy: dict | None = None,
) -> None:
    """Write a palette entry onto a compound fixture's zone dict."""
    policy = policy or {}
    zs = dict(zone_state_map.get(zone_id) or {})
    zs["r"] = int(entry.r)
    zs["g"] = int(entry.g)
    zs["b"] = int(entry.b)

    def _aux(role: str, explicit: Optional[int], derived: int) -> None:
        if explicit is not None:
            zs[role] = int(explicit)
            return
        if policy.get(role) == "direct":
            # Leave whatever the user had (including absence) alone.
            return
        zs[role] = derived

    _aux("w", entry.w, min(entry.r, entry.g, entry.b))
    _aux("a", entry.a, min(entry.r, entry.g) // 2)
    if entry.uv is not None:
        zs["uv"] = int(entry.uv)
    elif policy.get("uv") != "direct":
        # "mix" UV with no explicit value means zero. Matches the
        # renderer's ``_resolve_aux`` fallback.
        zs["uv"] = 0
    zs["on"] = True
    zone_state_map[zone_id] = zs


# ----- legacy hex-only entry points kept for tests + compatibility ---------
def _paint_light_flat(
    light: Light, hex_color: str, policy: dict | None = None
) -> None:
    r, g, b = _hex_to_rgb(hex_color)
    _apply_entry_flat(light, PaletteEntry(r=r, g=g, b=b), policy)


def _paint_zone(
    zone_state_map: dict,
    zone_id: str,
    hex_color: str,
    policy: dict | None = None,
) -> None:
    r, g, b = _hex_to_rgb(hex_color)
    _apply_entry_zone(zone_state_map, zone_id, PaletteEntry(r=r, g=g, b=b), policy)


def _zone_ids_for_light(
    light: Light, mode_by_id: dict[int, LightModelMode]
) -> list[str]:
    """Return the ordered list of zone ids for this light's mode, or [] for
    flat fixtures. Zones are ordered by (row, col) when those are available,
    otherwise by declaration order."""
    mode = mode_by_id.get(light.mode_id) if light.mode_id else None
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


@router.post("/{pid}/apply")
def apply_palette(
    pid: int, req: ApplyPaletteRequest, sess: Session = Depends(get_session)
) -> dict:
    p = sess.get(Palette, pid)
    if p is None:
        raise HTTPException(404, "palette not found")
    if not req.light_ids:
        return {"updated": 0}

    entries = _entries_for_palette(p)
    if not entries:
        raise HTTPException(400, "palette has no colors")

    lights = list(sess.exec(select(Light).where(Light.id.in_(req.light_ids))).all())
    order = {lid: i for i, lid in enumerate(req.light_ids)}
    lights.sort(key=lambda l: order.get(l.id, 0))
    if not lights:
        return {"updated": 0}

    # Resolve the modes referenced by these lights so we know their zones.
    mode_ids = {l.mode_id for l in lights if l.mode_id is not None}
    mode_by_id: dict[int, LightModelMode] = {}
    if mode_ids:
        rows = sess.exec(
            select(LightModelMode).where(LightModelMode.id.in_(mode_ids))
        ).all()
        mode_by_id = {m.id: m for m in rows}

    def _policy(light: Light) -> dict:
        return _policy_for(mode_by_id.get(light.mode_id) if light.mode_id else None)

    if req.spread == "across_fixture":
        for light in lights:
            zone_ids = _zone_ids_for_light(light, mode_by_id)
            policy = _policy(light)
            if not zone_ids:
                picks = _pick_entries(entries, 1, req.mode)
                _apply_entry_flat(light, picks[0], policy)
            else:
                picks = _pick_entries(entries, len(zone_ids), req.mode)
                zs_map = dict(light.zone_state or {})
                for zid, entry in zip(zone_ids, picks):
                    _apply_entry_zone(zs_map, zid, entry, policy)
                light.zone_state = zs_map
                light.on = True
            sess.add(light)

    elif req.spread == "across_zones":
        pairs: list[tuple[Light, str | None]] = []
        for light in lights:
            zone_ids = _zone_ids_for_light(light, mode_by_id)
            if not zone_ids:
                pairs.append((light, None))
            else:
                for zid in zone_ids:
                    pairs.append((light, zid))
        picks = _pick_entries(entries, len(pairs), req.mode)
        mutable_maps: dict[int, dict] = {l.id: {} for l in lights}
        for (light, zid), entry in zip(pairs, picks):
            policy = _policy(light)
            if zid is None:
                _apply_entry_flat(light, entry, policy)
                mutable_maps[light.id] = {}
            else:
                _apply_entry_zone(mutable_maps[light.id], zid, entry, policy)
                light.on = True
        for light in lights:
            if mutable_maps[light.id]:
                light.zone_state = mutable_maps[light.id]
            sess.add(light)

    else:  # across_lights (default)
        picks = _pick_entries(entries, len(lights), req.mode)
        for light, entry in zip(lights, picks):
            _apply_entry_flat(light, entry, _policy(light))
            sess.add(light)

    sess.commit()
    for light in lights:
        sess.refresh(light)
        extras = dict(light.extra_colors or {})
        manager.set_light_state(
            light.id,
            {
                "r": light.r,
                "g": light.g,
                "b": light.b,
                "w": light.w,
                "a": light.a,
                "uv": light.uv,
                "w2": extras.get("w2"),
                "w3": extras.get("w3"),
                "a2": extras.get("a2"),
                "uv2": extras.get("uv2"),
                "extra_colors": extras,
                "dimmer": light.dimmer,
                "on": light.on,
                "zone_state": dict(light.zone_state or {}),
                "motion_state": dict(light.motion_state or {}),
            },
        )
    if lights:
        ctrl_ids = {int(l.controller_id) for l in lights if l.id is not None}
        ctrl_id = next(iter(ctrl_ids)) if len(ctrl_ids) == 1 else None
        rgb: Optional[tuple[int, int, int]] = None
        if entries:
            first = entries[0]
            rgb = (int(first.r), int(first.g), int(first.b))
        base_state_log.record(
            "palette",
            title=f"Palette: {p.name}",
            light_ids=[l.id for l in lights if l.id is not None],
            controller_id=ctrl_id,
            rgb=rgb,
        )
    return {"updated": len(lights)}


# ---------------------------------------------------------------------------
# Claude one-shot palette generation
# ---------------------------------------------------------------------------
_GEN_TOOL_NAME = "propose_palette"

_GEN_SYSTEM_PROMPT = (
    "You are an expert lighting designer who creates harmonious DMX color "
    "palettes. The user describes a mood, theme, or reference; you respond "
    "with a palette of 3-8 colors via the propose_palette tool.\n\n"
    "Rules:\n"
    "- Give each color an R/G/B triple (0-255).\n"
    "- Only include w/a/uv values when the palette intentionally wants to "
    "drive the white, amber, or UV channels independently from RGB. For "
    "most pop/rock color palettes, leave them undefined so the fixture "
    "policy decides.\n"
    "- Prefer palettes that work well both as gradients and as cycles.\n"
    "- Keep the name short (2-4 words) and evocative.\n"
)


def _build_palette_tool_schema() -> dict[str, Any]:
    entry_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "r": {"type": "integer", "minimum": 0, "maximum": 255},
            "g": {"type": "integer", "minimum": 0, "maximum": 255},
            "b": {"type": "integer", "minimum": 0, "maximum": 255},
            "w": {"type": "integer", "minimum": 0, "maximum": 255},
            "a": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "description": "Amber channel (0-255).",
            },
            "uv": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "description": "UV channel (0-255). Sometimes labelled 'V'.",
            },
        },
        "required": ["r", "g", "b"],
    }
    return {
        "name": _GEN_TOOL_NAME,
        "description": (
            "Return a named palette of 3-8 colors. Each color may optionally "
            "include explicit W/A/UV values for fixtures that expose those "
            "channels directly."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": (
                        "One-sentence explanation of the palette the user "
                        "will see above the swatches."
                    ),
                },
                "entries": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 16,
                    "items": entry_schema,
                },
            },
            "required": ["name", "entries"],
        },
    }


@router.post("/generate")
def generate_palette(payload: PaletteGenerateRequest) -> PaletteGenerateResponse:
    """Ask Claude for a palette draft. Does not persist; the UI decides."""
    if not _config.ANTHROPIC_API_KEY:
        raise HTTPException(503, "Claude is not configured on this server")
    try:
        import anthropic
    except ImportError as exc:
        raise HTTPException(
            503, "anthropic package is not installed on the server"
        ) from exc

    client = anthropic.Anthropic(api_key=_config.ANTHROPIC_API_KEY)
    user_text = payload.prompt
    bits: list[str] = [user_text]
    if payload.num_colors:
        bits.append(f"Use exactly {payload.num_colors} colors.")
    if payload.include_aux:
        bits.append(
            "When appropriate, include w/a/uv values for a richer look on "
            "fixtures that expose those channels directly."
        )
    content_text = "\n\n".join(bits)

    try:
        message = client.messages.create(
            model=_config.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=_GEN_SYSTEM_PROMPT,
            tools=[_build_palette_tool_schema()],
            tool_choice={"type": "tool", "name": _GEN_TOOL_NAME},
            messages=[{"role": "user", "content": content_text}],
        )
    except anthropic.APIStatusError as exc:
        log.warning("Anthropic API error: %s", exc)
        raise HTTPException(502, f"Claude API error: {exc.message}") from exc
    except Exception as exc:  # pragma: no cover
        log.exception("palette generation failed")
        raise HTTPException(502, f"Claude request failed: {exc}") from exc

    tool_input: Optional[dict] = None
    for block in getattr(message, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "tool_use" and getattr(block, "name", None) == _GEN_TOOL_NAME:
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                tool_input = inp
                break
    if tool_input is None:
        raise HTTPException(502, "Claude did not return a palette.")

    raw_entries = tool_input.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise HTTPException(502, "Claude returned no palette entries.")
    entries: list[PaletteEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        try:
            entries.append(PaletteEntry(**item))
        except Exception:
            continue
    if not entries:
        raise HTTPException(502, "Claude returned invalid palette entries.")

    name = str(tool_input.get("name") or "").strip()[:128] or "Generated palette"
    summary = tool_input.get("summary")
    return PaletteGenerateResponse(
        name=name,
        entries=entries,
        summary=(summary.strip() if isinstance(summary, str) else None),
    )
