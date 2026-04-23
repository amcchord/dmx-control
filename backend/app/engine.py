"""Real-time effect engine.

The engine owns a monotonic clock and ticks at ``TICK_HZ`` in a background
asyncio task. On each tick it:

1. Loads the base state of every light that is currently driven by any
   active effect (reading the DB snapshot we cache between ticks).
2. Computes per-effect overlays via :mod:`.effects`.
3. Merges overlays into base state (with fade-in/out weighting) and writes
   the result into the ArtNet buffer via the deferred path, coalescing all
   updates into one UDP packet per controller per tick.
4. When an effect stops, re-renders the affected lights with pure base
   state so the rig returns cleanly to the manually-set colour.

Effects are non-destructive. The DB ``Light`` rows are never written by the
engine; users remain free to change base colours while an effect is playing
and the effect will ride on top of the new base.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from .artnet import manager
from .db import engine as db_engine
from .effects import (
    LightOverlay,
    compute_effect_overlays,
    merge_overlay_into_state,
    zone_ids_for_light,
)
from .models import Effect, Light, LightModelMode, Palette

log = logging.getLogger(__name__)

TICK_HZ = 30.0
TICK_INTERVAL = 1.0 / TICK_HZ


@dataclass
class EffectSpec:
    """Immutable snapshot of an effect as the engine runs it.

    We copy effect rows into EffectSpec so that callers editing the DB don't
    mutate a running effect mid-frame. To apply edits, stop + re-play the
    effect (the router does this automatically on update)."""

    handle: str
    effect_id: Optional[int]  # None for transient live effects
    name: str
    effect_type: str
    palette_colors: list[str]
    light_ids: list[int]
    targets: list[dict]
    spread: str
    params: dict


@dataclass
class RunState:
    spec: EffectSpec
    started_at: float
    # Seconds-since-start where the effect should be fading out; None while
    # still running normally.
    fade_out_start: Optional[float] = None
    # Set of light ids currently covered by the effect (recomputed each tick).
    touched: set[int] = field(default_factory=set)


class EffectEngine:
    """Asyncio tick loop driving all active effects.

    Thread-safety model: public methods are safe from any thread. They
    mutate ``_pending_commands`` behind a lock and the tick loop drains it
    at the top of each tick. All DB reads happen inside the tick (on the
    asyncio event loop thread via ``asyncio.to_thread``)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._active: dict[str, RunState] = {}
        # handle -> RunState; also includes effects currently fading out.
        self._pending_starts: list[EffectSpec] = []
        self._pending_stops: list[str] = []
        self._pending_stop_all: bool = False

        self._t0 = time.monotonic()

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
            # If a saved effect with this id is already playing, replace it.
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
        """Stop every active effect whose targets intersect ``light_ids``.

        Used when something (e.g. restoring a Scene snapshot) wants to take
        over the lights that effects are currently driving. Returns the
        number of effects marked for stop."""
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
                # An effect with no explicit targets resolves to "all lights"
                # at play time, so it always intersects any non-empty set.
                if not covers or covers & targets:
                    hits.append(handle)
            self._pending_stops.extend(hits)
            return len(hits)

    def active_snapshot(self) -> list[dict]:
        """Snapshot of currently-playing effects (including fading-out)."""
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "id": rs.spec.effect_id,
                    "handle": handle,
                    "name": rs.spec.name,
                    "effect_type": rs.spec.effect_type,
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
                    break  # stop_event was set
                except asyncio.TimeoutError:
                    pass
        finally:
            # On shutdown, restore base state for every touched light.
            try:
                await asyncio.to_thread(self._restore_all_and_flush)
            except Exception:
                log.exception("effect engine shutdown cleanup failed")

    def _tick(self) -> None:
        now = time.monotonic()

        # Drain command queue before computing this frame.
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

        # Refresh DB snapshots (base state can change while effects play).
        self._refresh_snapshots()

        # Collect which lights every effect wants to touch this frame so
        # that when an effect stops we know which lights to restore.
        all_touched: set[int] = set()
        # per-light: list of (overlay, fade_weight) to merge
        per_light: dict[int, list[tuple[LightOverlay, float]]] = {}

        completed_handles: list[str] = []

        for handle, rs in self._active.items():
            spec = rs.spec
            params = spec.params or {}
            fade_in = float(params.get("fade_in_s", 0.0))
            fade_out = float(params.get("fade_out_s", 0.0))

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

            t = age  # effect time reference is seconds since start

            overlays = compute_effect_overlays(
                effect_id=spec.effect_id or hash(spec.handle) & 0x7FFFFFFF,
                effect_type=spec.effect_type,
                palette_colors=spec.palette_colors,
                params=params,
                spread=spec.spread,
                light_ids=spec.light_ids,
                targets=spec.targets,
                t=t,
                lights_by_id=self._lights_by_id,
                modes_by_id=self._modes_by_id,
            )
            rs.touched = set(overlays.keys())
            for lid, ov in overlays.items():
                per_light.setdefault(lid, []).append((ov, fade_weight))
                all_touched.add(lid)

        # For every touched light, render base -> merge overlays -> push.
        for lid, contributions in per_light.items():
            light = self._lights_by_id.get(lid)
            if light is None:
                continue
            base_state = self._base_state_for(light)
            state = base_state
            zone_ids = set(
                zone_ids_for_light(light, self._modes_by_id)
            )
            for overlay, fade_weight in contributions:
                state = merge_overlay_into_state(
                    state, overlay, zone_ids, fade_weight
                )
            manager.set_light_state_deferred(lid, state)

        # Remove effects that finished fading out; restore any lights they
        # covered that are no longer covered by any other active effect.
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
        """Refresh cached lights/modes/palettes from SQLite.

        We read every tick because it keeps manual colour changes
        (``POST /api/lights/{id}/color``) visible under the running effect
        without needing a pub-sub. The DB is tiny so this is cheap."""
        with Session(db_engine) as sess:
            lights = sess.exec(select(Light)).all()
            modes = sess.exec(select(LightModelMode)).all()
            palettes = sess.exec(select(Palette)).all()
        self._lights_by_id = {l.id: l for l in lights if l.id is not None}
        self._modes_by_id = {m.id: m for m in modes if m.id is not None}
        self._palettes_by_id = {p.id: p for p in palettes if p.id is not None}

    def _base_state_for(self, light: Light) -> dict:
        return {
            "r": light.r,
            "g": light.g,
            "b": light.b,
            "w": light.w,
            "a": light.a,
            "uv": light.uv,
            "dimmer": light.dimmer,
            "on": light.on,
            "zone_state": dict(light.zone_state or {}),
            "motion_state": dict(light.motion_state or {}),
        }

    def _restore_all_and_flush(self) -> None:
        """Engine shutdown: push base state for every light we ever touched."""
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


def build_spec_from_effect(
    effect: Effect, palette: Optional[Palette]
) -> EffectSpec:
    colors = list(palette.colors) if palette and palette.colors else []
    if not colors:
        colors = ["#FFFFFF"]
    light_ids = list(effect.light_ids or [])
    targets = list(effect.targets or [])
    # Built-in effects ship without a fixed target list so they can be
    # played on any rig. When both target lists are empty we resolve to
    # every known light at play time.
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
        effect_type=effect.effect_type,
        palette_colors=colors,
        light_ids=light_ids,
        targets=targets,
        spread=effect.spread,
        params=dict(effect.params or {}),
    )
