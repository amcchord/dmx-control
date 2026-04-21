"""Scene CRUD + play/stop endpoints.

Scenes are saved effect presets. Playback is non-destructive: starting a
scene does not modify Light base colours, so stopping a scene cleanly
restores whatever manual state was in place before."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import AuthDep
from ..db import get_session
from ..engine import SceneSpec, build_spec_from_scene, engine, new_handle
from ..models import Palette, Scene
from ..schemas import (
    ActiveScene,
    BulkTarget,
    EffectParams,
    LiveSceneIn,
    SaveLiveRequest,
    SceneIn,
    SceneOut,
)

router = APIRouter(prefix="/api/scenes", tags=["scenes"], dependencies=[AuthDep])


# ---------------------------------------------------------------------------
# In-memory live scene registry
# ---------------------------------------------------------------------------
# Transient live scenes that have been started but not persisted as Scene
# rows. Keyed by engine handle so the client can stop a specific live
# playback and later promote it to a saved Scene.
_live_specs: dict[str, SceneSpec] = {}


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def _to_out(s: Scene) -> SceneOut:
    return SceneOut(
        id=s.id,
        name=s.name,
        effect_type=s.effect_type,
        palette_id=s.palette_id,
        light_ids=list(s.light_ids or []),
        targets=[BulkTarget(**t) for t in (s.targets or [])],
        spread=s.spread,
        params=EffectParams(**(s.params or {})),
        is_active=engine.is_scene_active(s.id) if s.id is not None else False,
        builtin=s.builtin,
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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("")
def list_scenes(sess: Session = Depends(get_session)) -> list[SceneOut]:
    rows = sess.exec(
        select(Scene).order_by(Scene.builtin.desc(), Scene.name)
    ).all()
    return [_to_out(s) for s in rows]


@router.post("", status_code=201)
def create_scene(
    payload: SceneIn, sess: Session = Depends(get_session)
) -> SceneOut:
    row = Scene(
        name=payload.name,
        effect_type=payload.effect_type,
        palette_id=payload.palette_id,
        light_ids=list(payload.light_ids or []),
        targets=_targets_to_dicts(payload.targets),
        spread=payload.spread,
        params=payload.params.model_dump(),
        is_active=False,
        builtin=False,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _to_out(row)


@router.patch("/{sid}")
def update_scene(
    sid: int, payload: SceneIn, sess: Session = Depends(get_session)
) -> SceneOut:
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")
    if row.builtin:
        raise HTTPException(400, "builtin scenes are read-only; clone to edit")
    row.name = payload.name
    row.effect_type = payload.effect_type
    row.palette_id = payload.palette_id
    row.light_ids = list(payload.light_ids or [])
    row.targets = _targets_to_dicts(payload.targets)
    row.spread = payload.spread
    row.params = payload.params.model_dump()
    sess.add(row)
    sess.commit()
    sess.refresh(row)

    # If this scene was running, restart it with the new params.
    if engine.is_scene_active(sid):
        engine.stop_by_scene_id(sid)
        colors = _resolve_palette_colors(sess, row.palette_id)
        spec = build_spec_from_scene(row, _peek_palette(sess, row.palette_id))
        spec.palette_colors = colors
        engine.play(spec)

    return _to_out(row)


@router.delete("/{sid}", status_code=204, response_model=None)
def delete_scene(sid: int, sess: Session = Depends(get_session)) -> None:
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")
    if row.builtin:
        raise HTTPException(400, "builtin scenes cannot be deleted")
    engine.stop_by_scene_id(sid)
    sess.delete(row)
    sess.commit()


@router.post("/{sid}/clone", status_code=201)
def clone_scene(sid: int, sess: Session = Depends(get_session)) -> SceneOut:
    src = sess.get(Scene, sid)
    if src is None:
        raise HTTPException(404, "scene not found")
    clone = Scene(
        name=f"{src.name} (copy)",
        effect_type=src.effect_type,
        palette_id=src.palette_id,
        light_ids=list(src.light_ids or []),
        targets=list(src.targets or []),
        spread=src.spread,
        params=dict(src.params or {}),
        is_active=False,
        builtin=False,
    )
    sess.add(clone)
    sess.commit()
    sess.refresh(clone)
    return _to_out(clone)


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------
def _peek_palette(sess: Session, pid: Optional[int]) -> Optional[Palette]:
    if pid is None:
        return None
    return sess.get(Palette, pid)


@router.post("/{sid}/play")
def play_scene(sid: int, sess: Session = Depends(get_session)) -> dict:
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")
    palette = _peek_palette(sess, row.palette_id)
    spec = build_spec_from_scene(row, palette)
    row.is_active = True
    sess.add(row)
    sess.commit()
    handle = engine.play(spec)
    return {"ok": True, "handle": handle}


@router.post("/{sid}/stop")
def stop_scene(sid: int, sess: Session = Depends(get_session)) -> dict:
    row = sess.get(Scene, sid)
    if row is None:
        raise HTTPException(404, "scene not found")
    n = engine.stop_by_scene_id(sid)
    row.is_active = False
    sess.add(row)
    sess.commit()
    return {"ok": True, "stopped": n}


@router.post("/stop-all")
def stop_all(sess: Session = Depends(get_session)) -> dict:
    n = engine.stop_all()
    # Clear is_active on every persisted scene.
    rows = sess.exec(select(Scene).where(Scene.is_active == True)).all()  # noqa: E712
    for r in rows:
        r.is_active = False
        sess.add(r)
    sess.commit()
    _live_specs.clear()
    return {"ok": True, "stopped": n}


@router.get("/active")
def active_scenes() -> list[ActiveScene]:
    return [ActiveScene(**row) for row in engine.active_snapshot()]


# ---------------------------------------------------------------------------
# Live (transient) scenes
# ---------------------------------------------------------------------------
@router.post("/live")
def play_live(
    payload: LiveSceneIn, sess: Session = Depends(get_session)
) -> dict:
    """Create-and-play an unnamed in-memory scene from the Dashboard.

    Returns a stable ``handle`` that can be passed back to
    ``POST /api/scenes/live/{handle}/stop`` or
    ``POST /api/scenes/live/{handle}/save``."""
    colors = _resolve_palette_colors(sess, payload.palette_id)
    handle = new_handle()
    spec = SceneSpec(
        handle=handle,
        scene_id=None,
        name=payload.name or f"Live {payload.effect_type}",
        effect_type=payload.effect_type,
        palette_colors=colors,
        light_ids=list(payload.light_ids or []),
        targets=_targets_to_dicts(payload.targets),
        spread=payload.spread,
        params=payload.params.model_dump(),
    )
    # Stash palette_id + ancillary data so "save" can persist the same
    # scene later.
    _live_specs[handle] = spec
    _live_palette[handle] = payload.palette_id
    engine.play(spec)
    return {"ok": True, "handle": handle, "name": spec.name}


@router.post("/live/{handle}/stop")
def stop_live(handle: str) -> dict:
    ok = engine.stop_by_handle(handle)
    _live_specs.pop(handle, None)
    _live_palette.pop(handle, None)
    if not ok:
        raise HTTPException(404, "live scene not running")
    return {"ok": True}


@router.post("/live/{handle}/save", status_code=201)
def save_live(
    handle: str,
    req: SaveLiveRequest,
    sess: Session = Depends(get_session),
) -> SceneOut:
    """Promote a running live scene to a persistent Scene row.

    The live handle keeps playing (as a live scene); the caller can then
    stop the live handle and play the saved scene to swap over cleanly."""
    spec = _live_specs.get(handle)
    if spec is None:
        raise HTTPException(404, "live scene not found")
    palette_id = _live_palette.get(handle)
    row = Scene(
        name=req.name,
        effect_type=spec.effect_type,
        palette_id=palette_id,
        light_ids=list(spec.light_ids),
        targets=list(spec.targets),
        spread=spec.spread,
        params=dict(spec.params),
        is_active=False,
        builtin=False,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _to_out(row)


# Tracks palette_id per live handle so save_live can persist it.
_live_palette: dict[str, Optional[int]] = {}
