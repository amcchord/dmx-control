"""Shared capture/apply helpers for Scene and State snapshots.

Scenes and rig-wide States both capture and restore a set of per-light
state dicts with an identical shape. Keep the helpers in one place so
the two routers stay in sync when fields are added."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from ..artnet import manager
from ..models import Light
from ..schemas import SceneLightState


def light_to_state(light: Light) -> SceneLightState:
    return SceneLightState(
        light_id=light.id,
        r=int(light.r or 0),
        g=int(light.g or 0),
        b=int(light.b or 0),
        w=int(light.w or 0),
        a=int(light.a or 0),
        uv=int(light.uv or 0),
        dimmer=int(light.dimmer if light.dimmer is not None else 255),
        on=bool(light.on),
        zone_state=dict(light.zone_state or {}),
        motion_state=dict(light.motion_state or {}),
    )


def state_from_rendered(light: Light, rendered: dict) -> SceneLightState:
    """Merge the live rendered RGB+on with the light's DB w/a/uv/dimmer.

    The rendered snapshot is decoded from the universe buffer, so it only
    includes r/g/b/on (flat + per-zone). We keep the white/amber/uv/dimmer
    fields from the DB since they aren't reconstructable from the wire."""
    info = rendered.get(light.id) or {}
    zs_out: dict[str, dict] = {}
    for zid, z in (info.get("zone_state") or {}).items():
        zs_out[zid] = {
            "r": int(z.get("r", 0)),
            "g": int(z.get("g", 0)),
            "b": int(z.get("b", 0)),
            "on": bool(z.get("on", True)),
        }
    return SceneLightState(
        light_id=light.id,
        r=int(info.get("r", light.r or 0)),
        g=int(info.get("g", light.g or 0)),
        b=int(info.get("b", light.b or 0)),
        w=int(light.w or 0),
        a=int(light.a or 0),
        uv=int(light.uv or 0),
        dimmer=int(light.dimmer if light.dimmer is not None else 255),
        on=bool(info.get("on", light.on)),
        zone_state=zs_out or dict(light.zone_state or {}),
        motion_state=dict(light.motion_state or {}),
    )


def capture_lights(
    lights: list[Light], *, from_rendered: bool
) -> list[dict]:
    """Capture a list of Light rows into serialized state dicts."""
    if from_rendered:
        rendered = manager.snapshot_rendered()
        states = [state_from_rendered(l, rendered) for l in lights]
    else:
        states = [light_to_state(l) for l in lights]
    return [s.model_dump() for s in states]


def select_scene_lights(
    sess: Session,
    *,
    controller_id: int,
    cross_controller: bool,
    light_ids: Optional[list[int]],
) -> list[Light]:
    """Resolve the set of Light rows to capture for a Scene.

    Scene-specific selection: either an explicit subset, or every light
    on one controller, or every light at all (cross-controller)."""
    stmt = select(Light)
    if light_ids is not None and len(light_ids) > 0:
        stmt = stmt.where(Light.id.in_(light_ids))
    elif not cross_controller:
        stmt = stmt.where(Light.controller_id == controller_id)
    return list(sess.exec(stmt).all())


def select_all_lights(sess: Session) -> list[Light]:
    """Every Light in the system, for rig-wide State snapshots."""
    return list(sess.exec(select(Light)).all())


def apply_state_to_light(light: Light, state: dict) -> None:
    light.r = int(state.get("r", 0))
    light.g = int(state.get("g", 0))
    light.b = int(state.get("b", 0))
    light.w = int(state.get("w", 0))
    light.a = int(state.get("a", 0))
    light.uv = int(state.get("uv", 0))
    light.dimmer = int(state.get("dimmer", 255))
    light.on = bool(state.get("on", True))
    light.zone_state = dict(state.get("zone_state") or {})
    light.motion_state = dict(state.get("motion_state") or {})


def push_light(light: Light) -> None:
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
