from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..artnet import rebuild_manager_sync
from ..auth import AuthDep
from ..db import get_session
from ..models import Light, LightModel
from ..schemas import LightModelIn, LightModelOut

router = APIRouter(prefix="/api/models", tags=["models"], dependencies=[AuthDep])


def _to_out(m: LightModel) -> LightModelOut:
    return LightModelOut(
        id=m.id,
        name=m.name,
        channels=list(m.channels),
        channel_count=m.channel_count,
        builtin=m.builtin,
    )


@router.get("")
def list_models(sess: Session = Depends(get_session)) -> list[LightModelOut]:
    rows = sess.exec(select(LightModel).order_by(LightModel.id)).all()
    return [_to_out(m) for m in rows]


@router.post("", status_code=201)
def create_model(payload: LightModelIn, sess: Session = Depends(get_session)) -> LightModelOut:
    m = LightModel(
        name=payload.name,
        channels=payload.channels,
        channel_count=len(payload.channels),
        builtin=False,
    )
    sess.add(m)
    sess.commit()
    sess.refresh(m)
    return _to_out(m)


@router.patch("/{mid}")
def update_model(
    mid: int, payload: LightModelIn, sess: Session = Depends(get_session)
) -> LightModelOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models are read-only; clone to edit")
    m.name = payload.name
    m.channels = payload.channels
    m.channel_count = len(payload.channels)
    sess.add(m)
    sess.commit()
    sess.refresh(m)
    rebuild_manager_sync()
    return _to_out(m)


@router.delete("/{mid}", status_code=204)
def delete_model(mid: int, sess: Session = Depends(get_session)) -> None:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    if m.builtin:
        raise HTTPException(400, "builtin models cannot be deleted")
    in_use = sess.exec(select(Light).where(Light.model_id == mid)).first()
    if in_use is not None:
        raise HTTPException(400, "model is in use by one or more lights")
    sess.delete(m)
    sess.commit()


@router.post("/{mid}/clone", status_code=201)
def clone_model(mid: int, sess: Session = Depends(get_session)) -> LightModelOut:
    m = sess.get(LightModel, mid)
    if m is None:
        raise HTTPException(404, "model not found")
    clone = LightModel(
        name=f"{m.name} (copy)",
        channels=list(m.channels),
        channel_count=m.channel_count,
        builtin=False,
    )
    sess.add(clone)
    sess.commit()
    sess.refresh(clone)
    return _to_out(clone)
