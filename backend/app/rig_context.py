"""Shared rig snapshot used by Claude-backed routers.

The designer and effect-chat endpoints both hand Claude a JSON snapshot
of the current rig (controllers, lights, palettes, active effects) so
the model's suggestions reference real ids. This module centralizes the
snapshot builder so the two routers stay in sync.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from .models import (
    Controller,
    Effect,
    Light,
    LightModel,
    LightModelMode,
    Palette,
)


def _mode_for_light(
    l: Light, modes_by_id: dict[int, LightModelMode]
) -> Optional[LightModelMode]:
    if l.mode_id is None:
        return None
    return modes_by_id.get(l.mode_id)


def zone_ids_for_mode(mode: Optional[LightModelMode]) -> list[str]:
    if mode is None or not isinstance(mode.layout, dict):
        return []
    zones = mode.layout.get("zones") or []
    out: list[str] = []
    for z in zones:
        if isinstance(z, dict):
            zid = z.get("id")
            if isinstance(zid, str) and zid:
                out.append(zid)
    return out


def motion_axes_for_mode(mode: Optional[LightModelMode]) -> list[str]:
    if mode is None or not isinstance(mode.layout, dict):
        return []
    motion = mode.layout.get("motion")
    if not isinstance(motion, dict):
        return []
    return [a for a in ("pan", "tilt", "zoom", "focus") if a in motion]


def _palette_entry_out(item: Any) -> Optional[dict[str, int]]:
    if not isinstance(item, dict):
        return None
    try:
        out = {
            "r": int(item.get("r", 0)),
            "g": int(item.get("g", 0)),
            "b": int(item.get("b", 0)),
        }
    except Exception:
        return None
    for aux in ("w", "a", "uv"):
        if item.get(aux) is not None:
            try:
                out[aux] = int(item[aux])
            except Exception:
                continue
    return out


def build_rig_context(
    sess: Session,
    *,
    include_effects: bool = False,
) -> dict[str, Any]:
    """Snapshot the rig into a JSON-serializable dict for Claude.

    ``include_effects`` adds a top-level ``effects`` list describing every
    saved effect (type, palette, target channels, params, active state).
    The designer leaves this off by default to keep its contract stable;
    the effect-chat router turns it on so Claude can suggest modifications
    to existing presets."""
    controllers = list(
        sess.exec(select(Controller).order_by(Controller.id)).all()
    )
    lights = list(
        sess.exec(
            select(Light).order_by(
                Light.controller_id, Light.position, Light.id
            )
        ).all()
    )
    models = list(sess.exec(select(LightModel)).all())
    modes = list(sess.exec(select(LightModelMode)).all())
    palettes = list(sess.exec(select(Palette).order_by(Palette.name)).all())

    model_by_id = {m.id: m for m in models}
    mode_by_id = {m.id: m for m in modes}

    ctrl_out: list[dict[str, Any]] = []
    for c in controllers:
        entry: dict[str, Any] = {
            "id": c.id,
            "name": c.name,
            "ip": c.ip,
            "universe": f"{c.net}:{c.subnet}:{c.universe}",
            "enabled": bool(c.enabled),
        }
        if c.notes:
            entry["notes"] = c.notes
        ctrl_out.append(entry)

    light_out: list[dict[str, Any]] = []
    for l in lights:
        m = model_by_id.get(l.model_id)
        mode = _mode_for_light(l, mode_by_id)
        entry = {
            "id": l.id,
            "name": l.name,
            "controller_id": l.controller_id,
            "start_address": l.start_address,
            "model": m.name if m else "?",
            "mode": mode.name if mode else "?",
            "channels": (
                list(mode.channels) if mode
                else list(m.channels or []) if m
                else []
            ),
            "current": {
                "r": int(l.r or 0),
                "g": int(l.g or 0),
                "b": int(l.b or 0),
                "dimmer": int(l.dimmer if l.dimmer is not None else 255),
                "on": bool(l.on),
            },
        }
        zones = zone_ids_for_mode(mode)
        if zones:
            entry["zones"] = zones
        axes = motion_axes_for_mode(mode)
        if axes:
            entry["motion_axes"] = axes
        if l.notes:
            entry["notes"] = l.notes
        light_out.append(entry)

    palette_out: list[dict[str, Any]] = []
    for p in palettes:
        entries: list[dict[str, int]] = []
        for raw in (p.entries or []):
            ent = _palette_entry_out(raw)
            if ent is not None:
                entries.append(ent)
        palette_out.append(
            {
                "id": p.id,
                "name": p.name,
                "colors": list(p.colors or []),
                "entries": entries,
            }
        )

    out: dict[str, Any] = {
        "controllers": ctrl_out,
        "lights": light_out,
        "palettes": palette_out,
    }

    if include_effects:
        effects = list(
            sess.exec(
                select(Effect).order_by(Effect.builtin.desc(), Effect.name)
            ).all()
        )
        out["effects"] = [
            {
                "id": e.id,
                "name": e.name,
                "effect_type": e.effect_type,
                "palette_id": e.palette_id,
                "spread": e.spread,
                "params": dict(e.params or {}),
                "target_channels": list(e.target_channels or ["rgb"]),
                "is_active": bool(e.is_active),
                "builtin": bool(e.builtin),
            }
            for e in effects
        ]

    return out
