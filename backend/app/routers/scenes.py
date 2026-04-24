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

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager
from ..auth import AuthDep
from ..db import get_session
from ..engine import engine
from ..models import Controller, Light, Scene
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
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")

    by_id = {int(entry["light_id"]): entry for entry in (row.lights or [])}
    if not by_id:
        return {"ok": True, "applied": 0}

    # Stop any running effect that overlaps this scene so the restored
    # static colours actually stick.
    engine.stop_affecting(set(by_id.keys()))

    lights = sess.exec(select(Light).where(Light.id.in_(list(by_id.keys())))).all()
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
    return {"ok": True, "applied": applied}


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
    return {"ok": True, "applied": len(lights)}
