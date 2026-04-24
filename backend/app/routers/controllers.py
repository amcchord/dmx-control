from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import manager, rebuild_manager_sync
from ..auth import AuthDep
from ..db import get_session
from ..models import Controller
from ..schemas import ControllerIn, ControllerOut

router = APIRouter(prefix="/api/controllers", tags=["controllers"], dependencies=[AuthDep])


def _to_out(c: Controller) -> ControllerOut:
    return ControllerOut(
        id=c.id,
        name=c.name,
        ip=c.ip,
        port=c.port,
        net=c.net,
        subnet=c.subnet,
        universe=c.universe,
        enabled=c.enabled,
        notes=c.notes,
    )


@router.get("")
def list_controllers(sess: Session = Depends(get_session)) -> list[ControllerOut]:
    rows = sess.exec(select(Controller).order_by(Controller.id)).all()
    return [_to_out(c) for c in rows]


@router.post("", status_code=201)
def create_controller(payload: ControllerIn, sess: Session = Depends(get_session)) -> ControllerOut:
    c = Controller(**payload.model_dump())
    sess.add(c)
    sess.commit()
    sess.refresh(c)
    rebuild_manager_sync()
    return _to_out(c)


@router.patch("/{cid}")
def update_controller(
    cid: int, payload: ControllerIn, sess: Session = Depends(get_session)
) -> ControllerOut:
    c = sess.get(Controller, cid)
    if c is None:
        raise HTTPException(404, "controller not found")
    for k, v in payload.model_dump().items():
        setattr(c, k, v)
    sess.add(c)
    sess.commit()
    sess.refresh(c)
    rebuild_manager_sync()
    return _to_out(c)


@router.delete("/{cid}", status_code=204)
def delete_controller(cid: int, sess: Session = Depends(get_session)) -> None:
    c = sess.get(Controller, cid)
    if c is None:
        raise HTTPException(404, "controller not found")
    sess.delete(c)
    sess.commit()
    rebuild_manager_sync()


@router.post("/{cid}/blackout")
def blackout(cid: int, sess: Session = Depends(get_session)) -> dict:
    c = sess.get(Controller, cid)
    if c is None:
        raise HTTPException(404, "controller not found")
    # Also reset persisted state so the blackout sticks across restarts.
    from ..models import Light

    lights = sess.exec(select(Light).where(Light.controller_id == cid)).all()
    for light in lights:
        light.r = light.g = light.b = light.w = light.a = light.uv = 0
        light.on = False
        sess.add(light)
    sess.commit()
    manager.blackout(cid)
    return {"ok": True}
