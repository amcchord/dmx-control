"""Multi-turn Claude chat for iterating on effect definitions.

Mirrors the designer router's shape but with a single forced tool
(``propose_effect``) that returns one :class:`EffectIn`-shaped payload
per turn. The client loads the proposal into the live editor; the user
says "faster, tighter window", Claude revises, repeat.

Each turn:

1. Server rebuilds the rig snapshot (including existing effects) so
   Claude has fresh context.
2. Sends the stored history + new user message to Claude with the
   ``propose_effect`` tool forced.
3. Streams text/tool deltas to the browser as SSE and persists the
   full assistant turn when complete.

This router is intentionally independent from the designer's tool set
so its storage/contract can evolve without touching designer chats.
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
from ..engine import EffectSpec, engine as effect_engine, new_handle
from ..models import Effect, EffectConversation, Palette
from ..rig_context import build_rig_context
from ..schemas import (
    EFFECT_FADE_MAX_S,
    EFFECT_SIZE_MAX,
    EFFECT_SPEED_HZ_MAX,
    EFFECT_TARGET_CHANNELS,
    EffectChatMessageOut,
    EffectConversationCreate,
    EffectConversationOut,
    EffectConversationRename,
    EffectConversationSummary,
    EffectMessageIn,
    EffectParams,
    EffectProposal,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/effect-chat",
    tags=["effect-chat"],
    dependencies=[AuthDep],
)


_TOOL_NAME = "propose_effect"
_MAX_TURNS_HISTORY = 40


_EFFECT_TYPES = [
    "static", "fade", "cycle", "chase", "pulse",
    "rainbow", "strobe", "sparkle", "wave",
]


def _build_tool_schema() -> dict[str, Any]:
    params_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "speed_hz": {
                "type": "number", "minimum": 0, "maximum": EFFECT_SPEED_HZ_MAX,
            },
            "direction": {
                "type": "string",
                "enum": ["forward", "reverse", "pingpong"],
            },
            "offset": {"type": "number", "minimum": 0, "maximum": 1},
            "intensity": {"type": "number", "minimum": 0, "maximum": 1},
            "size": {
                "type": "number", "minimum": 0, "maximum": EFFECT_SIZE_MAX,
            },
            "softness": {"type": "number", "minimum": 0, "maximum": 1},
            "fade_in_s": {
                "type": "number", "minimum": 0, "maximum": EFFECT_FADE_MAX_S,
            },
            "fade_out_s": {
                "type": "number", "minimum": 0, "maximum": EFFECT_FADE_MAX_S,
            },
        },
    }
    return {
        "name": _TOOL_NAME,
        "description": (
            "Return one animated effect that matches the user's latest "
            "ask. Use the rig snapshot's palette ids when the user "
            "mentions colors; leave palette_id null for rainbow or when "
            "no specific colors were requested."
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
                "effect_type": {"type": "string", "enum": _EFFECT_TYPES},
                "palette_id": {"type": "integer"},
                "spread": {
                    "type": "string",
                    "enum": [
                        "across_lights",
                        "across_fixture",
                        "across_zones",
                    ],
                },
                "params": params_schema,
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
            "required": ["proposal_id", "name", "effect_type"],
        },
    }


_SYSTEM_INTRO = (
    "You are a lighting designer who iteratively refines animated DMX "
    "effects. The user says what they want (\"warm amber pulse on the "
    "back row\") and you propose one effect via the propose_effect "
    "tool. On subsequent turns the user gives feedback (\"faster\", "
    "\"tighten the window\", \"use the Synthwave Sunset palette\") and "
    "you propose a revised version.\n\n"
    "Rules:\n"
    "- Every response MUST be a single propose_effect tool call.\n"
    "- Start proposal_id with 'p' followed by the turn number, e.g. "
    "'p1', 'p2'. Always emit a fresh id on each turn.\n"
    "- Pick palette_id from the rig snapshot when the user mentions a "
    "color, mood, or named palette. Use null only for rainbow or when "
    "no colors are implied.\n"
    "- speed_hz is cycles per second. 0.25-2.0 is a comfortable range "
    "for pulses/fades; 1-10 for chases; 3-20 for strobes.\n"
    "- Default target_channels to ['rgb']. When the user says things "
    "like 'chase the white channel', 'UV accent', or 'strobe sync', "
    "use ['w'], ['uv'], or ['strobe'] accordingly. Aux-channel targets "
    "leave the base RGB color untouched.\n"
    "- Keep summary to 1 short sentence; the UI puts it above the "
    "editor.\n"
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
def _clip_byte(v: Any) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    iv = int(v)
    if iv < 0:
        return 0
    if iv > 255:
        return 255
    return iv


_SPREAD_SET = {"across_lights", "across_fixture", "across_zones"}
_DIRECTION_SET = {"forward", "reverse", "pingpong"}
_EFFECT_TYPE_SET = set(_EFFECT_TYPES)


def _sanitize_params(raw: Any) -> dict[str, Any]:
    defaults = EffectParams().model_dump()
    if not isinstance(raw, dict):
        return defaults
    out = dict(defaults)

    def _num(key: str, lo: float, hi: float) -> None:
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = max(lo, min(hi, float(v)))

    _num("speed_hz", 0.0, EFFECT_SPEED_HZ_MAX)
    _num("offset", 0.0, 1.0)
    _num("intensity", 0.0, 1.0)
    _num("size", 0.0, EFFECT_SIZE_MAX)
    _num("softness", 0.0, 1.0)
    _num("fade_in_s", 0.0, EFFECT_FADE_MAX_S)
    _num("fade_out_s", 0.0, EFFECT_FADE_MAX_S)
    d = raw.get("direction")
    if isinstance(d, str) and d in _DIRECTION_SET:
        out["direction"] = d
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
    etype = raw.get("effect_type")
    if etype not in _EFFECT_TYPE_SET:
        return summary, None
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
    params = _sanitize_params(raw.get("params"))
    return summary, {
        "proposal_id": pid,
        "name": name,
        "summary": summary,
        "effect_type": etype,
        "palette_id": palette_id,
        "spread": spread,
        "params": params,
        "target_channels": tc,
        "light_ids": [],
        "targets": [],
    }


def _proposal_from_dict(p: dict[str, Any]) -> Optional[EffectProposal]:
    try:
        return EffectProposal(**p)
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
                    # Stored shape after sanitization is the proposal dict
                    # directly (not nested).
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
    return EffectConversationOut(
        id=row.id,
        name=row.name or "",
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        messages=rendered,
        last_proposal=last,
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
    msgs: list[dict[str, Any]] = []
    raw = list(stored)
    if len(raw) > _MAX_TURNS_HISTORY:
        raw = raw[-_MAX_TURNS_HISTORY:]
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, (str, list)):
            continue
        msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": new_user_text})
    return msgs


def _sse_event(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


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
        import anthropic

        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        final_content_blocks: list[dict[str, Any]] = []
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
                                blocks.append(
                                    {
                                        "type": "text",
                                        "text": getattr(blk, "text", "") or "",
                                    }
                                )
                            elif btype == "tool_use":
                                inp = getattr(blk, "input", None)
                                blocks.append(
                                    {
                                        "type": "tool_use",
                                        "id": getattr(blk, "id", "") or "",
                                        "name": getattr(blk, "name", "") or "",
                                        "input": inp if isinstance(inp, dict) else {},
                                    }
                                )
                        final_content_blocks.extend(blocks)
                        loop.call_soon_threadsafe(
                            queue.put_nowait, ("__done__", None)
                        )
            except anthropic.APIStatusError as exc:
                log.warning("Anthropic API error: %s", exc)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"message": f"Claude API error: {exc.message}"}),
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("__done__", None)
                )
            except Exception as exc:  # pragma: no cover
                log.exception("effect chat stream failed")
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"message": f"Claude request failed: {exc}"}),
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("__done__", None)
                )

        producer_task = loop.run_in_executor(None, producer)

        yield _sse_event("start", {"conversation_id": conversation_id})

        errored = False
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
                    errored = True
                    yield _sse_event("error", data)
                    break
                yield _sse_event(kind, data)
        finally:
            stop_flag["cancelled"] = True
            try:
                await producer_task
            except Exception:
                pass

        if errored or (stop_flag["cancelled"] and not final_content_blocks):
            return

        assistant_msg = {"role": "assistant", "content": final_content_blocks}
        user_msg = {"role": "user", "content": user_text}
        proposal_clean: Optional[dict[str, Any]] = None
        summary_text = ""
        for block in final_content_blocks:
            if (
                block.get("type") == "tool_use"
                and block.get("name") == _TOOL_NAME
            ):
                summary_text, proposal_clean = _sanitize_proposal(
                    block.get("input"), sess
                )
                break

        # Rewrite tool input in place so replayed history matches what we
        # stored as last_proposal.
        if proposal_clean is not None:
            for block in final_content_blocks:
                if (
                    block.get("type") == "tool_use"
                    and block.get("name") == _TOOL_NAME
                ):
                    block["input"] = dict(proposal_clean)
                    break

        try:
            refreshed = sess.get(EffectConversation, conversation_id)
            if refreshed is not None:
                msgs = list(refreshed.messages or [])
                msgs.append(user_msg)
                msgs.append(assistant_msg)
                refreshed.messages = msgs
                refreshed.last_proposal = proposal_clean
                if not refreshed.name:
                    refreshed.name = user_text.strip().splitlines()[0][:64]
                refreshed.updated_at = datetime.utcnow()
                sess.add(refreshed)
                sess.commit()
                sess.refresh(refreshed)
                out = _convo_to_out(refreshed)
                yield _sse_event(
                    "proposal",
                    out.last_proposal.model_dump() if out.last_proposal else None,
                )
                yield _sse_event("done", {"conversation": out.model_dump()})
        except Exception as exc:
            log.exception("failed to persist effect chat turn")
            yield _sse_event(
                "error", {"message": f"failed to persist turn: {exc}"}
            )

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
    # Override the proposal's target selection when the client wants to
    # play on the currently-selected lights/zones.
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

    palette_colors: list[str] = ["#FFFFFF"]
    pid = prop.get("palette_id")
    if isinstance(pid, int):
        pal = sess.get(Palette, pid)
        if pal is not None and pal.colors:
            palette_colors = list(pal.colors)

    handle = new_handle()
    spec = EffectSpec(
        handle=handle,
        effect_id=None,
        name=str(prop.get("name") or "Live effect"),
        effect_type=str(prop.get("effect_type")),
        palette_colors=palette_colors,
        light_ids=list(payload.light_ids or []),
        targets=[],
        spread=str(prop.get("spread") or "across_lights"),
        params=dict(prop.get("params") or {}),
        target_channels=list(prop.get("target_channels") or ["rgb"]),
    )
    effect_engine.play(spec)
    return {"ok": True, "handle": handle, "name": spec.name}


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
    name = (payload.name or prop.get("name") or "").strip()[:128] or "Effect"
    eff = Effect(
        name=name,
        effect_type=str(prop.get("effect_type")),
        palette_id=(
            prop.get("palette_id")
            if isinstance(prop.get("palette_id"), int)
            else None
        ),
        light_ids=[],
        targets=[],
        spread=str(prop.get("spread") or "across_lights"),
        params=dict(prop.get("params") or {}),
        target_channels=list(prop.get("target_channels") or ["rgb"]),
        is_active=False,
        builtin=False,
    )
    sess.add(eff)
    sess.commit()
    sess.refresh(eff)
    return {"ok": True, "id": eff.id, "name": name}
