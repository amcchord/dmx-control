"""Integration tests for the new ``/api/layers`` REST + the legacy
``/api/effects/{id}/play`` -> layer shim.

These confirm:

* Creating a layer persists an :class:`EffectLayer` row, pushes a spec
  to the engine, and returns the runtime snapshot.
* Patching opacity/blend/mute round-trips through the engine.
* Reordering updates ``z_index`` on every running layer.
* Deleting a layer stops it and removes the row.
* The legacy play endpoint creates a layer transparently so existing
  clients keep working.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete


@pytest.fixture()
def client():
    """Reuse the live app with auth disabled and per-test DB reset.

    We don't reload modules between tests because SQLAlchemy's metadata
    is shared across imports — instead we lean on the auth override and
    truncate the layer/effect tables before each test so the engine
    and DB start from a known state."""
    from app.auth import require_auth
    from app.db import engine as db_engine
    from app.engine import engine as effect_engine
    from app.main import app
    from app.models import Effect, EffectLayer

    async def _no_auth() -> None:
        return None

    app.dependency_overrides[require_auth] = _no_auth

    # Reset engine + DB tables touched by the layer flow.
    effect_engine.stop_all(immediate=True)
    with Session(db_engine) as sess:
        sess.exec(delete(EffectLayer))
        sess.exec(
            delete(Effect).where(Effect.builtin == False)  # noqa: E712
        )
        sess.commit()

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_auth, None)
        effect_engine.stop_all(immediate=True)
        with Session(db_engine) as sess:
            sess.exec(delete(EffectLayer))
            sess.commit()


def _login(client: TestClient) -> None:
    # Auth is overridden; this stays a no-op kept for parity with the
    # shape the rest of the suite uses.
    return None


def _seed_effect(client: TestClient) -> int:
    """Create a tiny single-color Lua effect via the public API."""
    src = (
        "NAME = 'Test'\n"
        "function render(ctx) return { r = 50, g = 0, b = 0, brightness = 1 } end\n"
    )
    r = client.post(
        "/api/effects",
        json={
            "name": "TestLayerEffect",
            "source": src,
            "palette_id": None,
            "light_ids": [],
            "spread": "across_lights",
            "params": {},
            "controls": {"intensity": 1.0, "fade_in_s": 0.0, "fade_out_s": 0.0},
            "target_channels": ["rgb"],
        },
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def test_create_layer_runs_via_engine(client):
    _login(client)
    eid = _seed_effect(client)

    r = client.post("/api/layers", json={"effect_id": eid, "opacity": 0.5})
    assert r.status_code == 201, r.text
    layer = r.json()
    assert layer["effect_id"] == eid
    assert layer["opacity"] == 0.5
    assert layer["blend_mode"] == "normal"

    listing = client.get("/api/layers").json()
    assert any(l["layer_id"] == layer["layer_id"] for l in listing)


def test_patch_layer_round_trips(client):
    _login(client)
    eid = _seed_effect(client)
    layer_id = client.post("/api/layers", json={"effect_id": eid}).json()[
        "layer_id"
    ]
    r = client.patch(
        f"/api/layers/{layer_id}",
        json={"opacity": 0.25, "blend_mode": "add", "mute": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["opacity"] == 0.25
    assert body["blend_mode"] == "add"
    assert body["mute"] is True


def test_reorder_layers_updates_z_index(client):
    _login(client)
    eid = _seed_effect(client)
    a = client.post("/api/layers", json={"effect_id": eid}).json()
    b = client.post("/api/layers", json={"effect_id": eid}).json()
    # Swap
    r = client.post(
        "/api/layers/reorder",
        json={
            "order": [
                {"layer_id": a["layer_id"], "z_index": b["z_index"] + 100},
                {"layer_id": b["layer_id"], "z_index": a["z_index"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    by_id = {l["layer_id"]: l for l in r.json()}
    assert by_id[a["layer_id"]]["z_index"] > by_id[b["layer_id"]]["z_index"]


def test_delete_layer_stops_engine(client):
    _login(client)
    eid = _seed_effect(client)
    layer_id = client.post("/api/layers", json={"effect_id": eid}).json()[
        "layer_id"
    ]
    r = client.delete(f"/api/layers/{layer_id}")
    assert r.status_code == 204, r.text
    listing = client.get("/api/layers").json()
    assert all(l["layer_id"] != layer_id for l in listing)


def test_legacy_play_creates_layer(client):
    _login(client)
    eid = _seed_effect(client)
    r = client.post(f"/api/effects/{eid}/play")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "layer_id" in body and isinstance(body["layer_id"], int)
    listing = client.get("/api/layers").json()
    assert any(
        l["layer_id"] == body["layer_id"] and l["effect_id"] == eid
        for l in listing
    )
    # Stop tears it down.
    r = client.post(f"/api/effects/{eid}/stop")
    assert r.status_code == 200, r.text
    listing = client.get("/api/layers").json()
    assert all(l["layer_id"] != body["layer_id"] for l in listing)


def test_clear_layers_panic_stops_everything(client):
    _login(client)
    eid = _seed_effect(client)
    client.post("/api/layers", json={"effect_id": eid})
    client.post("/api/layers", json={"effect_id": eid})
    r = client.post("/api/layers/clear")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert client.get("/api/layers").json() == []


def test_health_reports_engine_telemetry(client):
    _login(client)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "tick_hz" in body and body["tick_hz"] > 0
    assert "active_layers" in body
    assert "dropped_frames" in body
