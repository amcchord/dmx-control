"""Tests for ``POST /api/palettes/generate`` with a mocked Anthropic client.

We stub the ``anthropic`` module in ``sys.modules`` so the router's
``import anthropic`` inside :func:`generate_palette` resolves to our
fake, which returns a pre-baked ``tool_use`` block. The endpoint does
not persist anything, so we can keep the app's real DB out of the way.
"""

from __future__ import annotations

import sys
import types

import pytest


def _install_fake_anthropic(tool_input: dict, *, raise_status: bool = False) -> None:
    class _Block:
        def __init__(self, btype: str, name: str, inp: dict) -> None:
            self.type = btype
            self.name = name
            self.input = inp

    class _Message:
        def __init__(self, blocks):
            self.content = blocks

    class _APIStatusError(Exception):
        def __init__(self, message: str) -> None:
            self.message = message
            super().__init__(message)

    class _Messages:
        def create(self, **_kwargs):
            if raise_status:
                raise _APIStatusError("rate limited")
            return _Message(
                [_Block("tool_use", "propose_palette", tool_input)]
            )

    class _Client:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client  # type: ignore[attr-defined]
    fake.APIStatusError = _APIStatusError  # type: ignore[attr-defined]
    fake.APIError = _APIStatusError  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake


@pytest.fixture
def client(monkeypatch):
    """Build a TestClient with auth disabled and Claude key populated."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # The config module captured the env at first import; patch the
    # constant directly so the router sees a non-empty key.
    from app import config as cfg

    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "test-key", raising=True)

    from app.main import app
    from app.auth import require_auth

    async def _no_auth():
        return None

    app.dependency_overrides[require_auth] = _no_auth

    from fastapi.testclient import TestClient

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_generate_palette_returns_sanitized_entries(client):
    _install_fake_anthropic(
        {
            "name": "Moody Teal",
            "summary": "Cold teal + magenta accents.",
            "entries": [
                {"r": 0, "g": 60, "b": 80},
                {"r": 0, "g": 180, "b": 200, "w": 120},
                {"r": 220, "g": 30, "b": 120, "uv": 200},
            ],
        }
    )
    resp = client.post(
        "/api/palettes/generate",
        json={"prompt": "moody teal palette"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Moody Teal"
    assert body["summary"].startswith("Cold teal")
    assert len(body["entries"]) == 3
    # Explicit aux values survive.
    assert body["entries"][1]["w"] == 120
    assert body["entries"][2]["uv"] == 200


def test_generate_palette_filters_invalid_entries(client):
    _install_fake_anthropic(
        {
            "name": "Mixed Quality",
            "entries": [
                # Clearly invalid ``r`` field — Pydantic drops this one.
                {"r": "no", "g": 0, "b": 0},
                {"r": 10, "g": 20, "b": 30},
                {"r": 30, "g": 40, "b": 50, "uv": 111},
            ],
        }
    )
    resp = client.post(
        "/api/palettes/generate",
        json={"prompt": "test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["entries"]) == 2
    assert body["entries"][0]["r"] == 10
    assert body["entries"][1]["uv"] == 111


def test_generate_palette_rejects_empty_entries(client):
    _install_fake_anthropic({"name": "Empty", "entries": []})
    resp = client.post(
        "/api/palettes/generate",
        json={"prompt": "test"},
    )
    # No valid entries -> 502 (router guard against empty tool payloads).
    assert resp.status_code == 502


def test_generate_palette_requires_api_key(monkeypatch):
    """When ANTHROPIC_API_KEY is empty we return 503 without invoking Claude."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app import config as cfg

    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "", raising=True)
    from app.main import app
    from app.auth import require_auth
    from fastapi.testclient import TestClient

    async def _no_auth():
        return None

    app.dependency_overrides[require_auth] = _no_auth
    try:
        c = TestClient(app)
        resp = c.post(
            "/api/palettes/generate",
            json={"prompt": "test"},
        )
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.pop(require_auth, None)
