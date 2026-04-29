"""Multi-turn Claude chat for iterating on effect Lua scripts.

Each turn forces a single ``propose_effect`` tool call that emits one
proposal containing a Lua source string + a default ``params`` dict +
spread / palette / target channels. The client loads the proposal into
the editor; the user says "faster, tighten the window", Claude revises,
repeat.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..auth import AuthDep
from ..config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from ..db import get_session
from ..engine import (
    engine as effect_engine,
    play_transient_layer,
)
from ..lua import (
    LuaScript,
    ScriptError,
    builtin_sources,
    compile_script,
    get_builtin_source,
    smoke_test_source,
)
from ..lua.runtime import merge_with_schema
from ..models import Effect, EffectConversation, EffectLayer, Palette
from ..rig_context import build_rig_context
from ..schemas import (
    DesignerCritique,
    DesignerCritiqueRequest,
    DesignerCritiqueResponse,
    EFFECT_FADE_MAX_S,
    EFFECT_TARGET_CHANNELS,
    EffectChatMessageOut,
    EffectControls,
    EffectConversationCreate,
    EffectConversationOut,
    EffectConversationRename,
    EffectConversationSummary,
    EffectMessageIn,
    EffectProposal,
)
from .designer import (
    _last_user_text_from_history as _last_user_text_from_history,
    _run_verifier as _run_verifier,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/effect-chat",
    tags=["effect-chat"],
    dependencies=[AuthDep],
)


_TOOL_NAME = "propose_effect"
_MAX_TURNS_HISTORY = 40
# How many times to round-trip with Claude per chat turn when the first
# script smoke-tests fail. Includes the initial try, so 3 = original +
# up to two retries with the runtime error fed back as a tool_result.
_MAX_ATTEMPTS = 3


def _build_tool_schema() -> dict[str, Any]:
    builtin_names = sorted(builtin_sources().keys())
    return {
        "name": _TOOL_NAME,
        "description": (
            "Return one animated effect that matches the user's latest "
            "ask. Provide either ``builtin`` (a known builtin script "
            "name like 'chase' or 'pulse', recommended for the common "
            "cases) OR a custom ``source`` (raw Lua). Override knobs "
            "via ``params`` keyed by the script's PARAMS ids."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "One-line plain-language explanation visible to "
                        "the user above the loaded effect."
                    ),
                },
                "proposal_id": {"type": "string"},
                "name": {
                    "type": "string",
                    "description": "Short human name (1-4 words).",
                },
                "builtin": {
                    "type": "string",
                    "enum": builtin_names,
                    "description": (
                        "Builtin Lua script name. Mutually exclusive "
                        "with ``source``."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Custom Lua script. Must define render(ctx) or "
                        "tick(ctx). Use the helpers documented in the "
                        "system prompt; do NOT use io/os/require."
                    ),
                },
                "palette_id": {"type": "integer"},
                "spread": {
                    "type": "string",
                    "enum": [
                        "across_lights",
                        "across_fixture",
                        "across_zones",
                    ],
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Free-form param overrides keyed by the "
                        "script's PARAMS ids."
                    ),
                },
                "controls": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "intensity": {
                            "type": "number",
                            "minimum": 0, "maximum": 1,
                        },
                        "fade_in_s": {
                            "type": "number",
                            "minimum": 0, "maximum": EFFECT_FADE_MAX_S,
                        },
                        "fade_out_s": {
                            "type": "number",
                            "minimum": 0, "maximum": EFFECT_FADE_MAX_S,
                        },
                    },
                },
                "target_channels": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": sorted(EFFECT_TARGET_CHANNELS),
                    },
                    "description": (
                        "Default ['rgb']. Use ['w'] to chase only the "
                        "white LED, ['uv'] for UV accents, or ['strobe'] "
                        "to animate the fixture's strobe channel."
                    ),
                },
            },
            "required": ["proposal_id", "name"],
        },
    }


_LUA_API = """\
Effect script API:

  - Define one of:
      function render(ctx)           -- per-slot pure function (default)
      function tick(ctx)             -- whole-frame; ctx.slots is a list
  - ctx fields available in render:
      ctx.t        seconds since the effect started (number)
      ctx.i        this slot's index (0..n-1)
      ctx.n        slots in this group (number)
      ctx.frame    monotonic tick counter (integer)
      ctx.seed     deterministic seed unique per running effect
      ctx.params   table of script-declared knobs (see PARAMS)
      ctx.palette  palette object. IMPORTANT: helpers return THREE numbers,
                   not a table. Use:
                       local r, g, b = ctx.palette:smooth(phase)
                       local r, g, b = ctx.palette:step(phase)
                       local r, g, b = ctx.palette:get(i)   -- 1-indexed
                   ctx.palette:size() returns the entry count.
      ctx.slot     { light_id = int, zone_id = string|nil }
  - render() MUST return one of these literal table shapes (the keys must
    be NAMED, not positional, so use r=, g=, b=, brightness=):
        return { r = 255, g = 120, b = 0, brightness = 1.0 }
        return { active = false }   -- gap; let base color show through
    r/g/b are 0..255 integers; brightness is 0..1. Returning
    ``{ r, g, b }`` (positional) silently drops the values; always use
    named keys.
  - Globals available (sandbox is otherwise locked: no io/os/require):
      math, string, table
      color.hsv(h, s, v) -> r, g, b (three numbers, not a table)
      color.hex("#RRGGBB") -> r, g, b
      color.mix(r1, g1, b1, r2, g2, b2, w) -> r, g, b
      envelope.pulse / envelope.wave / envelope.chase / envelope.strobe
      direction.apply(phase, dir, cycles_done)
      per_index_offset(slider, n)        -- 1.0 == "perfect chase"
      noise.hash(...) -> [0,1)            -- deterministic
      noise.simplex(x, y) -> [0,1)        -- value-noise approximation
      easing.linear/quad_in/quad_out/quad_inout/cosine
  - PARAMS table (top of script) declares the knobs the UI should expose.
    Each entry: { id="speed_hz", label="Speed", type="number"|"choice"|
    "bool"|"color", min=, max=, default=, step=, suffix=, options={} }.
    The user's saved overrides arrive in ctx.params.

  Minimal correct example:
      PARAMS = {
        { id="speed_hz", label="Speed", type="number", min=0, max=10, default=1.0, suffix="Hz" },
      }
      function render(ctx)
        local phase = (ctx.t * (ctx.params.speed_hz or 0) + ctx.i / ctx.n) % 1
        local r, g, b = ctx.palette:smooth(phase)
        return { r = r, g = g, b = b, brightness = 1.0 }
      end
"""


_SYSTEM_INTRO = (
    "You are a lighting designer who iteratively refines animated DMX "
    "effects expressed as sandboxed Lua scripts. The user says what "
    "they want (\"warm amber pulse on the back row\") and you propose "
    "one effect via the propose_effect tool. On subsequent turns the "
    "user gives feedback (\"faster\", \"tighten the window\", \"use "
    "the Synthwave Sunset palette\") and you propose a revised "
    "version.\n\n"
    "Rules:\n"
    "- Every response MUST be a single propose_effect tool call.\n"
    "- Start proposal_id with 'p' followed by the turn number, e.g. "
    "'p1', 'p2'. Always emit a fresh id on each turn.\n"
    "- Prefer ``builtin`` when one of the standard scripts (fade, "
    "chase, pulse, strobe, sparkle, wave, rainbow, cycle, static) "
    "matches; only emit ``source`` when the user asked for something "
    "the builtins cannot express.\n"
    "- Pick palette_id from the rig snapshot when the user mentions a "
    "color, mood, or named palette. Use null only for rainbow or when "
    "no colors are implied.\n"
    "- Default target_channels to ['rgb']. When the user says things "
    "like 'chase the white channel', 'UV accent', or 'strobe sync', "
    "use ['w'], ['uv'], or ['strobe'] accordingly. Aux-channel targets "
    "leave the base RGB color untouched.\n"
    "- Keep summary to 1 short sentence; the UI puts it above the "
    "editor.\n\n"
    + _LUA_API
)


def _build_system_prompt(rig: dict[str, Any]) -> str:
    rig_json = json.dumps(rig, ensure_ascii=False, indent=2)
    return (
        _SYSTEM_INTRO
        + "\nRig snapshot (authoritative - do not invent ids):\n"
        + rig_json
    )


# ---------------------------------------------------------------------------
# Sanitize tool payload
# ---------------------------------------------------------------------------
_SPREAD_SET = {"across_lights", "across_fixture", "across_zones"}


def _sanitize_controls(raw: Any) -> dict[str, float]:
    out = {"intensity": 1.0, "fade_in_s": 0.25, "fade_out_s": 0.25}
    if not isinstance(raw, dict):
        return out
    v = raw.get("intensity")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out["intensity"] = max(0.0, min(1.0, float(v)))
    for key in ("fade_in_s", "fade_out_s"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = max(0.0, min(EFFECT_FADE_MAX_S, float(v)))
    return out


def _sanitize_proposal(
    raw: Any, sess: Session
) -> tuple[str, Optional[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return "", None
    summary = str(raw.get("summary") or "").strip()[:500]
    pid = raw.get("proposal_id")
    if not isinstance(pid, str) or not pid.strip():
        return summary, None
    pid = pid.strip()[:48]
    name = str(raw.get("name") or "").strip()[:128] or "Effect"

    source: Optional[str] = None
    raw_source = raw.get("source")
    if isinstance(raw_source, str) and raw_source.strip():
        source = raw_source
    if source is None:
        builtin = raw.get("builtin")
        if isinstance(builtin, str):
            source = get_builtin_source(builtin.strip())
    if source is None:
        return summary, None
    try:
        script = compile_script(source, chunkname="=chat")
    except ScriptError:
        return summary, None
    schema = list(script.meta.param_schema)

    spread = raw.get("spread") if raw.get("spread") in _SPREAD_SET else "across_lights"
    palette_id = raw.get("palette_id")
    valid_palette_ids = {
        p.id for p in sess.exec(select(Palette)).all() if p.id is not None
    }
    if not isinstance(palette_id, int) or palette_id not in valid_palette_ids:
        palette_id = None
    tc_raw = raw.get("target_channels")
    tc: list[str] = ["rgb"]
    if isinstance(tc_raw, list):
        seen: list[str] = []
        for x in tc_raw:
            if not isinstance(x, str):
                continue
            key = x.strip().lower()
            if key in EFFECT_TARGET_CHANNELS and key not in seen:
                seen.append(key)
        if seen:
            tc = seen
    params = merge_with_schema(schema, raw.get("params") or {})
    controls = _sanitize_controls(raw.get("controls"))
    return summary, {
        "proposal_id": pid,
        "name": name,
        "summary": summary,
        "source": source,
        "description": script.meta.description,
        "param_schema": schema,
        "palette_id": palette_id,
        "spread": spread,
        "params": params,
        "controls": controls,
        "target_channels": tc,
        "light_ids": [],
        "targets": [],
    }


def _proposal_from_dict(p: dict[str, Any]) -> Optional[EffectProposal]:
    try:
        controls = p.get("controls")
        if not isinstance(controls, dict):
            controls = {}
        return EffectProposal(
            proposal_id=p.get("proposal_id", ""),
            summary=p.get("summary"),
            name=p.get("name", ""),
            source=p.get("source", ""),
            description=p.get("description", ""),
            param_schema=list(p.get("param_schema") or []),
            palette_id=p.get("palette_id"),
            spread=p.get("spread", "across_lights"),
            params=dict(p.get("params") or {}),
            controls=EffectControls(**controls),
            target_channels=list(p.get("target_channels") or ["rgb"]),
            light_ids=list(p.get("light_ids") or []),
            targets=list(p.get("targets") or []),
        )
    except Exception:
        return None


def _render_message(raw_msg: dict[str, Any]) -> EffectChatMessageOut:
    role_raw = raw_msg.get("role", "assistant")
    role: Any = "assistant" if role_raw != "user" else "user"
    content = raw_msg.get("content")
    texts: list[str] = []
    proposal: Optional[EffectProposal] = None
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    texts.append(t)
            elif btype == "tool_use" and block.get("name") == _TOOL_NAME:
                inp = block.get("input") or {}
                if isinstance(inp, dict):
                    summary = inp.get("summary")
                    if isinstance(summary, str) and summary.strip():
                        texts.append(summary.strip())
                    proposal = _proposal_from_dict(inp)
    return EffectChatMessageOut(
        role=role,
        text="\n\n".join(t for t in texts if t),
        proposal=proposal,
    )


def _convo_to_out(row: EffectConversation) -> EffectConversationOut:
    rendered = [
        _render_message(raw)
        for raw in (row.messages or [])
        if isinstance(raw, dict)
    ]
    last = None
    if isinstance(row.last_proposal, dict):
        last = _proposal_from_dict(row.last_proposal)
    last_critique: Optional[dict] = None
    if isinstance(row.last_critique, dict) and row.last_critique:
        last_critique = dict(row.last_critique)
    return EffectConversationOut(
        id=row.id,
        name=row.name or "",
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        messages=rendered,
        last_proposal=last,
        last_critique=last_critique,
    )


def _convo_summary(row: EffectConversation) -> EffectConversationSummary:
    return EffectConversationSummary(
        id=row.id,
        name=row.name or "",
        message_count=len(row.messages or []),
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


# ---------------------------------------------------------------------------
# Status + CRUD
# ---------------------------------------------------------------------------
@router.get("/status")
def chat_status() -> dict[str, Any]:
    return {
        "enabled": bool(ANTHROPIC_API_KEY),
        "model": ANTHROPIC_MODEL,
    }


@router.get("/conversations")
def list_conversations(
    sess: Session = Depends(get_session),
) -> list[EffectConversationSummary]:
    rows = sess.exec(
        select(EffectConversation).order_by(
            EffectConversation.updated_at.desc()
        )
    ).all()
    return [_convo_summary(r) for r in rows]


@router.post("/conversations", status_code=201)
def create_conversation(
    payload: EffectConversationCreate,
    sess: Session = Depends(get_session),
) -> EffectConversationOut:
    name = (payload.name or "").strip()[:128]
    now = datetime.utcnow()
    row = EffectConversation(
        name=name,
        messages=[],
        last_proposal=None,
        created_at=now,
        updated_at=now,
    )
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _convo_to_out(row)


@router.get("/conversations/{cid}")
def get_conversation(
    cid: int, sess: Session = Depends(get_session)
) -> EffectConversationOut:
    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    return _convo_to_out(row)


@router.patch("/conversations/{cid}")
def rename_conversation(
    cid: int,
    payload: EffectConversationRename,
    sess: Session = Depends(get_session),
) -> EffectConversationOut:
    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    row.name = payload.name
    row.updated_at = datetime.utcnow()
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _convo_to_out(row)


@router.delete(
    "/conversations/{cid}", status_code=204, response_model=None
)
def delete_conversation(
    cid: int, sess: Session = Depends(get_session)
) -> None:
    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    sess.delete(row)
    sess.commit()


# ---------------------------------------------------------------------------
# Stream a chat turn
# ---------------------------------------------------------------------------
def _build_messages_for_api(
    stored: Iterable[dict[str, Any]], new_user_text: str
) -> list[dict[str, Any]]:
    """Turn the stored conversation log into the shape Anthropic expects.

    Every assistant ``tool_use`` block must be followed by a ``tool_result``
    in the next user turn. Our stored history doesn't include the tool
    results (the proposal is consumed UI-side, not by Claude), so we
    synthesize ``"applied"`` acknowledgments for any pending tool ids and
    fold them into the next user message - whether that's the next stored
    user turn or the brand-new one we're about to send."""
    msgs: list[dict[str, Any]] = []
    raw = list(stored)
    if len(raw) > _MAX_TURNS_HISTORY:
        raw = raw[-_MAX_TURNS_HISTORY:]

    pending_tool_ids: list[str] = []

    def _wrap_user(content: Any) -> dict[str, Any]:
        nonlocal pending_tool_ids
        if pending_tool_ids:
            blocks: list[dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": tid, "content": "applied"}
                for tid in pending_tool_ids
            ]
            if isinstance(content, str):
                if content:
                    blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        blocks.append(b)
            pending_tool_ids = []
            return {"role": "user", "content": blocks}
        return {"role": "user", "content": content}

    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, (str, list)):
            continue
        if role == "user":
            msgs.append(_wrap_user(content))
        else:
            msgs.append({"role": "assistant", "content": content})
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        tid = b.get("id")
                        if isinstance(tid, str) and tid:
                            pending_tool_ids.append(tid)

    msgs.append(_wrap_user(new_user_text))
    return msgs


def _sse_event(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _extract_tool_input(
    blocks: list[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Return ``(tool_input, tool_use_id)`` for the propose_effect call,
    or ``(None, None)`` if Claude didn't end up calling the tool."""
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use" and b.get("name") == _TOOL_NAME:
            inp = b.get("input")
            if isinstance(inp, dict):
                tid = b.get("id")
                return inp, tid if isinstance(tid, str) else None
    return None, None


def _palette_colors_for(
    sess: Session, palette_id: Any
) -> list[str]:
    if not isinstance(palette_id, int):
        return ["#FFFFFF"]
    pal = sess.get(Palette, palette_id)
    if pal is None or not pal.colors:
        return ["#FFFFFF"]
    return list(pal.colors)


def _format_retry_message(err: ScriptError) -> str:
    """Tool-result body fed back to Claude when a script smoke-test fails.

    Claude tends to do better with a concrete diagnostic + a hint about
    the most-likely cause than just the raw error string."""
    where = f" (line {err.line})" if err.line is not None else ""
    hints: list[str] = []
    msg = err.message.lower()
    if "index a number" in msg or "index a nil" in msg:
        hints.append(
            "Hint: ctx.palette:smooth(p), :step(p), :get(i), color.hsv(...) "
            "and color.hex(...) all return THREE numbers (r, g, b), not a "
            "table. Use ``local r, g, b = ctx.palette:smooth(p)``, never "
            "``local c = ctx.palette:smooth(p); c.r``."
        )
    if "attempt to call" in msg:
        hints.append(
            "Hint: only the helpers listed in the API spec are available. "
            "io / os / require / package / debug / load / dofile are "
            "intentionally absent."
        )
    if "must define" in msg:
        hints.append(
            "Hint: every script must define either ``function render(ctx)`` "
            "or ``function tick(ctx)`` at module scope."
        )
    if not hints:
        hints.append(
            "Hint: render() must return ``{ r=NUMBER, g=NUMBER, b=NUMBER, "
            "brightness=NUMBER }`` with named keys, or ``{ active=false }``."
        )
    return (
        f"Runtime error in your last propose_effect script{where}: "
        f"{err.message}\n\n"
        + "\n".join(hints)
        + "\n\nEmit a corrected propose_effect tool call. Keep PARAMS, "
        "name, palette_id, spread, and target_channels the same unless the "
        "fix requires changing them."
    )


async def _run_anthropic_attempt(
    api_messages: list[dict[str, Any]],
    system_prompt: str,
    tool_schema: dict[str, Any],
    request: Request,
    sse_queue: asyncio.Queue,
    *,
    stream_to_client: bool,
) -> Optional[list[dict[str, Any]]]:
    """Run one Anthropic streaming call.

    Pushes ``("text"|"tool_start"|"tool_delta", ...)`` events into
    ``sse_queue`` while the call streams. Returns the final content
    blocks list once Anthropic finishes, or ``None`` when the client
    disconnected or Anthropic raised an upstream error (in which case an
    ``error`` event was already enqueued)."""
    import anthropic

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    final_blocks: list[dict[str, Any]] = []
    stop_flag = {"cancelled": False}

    def producer() -> None:
        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            with client.messages.stream(
                model=ANTHROPIC_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=api_messages,
            ) as stream:
                for event in stream:
                    if stop_flag["cancelled"]:
                        break
                    etype = getattr(event, "type", None)
                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        btype = getattr(block, "type", None)
                        if btype == "tool_use":
                            name = getattr(block, "name", None)
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("tool_start", {"tool": name}),
                            )
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta":
                            t = getattr(delta, "text", "") or ""
                            if t:
                                loop.call_soon_threadsafe(
                                    queue.put_nowait,
                                    ("text", {"delta": t}),
                                )
                        elif dtype == "input_json_delta":
                            pj = getattr(delta, "partial_json", "") or ""
                            if pj:
                                loop.call_soon_threadsafe(
                                    queue.put_nowait,
                                    ("tool_delta", {"partial_json": pj}),
                                )
                if not stop_flag["cancelled"]:
                    final = stream.get_final_message()
                    blocks: list[dict[str, Any]] = []
                    for blk in getattr(final, "content", []) or []:
                        btype = getattr(blk, "type", None)
                        if btype == "text":
                            blocks.append({
                                "type": "text",
                                "text": getattr(blk, "text", "") or "",
                            })
                        elif btype == "tool_use":
                            inp = getattr(blk, "input", None)
                            blocks.append({
                                "type": "tool_use",
                                "id": getattr(blk, "id", "") or "",
                                "name": getattr(blk, "name", "") or "",
                                "input": inp if isinstance(inp, dict) else {},
                            })
                    final_blocks.extend(blocks)
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("__done__", None)
                    )
        except anthropic.APIStatusError as exc:
            log.warning("Anthropic API error: %s", exc)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("error", {"message": f"Claude API error: {exc.message}"}),
            )
            loop.call_soon_threadsafe(queue.put_nowait, ("__done__", None))
        except Exception as exc:  # pragma: no cover
            log.exception("effect chat stream failed")
            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("error", {"message": f"Claude request failed: {exc}"}),
            )
            loop.call_soon_threadsafe(queue.put_nowait, ("__done__", None))

    producer_task = loop.run_in_executor(None, producer)

    upstream_error: Optional[dict[str, Any]] = None
    try:
        while True:
            if await request.is_disconnected():
                stop_flag["cancelled"] = True
                break
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            kind, data = evt
            if kind == "__done__":
                break
            if kind == "error":
                upstream_error = data
                break
            if stream_to_client:
                await sse_queue.put((kind, data))
    finally:
        stop_flag["cancelled"] = True
        try:
            await producer_task
        except Exception:
            pass

    if upstream_error is not None:
        await sse_queue.put(("error", upstream_error))
        return None
    if not final_blocks:
        return None
    return final_blocks


@router.post("/conversations/{cid}/message")
async def stream_message(
    cid: int,
    payload: EffectMessageIn,
    request: Request,
    sess: Session = Depends(get_session),
) -> StreamingResponse:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "Claude is not configured on this server")

    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")

    try:
        import anthropic  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            503, "anthropic package is not installed on the server"
        ) from exc

    rig = build_rig_context(sess, include_effects=True)
    system_prompt = _build_system_prompt(rig)
    api_messages = _build_messages_for_api(row.messages or [], payload.message)
    tool_schema = _build_tool_schema()
    user_text = payload.message
    conversation_id = cid

    async def stream_gen() -> AsyncIterator[bytes]:
        # Single producer/consumer queue: the orchestrator coroutine
        # drives multiple Anthropic attempts and pushes SSE events here;
        # this generator just drains it and serializes to bytes.
        sse_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        await sse_queue.put(("start", {"conversation_id": conversation_id}))

        async def orchestrate() -> None:
            attempt_messages = list(api_messages)
            final_blocks: Optional[list[dict[str, Any]]] = None
            proposal_clean: Optional[dict[str, Any]] = None
            last_runtime_error: Optional[ScriptError] = None

            for attempt in range(_MAX_ATTEMPTS):
                if attempt > 0:
                    await sse_queue.put((
                        "retry",
                        {
                            "attempt": attempt + 1,
                            "max_attempts": _MAX_ATTEMPTS,
                            "reason": (
                                last_runtime_error.message
                                if last_runtime_error is not None
                                else "no proposal"
                            ),
                        },
                    ))

                blocks = await _run_anthropic_attempt(
                    attempt_messages,
                    system_prompt,
                    tool_schema,
                    request,
                    sse_queue,
                    stream_to_client=(attempt == 0),
                )
                if blocks is None:
                    return  # cancelled or upstream error already emitted

                tool_input, tool_use_id = _extract_tool_input(blocks)
                if tool_input is None:
                    final_blocks = blocks
                    break

                _summary, proposal_clean = _sanitize_proposal(tool_input, sess)
                if proposal_clean is None:
                    final_blocks = blocks
                    last_runtime_error = ScriptError(
                        "Claude did not return a usable Lua script"
                    )
                    break

                err = await asyncio.to_thread(
                    smoke_test_source,
                    proposal_clean["source"],
                    params=dict(proposal_clean.get("params") or {}),
                    palette_colors=_palette_colors_for(
                        sess, proposal_clean.get("palette_id")
                    ),
                )
                if err is None:
                    final_blocks = blocks
                    last_runtime_error = None
                    break

                last_runtime_error = err
                if attempt == _MAX_ATTEMPTS - 1:
                    final_blocks = blocks
                    break

                attempt_messages = list(attempt_messages) + [
                    {"role": "assistant", "content": blocks},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id or "",
                                "content": _format_retry_message(err),
                                "is_error": True,
                            }
                        ],
                    },
                ]

            if final_blocks is None:
                return

            if proposal_clean is not None and last_runtime_error is None:
                for block in final_blocks:
                    if (
                        block.get("type") == "tool_use"
                        and block.get("name") == _TOOL_NAME
                    ):
                        block["input"] = dict(proposal_clean)
                        break

            assistant_msg = {"role": "assistant", "content": final_blocks}
            user_msg = {"role": "user", "content": user_text}

            try:
                refreshed = sess.get(EffectConversation, conversation_id)
                if refreshed is None:
                    return
                msgs = list(refreshed.messages or [])
                msgs.append(user_msg)
                msgs.append(assistant_msg)
                refreshed.messages = msgs
                # Persist the proposal even if smoke-testing failed, so
                # the user can see (and potentially hand-fix) Claude's
                # latest attempt in the editor. The script_error event
                # warns them that it's still broken; the live preview WS
                # will re-emit the runtime error too.
                refreshed.last_proposal = proposal_clean
                if not refreshed.name:
                    refreshed.name = user_text.strip().splitlines()[0][:64]
                refreshed.updated_at = datetime.utcnow()
                sess.add(refreshed)
                sess.commit()
                sess.refresh(refreshed)
                out = _convo_to_out(refreshed)
                if last_runtime_error is not None:
                    await sse_queue.put((
                        "script_error",
                        {
                            "message": last_runtime_error.message,
                            "line": last_runtime_error.line,
                            "attempts": _MAX_ATTEMPTS,
                        },
                    ))
                await sse_queue.put((
                    "proposal",
                    out.last_proposal.model_dump()
                    if out.last_proposal else None,
                ))
                await sse_queue.put((
                    "done", {"conversation": out.model_dump()}
                ))
            except Exception as exc:
                log.exception("failed to persist effect chat turn")
                await sse_queue.put((
                    "error", {"message": f"failed to persist turn: {exc}"}
                ))

        async def runner() -> None:
            try:
                await orchestrate()
            finally:
                await sse_queue.put(("__finish__", None))

        runner_task = asyncio.create_task(runner())
        try:
            while True:
                try:
                    kind, data = await asyncio.wait_for(
                        sse_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    continue
                if kind == "__finish__":
                    break
                yield _sse_event(kind, data)
        finally:
            if not runner_task.done():
                runner_task.cancel()
            try:
                await runner_task
            except (asyncio.CancelledError, Exception):
                pass

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        stream_gen(), media_type="text/event-stream", headers=headers
    )


# ---------------------------------------------------------------------------
# Applying / saving a proposal
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field, field_validator


class EffectApplyRequest(BaseModel):
    proposal_id: str
    light_ids: list[int] = Field(default_factory=list)

    @field_validator("proposal_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("proposal_id must be non-empty")
        return s


class EffectSaveRequest(BaseModel):
    proposal_id: str
    name: Optional[str] = None


def _find_proposal(row: EffectConversation, pid: str) -> dict[str, Any]:
    lp = row.last_proposal
    if not isinstance(lp, dict):
        raise HTTPException(404, "no proposal in this conversation")
    if str(lp.get("proposal_id")) != pid:
        raise HTTPException(404, f"unknown proposal_id '{pid}'")
    return lp


@router.post("/conversations/{cid}/apply")
def apply_proposal(
    cid: int,
    payload: EffectApplyRequest,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    prop = _find_proposal(row, payload.proposal_id)
    source = prop.get("source")
    if not isinstance(source, str) or not source.strip():
        raise HTTPException(400, "proposal missing Lua source")
    try:
        script = compile_script(source, chunkname="=chat-apply")
    except ScriptError as e:
        raise HTTPException(400, {"error": e.to_dict()})

    palette_colors: list[str] = ["#FFFFFF"]
    pid = prop.get("palette_id")
    if isinstance(pid, int):
        pal = sess.get(Palette, pid)
        if pal is not None and pal.colors:
            palette_colors = list(pal.colors)

    schema = list(script.meta.param_schema)
    params = merge_with_schema(schema, prop.get("params") or {})
    controls_raw = prop.get("controls") or {}
    controls = _sanitize_controls(controls_raw)

    # Replace any prior transient layer started from this chat so the
    # layer rail stays clean across iterative re-plays.
    if row.last_layer_id is not None:
        old = sess.get(EffectLayer, row.last_layer_id)
        if old is not None:
            effect_engine.stop_by_layer_id(old.id, immediate=True)
            sess.delete(old)
            sess.commit()

    layer, handle = play_transient_layer(
        sess,
        name=str(prop.get("name") or "Live effect"),
        script=script,
        palette_colors=palette_colors,
        light_ids=list(payload.light_ids or []),
        targets=[],
        spread=str(prop.get("spread") or "across_lights"),
        params=params,
        target_channels=list(prop.get("target_channels") or ["rgb"]),
        intensity=controls["intensity"],
        fade_in_s=controls["fade_in_s"],
        fade_out_s=controls["fade_out_s"],
        palette_id=pid if isinstance(pid, int) else None,
    )
    row.last_layer_id = layer.id
    sess.add(row)
    sess.commit()
    return {
        "ok": True,
        "handle": handle,
        "name": layer.name,
        "layer_id": layer.id,
    }


@router.post("/conversations/{cid}/save")
def save_proposal(
    cid: int,
    payload: EffectSaveRequest,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    prop = _find_proposal(row, payload.proposal_id)
    source = prop.get("source")
    if not isinstance(source, str) or not source.strip():
        raise HTTPException(400, "proposal missing Lua source")
    try:
        script = compile_script(source, chunkname="=chat-save")
    except ScriptError as e:
        raise HTTPException(400, {"error": e.to_dict()})
    name = (payload.name or prop.get("name") or "").strip()[:128] or "Effect"
    schema = list(script.meta.param_schema)
    params = merge_with_schema(schema, prop.get("params") or {})
    controls = _sanitize_controls(prop.get("controls") or {})
    persisted = dict(params)
    persisted.update(controls)
    eff = Effect(
        name=name,
        source=source,
        param_schema=schema,
        palette_id=(
            prop.get("palette_id")
            if isinstance(prop.get("palette_id"), int)
            else None
        ),
        light_ids=[],
        targets=[],
        spread=str(prop.get("spread") or "across_lights"),
        params=persisted,
        target_channels=list(prop.get("target_channels") or ["rgb"]),
        is_active=False,
        builtin=False,
    )
    sess.add(eff)
    sess.commit()
    sess.refresh(eff)
    return {"ok": True, "id": eff.id, "name": name}


# ---------------------------------------------------------------------------
# Self-critique ("double-check")
# ---------------------------------------------------------------------------
def _wrap_effect_proposal_for_review(prop: dict[str, Any]) -> dict[str, Any]:
    """Reshape an effect-chat proposal into the same envelope the
    designer verifier expects, so we can reuse the shared verifier."""
    src = prop.get("source")
    effect_body: dict[str, Any] = {
        k: prop.get(k)
        for k in (
            "spread",
            "palette_id",
            "target_channels",
            "params",
            "controls",
            "light_ids",
            "targets",
        )
        if prop.get(k) is not None
    }
    if isinstance(src, str):
        if len(src) > 6000:
            effect_body["source"] = src[:6000] + "\n-- (truncated for review)"
        else:
            effect_body["source"] = src
    return {
        "proposal_id": prop.get("proposal_id"),
        "kind": "effect",
        "name": prop.get("name"),
        "notes": prop.get("summary") or prop.get("description"),
        "effect": effect_body,
    }


@router.post(
    "/conversations/{cid}/critique", response_model=DesignerCritiqueResponse
)
def critique_proposal(
    cid: int,
    payload: DesignerCritiqueRequest,
    sess: Session = Depends(get_session),
) -> DesignerCritiqueResponse:
    """Mirror of :func:`designer.critique_proposal` for the effect chat."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "Claude is not configured on this server")
    row = sess.get(EffectConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    prop = _find_proposal(row, payload.proposal_id)

    user_request = (
        (payload.user_request or "").strip()
        or _last_user_text_from_history(row.messages or [])
        or "(no user message recorded)"
    )

    rig = build_rig_context(sess)
    wrapped = _wrap_effect_proposal_for_review(prop)
    try:
        critique, _usage = _run_verifier(rig, user_request, wrapped)
    except Exception as exc:
        log.warning("verifier failed for effect-chat cid=%s: %s", cid, exc)
        critique = DesignerCritique(
            intent_summary=f"Verifier unavailable: {exc}",
            verdict="needs_review",
            confidence=0.0,
        )

    cache = dict(row.last_critique or {})
    cache[payload.proposal_id] = critique.model_dump()
    row.last_critique = cache
    row.updated_at = datetime.utcnow()
    sess.add(row)
    sess.commit()

    return DesignerCritiqueResponse(
        ok=True, proposal_id=payload.proposal_id, critique=critique
    )
