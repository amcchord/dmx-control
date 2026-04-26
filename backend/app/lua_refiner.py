"""Sub-agent helper that repairs broken Lua effect scripts via Claude.

Used by the designer router when Claude's main "design the rig" turn
emits a custom Lua source that compiles but fails the smoke test (or
fails compile in a way the designer prompt didn't anticipate). Rather
than dropping the proposal and leaving the user confused, we kick off a
small, focused Anthropic call that does exactly one thing: take the
broken script + diagnostic and emit a corrected ``propose_effect``
tool call. Loop until the smoke test passes or the attempt budget is
exhausted.

The same retry pattern lives inside the streaming
:mod:`app.routers.effect_chat` orchestrator. This module factors the
"single-shot fix" out so the designer can reuse the smoke-test +
diagnostic-feedback loop without inheriting the chat router's SSE
plumbing or conversation state."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from .lua import ScriptError, compile_script, smoke_test_source

log = logging.getLogger(__name__)

# Prompt fragments. The Lua API description is duplicated here rather
# than imported from ``effect_chat`` so the chat router can evolve its
# wording independently without breaking the designer's refiner.
_LUA_API_REF = """\
Effect script API:

  - Define one of:
      function render(ctx)           -- per-slot pure function (default)
      function tick(ctx)             -- whole-frame; ctx.slots is a list
  - ctx fields:
      ctx.t        seconds since the effect started (number)
      ctx.i        this slot's index (0..n-1)
      ctx.n        slots in this group (number)
      ctx.frame    monotonic tick counter (integer)
      ctx.seed     deterministic seed
      ctx.params   table of script-declared knobs
      ctx.palette  palette object; helpers return THREE numbers, not a table:
                       local r, g, b = ctx.palette:smooth(phase)
                       local r, g, b = ctx.palette:step(phase)
                       local r, g, b = ctx.palette:get(i)   -- 1-indexed
      ctx.slot     { light_id = int, zone_id = string|nil }
  - render() MUST return one of these literal table shapes (NAMED keys):
        return { r = 255, g = 120, b = 0, brightness = 1.0 }
        return { active = false }   -- gap; base color shows through
    r/g/b are 0..255 integers; brightness is 0..1.
  - Globals available (sandbox: no io/os/require/load/dofile/debug):
      math, string, table
      color.hsv(h, s, v) -> r, g, b (three numbers, not a table)
      color.hex("#RRGGBB") -> r, g, b
      color.mix(r1, g1, b1, r2, g2, b2, w) -> r, g, b
      envelope.pulse / envelope.wave / envelope.chase / envelope.strobe
      direction.apply(phase, dir, cycles_done)
      per_index_offset(slider, n)
      noise.hash(...) -> [0,1)
      noise.simplex(x, y) -> [0,1)
      easing.linear/quad_in/quad_out/quad_inout/cosine
  - PARAMS table (top of script) declares the knobs:
      { id="speed_hz", label="Speed", type="number", min=0, max=10,
        default=1.0, suffix="Hz" }
"""

_REFINER_SYSTEM = (
    "You are fixing a single broken Lua effect script for a sandboxed "
    "DMX engine. The user supplies the existing source, the params it "
    "was authored against, and the runtime diagnostic that proved it "
    "broken. Return ONE propose_effect tool call with a corrected "
    "``source`` (and updated ``params`` if a knob needs to change to "
    "satisfy the fix). Do NOT change the effect's intent, name, "
    "spread, palette_id, or target_channels unless the fix requires "
    "it.\n\n"
    + _LUA_API_REF
)

# Max attempts (initial + retries). Three keeps the user-facing latency
# bounded while still letting Claude correct cascading errors (e.g. a
# nil index that uncovers a follow-on type error on the next pass).
_DEFAULT_MAX_ATTEMPTS = 3


def _build_refiner_tool() -> dict[str, Any]:
    return {
        "name": "propose_effect",
        "description": (
            "Emit the corrected Lua effect. ``source`` is required and "
            "must compile + run cleanly across several slots and "
            "timesteps."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "proposal_id": {"type": "string"},
                "name": {"type": "string"},
                "source": {"type": "string"},
                "params": {"type": "object"},
                "summary": {"type": "string"},
            },
            "required": ["proposal_id", "name", "source"],
        },
    }


def _format_diagnostic(err: ScriptError) -> str:
    """Mirror the chat router's diagnostic format so Claude's been
    primed on the same wording across both code paths."""
    where = f" (line {err.line})" if err.line is not None else ""
    hints: list[str] = []
    msg = (err.message or "").lower()
    if "index a number" in msg or "index a nil" in msg:
        hints.append(
            "Hint: ctx.palette:smooth/:step/:get and color.hsv/color.hex "
            "all return THREE numbers (r, g, b), not a table. Always "
            "use ``local r, g, b = ctx.palette:smooth(p)``."
        )
    if "attempt to call" in msg:
        hints.append(
            "Hint: only the helpers documented in the API are "
            "available. io / os / require / load / dofile / debug / "
            "package are intentionally absent."
        )
    if "must define" in msg:
        hints.append(
            "Hint: the script must define either ``function "
            "render(ctx)`` or ``function tick(ctx)`` at module scope."
        )
    if "no value" in msg or "nil value" in msg:
        hints.append(
            "Hint: declare locals before use; ctx.params returns nil "
            "for missing keys. Coalesce with ``or DEFAULT``."
        )
    if "active = true" in msg or "never returned" in msg:
        hints.append(
            "Hint: every slot's render() result MUST include "
            "``active = true`` along with r/g/b/brightness, otherwise "
            "the engine treats the slot as a transparent gap."
        )
    if "ctx.t" in msg or "ctx.i" in msg or "ctx.n" in msg or (
        "always returned zero" in msg
    ):
        hints.append(
            "Hint: the correct ctx field names are ``ctx.t`` "
            "(seconds), ``ctx.i`` (slot index, 0..n-1), ``ctx.n`` "
            "(slot count). NOT ctx.time_s / ctx.index / ctx.count. "
            "r/g/b are 0..255 integers; if your math produces 0..1 "
            "floats, multiply by 255 before returning."
        )
    if not hints:
        hints.append(
            "Hint: render() must return ``{ r=NUMBER, g=NUMBER, "
            "b=NUMBER, brightness=NUMBER, active=true }`` or "
            "``{ active=false }``. r/g/b are 0..255 ints; brightness "
            "is 0..1."
        )
    return (
        f"Runtime error{where}: {err.message}\n\n"
        + "\n".join(hints)
        + "\n\nReturn a corrected propose_effect tool call. Keep the "
        "name, summary, and any structural decisions intact unless the "
        "fix demands changing them."
    )


@dataclass
class RefineRequest:
    """Inputs for one refinement run."""

    proposal_id: str
    name: str
    source: str
    params: dict[str, Any]
    summary: Optional[str] = None
    palette_colors: Optional[list[str]] = None


@dataclass
class RefineResult:
    """Outcome of a refinement run.

    ``ok`` is true when the (possibly Claude-fixed) source passes the
    smoke test. ``attempts`` counts every Anthropic round-trip we made
    (zero if the original passed straight away). ``error`` is the last
    smoke-test diagnostic when ``ok`` is false."""

    ok: bool
    source: str
    params: dict[str, Any]
    attempts: int
    error: Optional[ScriptError]


def refine_lua_source(
    req: RefineRequest,
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> RefineResult:
    """Smoke-test the supplied Lua source; if it fails, ask Claude to
    fix it via a single ``propose_effect`` tool call.

    Always tries the smoke test first so a healthy script costs zero
    Claude calls. When the smoke test fails:

    1. If ``ANTHROPIC_API_KEY`` is empty (designer disabled in this
       environment) we fail fast and return the original source plus
       the diagnostic so the caller can decide whether to drop the
       proposal.
    2. Otherwise loop up to ``max_attempts`` times: each attempt sends
       the broken source + diagnostic to Claude, smoke-tests Claude's
       reply, and either returns success or feeds the new diagnostic
       into the next attempt's user message.
    """
    err = smoke_test_source(
        req.source,
        params=dict(req.params or {}),
        palette_colors=req.palette_colors or ["#FFFFFF"],
    )
    if err is None:
        return RefineResult(
            ok=True, source=req.source, params=dict(req.params or {}),
            attempts=0, error=None,
        )

    if not ANTHROPIC_API_KEY:
        return RefineResult(
            ok=False, source=req.source, params=dict(req.params or {}),
            attempts=0, error=err,
        )

    # Lazy import: keeps app start-up fast in environments without the
    # anthropic package installed (e.g. minimal CI).
    try:
        import anthropic  # type: ignore
    except Exception:
        log.debug("anthropic package not installed; skipping refiner")
        return RefineResult(
            ok=False, source=req.source, params=dict(req.params or {}),
            attempts=0, error=err,
        )

    tool = _build_refiner_tool()
    current_source = req.source
    current_params = dict(req.params or {})
    last_err = err
    attempts = 0
    user_text = (
        f"Effect name: {req.name}\n"
        f"Summary: {req.summary or '(no summary)'}\n"
        f"params: {json.dumps(current_params)}\n\n"
        f"Current source:\n```lua\n{current_source}\n```\n\n"
        f"{_format_diagnostic(err)}"
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    history: list[dict[str, Any]] = [
        {"role": "user", "content": user_text}
    ]
    for _ in range(max_attempts):
        attempts += 1
        try:
            msg = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4096,
                system=_REFINER_SYSTEM,
                tools=[tool],
                tool_choice={"type": "tool", "name": "propose_effect"},
                messages=history,
            )
        except Exception as exc:  # network / rate limit / etc.
            log.warning("lua refiner Anthropic call failed: %s", exc)
            return RefineResult(
                ok=False, source=current_source, params=current_params,
                attempts=attempts, error=last_err,
            )

        tool_blocks = [
            blk for blk in (getattr(msg, "content", None) or [])
            if getattr(blk, "type", None) == "tool_use"
            and getattr(blk, "name", None) == "propose_effect"
        ]
        if not tool_blocks:
            return RefineResult(
                ok=False, source=current_source, params=current_params,
                attempts=attempts, error=last_err,
            )
        inp = getattr(tool_blocks[0], "input", None) or {}
        new_source = inp.get("source")
        if not isinstance(new_source, str) or not new_source.strip():
            return RefineResult(
                ok=False, source=current_source, params=current_params,
                attempts=attempts, error=last_err,
            )
        new_params_raw = inp.get("params")
        if isinstance(new_params_raw, dict):
            current_params = {
                k: v for k, v in new_params_raw.items()
                if isinstance(k, str)
            }

        # Pre-check: does it even compile? (smoke_test_source compiles
        # internally too but checking here gives us a better error.)
        try:
            compile_script(new_source, chunkname="=refiner")
        except ScriptError as ce:
            last_err = ce
            current_source = new_source
            history.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": getattr(tool_blocks[0], "id", "")
                        or "tool_refine",
                        "name": "propose_effect",
                        "input": dict(inp),
                    }
                ],
            })
            history.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(tool_blocks[0], "id", "")
                        or "tool_refine",
                        "content": _format_diagnostic(ce),
                        "is_error": True,
                    }
                ],
            })
            continue

        smoke_err = smoke_test_source(
            new_source,
            params=current_params,
            palette_colors=req.palette_colors or ["#FFFFFF"],
        )
        if smoke_err is None:
            return RefineResult(
                ok=True,
                source=new_source,
                params=current_params,
                attempts=attempts,
                error=None,
            )
        last_err = smoke_err
        current_source = new_source
        # Feed the diagnostic back through a synthetic tool-result so
        # the next attempt has the same shape Anthropic's tools API
        # expects after a tool_use.
        history.append({
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": getattr(tool_blocks[0], "id", "")
                    or "tool_refine",
                    "name": "propose_effect",
                    "input": dict(inp),
                }
            ],
        })
        history.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(tool_blocks[0], "id", "")
                    or "tool_refine",
                    "content": _format_diagnostic(smoke_err),
                    "is_error": True,
                }
            ],
        })

    return RefineResult(
        ok=False, source=current_source, params=current_params,
        attempts=attempts, error=last_err,
    )
