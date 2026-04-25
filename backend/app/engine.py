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
from .models import Effect, Light, LightModelMode, Palette

log = logging.getLogger(__name__)

TICK_HZ = 30.0
TICK_INTERVAL = 1.0 / TICK_HZ


@dataclass
class EffectSpec:
    """Immutable snapshot of an effect as the engine runs it.

    We copy effect rows into EffectSpec so that callers editing the DB
    don't mutate a running effect mid-frame. To apply edits, stop +
    re-play the effect (the router does this automatically on update)."""

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
        self._pending_starts: list[EffectSpec] = []
        self._pending_stops: list[str] = []
        self._pending_stop_all: bool = False

        # Cache of light base state for merging; refreshed lazily from DB.
        self._lights_by_id: dict[int, Light] = {}
        self._modes_by_id: dict[int, LightModelMode] = {}
        self._palettes_by_id: dict[int, Palette] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
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
        with self._lock:
            if spec.effect_id is not None:
                for handle, rs in list(self._active.items()):
                    if (
                        rs.spec.effect_id == spec.effect_id
                        and rs.fade_out_start is None
                    ):
                        self._pending_stops.append(handle)
            self._pending_starts.append(spec)
            return spec.handle

    def stop_by_handle(self, handle: str) -> bool:
        with self._lock:
            if handle in self._active:
                self._pending_stops.append(handle)
                return True
            return False

    def stop_by_effect_id(self, effect_id: int) -> int:
        with self._lock:
            hits = [
                h
                for h, rs in self._active.items()
                if rs.spec.effect_id == effect_id
                and rs.fade_out_start is None
            ]
            self._pending_stops.extend(hits)
            return len(hits)

    def stop_all(self) -> int:
        with self._lock:
            n = len([
                rs for rs in self._active.values()
                if rs.fade_out_start is None
            ])
            self._pending_stop_all = True
            return n

    def stop_affecting(self, light_ids: set[int]) -> int:
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
            self._pending_stops.extend(hits)
            return len(hits)

    def active_snapshot(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "id": rs.spec.effect_id,
                    "handle": handle,
                    "name": rs.spec.name,
                    "runtime_s": max(0.0, now - rs.started_at),
                }
                for handle, rs in self._active.items()
            ]

    def is_effect_active(self, effect_id: int) -> bool:
        with self._lock:
            return any(
                rs.spec.effect_id == effect_id
                and rs.fade_out_start is None
                for rs in self._active.values()
            )

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
        now = time.monotonic()

        with self._lock:
            starts = self._pending_starts
            stops = self._pending_stops
            stop_all = self._pending_stop_all
            self._pending_starts = []
            self._pending_stops = []
            self._pending_stop_all = False

        for spec in starts:
            self._active[spec.handle] = RunState(spec=spec, started_at=now)

        if stop_all:
            for rs in self._active.values():
                if rs.fade_out_start is None:
                    rs.fade_out_start = now
        for handle in stops:
            rs = self._active.get(handle)
            if rs and rs.fade_out_start is None:
                rs.fade_out_start = now

        if not self._active:
            return

        self._refresh_snapshots()

        per_light: dict[
            int, list[tuple[LightOverlay, float, list[str]]]
        ] = {}
        completed_handles: list[str] = []

        for handle, rs in list(self._active.items()):
            spec = rs.spec
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

            t = age
            try:
                overlays = compute_lua_overlays(
                    spec=spec,
                    t=t,
                    frame=rs.frame,
                    lights_by_id=self._lights_by_id,
                    modes_by_id=self._modes_by_id,
                )
            except ScriptError as e:
                log.warning(
                    "effect %s (%s) script error: %s; auto-stopping",
                    spec.effect_id, spec.name, e,
                )
                if rs.fade_out_start is None:
                    rs.fade_out_start = now
                continue
            rs.frame += 1

            rs.touched = set(overlays.keys())
            tc = list(spec.target_channels or ["rgb"])
            effective = fade_weight * spec.intensity
            for lid, ov in overlays.items():
                per_light.setdefault(lid, []).append((ov, effective, tc))

        for lid, contributions in per_light.items():
            light = self._lights_by_id.get(lid)
            if light is None:
                continue
            base_state = self._base_state_for(light)
            state = base_state
            zone_ids = set(zone_ids_for_light(light, self._modes_by_id))
            policy = self._policy_for_light(light)
            for overlay, fade_weight, target_channels in contributions:
                state = merge_overlay_into_state(
                    state,
                    overlay,
                    zone_ids,
                    fade_weight,
                    policy,
                    target_channels,
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
                    if lid not in still_covered and lid not in per_light:
                        to_restore.add(lid)
            for lid in to_restore:
                light = self._lights_by_id.get(lid)
                if light is None:
                    continue
                manager.set_light_state_deferred(
                    lid, self._base_state_for(light)
                )

        manager.flush_dirty()

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
