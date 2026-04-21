from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager, rebuild_manager_sync
from ..auth import AuthDep
from ..db import get_session
from ..models import Controller, Light, LightModel, LightModelMode
from ..schemas import (
    BulkColorRequest,
    BulkTarget,
    ColorRequest,
    LightIn,
    LightOut,
    ReorderLightsRequest,
)

router = APIRouter(prefix="/api/lights", tags=["lights"], dependencies=[AuthDep])


def _to_out(l: Light) -> LightOut:
    return LightOut(
        id=l.id,
        name=l.name,
        controller_id=l.controller_id,
        model_id=l.model_id,
        mode_id=l.mode_id,
        start_address=l.start_address,
        position=l.position,
        r=l.r,
        g=l.g,
        b=l.b,
        w=l.w,
        a=l.a,
        uv=l.uv,
        dimmer=l.dimmer,
        on=l.on,
        zone_state=dict(l.zone_state or {}),
        motion_state=dict(l.motion_state or {}),
    )


def _apply_zone_color(zs: dict, req: ColorRequest) -> dict:
    """Update one zone's color dict in place and return it."""
    zs["r"] = req.r
    zs["g"] = req.g
    zs["b"] = req.b
    if req.w is not None:
        zs["w"] = req.w
    if req.a is not None:
        zs["a"] = req.a
    if req.uv is not None:
        zs["uv"] = req.uv
    if req.dimmer is not None:
        zs["dimmer"] = req.dimmer
    if req.on is not None:
        zs["on"] = req.on
    else:
        zs["on"] = True
    return zs


def _apply_motion(light: Light, req: ColorRequest) -> None:
    if req.motion is None:
        return
    ms = dict(light.motion_state or {})
    for axis in ("pan", "tilt", "zoom", "focus"):
        val = getattr(req.motion, axis)
        if val is not None:
            ms[axis] = float(val)
    light.motion_state = ms


def _apply_color(light: Light, req: ColorRequest) -> None:
    """Apply a color request, respecting req.zone_id when present.

    - If ``zone_id`` is set, only that zone is updated.
    - If ``zone_id`` is absent, the flat fields (whole-fixture fallback) are
      updated, and any per-zone overrides are cleared so every zone picks
      up the new color through the fallback.
    """
    _apply_motion(light, req)
    if req.zone_id:
        zs_map = dict(light.zone_state or {})
        zs = dict(zs_map.get(req.zone_id) or {})
        _apply_zone_color(zs, req)
        zs_map[req.zone_id] = zs
        light.zone_state = zs_map
        # Keep flat fields in sync as a "last-touched" fallback so newly
        # added zones inherit something reasonable.
        return
    # Whole-fixture update: flat fields + wipe per-zone overrides.
    light.r = req.r
    light.g = req.g
    light.b = req.b
    if req.w is not None:
        light.w = req.w
    if req.a is not None:
        light.a = req.a
    if req.uv is not None:
        light.uv = req.uv
    if req.dimmer is not None:
        light.dimmer = req.dimmer
    if req.on is not None:
        light.on = req.on
    else:
        light.on = True
    light.zone_state = {}


def _push(light: Light) -> None:
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


def _resolve_mode(
    sess: Session, model_id: int, mode_id: int | None
) -> LightModelMode:
    """Return the mode row to use for a light.

    If ``mode_id`` is provided, it must belong to the given model. Otherwise
    fall back to the model's default mode (and, failing that, any mode)."""
    if mode_id is not None:
        mode = sess.get(LightModelMode, mode_id)
        if mode is None or mode.model_id != model_id:
            raise HTTPException(400, "mode does not belong to this model")
        return mode
    default = sess.exec(
        select(LightModelMode).where(
            LightModelMode.model_id == model_id,
            LightModelMode.is_default == True,  # noqa: E712
        )
    ).first()
    if default is None:
        default = sess.exec(
            select(LightModelMode).where(LightModelMode.model_id == model_id)
        ).first()
    if default is None:
        raise HTTPException(400, "model has no modes defined")
    return default


def _ensure_refs(
    sess: Session, controller_id: int, model_id: int
) -> tuple[Controller, LightModel]:
    ctrl = sess.get(Controller, controller_id)
    if ctrl is None:
        raise HTTPException(400, "controller does not exist")
    model = sess.get(LightModel, model_id)
    if model is None:
        raise HTTPException(400, "model does not exist")
    return ctrl, model


@router.get("")
def list_lights(sess: Session = Depends(get_session)) -> list[LightOut]:
    rows = sess.exec(
        select(Light).order_by(Light.controller_id, Light.position, Light.id)
    ).all()
    return [_to_out(l) for l in rows]


@router.get("/rendered")
def list_rendered_lights() -> dict:
    """Live snapshot of what the rig is outputting right now.

    Returns ``{light_id: {r, g, b, on, zone_state: {zone_id: {r,g,b,on}}}}``.
    The Dashboard polls this at a high rate while a scene is running so
    the light cards visibly animate alongside the physical fixtures."""
    return manager.snapshot_rendered()


@router.post("", status_code=201)
def create_light(payload: LightIn, sess: Session = Depends(get_session)) -> LightOut:
    ctrl, model = _ensure_refs(sess, payload.controller_id, payload.model_id)
    mode = _resolve_mode(sess, payload.model_id, payload.mode_id)
    if payload.start_address + mode.channel_count - 1 > 512:
        raise HTTPException(400, "start_address + channel_count exceeds 512")
    data = payload.model_dump()
    data["mode_id"] = mode.id
    l = Light(**data)
    sess.add(l)
    sess.commit()
    sess.refresh(l)
    rebuild_manager_sync()
    return _to_out(l)


@router.patch("/{lid}")
def update_light(lid: int, payload: LightIn, sess: Session = Depends(get_session)) -> LightOut:
    l = sess.get(Light, lid)
    if l is None:
        raise HTTPException(404, "light not found")
    ctrl, model = _ensure_refs(sess, payload.controller_id, payload.model_id)

    # If the model changed and no explicit mode was given, reset to default.
    effective_mode_id = payload.mode_id
    if payload.model_id != l.model_id and payload.mode_id is None:
        effective_mode_id = None
    mode = _resolve_mode(sess, payload.model_id, effective_mode_id)

    if payload.start_address + mode.channel_count - 1 > 512:
        raise HTTPException(400, "start_address + channel_count exceeds 512")
    data = payload.model_dump()
    data["mode_id"] = mode.id
    for k, v in data.items():
        setattr(l, k, v)
    sess.add(l)
    sess.commit()
    sess.refresh(l)
    rebuild_manager_sync()
    return _to_out(l)


@router.delete("/{lid}", status_code=204)
def delete_light(lid: int, sess: Session = Depends(get_session)) -> None:
    l = sess.get(Light, lid)
    if l is None:
        raise HTTPException(404, "light not found")
    sess.delete(l)
    sess.commit()
    rebuild_manager_sync()


@router.post("/{lid}/color")
def set_color(
    lid: int, req: ColorRequest, sess: Session = Depends(get_session)
) -> LightOut:
    l = sess.get(Light, lid)
    if l is None:
        raise HTTPException(404, "light not found")
    _apply_color(l, req)
    sess.add(l)
    sess.commit()
    sess.refresh(l)
    _push(l)
    return _to_out(l)


@router.post("/reorder")
def reorder_lights(
    req: ReorderLightsRequest, sess: Session = Depends(get_session)
) -> dict:
    """Reassign ``position`` so the given ``light_ids`` appear in that order.

    Positions are assigned 0..N-1 in list order. Lights not included in the
    request are left untouched. All referenced ids must exist."""
    if not req.light_ids:
        return {"updated": 0}
    rows = sess.exec(select(Light).where(Light.id.in_(req.light_ids))).all()
    by_id = {l.id: l for l in rows}
    missing = [lid for lid in req.light_ids if lid not in by_id]
    if missing:
        raise HTTPException(400, f"unknown light ids: {missing}")
    for idx, lid in enumerate(req.light_ids):
        l = by_id[lid]
        l.position = idx
        sess.add(l)
    sess.commit()
    return {"updated": len(req.light_ids)}


@router.post("/bulk-color")
def bulk_color(req: BulkColorRequest, sess: Session = Depends(get_session)) -> dict:
    """Apply the same color to many lights, optionally at zone granularity.

    ``light_ids`` updates the whole fixture (old behavior).
    ``targets[]`` allows per-light zone selection: each entry is
    ``{light_id, zone_id?}``. A light may appear multiple times in
    ``targets`` to update multiple zones in the same call.
    """
    wanted_ids: set[int] = set(req.light_ids or [])
    targets: list[BulkTarget] = list(req.targets or [])
    for t in targets:
        wanted_ids.add(t.light_id)
    if not wanted_ids:
        return {"updated": 0}

    rows = sess.exec(select(Light).where(Light.id.in_(wanted_ids))).all()
    by_id = {l.id: l for l in rows}

    touched: set[int] = set()

    # Whole-fixture updates first (so subsequent zone writes for the same
    # fixture land on top of the freshly reset zone_state).
    for lid in req.light_ids or []:
        l = by_id.get(lid)
        if l is None:
            continue
        whole = ColorRequest(
            r=req.r, g=req.g, b=req.b,
            w=req.w, a=req.a, uv=req.uv,
            dimmer=req.dimmer, on=req.on,
            motion=req.motion,
        )
        _apply_color(l, whole)
        sess.add(l)
        touched.add(l.id)

    for t in targets:
        l = by_id.get(t.light_id)
        if l is None:
            continue
        zoned = ColorRequest(
            r=req.r, g=req.g, b=req.b,
            w=req.w, a=req.a, uv=req.uv,
            dimmer=req.dimmer, on=req.on,
            zone_id=t.zone_id,
            motion=req.motion,
        )
        _apply_color(l, zoned)
        sess.add(l)
        touched.add(l.id)

    sess.commit()
    for lid in touched:
        l = by_id.get(lid)
        if l is None:
            continue
        sess.refresh(l)
        _push(l)
    return {"updated": len(touched)}
