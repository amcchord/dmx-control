"""In-memory log of base-state changes.

Manual color sets, scene/state applies, palette applies, and blackouts
all write directly to the per-light DB row (the engine's "base state")
and bypass the layer stack. Without a record of who did what, operators
look at a red light, see no running effect layer, and have no way to
answer "why is this light red?".

This module keeps a small ring buffer of recent base-state changes and
fans them out to the same WebSocket that already streams layers. The
log is *purely informational* — the engine never reads from it.

Persistence is deliberately session-scoped: the buffer resets on
backend restart. That's fine for the discoverability problem this is
solving (a log entry from yesterday isn't telling you why the rig
looks the way it does right now).
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal, Optional

LOG_MAX = 10

BaseStateKind = Literal[
    "manual_color", "scene", "state", "palette", "blackout"
]


@dataclass
class BaseStateChange:
    """One recorded base-state mutation.

    Fields are deliberately small + JSON-friendly so we can round-trip
    them through the layers WebSocket without an intermediate schema.

    - ``id`` is monotonic and server-assigned; clients can use it as a
      stable React key.
    - ``rgb`` is best-effort; populated for ``manual_color`` and (when
      meaningful) ``palette``.
    - ``controller_id`` is set when every affected light belongs to the
      same controller, otherwise ``None``.
    """

    id: int
    kind: BaseStateKind
    title: str
    light_count: int
    light_ids: list[int]
    controller_id: Optional[int]
    rgb: Optional[tuple[int, int, int]]
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        # JSON has no tuples — emit a list (or null) for symmetry.
        if d.get("rgb") is not None:
            d["rgb"] = list(d["rgb"])
        return d


class _BaseStateLog:
    """Thread-safe ring buffer + asyncio fan-out for base-state changes.

    The recording side runs on whatever thread serves the request (the
    routers call :meth:`record` synchronously from their handlers); the
    WS side subscribes from the asyncio loop. We use the engine's loop
    via :func:`set_loop` so cross-thread enqueues stay safe.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: list[BaseStateChange] = []
        self._next_id: int = 1
        self._listeners: set[asyncio.Queue[dict]] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the asyncio loop used for thread-safe broadcasts."""
        with self._lock:
            self._loop = loop

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def record(
        self,
        kind: BaseStateKind,
        *,
        title: str,
        light_ids: Iterable[int],
        controller_id: Optional[int] = None,
        rgb: Optional[tuple[int, int, int]] = None,
    ) -> BaseStateChange:
        ids = [int(i) for i in light_ids]
        with self._lock:
            entry = BaseStateChange(
                id=self._next_id,
                kind=kind,
                title=title,
                light_count=len(ids),
                light_ids=ids,
                controller_id=controller_id,
                rgb=rgb,
            )
            self._next_id += 1
            self._entries.append(entry)
            # Trim to the most recent LOG_MAX entries.
            if len(self._entries) > LOG_MAX:
                self._entries = self._entries[-LOG_MAX:]
            payload = {
                "type": "base_state",
                "log": [e.to_dict() for e in reversed(self._entries)],
            }
            queues = list(self._listeners)
            loop = self._loop

        if loop is not None:
            for q in queues:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, payload)
                except Exception:
                    # Queue closed/full — drop silently; the client
                    # will resync via the REST snapshot on reconnect.
                    pass
        return entry

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def snapshot(self) -> list[dict]:
        """Return the log as a list of dicts, newest first."""
        with self._lock:
            return [e.to_dict() for e in reversed(self._entries)]

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._next_id = 1

    # ------------------------------------------------------------------
    # WS pub/sub
    # ------------------------------------------------------------------
    def subscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._listeners.add(q)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._listeners.discard(q)


log = _BaseStateLog()


def _controller_id_for(light_ids: Iterable[int], lookup) -> Optional[int]:
    """Helper: return the shared controller_id for ``light_ids`` if every
    id maps to the same controller (per ``lookup(id) -> controller_id``),
    else ``None``. Callers use this to attach a controller hint to log
    entries when they've already loaded the rows.
    """
    cid: Optional[int] = None
    for lid in light_ids:
        v = lookup(lid)
        if v is None:
            return None
        if cid is None:
            cid = int(v)
        elif int(v) != cid:
            return None
    return cid
