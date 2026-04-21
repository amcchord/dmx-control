from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..artnet import manager
from ..auth import AuthDep
from ..db import get_session
from ..models import Controller, Light, LightModel

router = APIRouter(prefix="/api/state", tags=["state"], dependencies=[AuthDep])


@router.get("")
def get_state(sess: Session = Depends(get_session)) -> dict:
    controllers = sess.exec(select(Controller)).all()
    lights = sess.exec(select(Light)).all()
    models = {m.id: m for m in sess.exec(select(LightModel)).all()}

    universes: dict[int, dict] = {}
    for c in controllers:
        entry = manager._controllers.get(c.id)  # type: ignore[attr-defined]
        if entry is None:
            continue
        _, buf = entry
        universes[c.id] = {
            "controller_id": c.id,
            "net": c.net,
            "subnet": c.subnet,
            "universe": c.universe,
            "data": list(buf.data),
        }

    return {
        "controllers": [
            {"id": c.id, "name": c.name, "ip": c.ip, "enabled": c.enabled}
            for c in controllers
        ],
        "lights": [
            {
                "id": l.id,
                "name": l.name,
                "controller_id": l.controller_id,
                "start_address": l.start_address,
                "channel_count": models[l.model_id].channel_count if l.model_id in models else 0,
                "r": l.r,
                "g": l.g,
                "b": l.b,
                "on": l.on,
            }
            for l in lights
        ],
        "universes": list(universes.values()),
    }


@router.post("/resend")
def resend() -> dict:
    manager.send_all()
    return {"ok": True}
