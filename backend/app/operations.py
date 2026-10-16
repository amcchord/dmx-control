"""Bounded, durable look history. Captures base colors and the active layer stack."""
from datetime import datetime, timezone
import threading
import time

from sqlmodel import Session, select
from .db import engine as db_engine
from .models import Effect, EffectLayer, Light, LookCheckpoint, Palette
from .routers._capture import capture_lights, apply_state_to_light, push_light
from .engine import engine, build_spec_from_layer

_lock = threading.RLock()
_last_key = ""
_last_at = 0.0


def record_before(label: str) -> None:
    global _last_key, _last_at
    with _lock, Session(db_engine) as sess:
        now = time.monotonic()
        # A slider drag is a single action; preserve its starting value.
        if label == _last_key and now - _last_at < 1.5:
            _last_at = now
            return
        _last_key, _last_at = label, now
        active = {x.get("layer_id") for x in engine.layer_snapshot()}
        layers = [r.model_dump(mode="json") for r in sess.exec(select(EffectLayer)).all() if r.id in active]
        row = LookCheckpoint(label=label, created_at=datetime.now(timezone.utc).isoformat(),
                             lights=capture_lights(list(sess.exec(select(Light)).all()), from_rendered=False), layers=layers)
        sess.add(row)
        sess.commit()
        old = sess.exec(select(LookCheckpoint).order_by(LookCheckpoint.id.desc())).all()[12:]
        for item in old:
            sess.delete(item)
        sess.commit()


def history_status() -> dict:
    with Session(db_engine) as sess:
        rows = sess.exec(select(LookCheckpoint).order_by(LookCheckpoint.id.desc())).all()
        return {"available": bool(rows), "entries": [{"id": r.id, "label": r.label, "created_at": r.created_at} for r in rows]}


def restore(checkpoint_id: int | None = None) -> dict:
    global _last_key
    from .transitions import cancel
    cancel()
    with _lock, Session(db_engine) as sess:
        row = sess.get(LookCheckpoint, checkpoint_id) if checkpoint_id else sess.exec(select(LookCheckpoint).order_by(LookCheckpoint.id.desc())).first()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(404, "No previous look to restore")
        # Compile every saved layer before changing the live rig.
        prepared = []
        for saved in row.layers:
            effect = sess.get(Effect, saved.get("effect_id"))
            if not effect:
                from fastapi import HTTPException
                raise HTTPException(409, "A saved effect was removed. Choose a different look.")
            layer = EffectLayer(**{k: v for k, v in saved.items() if k not in {"id", "created_at"}})
            palette = sess.get(Palette, layer.palette_id) if layer.palette_id else None
            build_spec_from_layer(layer, effect, palette)
            prepared.append((layer, effect, palette))
        engine.stop_all(immediate=True)
        for effect in sess.exec(select(Effect).where(Effect.is_active == True)).all():
            effect.is_active = False
            sess.add(effect)
        for old in sess.exec(select(EffectLayer)).all():
            sess.delete(old)
        by_id = {x["light_id"]: x for x in row.lights}
        lights = sess.exec(select(Light)).all()
        for light in lights:
            if light.id in by_id:
                apply_state_to_light(light, by_id[light.id])
                sess.add(light)
        sess.commit()
        for light in lights:
            if light.id in by_id:
                push_light(light)
        for layer, effect, palette in prepared:
            sess.add(layer)
            sess.flush()
            engine.play(build_spec_from_layer(layer, effect, palette))
        # Remove the restored checkpoint so another Undo walks back one step.
        label = row.label
        sess.delete(row)
        sess.commit()
        _last_key = ""
        return {"ok": True, "label": label}


def is_live_change(method: str, path: str) -> bool:
    if method not in {"POST", "PATCH", "DELETE"}:
        return False
    return (
        path.startswith("/api/layers")
        or (path.startswith("/api/lights/") and (path.endswith("/color") or path.endswith("/bulk-color")))
        or (path.startswith(("/api/scenes/", "/api/states/", "/api/palettes/")) and path.endswith("/apply"))
        or (path.startswith("/api/controllers/") and path.endswith("/blackout"))
        or (path.startswith("/api/effects/") and (path.endswith(("/play", "/stop", "/stop-all")) or path == "/api/effects/live"))
    )
