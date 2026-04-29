"""Scene CRUD + apply endpoints.

A Scene is a snapshot of light state (RGB/WAUV/dimmer/on + per-zone +
per-axis motion) that can be saved and later re-applied in a single
click. Each scene belongs to a primary ``controller_id`` used for the
per-controller dropdown on the Lights page; scenes may also span
multiple controllers via ``cross_controller=True``.

Blackout is exposed as a virtual builtin in :func:`list_scenes` so the
UI can treat it uniformly in the dropdown without having to persist a
row."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

log = logging.getLogger(__name__)

from ..artnet import manager
from ..auth import AuthDep
from ..base_state_log import log as base_state_log
from ..db import get_session
from ..engine import build_spec_from_layer, engine
from ..lua import ScriptError
from ..models import Controller, Effect, EffectLayer, Light, Palette, Scene
from ..schemas import (
    SceneCreate,
    SceneLightState,
    SceneOut,
    SceneUpdate,
)
from ._capture import (
    apply_state_to_light,
    capture_lights,
    push_light,
    select_scene_lights,
)

router = APIRouter(prefix="/api/scenes", tags=["scenes"], dependencies=[AuthDep])


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------
def _row_to_out(row: Scene) -> SceneOut:
    return SceneOut(
        id=row.id,
        name=row.name,
        controller_id=row.controller_id,
        cross_controller=bool(row.cross_controller),
        lights=[SceneLightState(**l) for l in (row.lights or [])],
        layers=list(row.layers or []),
        builtin=False,
    )


def _virtual_blackout(controller: Controller) -> SceneOut:
    """A placeholder "Blackout" scene for a controller.

    Keeps the dropdown uniform: the frontend can render every entry with
    the same component and simply branch on ``builtin`` + ``id is None``
    when it comes time to apply."""
    return SceneOut(
        id=None,
        name="Blackout",
        controller_id=controller.id,
        cross_controller=False,
        lights=[],
        layers=[],
        builtin=True,
    )


# ---------------------------------------------------------------------------
# Capture helpers
# ---------------------------------------------------------------------------
def _capture(
    sess: Session,
    *,
    controller_id: int,
    cross_controller: bool,
    light_ids: Optional[list[int]],
    from_rendered: bool,
) -> list[dict]:
    lights = select_scene_lights(
        sess,
        controller_id=controller_id,
        cross_controller=cross_controller,
        light_ids=light_ids,
    )
    return capture_lights(lights, from_rendered=from_rendered)


# ---------------------------------------------------------------------------
# CRUD + listing
# ---------------------------------------------------------------------------
@router.get("")
def list_scenes(
    controller_id: Optional[int] = None,
    sess: Session = Depends(get_session),
) -> list[SceneOut]:
    """List scenes. When ``controller_id`` is set, only scenes for that
    controller (including cross-controller scenes that cover it) are
    returned, with a virtual Blackout entry prepended."""
    controllers = {
        c.id: c for c in sess.exec(select(Controller).order_by(Controller.id)).all()
    }

    if controller_id is not None:
        ctrl = controllers.get(controller_id)
        if ctrl is None:
            raise HTTPException(404, "controller not found")
        out: list[SceneOut] = [_virtual_blackout(ctrl)]
        rows = sess.exec(
            select(Scene)
            .where(Scene.controller_id == controller_id)
            .order_by(Scene.name)
        ).all()
        for row in rows:
            out.append(_row_to_out(row))
        return out

    out: list[SceneOut] = []
    # One virtual blackout per known controller so a global listing can
    # still surface it (e.g. for the /scenes management page).
    for c in controllers.values():
        out.append(_virtual_blackout(c))
    rows = sess.exec(
        select(Scene).order_by(Scene.controller_id, Scene.name)
    ).all()
    for row in rows:
        out.append(_row_to_out(row))
    return out


@router.post("", status_code=201)
def create_scene(
    payload: SceneCreate, sess: Session = Depends(get_session)
) -> SceneOut:
    ctrl = sess.get(Controller, payload.controller_id)
    if ctrl is None:
        raise HTTPException(404, "controller not found")
    captured = _capture(
        sess,
        controller_id=payload.controller_id,
        cross_controller=payload.cross_controller,
        light_ids=payload.light_ids,
        from_rendered=payload.from_rendered,
    )
    if not captured:
        raise HTTPException(
            400,
            "nothing to capture: no matching lights",
        )
    row = Scene(
        name=payload.name,
        controller_id=payload.controller_id,
        cross_controller=bool(payload.cross_controller),
        lights=captured,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _row_to_out(row)


@router.patch("/{sid}")
def update_scene(
    sid: int, payload: SceneUpdate, sess: Session = Depends(get_session)
) -> SceneOut:
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")
    if payload.name is not None:
        row.name = payload.name
    if payload.controller_id is not None:
        ctrl = sess.get(Controller, payload.controller_id)
        if ctrl is None:
            raise HTTPException(404, "controller not found")
        row.controller_id = payload.controller_id
    if payload.cross_controller is not None:
        row.cross_controller = bool(payload.cross_controller)
    if payload.recapture:
        row.lights = _capture(
            sess,
            controller_id=row.controller_id,
            cross_controller=row.cross_controller,
            light_ids=payload.light_ids,
            from_rendered=payload.from_rendered,
        )
    if payload.layers is not None:
        # Replace the saved layer stack wholesale; entries are stored as
        # JSON dicts and validated lazily at apply time.
        row.layers = [
            dict(entry) for entry in payload.layers if isinstance(entry, dict)
        ]
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _row_to_out(row)


@router.delete("/{sid}", status_code=204, response_model=None)
def delete_scene(sid: int, sess: Session = Depends(get_session)) -> None:
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")
    sess.delete(row)
    sess.commit()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
@router.post("/{sid}/apply")
def apply_scene(sid: int, sess: Session = Depends(get_session)) -> dict:
    """Atomically apply a scene: clear running layers, push the saved
    base snapshot onto every covered light, then start each saved layer
    on top in the order it was authored.

    Old single-snapshot scenes (no ``layers``) behave like before."""
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")

    by_id = {int(entry["light_id"]): entry for entry in (row.lights or [])}
    saved_layers = list(row.layers or [])
    affected = set(by_id.keys())

    # Atomic swap: stop everything that touches these lights so the
    # snapshot lands clean; if any saved layer fails to compile we keep
    # the static base (no half-applied stack).
    engine.stop_affecting(affected, immediate=True)
    if not by_id and not saved_layers:
        return {"ok": True, "applied": 0, "layers": 0}

    lights = (
        sess.exec(select(Light).where(Light.id.in_(list(affected)))).all()
        if affected
        else []
    )
    applied = 0
    for light in lights:
        entry = by_id.get(light.id)
        if entry is None:
            continue
        apply_state_to_light(light, entry)
        sess.add(light)
        applied += 1
    sess.commit()
    for light in lights:
        push_light(light)

    # Push every saved layer.
    started = 0
    if saved_layers:
        z = 100
        for spec in saved_layers:
            if not isinstance(spec, dict):
                continue
            try:
                effect_id = int(spec.get("effect_id"))
            except (TypeError, ValueError):
                continue
            effect = sess.get(Effect, effect_id)
            if effect is None:
                log.warning("scene %s references missing effect %s", sid, effect_id)
                continue
            layer = EffectLayer(
                effect_id=effect_id,
                z_index=int(spec.get("z_index") or z),
                blend_mode=str(spec.get("blend_mode") or "normal"),
                opacity=float(spec.get("opacity") or 1.0),
                intensity=float(spec.get("intensity") or 1.0),
                fade_in_s=float(spec.get("fade_in_s") or 0.25),
                fade_out_s=float(spec.get("fade_out_s") or 0.25),
                target_channels=list(
                    spec.get("target_channels")
                    or effect.target_channels
                    or ["rgb"]
                ),
                spread=str(spec.get("spread") or effect.spread or "across_lights"),
                light_ids=list(spec.get("light_ids") or []),
                targets=list(spec.get("targets") or []),
                palette_id=spec.get("palette_id") or effect.palette_id,
                params_override=dict(spec.get("params_override") or {}),
                mask_light_ids=list(spec.get("mask_light_ids") or []),
                is_active=True,
            )
            sess.add(layer)
            sess.flush()
            palette = None
            pid = layer.palette_id
            if pid is not None:
                palette = sess.get(Palette, pid)
            try:
                engine.play(build_spec_from_layer(layer, effect, palette))
                started += 1
                z = layer.z_index + 100
            except ScriptError:
                sess.delete(layer)
        sess.commit()
    if applied or saved_layers:
        base_state_log.record(
            "scene",
            title=f"Scene: {row.name}",
            light_ids=list(affected),
            controller_id=int(row.controller_id) if row.controller_id else None,
        )
    return {"ok": True, "applied": applied, "layers": started}


@router.post("/blackout/{cid}/apply")
def apply_blackout(cid: int, sess: Session = Depends(get_session)) -> dict:
    """Apply the virtual Blackout scene for one controller.

    Mirrors ``POST /api/controllers/{cid}/blackout`` (kept for backwards
    compatibility) but lives under /api/scenes so the Lights-page
    dropdown can route every selection through a single endpoint shape."""
    ctrl = sess.get(Controller, cid)
    if ctrl is None:
        raise HTTPException(404, "controller not found")
    lights = sess.exec(select(Light).where(Light.controller_id == cid)).all()
    affected = {l.id for l in lights if l.id is not None}
    engine.stop_affecting(affected)
    for light in lights:
        light.r = light.g = light.b = light.w = light.a = light.uv = 0
        light.on = False
        light.zone_state = {}
        sess.add(light)
    sess.commit()
    manager.blackout(cid)
    base_state_log.record(
        "blackout",
        title=f"Blackout · {ctrl.name}",
        light_ids=list(affected),
        controller_id=cid,
        rgb=(0, 0, 0),
    )
    return {"ok": True, "applied": len(lights)}
