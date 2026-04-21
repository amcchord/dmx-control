from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager, rebuild_manager_sync
from ..auth import AuthDep
from ..db import get_session
from ..models import Controller, Light, LightModel
from ..schemas import BulkColorRequest, ColorRequest, LightIn, LightOut

router = APIRouter(prefix="/api/lights", tags=["lights"], dependencies=[AuthDep])


def _to_out(l: Light) -> LightOut:
    return LightOut(
        id=l.id,
        name=l.name,
        controller_id=l.controller_id,
        model_id=l.model_id,
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
    )


def _apply_color(light: Light, req: ColorRequest) -> None:
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
        },
    )


def _ensure_refs(sess: Session, controller_id: int, model_id: int) -> tuple[Controller, LightModel]:
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


@router.post("", status_code=201)
def create_light(payload: LightIn, sess: Session = Depends(get_session)) -> LightOut:
    ctrl, model = _ensure_refs(sess, payload.controller_id, payload.model_id)
    if payload.start_address + model.channel_count - 1 > 512:
        raise HTTPException(400, "start_address + channel_count exceeds 512")
    l = Light(**payload.model_dump())
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
    if payload.start_address + model.channel_count - 1 > 512:
        raise HTTPException(400, "start_address + channel_count exceeds 512")
    for k, v in payload.model_dump().items():
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


@router.post("/bulk-color")
def bulk_color(req: BulkColorRequest, sess: Session = Depends(get_session)) -> dict:
    if not req.light_ids:
        return {"updated": 0}
    rows = sess.exec(select(Light).where(Light.id.in_(req.light_ids))).all()
    for l in rows:
        _apply_color(l, req)
        sess.add(l)
    sess.commit()
    for l in rows:
        sess.refresh(l)
        _push(l)
    return {"updated": len(rows)}
