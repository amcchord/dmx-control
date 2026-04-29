"""Layered effect compositing (Photoshop-style stack).

Each :class:`EffectLayer` row is one running layer on the rig. The engine
composites layers from low z_index to high using the layer's
``blend_mode``/``opacity``/``mask`` to produce the final per-light state
every tick. The legacy ``POST /api/effects/{id}/play`` endpoint creates
a transient layer behind the scenes, so callers that haven't migrated to
the new API still work.

The WebSocket at ``/api/layers/ws`` streams layer + health snapshots so
the mobile Now Playing pill, the desktop Live rail, and the Effects
Composer all read from one source of truth.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from sqlmodel import Session, select

from ..auth import AuthDep, is_authenticated_request
from ..base_state_log import log as base_state_log
from ..db import get_session
from ..engine import build_spec_from_layer, engine
from ..lua import ScriptError
from ..models import Effect, EffectLayer, Palette
from ..schemas import (
    LayerCreate,
    LayerOut,
    LayerPatch,
    LayerReorder,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/layers", tags=["layers"], dependencies=[AuthDep])
ws_router = APIRouter(prefix="/api/layers", tags=["layers"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _runtime_layer_to_out(snap: dict) -> LayerOut:
    """Build a ``LayerOut`` from the engine's runtime snapshot dict."""
    return LayerOut(
        handle=snap["handle"],
        layer_id=snap.get("layer_id"),
        effect_id=snap.get("id"),
        name=snap.get("name") or "",
        runtime_s=float(snap.get("runtime_s") or 0.0),
        z_index=int(snap.get("z_index") or 100),
        blend_mode=snap.get("blend_mode") or "normal",
        opacity=float(snap.get("opacity") or 1.0),
        intensity=float(snap.get("intensity") or 1.0),
        target_channels=list(snap.get("target_channels") or ["rgb"]),
        mute=bool(snap.get("mute")),
        solo=bool(snap.get("solo")),
        auto_muted=bool(snap.get("auto_muted")),
        stopping=bool(snap.get("stopping")),
        error=snap.get("error"),
        error_count=int(snap.get("error_count") or 0),
        last_tick_ms=float(snap.get("last_tick_ms") or 0.0),
        mask_light_ids=list(snap.get("mask_light_ids") or []),
    )


def _next_z(sess: Session) -> int:
    """Return a sparse next z_index above every existing layer."""
    rows = sess.exec(select(EffectLayer.z_index)).all()
    if not rows:
        return 100
    top = 0
    for v in rows:
        if isinstance(v, int) and v > top:
            top = v
    return top + 100


def _start_layer(sess: Session, row: EffectLayer) -> Optional[str]:
    """Compile + push a layer row to the engine. Returns the engine handle
    or ``None`` if the script failed to compile (caller decides how to
    surface that)."""
    if row.effect_id is None:
        return None
    effect = sess.get(Effect, row.effect_id)
    if effect is None:
        return None
    palette = (
        sess.get(Palette, row.palette_id)
        if row.palette_id is not None
        else (
            sess.get(Palette, effect.palette_id)
            if effect.palette_id is not None
            else None
        )
    )
    try:
        spec = build_spec_from_layer(row, effect, palette)
    except ScriptError as e:
        log.warning("layer %s failed to compile: %s", row.id, e)
        return None
    return engine.play(spec)


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
@router.get("")
def list_layers() -> list[LayerOut]:
    return [_runtime_layer_to_out(l) for l in engine.layer_snapshot()]


@router.post("", status_code=201)
def create_layer(
    payload: LayerCreate, sess: Session = Depends(get_session)
) -> LayerOut:
    """Add a new layer on top of the stack from a saved effect.

    ``z_index`` defaults to ``max(existing) + 100`` so the new layer
    renders on top. Pass an explicit value to insert mid-stack."""
    effect = sess.get(Effect, payload.effect_id)
    if effect is None:
        raise HTTPException(404, "effect not found")
    z_index = (
        int(payload.z_index)
        if payload.z_index is not None
        else _next_z(sess)
    )
    row = EffectLayer(
        effect_id=payload.effect_id,
        name=payload.name,
        z_index=z_index,
        blend_mode=payload.blend_mode,
        opacity=payload.opacity,
        intensity=payload.intensity,
        fade_in_s=payload.fade_in_s,
        fade_out_s=payload.fade_out_s,
        mute=payload.mute,
        solo=payload.solo,
        mask_light_ids=list(payload.mask_light_ids or []),
        target_channels=list(
            payload.target_channels or effect.target_channels or ["rgb"]
        ),
        spread=str(payload.spread or effect.spread or "across_lights"),
        light_ids=list(payload.light_ids or []),
        targets=[t.model_dump() for t in (payload.targets or [])],
        palette_id=payload.palette_id
        if payload.palette_id is not None
        else effect.palette_id,
        params_override=dict(payload.params_override or {}),
        is_active=True,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    handle = _start_layer(sess, row)
    if handle is None:
        # Rollback: layer compilation failed.
        sess.delete(row)
        sess.commit()
        raise HTTPException(400, "layer script failed to compile")
    snap = next(
        (l for l in engine.layer_snapshot() if l.get("handle") == handle), None
    )
    return _runtime_layer_to_out(snap or {"handle": handle, "name": effect.name})


@router.patch("/{layer_id}")
def patch_layer(
    layer_id: int, payload: LayerPatch, sess: Session = Depends(get_session)
) -> LayerOut:
    row = sess.get(EffectLayer, layer_id)
    if row is None:
        raise HTTPException(404, "layer not found")
    update = payload.model_dump(exclude_unset=True)
    for key, value in update.items():
        if key == "params_override":
            row.params_override = dict(value or {})
        elif key == "mask_light_ids":
            row.mask_light_ids = list(value or [])
        elif key == "target_channels":
            row.target_channels = list(value or [])
        else:
            setattr(row, key, value)
    sess.add(row)
    sess.commit()

    handle = engine.find_handle_for_layer(layer_id)
    if handle is not None:
        engine.patch_layer(handle, update)
    snap = next(
        (l for l in engine.layer_snapshot() if l.get("layer_id") == layer_id),
        None,
    )
    if snap is None:
        raise HTTPException(409, "layer is not running")
    return _runtime_layer_to_out(snap)


@router.post("/reorder")
def reorder_layers(
    payload: LayerReorder, sess: Session = Depends(get_session)
) -> list[LayerOut]:
    for entry in payload.order or []:
        try:
            lid = int(entry.get("layer_id"))
            z = int(entry.get("z_index"))
        except (TypeError, ValueError, AttributeError):
            continue
        row = sess.get(EffectLayer, lid)
        if row is None:
            continue
        row.z_index = z
        sess.add(row)
        handle = engine.find_handle_for_layer(lid)
        if handle is not None:
            engine.patch_layer(handle, {"z_index": z})
    sess.commit()
    return [_runtime_layer_to_out(l) for l in engine.layer_snapshot()]


@router.delete("/{layer_id}", status_code=204, response_model=None)
def delete_layer(layer_id: int, sess: Session = Depends(get_session)) -> None:
    row = sess.get(EffectLayer, layer_id)
    if row is None:
        raise HTTPException(404, "layer not found")
    # Hard stop: deleting a layer is an explicit "gone now" action, no
    # fade-out so callers reading state right after see a clean rig.
    engine.stop_by_layer_id(layer_id, immediate=True)
    sess.delete(row)
    sess.commit()


@router.post("/clear")
def clear_layers(sess: Session = Depends(get_session)) -> dict:
    """Panic stop: stop every running layer and delete every persisted
    layer row. Used by the panic/blackout button."""
    n = engine.stop_all(immediate=True)
    rows = sess.exec(select(EffectLayer)).all()
    for row in rows:
        sess.delete(row)
    sess.commit()
    return {"ok": True, "stopped": n}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@ws_router.websocket("/ws")
async def layers_ws(websocket: WebSocket) -> None:
    """Streams `{type: "layers", layers: [...], health: {...}}` whenever
    the layer set changes. Sends an immediate snapshot on connect so a
    client never has to poll once subscribed.

    The protocol is fire-and-forget; clients should reconnect on close.
    """
    await websocket.accept()
    if not is_authenticated_request(websocket):
        await websocket.close(code=4401)
        return

    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)
    engine.subscribe(queue)
    # The base-state log shares this WS so clients only have to manage
    # one connection: it sends ``{type: "base_state", log: [...]}``
    # frames whenever a manual color / scene / state / palette / blackout
    # is recorded.
    base_state_log.subscribe(queue)
    try:
        # Initial snapshots — both stacks at once.
        await websocket.send_json({
            "type": "layers",
            "layers": engine.layer_snapshot(),
            "health": engine.health_snapshot(),
        })
        await websocket.send_json({
            "type": "base_state",
            "log": base_state_log.snapshot(),
        })
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # Heartbeat keeps proxies happy.
                msg = {
                    "type": "heartbeat",
                    "health": engine.health_snapshot(),
                }
            try:
                await websocket.send_json(msg)
            except (WebSocketDisconnect, RuntimeError):
                break
    finally:
        engine.unsubscribe(queue)
        base_state_log.unsubscribe(queue)
        try:
            await websocket.close()
        except Exception:
            pass
