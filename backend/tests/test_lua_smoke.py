"""Tests for ``smoke_test_source`` and the lenient render-result coercion.

These guard against the regression caused by
``Warm Flickering Fire``, where Claude wrote a script that compiled
cleanly but blew up at runtime (``attempt to index a number value`` on
the ``ctx.palette:smooth`` return) and we surfaced it to the user as a
black preview. The smoke test now catches that class of bug before the
proposal ever leaves the backend.
"""

from __future__ import annotations

import pytest


def test_smoke_test_passes_on_a_correct_script():
    from app.lua import smoke_test_source

    src = """
PARAMS = { { id='speed_hz', type='number', min=0, max=10, default=1, label='Speed' } }
function render(ctx)
  local r, g, b = ctx.palette:smooth(ctx.t * (ctx.params.speed_hz or 1))
  return { r = r, g = g, b = b, brightness = 1.0 }
end
"""
    assert smoke_test_source(src) is None


def test_smoke_test_catches_palette_indexed_as_table():
    """Reproduces the Warm Flickering Fire crash: Claude indexed the
    return of ``palette:smooth`` as if it were a table."""
    from app.lua import smoke_test_source

    src = """
function render(ctx)
  local col = ctx.palette:smooth(0.5)
  return { r = col.r, g = col.g, b = col.b, brightness = 1.0 }
end
"""
    err = smoke_test_source(src)
    assert err is not None
    assert "index a number" in err.message


def test_smoke_test_catches_compile_errors():
    from app.lua import smoke_test_source

    src = "function render(ctx) :::: end"
    err = smoke_test_source(src)
    assert err is not None


def test_smoke_test_runs_with_real_palette_colors():
    from app.lua import smoke_test_source

    src = """
function render(ctx)
  local r, g, b = ctx.palette:get(1)
  return { r = r, g = g, b = b }
end
"""
    err = smoke_test_source(
        src, palette_colors=["#FF0000", "#00FF00", "#0000FF"]
    )
    assert err is None


def test_render_accepts_positional_rgb():
    """Mirrors what Claude wrote: ``return { r, g, b, brightness = b }``
    where r/g/b end up in slots [1]/[2]/[3] alongside the named brightness.
    The coerce should still extract the correct color so the user gets
    a useful preview even when the script returns a mixed table."""
    from app.lua import compile_script

    src = """
function render(ctx)
  return { 200, 100, 50, brightness = 0.75 }
end
"""
    s = compile_script(src)
    pal = s.make_palette([(255, 0, 0)])
    ctx = s.new_table()
    ctx["t"] = 0
    ctx["i"] = 0
    ctx["n"] = 1
    ctx["seed"] = 1
    ctx["palette"] = pal
    ctx["params"] = s.new_table()
    out = s.render_slot(ctx)
    assert out["active"] is True
    assert out["r"] == 200
    assert out["g"] == 100
    assert out["b"] == 50
    assert out["brightness"] == pytest.approx(0.75)
