"""Rig-wide State CRUD + apply endpoints.

A State is a rig-wide snapshot covering every :class:`Light` on every
:class:`Controller`. It mirrors :mod:`.scenes` but has no primary
``controller_id``: every state always applies to the whole rig.

Blackout-all is exposed as a virtual builtin in :func:`list_states` so
the UI can treat it uniformly in the dropdown without having to persist
a row."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager
from ..auth import AuthDep
from ..db import get_session
from ..engine import engine
from ..models import Controller, Light, State
from ..schemas import (
    SceneLightState,
    StateCreate,
    StateOut,
    StateUpdate,
)
from ._capture import (
    apply_state_to_light,
    capture_lights,
    push_light,
    select_all_lights,
)

router = APIRouter(prefix="/api/states", tags=["states"], dependencies=[AuthDep])


VIRTUAL_BLACKOUT_NAME = "Blackout all"


def _row_to_out(row: State) -> StateOut:
    return StateOut(
        id=row.id,
        name=row.name,
        lights=[SceneLightState(**l) for l in (row.lights or [])],
        builtin=False,
    )


def _virtual_blackout() -> StateOut:
    """A placeholder rig-wide blackout state.

    Keeps the dropdown uniform: the frontend can render every entry with
    the same component and simply branch on ``builtin`` + ``id is None``
    when it comes time to apply."""
    return StateOut(
        id=None,
        name=VIRTUAL_BLACKOUT_NAME,
        lights=[],
        builtin=True,
    )


def _capture(sess: Session, *, from_rendered: bool) -> list[dict]:
    lights = select_all_lights(sess)
    return capture_lights(lights, from_rendered=from_rendered)


# ---------------------------------------------------------------------------
# CRUD + listing
# ---------------------------------------------------------------------------
@router.get("")
def list_states(sess: Session = Depends(get_session)) -> list[StateOut]:
    """List persisted rig-wide states with a virtual Blackout first."""
    out: list[StateOut] = [_virtual_blackout()]
    rows = sess.exec(select(State).order_by(State.name)).all()
    for row in rows:
        out.append(_row_to_out(row))
    return out


@router.post("", status_code=201)
def create_state(
    payload: StateCreate, sess: Session = Depends(get_session)
) -> StateOut:
    captured = _capture(sess, from_rendered=payload.from_rendered)
    if not captured:
        raise HTTPException(
            400,
            "nothing to capture: no lights configured",
        )
    row = State(name=payload.name, lights=captured)
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _row_to_out(row)


@router.patch("/{sid}")
def update_state(
    sid: int, payload: StateUpdate, sess: Session = Depends(get_session)
) -> StateOut:
    row = sess.get(State, sid)
    if row is None:
        raise HTTPException(404, "state not found")
    if payload.name is not None:
        row.name = payload.name
    if payload.recapture:
        row.lights = _capture(sess, from_rendered=payload.from_rendered)
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _row_to_out(row)


@router.delete("/{sid}", status_code=204, response_model=None)
def delete_state(sid: int, sess: Session = Depends(get_session)) -> None:
    row = sess.get(State, sid)
    if row is None:
        raise HTTPException(404, "state not found")
    sess.delete(row)
    sess.commit()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
# NOTE: declare the literal ``/blackout/apply`` route BEFORE the parameterized
# ``/{sid}/apply`` below. FastAPI matches routes in declaration order, so if
# the parameterized route comes first it tries to parse ``"blackout"`` as an
# int ``sid`` and returns 422 Unprocessable Content.
@router.post("/blackout/apply")
def apply_blackout_all(sess: Session = Depends(get_session)) -> dict:
    """Rig-wide blackout: zero every light on every controller."""
    lights = sess.exec(select(Light)).all()
    affected = {l.id for l in lights if l.id is not None}
    engine.stop_affecting(affected)
    for light in lights:
        light.r = light.g = light.b = light.w = light.a = light.uv = 0
        light.on = False
        light.zone_state = {}
        sess.add(light)
    sess.commit()
    controllers = sess.exec(select(Controller)).all()
    for ctrl in controllers:
        if ctrl.id is not None:
            manager.blackout(ctrl.id)
    return {"ok": True, "applied": len(lights)}


@router.post("/{sid}/apply")
def apply_state(sid: int, sess: Session = Depends(get_session)) -> dict:
    row = sess.get(State, sid)
    if row is None:
        raise HTTPException(404, "state not found")

    by_id = {int(entry["light_id"]): entry for entry in (row.lights or [])}
    if not by_id:
        return {"ok": True, "applied": 0}

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
