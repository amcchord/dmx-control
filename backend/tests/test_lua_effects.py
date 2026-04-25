"""Tests for the Lua effect runtime + builtins + lint endpoint.

These exercise the sandbox (no io/os/require), the per-call instruction
budget, the param-schema parser, every shipped builtin, and the
``POST /api/effects/lint`` route.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Direct runtime tests (no FastAPI / DB)
# ---------------------------------------------------------------------------
def _compile(src: str):
    from app.lua import compile_script
    return compile_script(src)


def _ctx_for(script, *, n: int = 8, t: float = 0.5, i: int = 0) -> object:
    pal = script.make_palette([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    ctx = script.new_table()
    ctx["t"] = t
    ctx["i"] = i
    ctx["n"] = n
    ctx["frame"] = int(t * 30)
    ctx["seed"] = 1
    ctx["palette"] = pal
    params = script.new_table()
    for entry in script.meta.param_schema:
        params[entry["id"]] = entry.get("default", 0)
    ctx["params"] = params
    slot = script.new_table()
    slot["light_id"] = i + 1
    slot["zone_id"] = None
    ctx["slot"] = slot
    return ctx


def test_compile_extracts_metadata():
    s = _compile(
        """
NAME = "Demo"
DESCRIPTION = "Just a demo"
PARAMS = {
  { id = 'speed_hz', label = 'Speed', type = 'number', min = 0, max = 10, default = 1.5, suffix = 'Hz' },
  { id = 'mode', label = 'Mode', type = 'choice', options = {'a', 'b'}, default = 'b' },
}
function render(ctx)
  return { r = 255, g = 0, b = 0, brightness = 1.0 }
end
"""
    )
    assert s.meta.name == "Demo"
    assert s.meta.description == "Just a demo"
    assert s.has_render and not s.has_tick
    schema = s.meta.param_schema
    assert {entry["id"] for entry in schema} == {"speed_hz", "mode"}
    speed = next(e for e in schema if e["id"] == "speed_hz")
    assert speed["min"] == 0 and speed["max"] == 10 and speed["default"] == 1.5
    mode = next(e for e in schema if e["id"] == "mode")
    assert mode["options"] == ["a", "b"] and mode["default"] == "b"


def test_render_returns_clamped_rgb():
    s = _compile(
        "function render(ctx) return { r = 999, g = -10, b = 200, brightness = 1 } end"
    )
    out = s.render_slot(_ctx_for(s))
    assert out == {"active": True, "r": 255, "g": 0, "b": 200, "brightness": 1.0}


def test_render_inactive_when_active_false():
    s = _compile(
        "function render(ctx) return { active = false } end"
    )
    out = s.render_slot(_ctx_for(s))
    assert out["active"] is False


def test_script_must_define_render_or_tick():
    from app.lua import ScriptError
    with pytest.raises(ScriptError, match="must define render"):
        _compile("NAME = 'x'")


def test_syntax_error_carries_line():
    from app.lua import ScriptError
    with pytest.raises(ScriptError) as exc:
        _compile("function render(ctx) return  end\nlocal y = oops :: bad")
    assert exc.value.line == 2


@pytest.mark.parametrize(
    "snippet",
    [
        "io.open('/etc/passwd', 'r')",
        "os.execute('ls')",
        "package.loadlib('a', 'b')",
        "debug.getinfo(1)",
        "load('return 1')()",
        "loadstring('return 1')()",
        "dofile('/etc/passwd')",
        "loadfile('/etc/passwd')",
        "require('os')",
    ],
)
def test_sandbox_blocks_dangerous_globals(snippet: str):
    """Each of these should raise a ScriptError at runtime."""
    from app.lua import ScriptError
    s = _compile(f"function render(ctx)\n  {snippet}\n  return {{ r=255 }}\nend")
    with pytest.raises(ScriptError):
        s.render_slot(_ctx_for(s))


def test_runaway_loop_is_interrupted_by_budget():
    from app.lua import ScriptError
    s = _compile(
        """
function render(ctx)
  while true do end
  return { r = 0, g = 0, b = 0 }
end
"""
    )
    with pytest.raises(ScriptError, match="budget"):
        s.render_slot(_ctx_for(s))


def test_palette_helpers_return_expected_colors():
    s = _compile(
        """
function render(ctx)
  local r, g, b = ctx.palette:smooth(0.5)
  return { r = r, g = g, b = b }
end
"""
    )
    out = s.render_slot(_ctx_for(s))
    assert out["active"]
    # Mid-stop interpolation between green and blue should be a teal-ish.
    assert out["r"] == 0
    assert 100 < out["g"] < 200
    assert 50 < out["b"] < 200


def test_color_hsv_helper():
    s = _compile(
        """
function render(ctx)
  local r, g, b = color.hsv(0.0, 1.0, 1.0)
  return { r = r, g = g, b = b }
end
"""
    )
    out = s.render_slot(_ctx_for(s))
    assert (out["r"], out["g"], out["b"]) == (255, 0, 0)


def test_noise_hash_is_deterministic():
    s = _compile(
        """
function render(ctx)
  local h = noise.hash('a', 'b', ctx.i)
  return { r = math.floor(h * 255), g = 0, b = 0 }
end
"""
    )
    a = s.render_slot(_ctx_for(s, i=1))
    b = s.render_slot(_ctx_for(s, i=1))
    assert a == b
    c = s.render_slot(_ctx_for(s, i=2))
    assert c != a


# ---------------------------------------------------------------------------
# Builtin scripts: each compiles + renders something for at least one slot
# ---------------------------------------------------------------------------
def test_every_builtin_compiles_and_runs():
    from app.lua import builtin_sources, compile_script

    sources = builtin_sources()
    assert sources, "no builtin Lua scripts found"
    for name, src in sources.items():
        s = compile_script(src, chunkname=f"={name}")
        assert s.meta.name, f"builtin {name} missing NAME"
        # Run for several slots over time so chase / strobe / sparkle each
        # have a chance to be active.
        any_active = False
        for t in (0.0, 0.25, 0.5, 0.9, 1.5):
            for i in range(8):
                ctx = _ctx_for(s, n=8, t=t, i=i)
                out = s.render_slot(ctx)
                if out.get("active"):
                    any_active = True
                    break
            if any_active:
                break
        assert any_active, f"builtin {name} never produced an active frame"


def test_chase_uses_palette_and_envelope():
    from app.lua import builtin_sources, compile_script

    src = builtin_sources()["chase"]
    s = compile_script(src, chunkname="=chase")
    # Sweep one full cycle and make sure brightness > 0 somewhere.
    n = 8
    saw_lit = False
    for frame in range(60):
        ctx = _ctx_for(s, n=n, t=frame / 30.0, i=0)
        out = s.render_slot(ctx)
        if out.get("active") and out.get("brightness", 0) > 0:
            saw_lit = True
            break
    assert saw_lit


# ---------------------------------------------------------------------------
# FastAPI lint endpoint
# ---------------------------------------------------------------------------
@pytest.fixture
def app_client():
    """TestClient with auth disabled so AuthDep-protected routes accept us."""
    from fastapi.testclient import TestClient

    from app.auth import require_auth
    from app.main import app

    async def _no_auth():
        return None

    app.dependency_overrides[require_auth] = _no_auth
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_lint_endpoint_ok(app_client):
    src = """
NAME = "T"
PARAMS = { { id = 'speed', type = 'number', min = 0, max = 10, default = 1 } }
function render(ctx) return { r = 0, g = 0, b = 0 } end
"""
    r = app_client.post("/api/effects/lint", json={"source": src})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "T"
    assert body["has_render"] is True
    assert body["has_tick"] is False
    assert any(p["id"] == "speed" for p in body["param_schema"])


def test_lint_endpoint_reports_syntax_error(app_client):
    r = app_client.post(
        "/api/effects/lint", json={"source": "function render(ctx) ::: end"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error"] is not None
    assert "message" in body["error"]


def test_lint_endpoint_rejects_no_render(app_client):
    r = app_client.post(
        "/api/effects/lint", json={"source": "NAME = 'x'\n"}
    )
    body = r.json()
    assert body["ok"] is False
    assert "render" in (body["error"] or {}).get("message", "")


def test_lint_endpoint_rejects_oversize_source(app_client):
    huge = "-- " + ("x" * 100000)
    r = app_client.post("/api/effects/lint", json={"source": huge})
    # The schema rejects oversize sources at the validator boundary.
    assert r.status_code == 422
