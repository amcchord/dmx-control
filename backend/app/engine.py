"""Real-time effect engine.

The engine owns a monotonic clock and ticks at ``TICK_HZ`` in a background
asyncio task. On each tick it:

1. Loads the base state of every light that is currently driven by any
   active effect (reading the DB snapshot we cache between ticks).
2. Calls each script's ``render(ctx)`` (or ``tick(ctx)``) to produce
   per-slot RGB + brightness; the engine wraps that with the script's
   fade-in/out envelope, intensity multiplier, and target-channel mask.
3. Merges overlays into base state and writes the result into the ArtNet
   buffer via the deferred path, coalescing all updates into one UDP
   packet per controller per tick.
4. When an effect stops, re-renders the affected lights with pure base
   state so the rig returns cleanly to the manually-set colour.

Effects are non-destructive. The DB ``Light`` rows are never written by
the engine; users remain free to change base colours while an effect is
playing and the effect will ride on top of the new base.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from .artnet import manager
from .db import engine as db_engine
from .effects import (
    LightOverlay,
    TargetSlot,
    compute_lua_overlays,
    expand_slots,
    merge_overlay_into_state,
    zone_ids_for_light,
)
from .lua import LuaScript, ScriptError, compile_script, get_builtin_source
from .lua.runtime import merge_with_schema
from .models import Effect, EffectLayer, Light, LightModelMode, Palette

log = logging.getLogger(__name__)

TICK_HZ = 30.0
TICK_INTERVAL = 1.0 / TICK_HZ


@dataclass
class EffectSpec:
    """Immutable snapshot of an effect as the engine runs it.

    We copy effect rows into EffectSpec so that callers editing the DB
    don't mutate a running effect mid-frame. To apply edits, stop +
    re-play the effect (the router does this automatically on update).

    Layer-aware fields (``layer_id``, ``z_index``, ``blend_mode``,
    ``opacity``, ``mask_light_ids``, ``solo``, ``mute``) are populated
    when the spec was built from an :class:`EffectLayer` row; legacy
    callers that play a bare :class:`Effect` row default these fields
    to "single layer with normal blending"."""

    handle: str
    effect_id: Optional[int]  # None for transient live effects
    name: str
    script: LuaScript
    palette_colors: list[str]
    light_ids: list[int]
    targets: list[dict]
    spread: str
    params: dict
    intensity: float = 1.0
    fade_in_s: float = 0.25
    fade_out_s: float = 0.25
    target_channels: list[str] = field(default_factory=lambda: ["rgb"])
    layer_id: Optional[int] = None
    z_index: int = 100
    blend_mode: str = "normal"
    opacity: float = 1.0
    mask_light_ids: list[int] = field(default_factory=list)
    solo: bool = False
    mute: bool = False

    @property
    def script_meta(self) -> dict[str, Any]:
        m = self.script.meta
        return {
            "name": m.name,
            "description": m.description,
            "param_schema": list(m.param_schema),
            "has_render": self.script.has_render,
            "has_tick": self.script.has_tick,
        }


@dataclass
class RunState:
    spec: EffectSpec
    started_at: float
    fade_out_start: Optional[float] = None
    touched: set[int] = field(default_factory=set)
    # Frames-since-start counter (cheap script-side seed).
    frame: int = 0
    # Layer telemetry surfaced via ``active_snapshot`` and the WS layer
    # store. ``last_error`` is sticky until the engine renders a clean
    # tick again so the UI can flag a failing layer until the user
    # acknowledges it.
    last_error: Optional[str] = None
    last_tick_ms: float = 0.0
    error_count: int = 0
    auto_muted: bool = False


# ---------------------------------------------------------------------------
# Spec construction helpers
# ---------------------------------------------------------------------------
def _resolve_palette_colors(palette: Optional[Palette]) -> list[str]:
    if palette is None or not palette.colors:
        return ["#FFFFFF"]
    return list(palette.colors)


def _palette_rgb_triples(colors: list[str]) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for hx in colors:
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


def _resolve_source(row: Effect) -> str:
    """Return the Lua source for an Effect row, falling back to legacy
    ``effect_type`` for rows that haven't been migrated yet."""
    if row.source and row.source.strip():
        return row.source
    if row.effect_type:
        legacy = get_builtin_source(row.effect_type)
        if legacy is not None:
            return legacy
    raise ScriptError(
        f"effect {row.id} ({row.name!r}) has no Lua source"
    )


def build_spec_from_effect(
    effect: Effect, palette: Optional[Palette]
) -> EffectSpec:
    """Compile + snapshot a saved effect for engine playback."""
    src = _resolve_source(effect)
    script = compile_script(src, chunkname=f"=effect[{effect.id}]")
    schema = list(script.meta.param_schema)
    raw_params = dict(effect.params or {})
    intensity, fade_in, fade_out, params = _split_params(raw_params, schema)

    colors = _resolve_palette_colors(palette)
    light_ids = list(effect.light_ids or [])
    targets = list(effect.targets or [])
    if not light_ids and not targets:
        try:
            with Session(db_engine) as sess:
                rows = sess.exec(select(Light.id)).all()
                light_ids = [r for r in rows if r is not None]
        except Exception:
            log.exception(
                "failed to resolve 'all lights' for effect %s", effect.id
            )
    return EffectSpec(
        handle=new_handle(),
        effect_id=effect.id,
        name=effect.name,
        script=script,
        palette_colors=colors,
        light_ids=light_ids,
        targets=targets,
        spread=effect.spread,
        params=params,
        intensity=intensity,
        fade_in_s=fade_in,
        fade_out_s=fade_out,
        target_channels=list(effect.target_channels or ["rgb"]),
    )


def build_spec_from_layer(
    layer: EffectLayer,
    effect: Optional[Effect],
    palette: Optional[Palette],
) -> EffectSpec:
    """Compile + snapshot an :class:`EffectLayer` for engine playback.

    The layer's ``params_override`` shallow-merges over the referenced
    effect row's ``params``; everything else (blend_mode, opacity,
    mask, z_index, etc.) is layer-owned and not stored on the effect.
    Layers without an ``effect_id`` (live transient layers) are not
    supported by this builder — callers should construct the
    :class:`EffectSpec` directly in that case."""
    if effect is None:
        raise ScriptError(
            f"layer {layer.id} references missing effect {layer.effect_id}"
        )
    src = _resolve_source(effect)
    script = compile_script(src, chunkname=f"=layer[{layer.id}]")
    schema = list(script.meta.param_schema)
    raw_params = dict(effect.params or {})
    raw_params.update(layer.params_override or {})
    intensity_legacy, fade_in_legacy, fade_out_legacy, params = _split_params(
        raw_params, schema
    )
    # Layer rows own intensity/fade outright; legacy values from the
    # effect row are only used when the layer hasn't set them.
    intensity = float(layer.intensity)
    if intensity != 1.0:
        pass
    elif intensity_legacy != 1.0:
        intensity = intensity_legacy
    fade_in = float(layer.fade_in_s)
    if fade_in == 0.25 and fade_in_legacy != 0.25:
        fade_in = fade_in_legacy
    fade_out = float(layer.fade_out_s)
    if fade_out == 0.25 and fade_out_legacy != 0.25:
        fade_out = fade_out_legacy

    colors = _resolve_palette_colors(palette)
    light_ids = list(layer.light_ids or effect.light_ids or [])
    targets = list(layer.targets or effect.targets or [])
    if not light_ids and not targets:
        try:
            with Session(db_engine) as sess:
                rows = sess.exec(select(Light.id)).all()
                light_ids = [r for r in rows if r is not None]
        except Exception:
            log.exception(
                "failed to resolve 'all lights' for layer %s", layer.id
            )

    target_channels = list(
        layer.target_channels or effect.target_channels or ["rgb"]
    )

    return EffectSpec(
        handle=new_handle(),
        effect_id=effect.id,
        name=layer.name or effect.name,
        script=script,
        palette_colors=colors,
        light_ids=light_ids,
        targets=targets,
        spread=layer.spread or effect.spread,
        params=params,
        intensity=intensity,
        fade_in_s=fade_in,
        fade_out_s=fade_out,
        target_channels=target_channels,
        layer_id=layer.id,
        z_index=int(layer.z_index),
        blend_mode=str(layer.blend_mode or "normal"),
        opacity=float(layer.opacity),
        mask_light_ids=list(layer.mask_light_ids or []),
        solo=bool(layer.solo),
        mute=bool(layer.mute),
    )


def build_spec_from_transient_layer(
    layer: EffectLayer,
    script: LuaScript,
    palette_colors: list[str],
) -> EffectSpec:
    """Build an :class:`EffectSpec` for a transient layer (``effect_id=None``).

    Mirrors :func:`build_spec_from_layer` but takes a pre-compiled
    :class:`LuaScript` directly instead of resolving an :class:`Effect`
    row, so callers (Designer apply, Effect Chat apply, ``/api/effects/
    live``) can register a real ``EffectLayer`` row in the live stack
    without persisting a saved effect first. ``layer.id`` flows through
    to ``spec.layer_id`` so the run shows up in ``LiveLayersPanel`` and
    the master fader."""
    light_ids = list(layer.light_ids or [])
    targets = list(layer.targets or [])
    if not light_ids and not targets:
        try:
            with Session(db_engine) as sess:
                rows = sess.exec(select(Light.id)).all()
                light_ids = [r for r in rows if r is not None]
        except Exception:
            log.exception(
                "failed to resolve 'all lights' for transient layer %s",
                layer.id,
            )
    target_channels = list(layer.target_channels or ["rgb"])
    colors = list(palette_colors) if palette_colors else ["#FFFFFF"]
    return EffectSpec(
        handle=new_handle(),
        effect_id=None,
        name=layer.name or "Live effect",
        script=script,
        palette_colors=colors,
        light_ids=light_ids,
        targets=targets,
        spread=str(layer.spread or "across_lights"),
        params=dict(layer.params_override or {}),
        intensity=float(layer.intensity),
        fade_in_s=float(layer.fade_in_s),
        fade_out_s=float(layer.fade_out_s),
        target_channels=target_channels,
        layer_id=layer.id,
        z_index=int(layer.z_index),
        blend_mode=str(layer.blend_mode or "normal"),
        opacity=float(layer.opacity),
        mask_light_ids=list(layer.mask_light_ids or []),
        solo=bool(layer.solo),
        mute=bool(layer.mute),
    )


def _next_layer_z(sess: Session) -> int:
    rows = sess.exec(select(EffectLayer.z_index)).all()
    top = 0
    for v in rows:
        if isinstance(v, int) and v > top:
            top = v
    return top + 100


def play_transient_layer(
    sess: Session,
    *,
    name: str,
    script: LuaScript,
    palette_colors: list[str],
    light_ids: list[int],
    targets: list[dict],
    spread: str,
    params: dict,
    target_channels: list[str],
    intensity: float,
    fade_in_s: float,
    fade_out_s: float,
    palette_id: Optional[int] = None,
) -> tuple[EffectLayer, str]:
    """Persist a transient :class:`EffectLayer` (``effect_id=None``) and
    push it onto the running stack.

    Returns ``(layer, handle)``. The row carries the layer-side controls
    (z_index/blend/opacity/mute/solo) and the script-facing knobs in
    ``params_override``; the script source itself is intentionally not
    persisted (transient layers are dropped on app restart by the
    orphan-cleanup pass in ``main.py``).

    Callers that want re-play idempotency (replace prior transient row
    on a new "Play") should remember the returned ``layer.id`` and
    delete it before calling this again."""
    layer = EffectLayer(
        effect_id=None,
        name=name,
        z_index=_next_layer_z(sess),
        blend_mode="normal",
        opacity=1.0,
        intensity=float(intensity),
        fade_in_s=float(fade_in_s),
        fade_out_s=float(fade_out_s),
        target_channels=list(target_channels or ["rgb"]),
        spread=str(spread or "across_lights"),
        light_ids=list(light_ids or []),
        targets=list(targets or []),
        palette_id=palette_id,
        params_override=dict(params or {}),
        is_active=True,
    )
    sess.add(layer)
    sess.commit()
    sess.refresh(layer)
    spec = build_spec_from_transient_layer(layer, script, palette_colors)
    handle = engine.play(spec)
    return layer, handle


def _split_params(
    raw: dict[str, Any], schema: list[dict[str, Any]]
) -> tuple[float, float, float, dict[str, Any]]:
    """Extract engine controls from a raw params dict.

    ``intensity``/``fade_in_s``/``fade_out_s`` are engine-level controls
    that older saved presets stored alongside the script-facing knobs;
    we strip them here so they don't leak into ``ctx.params``."""
    intensity = float(raw.pop("intensity", 1.0))
    if intensity < 0.0:
        intensity = 0.0
    elif intensity > 1.0:
        intensity = 1.0
    fade_in = float(raw.pop("fade_in_s", 0.25))
    if fade_in < 0.0:
        fade_in = 0.0
    fade_out = float(raw.pop("fade_out_s", 0.25))
    if fade_out < 0.0:
        fade_out = 0.0
    params = merge_with_schema(schema, raw)
    return intensity, fade_in, fade_out, params


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class EffectEngine:
    """Asyncio tick loop driving all active effects."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._active: dict[str, RunState] = {}
        self._pending_stops: list[str] = []
        self._pending_stop_all: bool = False

        # Cache of light base state for merging; refreshed lazily from DB.
        self._lights_by_id: dict[int, Light] = {}
        self._modes_by_id: dict[int, LightModelMode] = {}
        self._palettes_by_id: dict[int, Palette] = {}

        # Telemetry (engine-wide, surfaced on /api/health).
        self._tick_count: int = 0
        self._dropped_frames: int = 0
        self._last_tick_ms: float = 0.0
        self._auto_mute_threshold: int = 5

        # WS subscribers — async callbacks invoked when layer state changes.
        self._listeners: set[asyncio.Queue[dict]] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        # Hand the same loop to the base-state log so it can broadcast
        # records over the layers WebSocket from request-handling
        # threads (the routers call record() synchronously).
        from .base_state_log import log as base_state_log

        base_state_log.set_loop(self._loop)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="effect-engine")
        log.info("effect engine started at %.1f FPS", TICK_HZ)

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
        self._stop_event = None
        log.info("effect engine stopped")

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------
    def play(self, spec: EffectSpec) -> str:
        """Add a layer to the running stack.

        Two semantic shifts from the pre-layer engine:

        * No auto-stop of other instances of the same effect — every
          layer is an explicit instance, so two ``+ Wash`` clicks land
          two stacked layers (use blend/opacity to control how they
          combine).
        * Synchronous registration: the new spec is in ``_active`` the
          moment this returns, so callers can read it back via
          ``layer_snapshot()`` without waiting for the next tick.
        """
        with self._lock:
            self._active[spec.handle] = RunState(
                spec=spec, started_at=time.monotonic()
            )
        self._broadcast_layers()
        return spec.handle

    def stop_by_handle(self, handle: str, immediate: bool = False) -> bool:
        with self._lock:
            if handle not in self._active:
                return False
            if immediate:
                self._active.pop(handle, None)
            else:
                self._pending_stops.append(handle)
            return True

    def stop_by_effect_id(self, effect_id: int, immediate: bool = False) -> int:
        with self._lock:
            hits = [
                h
                for h, rs in self._active.items()
                if rs.spec.effect_id == effect_id
                and rs.fade_out_start is None
            ]
            if immediate:
                for h in hits:
                    self._active.pop(h, None)
            else:
                self._pending_stops.extend(hits)
            return len(hits)

    def stop_all(self, immediate: bool = False) -> int:
        with self._lock:
            n = len([
                rs for rs in self._active.values()
                if rs.fade_out_start is None
            ])
            if immediate:
                self._active.clear()
            else:
                self._pending_stop_all = True
            return n

    def stop_affecting(self, light_ids: set[int], immediate: bool = False) -> int:
        if not light_ids:
            return 0
        targets = set(light_ids)
        with self._lock:
            hits: list[str] = []
            for handle, rs in self._active.items():
                if rs.fade_out_start is not None:
                    continue
                covers: set[int] = set(rs.spec.light_ids or [])
                for t in rs.spec.targets or []:
                    lid = t.get("light_id")
                    if isinstance(lid, int):
                        covers.add(lid)
                if rs.touched:
                    covers |= rs.touched
                if not covers or covers & targets:
                    hits.append(handle)
            if immediate:
                for h in hits:
                    self._active.pop(h, None)
            else:
                self._pending_stops.extend(hits)
            return len(hits)

    def patch_layer(self, handle: str, patch: dict) -> bool:
        """Apply a property patch to an active layer (mute/solo/
        opacity/blend_mode/z_index/intensity/mask_light_ids).

        Patches apply synchronously under the engine lock so callers
        can read the new state back immediately. The compositor reads
        layer state inside ``_tick`` under the same lock so there's no
        mid-frame race."""
        with self._lock:
            rs = self._active.get(handle)
            if rs is None:
                return False
            self._apply_layer_patch(rs, dict(patch))
        self._broadcast_layers()
        return True

    def active_snapshot(self) -> list[dict]:
        return self.layer_snapshot()

    def layer_snapshot(self) -> list[dict]:
        """Snapshot of every active layer (used by the WS layer store)."""
        now = time.monotonic()
        with self._lock:
            out: list[dict] = []
            for handle, rs in self._active.items():
                spec = rs.spec
                out.append({
                    "handle": handle,
                    "id": spec.effect_id,
                    "layer_id": spec.layer_id,
                    "name": spec.name,
                    "runtime_s": max(0.0, now - rs.started_at),
                    "z_index": spec.z_index,
                    "blend_mode": spec.blend_mode,
                    "opacity": spec.opacity,
                    "intensity": spec.intensity,
                    "target_channels": list(spec.target_channels),
                    "mute": spec.mute or rs.auto_muted,
                    "solo": spec.solo,
                    "auto_muted": rs.auto_muted,
                    "stopping": rs.fade_out_start is not None,
                    "error": rs.last_error,
                    "error_count": rs.error_count,
                    "last_tick_ms": rs.last_tick_ms,
                    "mask_light_ids": list(spec.mask_light_ids),
                })
            out.sort(key=lambda l: (l["z_index"], l["layer_id"] or 0, l["handle"]))
            return out

    def health_snapshot(self) -> dict:
        with self._lock:
            return {
                "tick_count": self._tick_count,
                "dropped_frames": self._dropped_frames,
                "last_tick_ms": self._last_tick_ms,
                "active_layers": len(self._active),
                "tick_hz": TICK_HZ,
            }

    def is_effect_active(self, effect_id: int) -> bool:
        with self._lock:
            return any(
                rs.spec.effect_id == effect_id
                and rs.fade_out_start is None
                for rs in self._active.values()
            )

    def stop_by_layer_id(self, layer_id: int, immediate: bool = False) -> int:
        with self._lock:
            hits = [
                h for h, rs in self._active.items()
                if rs.spec.layer_id == layer_id and rs.fade_out_start is None
            ]
            if immediate:
                for h in hits:
                    self._active.pop(h, None)
            else:
                self._pending_stops.extend(hits)
            return len(hits)

    def find_handle_for_layer(self, layer_id: int) -> Optional[str]:
        with self._lock:
            for handle, rs in self._active.items():
                if rs.spec.layer_id == layer_id:
                    return handle
            return None

    # ------------------------------------------------------------------
    # WS broadcast plumbing
    # ------------------------------------------------------------------
    def subscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._listeners.add(q)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._listeners.discard(q)

    def _broadcast_layers(self) -> None:
        if self._loop is None:
            return
        snapshot = {
            "type": "layers",
            "layers": self.layer_snapshot(),
            "health": self.health_snapshot(),
        }
        with self._lock:
            queues = list(self._listeners)
        for q in queues:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, snapshot)
            except Exception:
                # Queue closed/full — drop silently; caller will reconnect.
                pass

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        assert self._stop_event is not None
        try:
            while not self._stop_event.is_set():
                tick_start = time.monotonic()
                try:
                    await asyncio.to_thread(self._tick)
                except Exception:
                    log.exception("effect engine tick failed")
                elapsed = time.monotonic() - tick_start
                delay = max(0.001, TICK_INTERVAL - elapsed)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=delay
                    )
                    break
                except asyncio.TimeoutError:
                    pass
        finally:
            try:
                await asyncio.to_thread(self._restore_all_and_flush)
            except Exception:
                log.exception("effect engine shutdown cleanup failed")

    def _tick(self) -> None:
        tick_start = time.monotonic()
        now = tick_start

        with self._lock:
            stops = self._pending_stops
            stop_all = self._pending_stop_all
            self._pending_stops = []
            self._pending_stop_all = False
            self._tick_count += 1

        if stop_all:
            for rs in self._active.values():
                if rs.fade_out_start is None:
                    rs.fade_out_start = now
        for handle in stops:
            rs = self._active.get(handle)
            if rs and rs.fade_out_start is None:
                rs.fade_out_start = now

        layers_changed = bool(stops or stop_all)

        if not self._active:
            if layers_changed:
                self._broadcast_layers()
            return

        self._refresh_snapshots()

        # Deterministic bottom-up layer order: lower z_index renders
        # first; ties broken by layer_id then handle.
        ordered = sorted(
            self._active.items(),
            key=lambda it: (
                it[1].spec.z_index,
                it[1].spec.layer_id or 0,
                it[0],
            ),
        )

        any_solo = any(
            rs.spec.solo for _, rs in ordered if not rs.spec.mute
        )

        per_layer_overlays: list[
            tuple[str, RunState, dict[int, LightOverlay], float]
        ] = []
        completed_handles: list[str] = []

        for handle, rs in ordered:
            spec = rs.spec
            if spec.mute or rs.auto_muted:
                continue
            if any_solo and not spec.solo:
                continue

            fade_in = float(spec.fade_in_s)
            fade_out = float(spec.fade_out_s)
            age = max(0.0, now - rs.started_at)
            fade_weight = 1.0
            if fade_in > 0.0 and age < fade_in:
                fade_weight = age / fade_in
            if rs.fade_out_start is not None:
                fo_age = now - rs.fade_out_start
                if fade_out <= 0.0:
                    fade_weight = 0.0
                else:
                    fade_weight = min(
                        fade_weight, max(0.0, 1.0 - fo_age / fade_out)
                    )
                if fade_weight <= 0.0:
                    completed_handles.append(handle)

            layer_start = time.monotonic()
            try:
                overlays = compute_lua_overlays(
                    spec=spec,
                    t=age,
                    frame=rs.frame,
                    lights_by_id=self._lights_by_id,
                    modes_by_id=self._modes_by_id,
                )
            except ScriptError as e:
                rs.error_count += 1
                rs.last_error = str(e)
                log.warning(
                    "layer %s (%s) script error #%d: %s",
                    spec.layer_id or spec.effect_id, spec.name,
                    rs.error_count, e,
                )
                # Auto-mute after repeated failures so a bad script
                # can't take the rest of the rig down with it.
                if rs.error_count >= self._auto_mute_threshold:
                    rs.auto_muted = True
                    log.warning(
                        "auto-muting layer %s after %d errors",
                        spec.layer_id or spec.effect_id, rs.error_count,
                    )
                continue
            rs.last_tick_ms = (time.monotonic() - layer_start) * 1000.0
            rs.last_error = None
            rs.frame += 1

            mask = set(spec.mask_light_ids or [])
            if mask:
                overlays = {
                    lid: ov for lid, ov in overlays.items() if lid in mask
                }
            rs.touched = set(overlays.keys())
            effective = fade_weight * max(0.0, min(1.0, spec.intensity))
            per_layer_overlays.append((handle, rs, overlays, effective))

        # Aggregate the set of lights touched anywhere in the stack.
        touched_lights: set[int] = set()
        for _, _, overlays, _ in per_layer_overlays:
            touched_lights.update(overlays.keys())

        # Composite bottom-to-top per light.
        for lid in touched_lights:
            light = self._lights_by_id.get(lid)
            if light is None:
                continue
            zone_ids = set(zone_ids_for_light(light, self._modes_by_id))
            policy = self._policy_for_light(light)
            state = self._base_state_for(light)
            for _, rs, overlays, eff in per_layer_overlays:
                ov = overlays.get(lid)
                if ov is None:
                    continue
                state = merge_overlay_into_state(
                    state,
                    ov,
                    zone_ids,
                    eff,
                    policy,
                    list(rs.spec.target_channels or ["rgb"]),
                    blend_mode=rs.spec.blend_mode,
                    layer_opacity=rs.spec.opacity,
                )
            manager.set_light_state_deferred(lid, state)

        if completed_handles:
            still_covered: set[int] = set()
            for handle, rs in self._active.items():
                if handle in completed_handles:
                    continue
                still_covered |= rs.touched
            to_restore: set[int] = set()
            for handle in completed_handles:
                rs = self._active.pop(handle, None)
                if rs is None:
                    continue
                for lid in rs.touched:
                    if lid not in still_covered and lid not in touched_lights:
                        to_restore.add(lid)
            for lid in to_restore:
                light = self._lights_by_id.get(lid)
                if light is None:
                    continue
                manager.set_light_state_deferred(
                    lid, self._base_state_for(light)
                )
            layers_changed = True

        manager.flush_dirty()

        elapsed_ms = (time.monotonic() - tick_start) * 1000.0
        with self._lock:
            self._last_tick_ms = elapsed_ms
            if elapsed_ms > (1000.0 / TICK_HZ) * 1.5:
                self._dropped_frames += 1

        if layers_changed:
            self._broadcast_layers()

    def _apply_layer_patch(self, rs: RunState, patch: dict) -> None:
        """Apply a runtime patch to a layer's spec.

        Only a small whitelist of fields can change at runtime; ignore
        everything else so a sloppy client can't replace the layer's
        Lua source out from under us mid-tick."""
        spec = rs.spec
        if "mute" in patch:
            spec.mute = bool(patch["mute"])
            if not spec.mute:
                rs.auto_muted = False
                rs.error_count = 0
        if "solo" in patch:
            spec.solo = bool(patch["solo"])
        if "opacity" in patch:
            try:
                spec.opacity = max(0.0, min(1.0, float(patch["opacity"])))
            except (TypeError, ValueError):
                pass
        if "intensity" in patch:
            try:
                spec.intensity = max(0.0, min(1.0, float(patch["intensity"])))
            except (TypeError, ValueError):
                pass
        if "blend_mode" in patch:
            mode = patch["blend_mode"]
            if isinstance(mode, str):
                spec.blend_mode = mode
        if "z_index" in patch:
            try:
                spec.z_index = int(patch["z_index"])
            except (TypeError, ValueError):
                pass
        if "mask_light_ids" in patch:
            ids = patch.get("mask_light_ids") or []
            if isinstance(ids, list):
                spec.mask_light_ids = [
                    int(x) for x in ids if isinstance(x, (int, float))
                ]
        if "target_channels" in patch:
            tc = patch.get("target_channels") or []
            if isinstance(tc, list):
                spec.target_channels = [
                    str(x) for x in tc if isinstance(x, str)
                ] or ["rgb"]

    # ------------------------------------------------------------------
    # DB snapshot helpers
    # ------------------------------------------------------------------
    def _refresh_snapshots(self) -> None:
        with Session(db_engine) as sess:
            lights = sess.exec(select(Light)).all()
            modes = sess.exec(select(LightModelMode)).all()
            palettes = sess.exec(select(Palette)).all()
        self._lights_by_id = {l.id: l for l in lights if l.id is not None}
        self._modes_by_id = {m.id: m for m in modes if m.id is not None}
        self._palettes_by_id = {p.id: p for p in palettes if p.id is not None}

    def _base_state_for(self, light: Light) -> dict:
        extras = dict(getattr(light, "extra_colors", {}) or {})
        return {
            "r": light.r,
            "g": light.g,
            "b": light.b,
            "w": light.w,
            "a": light.a,
            "uv": light.uv,
            "w2": extras.get("w2"),
            "w3": extras.get("w3"),
            "a2": extras.get("a2"),
            "uv2": extras.get("uv2"),
            "extra_colors": extras,
            "dimmer": light.dimmer,
            "on": light.on,
            "zone_state": dict(light.zone_state or {}),
            "motion_state": dict(light.motion_state or {}),
        }

    def _policy_for_light(self, light: Light) -> dict:
        if light.mode_id is None:
            return {}
        mode = self._modes_by_id.get(light.mode_id)
        if mode is None:
            return {}
        if isinstance(mode.color_policy, dict):
            return dict(mode.color_policy)
        return {}

    def _restore_all_and_flush(self) -> None:
        try:
            self._refresh_snapshots()
        except Exception:
            return
        touched: set[int] = set()
        for rs in self._active.values():
            touched |= rs.touched
        for lid in touched:
            light = self._lights_by_id.get(lid)
            if light is None:
                continue
            manager.set_light_state_deferred(lid, self._base_state_for(light))
        manager.flush_dirty()
        self._active.clear()


engine = EffectEngine()


def new_handle() -> str:
    return uuid.uuid4().hex
