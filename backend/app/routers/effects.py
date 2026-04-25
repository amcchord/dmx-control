"""Effect CRUD + play/stop endpoints + Lua lint + WebSocket preview.

Effects are saved Lua scripts. Playback is non-destructive: starting an
effect does not modify Light base colours, so stopping an effect cleanly
restores whatever manual state was in place before."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from sqlmodel import Session, select

from ..auth import AuthDep
from ..db import get_session
from ..effects import (
    LightOverlay,
    compute_lua_overlays,
    expand_slots,
)
from ..engine import (
    EffectSpec,
    build_spec_from_effect,
    engine,
    new_handle,
)
from ..lua import LuaScript, ScriptError, compile_script
from ..lua.runtime import merge_with_schema
from ..models import Effect, Palette
from ..schemas import (
    ActiveEffect,
    BulkTarget,
    EffectControls,
    EffectIn,
    EffectLintRequest,
    EffectLintResponse,
    EffectOut,
    LiveEffectIn,
    SaveLiveRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/effects", tags=["effects"], dependencies=[AuthDep])

# WebSocket routes can't reuse ``AuthDep`` because the ``require_auth``
# dependency is typed for ``Request`` and FastAPI won't inject a
# WebSocket into it. We register the preview WS on a separate router and
# perform the cookie check manually inside the handler.
ws_router = APIRouter(prefix="/api/effects", tags=["effects"])


# ---------------------------------------------------------------------------
# In-memory live effect registry
# ---------------------------------------------------------------------------
_live_specs: dict[str, EffectSpec] = {}
_live_palette: dict[str, Optional[int]] = {}


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def _to_out(e: Effect) -> EffectOut:
    schema = list(e.param_schema or [])
    description = ""
    if not schema and e.source:
        # Compile lazily to surface metadata for rows seeded before the
        # param_schema column existed. Tolerated to be slow on legacy
        # paths since the seeder fills these in on first boot.
        try:
            compiled = compile_script(e.source)
            schema = list(compiled.meta.param_schema)
            description = compiled.meta.description
        except ScriptError:
            schema = []
    else:
        try:
            compiled = compile_script(e.source) if e.source else None
            description = compiled.meta.description if compiled else ""
        except ScriptError:
            description = ""
    raw_params = dict(e.params or {})
    intensity = float(raw_params.pop("intensity", 1.0))
    fade_in = float(raw_params.pop("fade_in_s", 0.25))
    fade_out = float(raw_params.pop("fade_out_s", 0.25))
    user_params = merge_with_schema(schema, raw_params)
    return EffectOut(
        id=e.id,
        name=e.name,
        source=e.source or "",
        description=description,
        param_schema=schema,
        palette_id=e.palette_id,
        light_ids=list(e.light_ids or []),
        targets=[BulkTarget(**t) for t in (e.targets or [])],
        spread=e.spread,
        params=user_params,
        controls=EffectControls(
            intensity=intensity, fade_in_s=fade_in, fade_out_s=fade_out
        ),
        target_channels=list(e.target_channels or ["rgb"]),
        is_active=engine.is_effect_active(e.id) if e.id is not None else False,
        builtin=e.builtin,
    )


def _targets_to_dicts(targets: Optional[list[BulkTarget]]) -> list[dict]:
    if not targets:
        return []
    return [t.model_dump() for t in targets]


def _resolve_palette_colors(
    sess: Session, palette_id: Optional[int]
) -> list[str]:
    if palette_id is None:
        return ["#FFFFFF"]
    p = sess.get(Palette, palette_id)
    if p is None or not p.colors:
        return ["#FFFFFF"]
    return list(p.colors)


def _peek_palette(sess: Session, pid: Optional[int]) -> Optional[Palette]:
    if pid is None:
        return None
    return sess.get(Palette, pid)


def _persist_params(
    payload_params: dict[str, Any], controls: EffectControls
) -> dict[str, Any]:
    """Combine script-facing params with the engine's controls envelope.

    The Effect row's ``params`` JSON is the union of the two so that
    ``EffectIn`` payloads round-trip through ``EffectOut`` cleanly."""
    out = dict(payload_params or {})
    out["intensity"] = float(controls.intensity)
    out["fade_in_s"] = float(controls.fade_in_s)
    out["fade_out_s"] = float(controls.fade_out_s)
    return out


def _compile_or_400(source: str) -> LuaScript:
    try:
        return compile_script(source, chunkname="=effect")
    except ScriptError as e:
        detail: dict[str, Any] = {"message": e.message}
        if e.line is not None:
            detail["line"] = e.line
        raise HTTPException(400, {"error": detail})


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("")
def list_effects(sess: Session = Depends(get_session)) -> list[EffectOut]:
    rows = sess.exec(
        select(Effect).order_by(Effect.builtin.desc(), Effect.name)
    ).all()
    return [_to_out(e) for e in rows]


@router.post("", status_code=201)
def create_effect(
    payload: EffectIn, sess: Session = Depends(get_session)
) -> EffectOut:
    script = _compile_or_400(payload.source)
    schema = list(script.meta.param_schema)
    user_params = merge_with_schema(schema, payload.params or {})
    row = Effect(
        name=payload.name,
        source=payload.source,
        param_schema=schema,
        palette_id=payload.palette_id,
        light_ids=list(payload.light_ids or []),
        targets=_targets_to_dicts(payload.targets),
        spread=payload.spread,
        params=_persist_params(user_params, payload.controls),
        target_channels=list(payload.target_channels or ["rgb"]),
        is_active=False,
        builtin=False,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _to_out(row)


@router.patch("/{eid}")
def update_effect(
    eid: int, payload: EffectIn, sess: Session = Depends(get_session)
) -> EffectOut:
    row = sess.get(Effect, eid)
    if row is None:
        raise HTTPException(404, "effect not found")
    if row.builtin:
        raise HTTPException(400, "builtin effects are read-only; clone to edit")
    script = _compile_or_400(payload.source)
    schema = list(script.meta.param_schema)
    user_params = merge_with_schema(schema, payload.params or {})
    row.name = payload.name
    row.source = payload.source
    row.param_schema = schema
    row.palette_id = payload.palette_id
    row.light_ids = list(payload.light_ids or [])
    row.targets = _targets_to_dicts(payload.targets)
    row.spread = payload.spread
    row.params = _persist_params(user_params, payload.controls)
    row.target_channels = list(payload.target_channels or ["rgb"])
    sess.add(row)
    sess.commit()
    sess.refresh(row)

    if engine.is_effect_active(eid):
        engine.stop_by_effect_id(eid)
        try:
            spec = build_spec_from_effect(row, _peek_palette(sess, row.palette_id))
            engine.play(spec)
        except ScriptError as e:
            log.warning("failed to restart effect %s after update: %s", eid, e)

    return _to_out(row)


@router.delete("/{eid}", status_code=204, response_model=None)
def delete_effect(eid: int, sess: Session = Depends(get_session)) -> None:
    row = sess.get(Effect, eid)
    if row is None:
        raise HTTPException(404, "effect not found")
    if row.builtin:
        raise HTTPException(400, "builtin effects cannot be deleted")
    engine.stop_by_effect_id(eid)
    sess.delete(row)
    sess.commit()


@router.post("/{eid}/clone", status_code=201)
def clone_effect(eid: int, sess: Session = Depends(get_session)) -> EffectOut:
    src = sess.get(Effect, eid)
    if src is None:
        raise HTTPException(404, "effect not found")
    clone = Effect(
        name=f"{src.name} (copy)",
        source=src.source,
        param_schema=list(src.param_schema or []),
        palette_id=src.palette_id,
        light_ids=list(src.light_ids or []),
        targets=list(src.targets or []),
        spread=src.spread,
        params=dict(src.params or {}),
        target_channels=list(src.target_channels or ["rgb"]),
        is_active=False,
        builtin=False,
    )
    sess.add(clone)
    sess.commit()
    sess.refresh(clone)
    return _to_out(clone)


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
@router.post("/lint")
def lint_effect(payload: EffectLintRequest) -> EffectLintResponse:
    """Compile a Lua source and return its metadata or error details."""
    try:
        script = compile_script(payload.source, chunkname="=lint")
    except ScriptError as e:
        return EffectLintResponse(ok=False, error=e.to_dict())
    return EffectLintResponse(
        ok=True,
        name=script.meta.name,
        description=script.meta.description,
        param_schema=list(script.meta.param_schema),
        has_render=script.has_render,
        has_tick=script.has_tick,
    )


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------
@router.post("/{eid}/play")
def play_effect(eid: int, sess: Session = Depends(get_session)) -> dict:
    row = sess.get(Effect, eid)
    if row is None:
        raise HTTPException(404, "effect not found")
    palette = _peek_palette(sess, row.palette_id)
    try:
        spec = build_spec_from_effect(row, palette)
    except ScriptError as e:
        raise HTTPException(400, {"error": e.to_dict()})
    row.is_active = True
    sess.add(row)
    sess.commit()
    handle = engine.play(spec)
    return {"ok": True, "handle": handle}


@router.post("/{eid}/stop")
def stop_effect(eid: int, sess: Session = Depends(get_session)) -> dict:
    row = sess.get(Effect, eid)
    if row is None:
        raise HTTPException(404, "effect not found")
    n = engine.stop_by_effect_id(eid)
    row.is_active = False
    sess.add(row)
    sess.commit()
    return {"ok": True, "stopped": n}


@router.post("/stop-all")
def stop_all(sess: Session = Depends(get_session)) -> dict:
    n = engine.stop_all()
    rows = sess.exec(select(Effect).where(Effect.is_active == True)).all()  # noqa: E712
    for r in rows:
        r.is_active = False
        sess.add(r)
    sess.commit()
    _live_specs.clear()
    _live_palette.clear()
    return {"ok": True, "stopped": n}


@router.get("/active")
def active_effects() -> list[ActiveEffect]:
    return [ActiveEffect(**row) for row in engine.active_snapshot()]


# ---------------------------------------------------------------------------
# Live (transient) effects
# ---------------------------------------------------------------------------
def _live_spec(payload: LiveEffectIn, sess: Session) -> EffectSpec:
    script = _compile_or_400(payload.source)
    schema = list(script.meta.param_schema)
    user_params = merge_with_schema(schema, payload.params or {})
    colors = _resolve_palette_colors(sess, payload.palette_id)
    handle = new_handle()
    return EffectSpec(
        handle=handle,
        effect_id=None,
        name=payload.name or script.meta.name or "Live effect",
        script=script,
        palette_colors=colors,
        light_ids=list(payload.light_ids or []),
        targets=_targets_to_dicts(payload.targets),
        spread=payload.spread,
        params=user_params,
        intensity=float(payload.controls.intensity),
        fade_in_s=float(payload.controls.fade_in_s),
        fade_out_s=float(payload.controls.fade_out_s),
        target_channels=list(payload.target_channels or ["rgb"]),
    )


@router.post("/live")
def play_live(
    payload: LiveEffectIn, sess: Session = Depends(get_session)
) -> dict:
    spec = _live_spec(payload, sess)
    _live_specs[spec.handle] = spec
    _live_palette[spec.handle] = payload.palette_id
    engine.play(spec)
    return {"ok": True, "handle": spec.handle, "name": spec.name}


@router.post("/live/{handle}/stop")
def stop_live(handle: str) -> dict:
    ok = engine.stop_by_handle(handle)
    _live_specs.pop(handle, None)
    _live_palette.pop(handle, None)
    if not ok:
        raise HTTPException(404, "live effect not running")
    return {"ok": True}


@router.post("/live/{handle}/save", status_code=201)
def save_live(
    handle: str,
    req: SaveLiveRequest,
    sess: Session = Depends(get_session),
) -> EffectOut:
    spec = _live_specs.get(handle)
    if spec is None:
        raise HTTPException(404, "live effect not found")
    palette_id = _live_palette.get(handle)
    persisted = _persist_params(
        dict(spec.params),
        EffectControls(
            intensity=spec.intensity,
            fade_in_s=spec.fade_in_s,
            fade_out_s=spec.fade_out_s,
        ),
    )
    row = Effect(
        name=req.name,
        source=spec.script.source,
        param_schema=list(spec.script.meta.param_schema),
        palette_id=palette_id,
        light_ids=list(spec.light_ids),
        targets=list(spec.targets),
        spread=spec.spread,
        params=persisted,
        target_channels=list(spec.target_channels or ["rgb"]),
        is_active=False,
        builtin=False,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _to_out(row)


# ---------------------------------------------------------------------------
# WebSocket preview
# ---------------------------------------------------------------------------
PREVIEW_TICK_HZ = 30.0
PREVIEW_TICK_DT = 1.0 / PREVIEW_TICK_HZ
PREVIEW_MAX_CELLS = 64


class PreviewSession:
    """One websocket preview - owns a LuaScript that we hot-recompile
    when the client patches it."""

    def __init__(self) -> None:
        self.script: Optional[LuaScript] = None
        self.params: dict[str, Any] = {}
        self.palette_colors: list[str] = ["#FFFFFF"]
        self.cells: int = 16
        self.spread: str = "across_lights"
        self.target_channels: list[str] = ["rgb"]
        self.intensity: float = 1.0
        self.t0: float = time.monotonic()
        self.frame: int = 0

    def patch(self, msg: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Apply an incoming patch dict. Returns an error dict on failure
        so the caller can stream it back; raises only on protocol errors."""
        source = msg.get("source")
        if isinstance(source, str):
            try:
                self.script = compile_script(source, chunkname="=preview")
            except ScriptError as e:
                return e.to_dict()
            self.t0 = time.monotonic()
            self.frame = 0
        params = msg.get("params")
        if isinstance(params, dict) and self.script is not None:
            schema = list(self.script.meta.param_schema)
            self.params = merge_with_schema(schema, params)
        palette = msg.get("palette")
        if isinstance(palette, list):
            colors: list[str] = []
            for entry in palette:
                if isinstance(entry, str):
                    colors.append(entry)
                elif isinstance(entry, dict):
                    try:
                        colors.append(
                            "#{:02X}{:02X}{:02X}".format(
                                int(entry.get("r", 0)),
                                int(entry.get("g", 0)),
                                int(entry.get("b", 0)),
                            )
                        )
                    except (TypeError, ValueError):
                        continue
            if colors:
                self.palette_colors = colors
        cells = msg.get("cells")
        if isinstance(cells, int) and 1 <= cells <= PREVIEW_MAX_CELLS:
            self.cells = cells
        spread = msg.get("spread")
        if isinstance(spread, str):
            self.spread = spread
        tc = msg.get("target_channels")
        if isinstance(tc, list):
            self.target_channels = [
                str(x) for x in tc if isinstance(x, str)
            ] or ["rgb"]
        intensity = msg.get("intensity")
        if isinstance(intensity, (int, float)):
            self.intensity = max(0.0, min(1.0, float(intensity)))
        return None

    def render_frame(self) -> dict[str, Any]:
        if self.script is None:
            return {"frame": self.frame, "strips": []}
        t = time.monotonic() - self.t0
        strips = _preview_render(
            script=self.script,
            t=t,
            frame=self.frame,
            n=self.cells,
            params=self.params,
            palette_colors=self.palette_colors,
            target_channels=self.target_channels,
            intensity=self.intensity,
        )
        self.frame += 1
        return {"frame": self.frame, "strips": strips, "t": t}


def _preview_palette_triples(colors: list[str]) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for hx in colors or []:
        s = hx.strip().lstrip("#")
        if len(s) != 6:
            continue
        try:
            out.append((int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)))
        except ValueError:
            continue
    if not out:
        out.append((255, 255, 255))
    return out


def _preview_render(
    *,
    script: LuaScript,
    t: float,
    frame: int,
    n: int,
    params: dict[str, Any],
    palette_colors: list[str],
    target_channels: list[str],
    intensity: float,
) -> list[dict[str, Any]]:
    """Run the script once per slot and project the result onto every
    requested target channel.

    Returns one ``strip`` per target channel:
    ``[{ target: "rgb", cells: [...] }, { target: "w", cells: [...] }]``.
    RGB cells carry ``{r, g, b, brightness}``; aux-channel cells carry
    a scalar ``brightness`` (the same envelope an aux-only effect drives
    onto the fixture's W / A / UV / dimmer / strobe fader)."""
    pal_obj = script.make_palette(_preview_palette_triples(palette_colors))
    targets = list(target_channels or ["rgb"])
    if not targets:
        targets = ["rgb"]
    raw: list[dict[str, Any]] = []
    for i in range(max(1, n)):
        ctx = script.new_table()
        ctx["t"] = float(t)
        ctx["i"] = i
        ctx["n"] = n
        ctx["frame"] = frame
        ctx["seed"] = 1
        ctx["palette"] = pal_obj
        params_tbl = script.new_table()
        for k, v in (params or {}).items():
            params_tbl[k] = v
        ctx["params"] = params_tbl
        slot_tbl = script.new_table()
        slot_tbl["light_id"] = i + 1
        slot_tbl["zone_id"] = None
        ctx["slot"] = slot_tbl
        raw.append(script.render_slot(ctx))

    strips: list[dict[str, Any]] = []
    for target in targets:
        cells: list[dict[str, Any]] = []
        for r in raw:
            if not r.get("active", False):
                cells.append({"active": False})
                continue
            bri = float(r.get("brightness", 1.0)) * intensity
            if bri < 0.0:
                bri = 0.0
            elif bri > 1.0:
                bri = 1.0
            if target == "rgb":
                cells.append({
                    "active": True,
                    "r": int(r.get("r", 0)),
                    "g": int(r.get("g", 0)),
                    "b": int(r.get("b", 0)),
                    "brightness": bri,
                })
            else:
                # Aux scalar = max(rgb)/255 * envelope. Mirrors what
                # ``_scalar_from_rgb`` collapses to on the fixture.
                rr, gg, bb = (
                    int(r.get("r", 0)),
                    int(r.get("g", 0)),
                    int(r.get("b", 0)),
                )
                scalar = max(rr, gg, bb) / 255.0
                cells.append({
                    "active": True,
                    "brightness": max(0.0, min(1.0, scalar * bri)),
                })
        strips.append({"target": target, "cells": cells})
    return strips


@ws_router.websocket("/preview/ws")
async def preview_ws(websocket: WebSocket) -> None:
    """Stream computed preview cells to the client at 30 Hz.

    The client sends a JSON object once with the script + params + palette;
    can later send ``{patch: {...}}`` to hot-update without reconnecting.
    """
    await websocket.accept()

    # Verify the user is authenticated. We re-use the cookie check the
    # AuthDep does for HTTP routes so unauthenticated browsers cannot hit
    # the preview WS even if they reach the path.
    from ..auth import is_authenticated_request  # noqa: WPS433 - local import

    if not is_authenticated_request(websocket):
        await websocket.close(code=4401)
        return

    session = PreviewSession()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def reader() -> None:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                await queue.put({"__close__": True})
                return
            except Exception:
                await queue.put({"__close__": True})
                return
            await queue.put(msg)

    reader_task = asyncio.create_task(reader())
    try:
        while True:
            try:
                msg = await asyncio.wait_for(
                    queue.get(), timeout=PREVIEW_TICK_DT
                )
            except asyncio.TimeoutError:
                msg = None
            if msg is not None:
                if msg.get("__close__"):
                    break
                payload = msg.get("patch") if "patch" in msg else msg
                if isinstance(payload, dict):
                    err = session.patch(payload)
                    if err is not None:
                        await websocket.send_json({"error": err})
                        continue
            try:
                frame = session.render_frame()
            except ScriptError as e:
                await websocket.send_json({"error": e.to_dict()})
                continue
            await websocket.send_json(frame)
    finally:
        reader_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
