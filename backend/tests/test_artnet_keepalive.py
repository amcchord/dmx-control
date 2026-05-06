"""Tests for the periodic ArtNet keepalive plumbing.

Covers :meth:`ArtNetManager.send_stale`, which the keepalive task in
``main.py`` calls to re-send any controller whose buffer hasn't been
pushed recently. This is the recovery path for power-cycled fixtures
on a static show, so it has to: skip recently-sent controllers, treat
"never sent" as stale (so a freshly-enabled controller gets primed),
and ignore disabled controllers."""

from __future__ import annotations

import time

from app.artnet import ArtNetManager, UniverseBuffer
from app.models import Controller


def _make_manager_with_controller(enabled: bool = True) -> tuple[ArtNetManager, Controller, list[int]]:
    mgr = ArtNetManager()
    ctrl = Controller(
        id=1, name="c1", ip="127.0.0.1", port=6454,
        net=0, subnet=0, universe=0, enabled=enabled,
    )
    buf = UniverseBuffer(ctrl.net, ctrl.subnet, ctrl.universe)
    mgr._controllers[ctrl.id] = (ctrl, buf)

    sent: list[int] = []

    def _fake_send(c: Controller, b: UniverseBuffer) -> None:
        sent.append(c.id)
        mgr._last_sent_at[c.id] = time.monotonic()

    mgr._send_buffer = _fake_send  # type: ignore[assignment]
    return mgr, ctrl, sent


def test_send_stale_resends_when_no_recent_send():
    mgr, ctrl, sent = _make_manager_with_controller()
    # Pretend the last send was 11s ago; the keepalive interval is 10s.
    mgr._last_sent_at[ctrl.id] = time.monotonic() - 11.0

    assert mgr.send_stale(10.0) == 1
    assert sent == [ctrl.id]
    mgr.close()


def test_send_stale_skips_recently_sent():
    mgr, ctrl, sent = _make_manager_with_controller()
    mgr._last_sent_at[ctrl.id] = time.monotonic()

    assert mgr.send_stale(10.0) == 0
    assert sent == []
    mgr.close()


def test_send_stale_primes_never_sent_controller():
    mgr, ctrl, sent = _make_manager_with_controller()
    # No entry in _last_sent_at -> treated as stale, gets primed.
    assert ctrl.id not in mgr._last_sent_at
    assert mgr.send_stale(10.0) == 1
    assert sent == [ctrl.id]
    mgr.close()


def test_send_stale_skips_disabled_controllers():
    mgr, ctrl, sent = _make_manager_with_controller(enabled=False)
    mgr._last_sent_at[ctrl.id] = time.monotonic() - 11.0

    assert mgr.send_stale(10.0) == 0
    assert sent == []
    mgr.close()
