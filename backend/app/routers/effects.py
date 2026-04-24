"""Effect CRUD + play/stop endpoints.

Effects are saved animated presets (cycle/fade/rainbow/etc). Playback is
non-destructive: starting an effect does not modify Light base colours,
so stopping an effect cleanly restores whatever manual state was in place
before."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import AuthDep
from ..db import get_session
from ..engine import EffectSpec, build_spec_from_effect, engine, new_handle
from ..models import Effect, Palette
from ..schemas import (
    ActiveEffect,
    BulkTarget,
    EffectIn,
    EffectOut,
    EffectParams,
    LiveEffectIn,
    SaveLiveRequest,
)

router = APIRouter(prefix="/api/effects", tags=["effects"], dependencies=[AuthDep])


# ---------------------------------------------------------------------------
# In-memory live effect registry
# ---------------------------------------------------------------------------
# Transient live effects that have been started but not persisted as Effect
# rows. Keyed by engine handle so the client can stop a specific live
# playback and later promote it to a saved Effect.
_live_specs: dict[str, EffectSpec] = {}


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def _to_out(e: Effect) -> EffectOut:
    return EffectOut(
        id=e.id,
        name=e.name,
        effect_type=e.effect_type,
        palette_id=e.palette_id,
        light_ids=list(e.light_ids or []),
        targets=[BulkTarget(**t) for t in (e.targets or [])],
        spread=e.spread,
        params=EffectParams(**(e.params or {})),
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
    row = Effect(
        name=payload.name,
        effect_type=payload.effect_type,
        palette_id=payload.palette_id,
        light_ids=list(payload.light_ids or []),
        targets=_targets_to_dicts(payload.targets),
        spread=payload.spread,
        params=payload.params.model_dump(),
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
    row.name = payload.name
    row.effect_type = payload.effect_type
    row.palette_id = payload.palette_id
    row.light_ids = list(payload.light_ids or [])
    row.targets = _targets_to_dicts(payload.targets)
    row.spread = payload.spread
    row.params = payload.params.model_dump()
    row.target_channels = list(payload.target_channels or ["rgb"])
    sess.add(row)
    sess.commit()
    sess.refresh(row)

    # If this effect was running, restart it with the new params.
    if engine.is_effect_active(eid):
        engine.stop_by_effect_id(eid)
        colors = _resolve_palette_colors(sess, row.palette_id)
        spec = build_spec_from_effect(row, _peek_palette(sess, row.palette_id))
        spec.palette_colors = colors
        engine.play(spec)

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
        effect_type=src.effect_type,
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
# Playback
# ---------------------------------------------------------------------------
def _peek_palette(sess: Session, pid: Optional[int]) -> Optional[Palette]:
    if pid is None:
        return None
    return sess.get(Palette, pid)


@router.post("/{eid}/play")
def play_effect(eid: int, sess: Session = Depends(get_session)) -> dict:
    row = sess.get(Effect, eid)
    if row is None:
        raise HTTPException(404, "effect not found")
    palette = _peek_palette(sess, row.palette_id)
    spec = build_spec_from_effect(row, palette)
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
    # Clear is_active on every persisted effect.
    rows = sess.exec(select(Effect).where(Effect.is_active == True)).all()  # noqa: E712
    for r in rows:
        r.is_active = False
        sess.add(r)
    sess.commit()
    _live_specs.clear()
    return {"ok": True, "stopped": n}


@router.get("/active")
def active_effects() -> list[ActiveEffect]:
    return [ActiveEffect(**row) for row in engine.active_snapshot()]


# ---------------------------------------------------------------------------
# Live (transient) effects
# ---------------------------------------------------------------------------
@router.post("/live")
def play_live(
    payload: LiveEffectIn, sess: Session = Depends(get_session)
) -> dict:
    """Create-and-play an unnamed in-memory effect from the Dashboard.

    Returns a stable ``handle`` that can be passed back to
    ``POST /api/effects/live/{handle}/stop`` or
    ``POST /api/effects/live/{handle}/save``."""
    colors = _resolve_palette_colors(sess, payload.palette_id)
    handle = new_handle()
    spec = EffectSpec(
        handle=handle,
        effect_id=None,
        name=payload.name or f"Live {payload.effect_type}",
        effect_type=payload.effect_type,
        palette_colors=colors,
        light_ids=list(payload.light_ids or []),
        targets=_targets_to_dicts(payload.targets),
        spread=payload.spread,
        params=payload.params.model_dump(),
        target_channels=list(payload.target_channels or ["rgb"]),
    )
    # Stash palette_id + ancillary data so "save" can persist the same
    # effect later.
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
        raise HTTPException(404, "live effect not running")
    return {"ok": True}


@router.post("/live/{handle}/save", status_code=201)
def save_live(
    handle: str,
    req: SaveLiveRequest,
    sess: Session = Depends(get_session),
) -> EffectOut:
    """Promote a running live effect to a persistent Effect row.

    The live handle keeps playing (as a live effect); the caller can then
    stop the live handle and play the saved effect to swap over cleanly."""
    spec = _live_specs.get(handle)
    if spec is None:
        raise HTTPException(404, "live effect not found")
    palette_id = _live_palette.get(handle)
    row = Effect(
        name=req.name,
        effect_type=spec.effect_type,
        palette_id=palette_id,
        light_ids=list(spec.light_ids),
        targets=list(spec.targets),
        spread=spec.spread,
        params=dict(spec.params),
        target_channels=list(spec.target_channels or ["rgb"]),
        is_active=False,
        builtin=False,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _to_out(row)


# Tracks palette_id per live handle so save_live can persist it.
_live_palette: dict[str, Optional[int]] = {}
