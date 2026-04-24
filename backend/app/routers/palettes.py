import random

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager
from ..auth import AuthDep
from ..db import get_session
from ..models import Light, LightModelMode, Palette
from ..schemas import ApplyPaletteRequest, PaletteIn, PaletteOut

router = APIRouter(prefix="/api/palettes", tags=["palettes"], dependencies=[AuthDep])


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return (r, g, b)


def _to_out(p: Palette) -> PaletteOut:
    return PaletteOut(id=p.id, name=p.name, colors=list(p.colors), builtin=p.builtin)


@router.get("")
def list_palettes(sess: Session = Depends(get_session)) -> list[PaletteOut]:
    rows = sess.exec(select(Palette).order_by(Palette.builtin.desc(), Palette.name)).all()
    return [_to_out(p) for p in rows]


@router.post("", status_code=201)
def create_palette(payload: PaletteIn, sess: Session = Depends(get_session)) -> PaletteOut:
    p = Palette(name=payload.name, colors=payload.colors, builtin=False)
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
    p.name = payload.name
    p.colors = payload.colors
    sess.add(p)
    sess.commit()
    sess.refresh(p)
    return _to_out(p)


@router.delete("/{pid}", status_code=204)
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
    clone = Palette(name=f"{p.name} (copy)", colors=list(p.colors), builtin=False)
    sess.add(clone)
    sess.commit()
    sess.refresh(clone)
    return _to_out(clone)


def _pick_colors(colors: list[str], n: int, mode: str) -> list[str]:
    """Return a list of n hex colors drawn from the palette according to mode."""
    if n <= 0:
        return []
    if mode == "random":
        return [random.choice(colors) for _ in range(n)]
    if mode == "gradient":
        if len(colors) == 1 or n == 1:
            return [colors[0]] * n
        picks: list[str] = []
        for i in range(n):
            t = i / (n - 1)
            pos = t * (len(colors) - 1)
            lo = int(pos)
            hi = min(lo + 1, len(colors) - 1)
            frac = pos - lo
            r1, g1, b1 = _hex_to_rgb(colors[lo])
            r2, g2, b2 = _hex_to_rgb(colors[hi])
            r = int(round(r1 + (r2 - r1) * frac))
            g = int(round(g1 + (g2 - g1) * frac))
            b = int(round(b1 + (b2 - b1) * frac))
            picks.append(f"#{r:02X}{g:02X}{b:02X}")
        return picks
    # cycle (default)
    return [colors[i % len(colors)] for i in range(n)]


def _policy_for(mode: LightModelMode | None) -> dict:
    """Return the mode's color_policy dict, or {} when unset."""
    if mode is None:
        return {}
    if isinstance(mode.color_policy, dict):
        return dict(mode.color_policy)
    return {}


def _paint_light_flat(
    light: Light, hex_color: str, policy: dict | None = None
) -> None:
    r, g, b = _hex_to_rgb(hex_color)
    policy = policy or {}
    light.r = r
    light.g = g
    light.b = b
    # Preserve user-owned "direct" W/A values — palette paint only touches
    # channels that still behave as RGB-derived mixes.
    if policy.get("w") != "direct":
        light.w = min(r, g, b)
    if policy.get("a") != "direct":
        light.a = min(r, g) // 2
    light.on = True
    light.zone_state = {}


def _paint_zone(
    zone_state_map: dict,
    zone_id: str,
    hex_color: str,
    policy: dict | None = None,
) -> None:
    r, g, b = _hex_to_rgb(hex_color)
    policy = policy or {}
    zs = dict(zone_state_map.get(zone_id) or {})
    zs["r"] = r
    zs["g"] = g
    zs["b"] = b
    if policy.get("w") != "direct":
        zs["w"] = min(r, g, b)
    if policy.get("a") != "direct":
        zs["a"] = min(r, g) // 2
    zs["on"] = True
    zone_state_map[zone_id] = zs


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
    if not p.colors:
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

    colors = p.colors

    def _policy(light: Light) -> dict:
        return _policy_for(mode_by_id.get(light.mode_id) if light.mode_id else None)

    if req.spread == "across_fixture":
        # Each fixture gets the palette rolled across its own zones.
        for light in lights:
            zone_ids = _zone_ids_for_light(light, mode_by_id)
            policy = _policy(light)
            if not zone_ids:
                # Single-zone fixture: behave like across_lights with one
                # fixture — pick the first color.
                picks = _pick_colors(colors, 1, req.mode)
                _paint_light_flat(light, picks[0], policy)
            else:
                picks = _pick_colors(colors, len(zone_ids), req.mode)
                zs_map = dict(light.zone_state or {})
                for zid, hex_color in zip(zone_ids, picks):
                    _paint_zone(zs_map, zid, hex_color, policy)
                light.zone_state = zs_map
                light.on = True
            sess.add(light)

    elif req.spread == "across_zones":
        # Flatten every zone across the whole selection into one long list
        # and spread the palette across it end-to-end.
        pairs: list[tuple[Light, str | None]] = []
        for light in lights:
            zone_ids = _zone_ids_for_light(light, mode_by_id)
            if not zone_ids:
                pairs.append((light, None))
            else:
                for zid in zone_ids:
                    pairs.append((light, zid))
        picks = _pick_colors(colors, len(pairs), req.mode)
        # Reset zone_state for all lights first so we don't carry stale
        # per-zone overrides from a previous spread.
        mutable_maps: dict[int, dict] = {l.id: {} for l in lights}
        for (light, zid), hex_color in zip(pairs, picks):
            policy = _policy(light)
            if zid is None:
                _paint_light_flat(light, hex_color, policy)
                mutable_maps[light.id] = {}
            else:
                _paint_zone(mutable_maps[light.id], zid, hex_color, policy)
                light.on = True
        for light in lights:
            if mutable_maps[light.id]:
                light.zone_state = mutable_maps[light.id]
            sess.add(light)

    else:  # across_lights (default, preserves today's behavior)
        picks = _pick_colors(colors, len(lights), req.mode)
        for light, hex_color in zip(lights, picks):
            _paint_light_flat(light, hex_color, _policy(light))
            sess.add(light)

    sess.commit()
    for light in lights:
        sess.refresh(light)
        manager.set_light_state(
            light.id,
            {
                "r": light.r,
                "g": light.g,
                "b": light.b,
                "w": light.w,
                "a": light.a,
                "uv": light.uv,
                "dimmer": light.dimmer,
                "on": light.on,
                "zone_state": dict(light.zone_state or {}),
                "motion_state": dict(light.motion_state or {}),
            },
        )
    return {"updated": len(lights)}
