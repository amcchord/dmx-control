"""Tests for the Lua refiner sub-agent.

The refiner is invoked from the designer router whenever Claude emits
a custom Lua source that fails the smoke test. We don't want every test
to spend tokens on the real Anthropic API, so the Anthropic client is
stubbed via ``sys.modules`` (mirroring the pattern in
``test_palette_generate``) and we drive the loop with hand-baked
"propose_effect" tool blocks.

What's covered:

* Healthy script → ``refine_lua_source`` returns immediately with zero
  attempts (no Anthropic call made).
* Broken script + Claude returns a clean fix on attempt 1 → ``ok=True``
  and the refined source is the one we baked into the stub.
* Broken script + Claude keeps emitting broken fixes → after the
  attempt budget runs out, ``ok=False`` and the diagnostic is the
  final smoke-test error.
* Refiner sees no ``ANTHROPIC_API_KEY`` → it falls back to "smoke test
  only" semantics (no Claude call, original source returned with
  ``ok=False``).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


HEALTHY_SOURCE = """\
NAME = "Healthy"
function render(ctx)
  return { r = 100, g = 50, b = 200, brightness = 1.0 }
end
"""

BROKEN_SOURCE = """\
NAME = "Bad"
function render(ctx)
  -- ctx.palette:smooth returns three numbers, not a table; this
  -- attempts to index a number and blows up at runtime.
  local c = ctx.palette:smooth(0.5)
  return { r = c.r, g = c.g, b = c.b, brightness = 1.0 }
end
"""

# Compiles + runs cleanly but always emits zero output: a script that
# reads from nonexistent ctx fields. Pre-tightening, this snuck through
# the smoke test because it doesn't error — it just silently produces
# black. The new smoke check rejects it.
SILENTLY_BLACK_SOURCE = """\
NAME = "SilentBlack"
function render(ctx)
  -- Wrong field name: ctx.time_s doesn't exist, falls back to 0.
  local t = ctx.time_s or 0
  return { r = t, g = 0, b = 0 }
end
"""


def _install_fake_anthropic(scripts: list[str]) -> list[dict[str, Any]]:
    """Stub ``anthropic.Anthropic`` so each ``messages.create`` call
    returns the next entry in ``scripts`` as a propose_effect tool_use.

    Returns a list that captures the message history each call sees
    so tests can assert the diagnostic-feedback loop is wiring tool
    results correctly."""
    seen_calls: list[dict[str, Any]] = []
    iterator = iter(scripts)

    class _Block:
        def __init__(self, btype: str, name: str, inp: dict, _id: str = "tu1") -> None:
            self.type = btype
            self.name = name
            self.input = inp
            self.id = _id

    class _Msg:
        def __init__(self, blocks: list[_Block]) -> None:
            self.content = blocks

    class _Messages:
        def create(self, **kwargs: Any) -> _Msg:
            seen_calls.append(kwargs)
            try:
                src = next(iterator)
            except StopIteration:
                # Exhausted — return an empty message so the caller
                # bails out with ok=False.
                return _Msg([])
            inp = {
                "proposal_id": "p1",
                "name": "Refined",
                "source": src,
                "params": {},
            }
            return _Msg([_Block("tool_use", "propose_effect", inp)])

    class _Client:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake
    return seen_calls


@pytest.fixture
def with_api_key(monkeypatch):
    monkeypatch.setattr("app.lua_refiner.ANTHROPIC_API_KEY", "test-key")


def test_healthy_source_skips_anthropic(with_api_key):
    """Smoke test passes ⇒ no Claude call at all."""
    seen = _install_fake_anthropic(scripts=[])
    from app.lua_refiner import RefineRequest, refine_lua_source

    res = refine_lua_source(
        RefineRequest(
            proposal_id="p1",
            name="Healthy",
            source=HEALTHY_SOURCE,
            params={},
        )
    )
    assert res.ok is True
    assert res.attempts == 0
    assert res.error is None
    assert seen == []


def test_broken_source_fixed_on_first_attempt(with_api_key):
    seen = _install_fake_anthropic(scripts=[HEALTHY_SOURCE])
    from app.lua_refiner import RefineRequest, refine_lua_source

    res = refine_lua_source(
        RefineRequest(
            proposal_id="p1",
            name="Bad",
            source=BROKEN_SOURCE,
            params={},
        ),
        max_attempts=3,
    )
    assert res.ok is True
    assert res.attempts == 1
    assert res.source == HEALTHY_SOURCE
    assert len(seen) == 1
    # The first call's user message should mention the runtime error so
    # Claude has a fighting chance at fixing it.
    msgs = seen[0]["messages"]
    assert msgs[0]["role"] == "user"
    assert "Runtime error" in msgs[0]["content"]


def test_broken_source_persistently_broken_returns_failure(with_api_key):
    """When Claude keeps emitting bad scripts, the refiner exhausts the
    attempt budget and reports the last diagnostic to the caller."""
    seen = _install_fake_anthropic(scripts=[BROKEN_SOURCE, BROKEN_SOURCE, BROKEN_SOURCE])
    from app.lua_refiner import RefineRequest, refine_lua_source

    res = refine_lua_source(
        RefineRequest(
            proposal_id="p1",
            name="Bad",
            source=BROKEN_SOURCE,
            params={},
        ),
        max_attempts=3,
    )
    assert res.ok is False
    assert res.attempts == 3
    assert res.error is not None
    # Each follow-up attempt should have grown the message history with
    # a tool_use + tool_result pair so Claude sees the diagnostic.
    assert len(seen) == 3
    final_msgs = seen[-1]["messages"]
    # First user msg + 2 prior (assistant tool_use, user tool_result) per
    # retry = at least 5 messages on the third call.
    assert len(final_msgs) >= 5
    # The last user message must carry a tool_result block.
    last = final_msgs[-1]
    assert last["role"] == "user"
    assert any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        for b in last["content"]
    )


def test_silently_black_source_is_caught_and_refined(with_api_key):
    """A script that compiles + runs without errors but always returns
    zeros (e.g. wrong ctx field names) used to slip past the smoke
    test. The smoke test now flags these as failures so the refiner
    can repair them — exactly the failure mode we hit with Claude's
    'kaleido strobe' that read ``ctx.time_s`` instead of ``ctx.t``."""
    seen = _install_fake_anthropic(scripts=[HEALTHY_SOURCE])
    from app.lua_refiner import RefineRequest, refine_lua_source

    res = refine_lua_source(
        RefineRequest(
            proposal_id="p1",
            name="SilentBlack",
            source=SILENTLY_BLACK_SOURCE,
            params={},
        ),
        max_attempts=2,
    )
    assert res.ok is True
    assert res.attempts == 1
    # The diagnostic Claude saw should mention the actual error so it
    # has a fighting chance at the fix.
    user_msg = seen[0]["messages"][0]["content"]
    assert "active" in user_msg.lower() or "zero" in user_msg.lower()


def test_smoke_test_flags_silent_black_source():
    """Direct-API check on the smoke test itself, independent of the
    refiner. This is the regression that prevents the kaleido-strobe
    bug from re-occurring."""
    from app.lua import smoke_test_source

    err = smoke_test_source(SILENTLY_BLACK_SOURCE)
    assert err is not None
    # Either of the two new diagnostics is acceptable depending on
    # whether the script returned no ``active`` or returned only zeros.
    assert (
        "active" in err.message.lower()
        or "always returned zero" in err.message.lower()
    )


def test_no_api_key_returns_failure_without_calling_claude(monkeypatch):
    """Without credentials, the refiner reports the smoke-test error
    without attempting a Claude call. The designer can then decide to
    drop the proposal."""
    monkeypatch.setattr("app.lua_refiner.ANTHROPIC_API_KEY", "")
    sys.modules.pop("anthropic", None)
    from app.lua_refiner import RefineRequest, refine_lua_source

    res = refine_lua_source(
        RefineRequest(
            proposal_id="p1",
            name="Bad",
            source=BROKEN_SOURCE,
            params={},
        ),
    )
    assert res.ok is False
    assert res.attempts == 0
    assert res.error is not None
