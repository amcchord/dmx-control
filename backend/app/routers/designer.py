"""Designer chat: Claude Opus designs rig States and Scenes.

The designer is a multi-turn chat backed by Anthropic's Messages API. Each
user turn is sent to Claude along with the full rig context (every
controller, every light with model/mode/zones/notes, every palette) and
the prior conversation history. Claude is required to answer with a
single ``propose_rig_design`` tool call describing one or more
**proposals**:

- ``kind='state'`` - a rig-wide snapshot (every addressed light).
- ``kind='scene'`` - a controller-scoped snapshot.

Apply and Save endpoints operate on the most recent set of proposals
cached in ``DesignerConversation.last_proposal``; proposals are keyed
by a short ``proposal_id`` that Claude assigns per turn.

The send-message endpoint streams Claude's response to the browser as
Server-Sent Events so the UI can show tokens arrive live. The full
assistant turn (text + tool_use) is persisted in a single transaction
when the stream completes; disconnecting mid-stream cleanly drops the
turn.
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

from ..artnet import manager
from ..auth import AuthDep
from ..config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from ..db import get_session
from ..engine import engine as effect_engine
from ..models import (
    Controller,
    DesignerConversation,
    Light,
    LightModel,
    LightModelMode,
    Palette,
    Scene,
    State,
)
from ..schemas import (
    DesignerApplyRequest,
    DesignerConversationCreate,
    DesignerConversationOut,
    DesignerConversationRename,
    DesignerConversationSummary,
    DesignerMessageIn,
    DesignerMessageOut,
    DesignerProposal,
    DesignerProposalLight,
    DesignerSaveRequest,
)
from ._capture import apply_state_to_light, push_light

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/designer", tags=["designer"], dependencies=[AuthDep]
)


_TOOL_NAME = "propose_rig_design"
_MAX_TURNS_HISTORY = 40


# ---------------------------------------------------------------------------
# Tool schema (forced tool_choice)
# ---------------------------------------------------------------------------
def _build_tool_schema() -> dict[str, Any]:
    light_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "light_id": {
                "type": "integer",
                "description": "Light.id from the rig context.",
            },
            "on": {"type": "boolean"},
            "dimmer": {"type": "integer", "minimum": 0, "maximum": 255},
            "r": {"type": "integer", "minimum": 0, "maximum": 255},
            "g": {"type": "integer", "minimum": 0, "maximum": 255},
            "b": {"type": "integer", "minimum": 0, "maximum": 255},
            "w": {"type": "integer", "minimum": 0, "maximum": 255},
            "a": {"type": "integer", "minimum": 0, "maximum": 255},
            "uv": {"type": "integer", "minimum": 0, "maximum": 255},
            "zone_state": {
                "type": "object",
                "description": (
                    "Optional per-zone overrides for compound fixtures, "
                    "keyed by zone_id. Each zone value has the same "
                    "r/g/b/w/a/uv/dimmer/on shape as the light root."
                ),
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "r": {"type": "integer", "minimum": 0, "maximum": 255},
                        "g": {"type": "integer", "minimum": 0, "maximum": 255},
                        "b": {"type": "integer", "minimum": 0, "maximum": 255},
                        "w": {"type": "integer", "minimum": 0, "maximum": 255},
                        "a": {"type": "integer", "minimum": 0, "maximum": 255},
                        "uv": {"type": "integer", "minimum": 0, "maximum": 255},
                        "dimmer": {
                            "type": "integer", "minimum": 0, "maximum": 255
                        },
                        "on": {"type": "boolean"},
                    },
                },
            },
            "motion_state": {
                "type": "object",
                "additionalProperties": False,
                "description": (
                    "Pan/tilt/zoom/focus as floats in [0,1]. Only include "
                    "axes the fixture actually exposes in its layout."
                ),
                "properties": {
                    "pan": {"type": "number", "minimum": 0, "maximum": 1},
                    "tilt": {"type": "number", "minimum": 0, "maximum": 1},
                    "zoom": {"type": "number", "minimum": 0, "maximum": 1},
                    "focus": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "required": ["light_id", "r", "g", "b"],
    }
    proposal_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": (
                    "Short stable id, e.g. 'p1', 'sunset', 'drop'. Must be "
                    "unique within this tool call."
                ),
            },
            "kind": {"type": "string", "enum": ["state", "scene"]},
            "name": {
                "type": "string",
                "description": (
                    "Human-friendly name, e.g. 'Sunset wash' or "
                    "'Chorus hit'."
                ),
            },
            "controller_id": {
                "type": "integer",
                "description": (
                    "Required when kind='scene'; the Controller.id this "
                    "scene targets."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Short designer note about this proposal.",
            },
            "lights": {
                "type": "array",
                "items": light_schema,
                "description": (
                    "Every light you want to set. Omit lights you intend "
                    "to leave untouched."
                ),
            },
        },
        "required": ["proposal_id", "kind", "name", "lights"],
    }
    return {
        "name": _TOOL_NAME,
        "description": (
            "Propose one or more rig designs. Each proposal is either a "
            "rig-wide State or a per-controller Scene. The UI will let "
            "the user Apply or Save any of them."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "1-3 sentence description of what you're "
                        "proposing, visible to the user."
                    ),
                },
                "proposals": {
                    "type": "array",
                    "minItems": 1,
                    "items": proposal_schema,
                },
            },
            "required": ["summary", "proposals"],
        },
    }


# ---------------------------------------------------------------------------
# Rig context + system prompt
# ---------------------------------------------------------------------------
def _mode_for_light(
    l: Light, modes_by_id: dict[int, LightModelMode]
) -> Optional[LightModelMode]:
    if l.mode_id is None:
        return None
    return modes_by_id.get(l.mode_id)


def _zone_ids_for_mode(mode: Optional[LightModelMode]) -> list[str]:
    if mode is None or not isinstance(mode.layout, dict):
        return []
    zones = mode.layout.get("zones") or []
    out: list[str] = []
    for z in zones:
        if isinstance(z, dict):
            zid = z.get("id")
            if isinstance(zid, str) and zid:
                out.append(zid)
    return out


def _motion_axes_for_mode(mode: Optional[LightModelMode]) -> list[str]:
    if mode is None or not isinstance(mode.layout, dict):
        return []
    motion = mode.layout.get("motion")
    if not isinstance(motion, dict):
        return []
    return [a for a in ("pan", "tilt", "zoom", "focus") if a in motion]


def _build_rig_context(sess: Session) -> dict[str, Any]:
    """Snapshot the rig into a JSON-serializable dict for the system prompt."""
    controllers = list(
        sess.exec(select(Controller).order_by(Controller.id)).all()
    )
    lights = list(sess.exec(select(Light).order_by(Light.controller_id, Light.position, Light.id)).all())
    models = list(sess.exec(select(LightModel)).all())
    modes = list(sess.exec(select(LightModelMode)).all())
    palettes = list(sess.exec(select(Palette).order_by(Palette.name)).all())

    model_by_id = {m.id: m for m in models}
    mode_by_id = {m.id: m for m in modes}

    ctrl_out: list[dict[str, Any]] = []
    for c in controllers:
        entry: dict[str, Any] = {
            "id": c.id,
            "name": c.name,
            "ip": c.ip,
            "universe": f"{c.net}:{c.subnet}:{c.universe}",
            "enabled": bool(c.enabled),
        }
        if c.notes:
            entry["notes"] = c.notes
        ctrl_out.append(entry)

    light_out: list[dict[str, Any]] = []
    for l in lights:
        m = model_by_id.get(l.model_id)
        mode = _mode_for_light(l, mode_by_id)
        entry: dict[str, Any] = {
            "id": l.id,
            "name": l.name,
            "controller_id": l.controller_id,
            "start_address": l.start_address,
            "model": m.name if m else "?",
            "mode": mode.name if mode else "?",
            "channels": list(mode.channels) if mode else list(m.channels or []) if m else [],
            "current": {
                "r": int(l.r or 0),
                "g": int(l.g or 0),
                "b": int(l.b or 0),
                "dimmer": int(l.dimmer if l.dimmer is not None else 255),
                "on": bool(l.on),
            },
        }
        zones = _zone_ids_for_mode(mode)
        if zones:
            entry["zones"] = zones
        axes = _motion_axes_for_mode(mode)
        if axes:
            entry["motion_axes"] = axes
        if l.notes:
            entry["notes"] = l.notes
        light_out.append(entry)

    palette_out = [
        {"name": p.name, "colors": list(p.colors or [])} for p in palettes
    ]

    return {
        "controllers": ctrl_out,
        "lights": light_out,
        "palettes": palette_out,
    }


_SYSTEM_INTRO = (
    "You are a lighting designer for a live stage rig. The user gives you "
    "creative prompts (a mood, a song section, a theme) and you respond "
    "with one or more concrete rig designs using the propose_rig_design "
    "tool. Every response MUST be a single tool call - do not reply with "
    "plain prose only.\n\n"
    "Each proposal is either:\n"
    "  - kind='state': a rig-wide look covering every fixture you want to "
    "set (omit lights you want to leave alone).\n"
    "  - kind='scene': a look scoped to one controller_id.\n\n"
    "Rules:\n"
    "- Only reference light_id values that exist in the rig snapshot.\n"
    "- RGB components are 0..255 integers; dimmer is 0..255.\n"
    "- Use on=false to explicitly blackout a fixture.\n"
    "- For compound fixtures (with zones), prefer zone_state for rich "
    "looks (gradients across pixels, eye/head/ball splits).\n"
    "- For moving heads, set motion_state pan/tilt/zoom/focus as floats "
    "in [0,1]. 0.5 is the center; ends are 0 and 1.\n"
    "- Honor user notes on controllers and lights (they describe purpose "
    "and stage position).\n"
    "- Keep proposal names short (1-4 words).\n"
    "- When the user asks for multiple looks (a 'show', 'sunset to "
    "night', etc.), emit multiple proposals in one call.\n"
    "- Use named palettes from the rig snapshot as color inspiration "
    "when appropriate.\n"
)


def _build_system_prompt(rig: dict[str, Any]) -> str:
    """Compose the final system string sent to Claude."""
    rig_json = json.dumps(rig, ensure_ascii=False, indent=2)
    return (
        _SYSTEM_INTRO
        + "\nRig snapshot (authoritative - do not invent ids):\n"
        + rig_json
    )


# ---------------------------------------------------------------------------
# Output sanitization
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


def _clip_unit(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    fv = float(v)
    if fv < 0.0:
        return 0.0
    if fv > 1.0:
        return 1.0
    return fv


def _sanitize_proposal_light(
    raw: Any,
    *,
    known_light_ids: set[int],
    light_zones_by_id: dict[int, set[str]],
    light_axes_by_id: dict[int, set[str]],
) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    lid = raw.get("light_id")
    if not isinstance(lid, int) or isinstance(lid, bool):
        return None
    if lid not in known_light_ids:
        return None

    out: dict[str, Any] = {"light_id": lid}
    for key in ("r", "g", "b"):
        val = _clip_byte(raw.get(key))
        out[key] = val if val is not None else 0
    for key in ("w", "a", "uv", "dimmer"):
        val = _clip_byte(raw.get(key))
        if val is not None:
            out[key] = val
    if "dimmer" not in out:
        out["dimmer"] = 255
    if "on" in raw and isinstance(raw["on"], bool):
        out["on"] = raw["on"]
    else:
        out["on"] = True

    # Per-zone overrides, filtered by the fixture's layout zone ids.
    zs_raw = raw.get("zone_state")
    zs_out: dict[str, dict[str, Any]] = {}
    valid_zones = light_zones_by_id.get(lid, set())
    if isinstance(zs_raw, dict):
        for zid, zval in zs_raw.items():
            if not isinstance(zid, str):
                continue
            if valid_zones and zid not in valid_zones:
                continue
            if not isinstance(zval, dict):
                continue
            cleaned: dict[str, Any] = {}
            for k in ("r", "g", "b", "w", "a", "uv", "dimmer"):
                val = _clip_byte(zval.get(k))
                if val is not None:
                    cleaned[k] = val
            if "on" in zval and isinstance(zval["on"], bool):
                cleaned["on"] = zval["on"]
            if cleaned:
                zs_out[zid] = cleaned
    out["zone_state"] = zs_out

    # Motion axes, filtered by the fixture's exposed axes.
    m_raw = raw.get("motion_state")
    m_out: dict[str, float] = {}
    valid_axes = light_axes_by_id.get(lid, set())
    if isinstance(m_raw, dict):
        for axis in ("pan", "tilt", "zoom", "focus"):
            if valid_axes and axis not in valid_axes:
                continue
            val = _clip_unit(m_raw.get(axis))
            if val is not None:
                m_out[axis] = val
    out["motion_state"] = m_out
    return out


def _sanitize_proposal(
    raw: Any, *, rig_light_ids: set[int], rig_controller_ids: set[int],
    light_zones_by_id: dict[int, set[str]],
    light_axes_by_id: dict[int, set[str]],
    used_ids: set[str],
) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    pid = raw.get("proposal_id")
    if not isinstance(pid, str) or not pid.strip():
        return None
    pid = pid.strip()[:48]
    if pid in used_ids:
        return None

    kind = raw.get("kind")
    if kind not in ("state", "scene"):
        return None
    name = str(raw.get("name") or "").strip()[:128] or "Proposal"

    controller_id: Optional[int] = None
    if kind == "scene":
        cid = raw.get("controller_id")
        if not isinstance(cid, int) or cid not in rig_controller_ids:
            return None
        controller_id = cid

    lights_in = raw.get("lights")
    if not isinstance(lights_in, list):
        return None
    cleaned_lights: list[dict[str, Any]] = []
    seen_light_ids: set[int] = set()
    for entry in lights_in:
        cleaned = _sanitize_proposal_light(
            entry,
            known_light_ids=rig_light_ids,
            light_zones_by_id=light_zones_by_id,
            light_axes_by_id=light_axes_by_id,
        )
        if cleaned is None:
            continue
        lid = cleaned["light_id"]
        if lid in seen_light_ids:
            continue
        seen_light_ids.add(lid)
        cleaned_lights.append(cleaned)
    if not cleaned_lights:
        return None

    notes = raw.get("notes")
    notes_str = str(notes).strip()[:500] if notes else None

    out: dict[str, Any] = {
        "proposal_id": pid,
        "kind": kind,
        "name": name,
        "lights": cleaned_lights,
    }
    if controller_id is not None:
        out["controller_id"] = controller_id
    if notes_str:
        out["notes"] = notes_str
    return out


def _sanitize_tool_payload(
    raw: Any, sess: Session
) -> tuple[str, list[dict[str, Any]]]:
    """Return (summary, proposals[]) with all ids validated against the rig."""
    lights = sess.exec(select(Light)).all()
    controllers = sess.exec(select(Controller)).all()
    modes = sess.exec(select(LightModelMode)).all()

    mode_by_id = {m.id: m for m in modes}
    rig_light_ids: set[int] = {l.id for l in lights if l.id is not None}
    rig_controller_ids: set[int] = {
        c.id for c in controllers if c.id is not None
    }
    light_zones_by_id: dict[int, set[str]] = {}
    light_axes_by_id: dict[int, set[str]] = {}
    for l in lights:
        if l.id is None:
            continue
        mode = mode_by_id.get(l.mode_id) if l.mode_id is not None else None
        zones = set(_zone_ids_for_mode(mode))
        if zones:
            light_zones_by_id[l.id] = zones
        axes = set(_motion_axes_for_mode(mode))
        if axes:
            light_axes_by_id[l.id] = axes

    if not isinstance(raw, dict):
        return "", []
    summary = str(raw.get("summary") or "").strip()[:1000]
    proposals_raw = raw.get("proposals")
    if not isinstance(proposals_raw, list):
        return summary, []

    used_ids: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for entry in proposals_raw:
        res = _sanitize_proposal(
            entry,
            rig_light_ids=rig_light_ids,
            rig_controller_ids=rig_controller_ids,
            light_zones_by_id=light_zones_by_id,
            light_axes_by_id=light_axes_by_id,
            used_ids=used_ids,
        )
        if res is None:
            continue
        used_ids.add(res["proposal_id"])
        cleaned.append(res)
    return summary, cleaned


# ---------------------------------------------------------------------------
# Conversation serialization
# ---------------------------------------------------------------------------
def _render_message(raw_msg: dict[str, Any]) -> DesignerMessageOut:
    """Convert one stored Anthropic-shaped message into a UI-friendly form."""
    role_raw = raw_msg.get("role", "assistant")
    role: Any = "assistant"
    if role_raw == "user":
        role = "user"
    content = raw_msg.get("content")
    text_out: list[str] = []
    proposals: list[DesignerProposal] = []
    if isinstance(content, str):
        text_out.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    text_out.append(t)
            elif btype == "tool_use" and block.get("name") == _TOOL_NAME:
                inp = block.get("input") or {}
                if isinstance(inp, dict):
                    summary = inp.get("summary")
                    if isinstance(summary, str) and summary.strip():
                        text_out.append(summary.strip())
                    for p in inp.get("proposals") or []:
                        if isinstance(p, dict):
                            try:
                                proposals.append(
                                    DesignerProposal(
                                        proposal_id=str(p.get("proposal_id")),
                                        kind=p.get("kind", "state"),
                                        name=str(p.get("name") or ""),
                                        controller_id=p.get("controller_id"),
                                        notes=p.get("notes"),
                                        lights=[
                                            DesignerProposalLight(**lp)
                                            for lp in (p.get("lights") or [])
                                            if isinstance(lp, dict)
                                        ],
                                    )
                                )
                            except Exception:
                                continue
    return DesignerMessageOut(
        role=role,
        text="\n\n".join(s for s in text_out if s),
        proposals=proposals,
    )


def _convo_to_out(row: DesignerConversation) -> DesignerConversationOut:
    rendered: list[DesignerMessageOut] = []
    for raw in row.messages or []:
        if isinstance(raw, dict):
            rendered.append(_render_message(raw))
    last_props: list[DesignerProposal] = []
    lp = row.last_proposal
    if isinstance(lp, dict):
        for p in lp.get("proposals") or []:
            if isinstance(p, dict):
                try:
                    last_props.append(
                        DesignerProposal(
                            proposal_id=str(p.get("proposal_id")),
                            kind=p.get("kind", "state"),
                            name=str(p.get("name") or ""),
                            controller_id=p.get("controller_id"),
                            notes=p.get("notes"),
                            lights=[
                                DesignerProposalLight(**lp)
                                for lp in (p.get("lights") or [])
                                if isinstance(lp, dict)
                            ],
                        )
                    )
                except Exception:
                    continue
    return DesignerConversationOut(
        id=row.id,
        name=row.name or "",
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        messages=rendered,
        last_proposals=last_props,
    )


def _convo_summary(row: DesignerConversation) -> DesignerConversationSummary:
    return DesignerConversationSummary(
        id=row.id,
        name=row.name or "",
        message_count=len(row.messages or []),
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------
@router.get("/status")
def designer_status() -> dict[str, Any]:
    return {
        "enabled": bool(ANTHROPIC_API_KEY),
        "model": ANTHROPIC_MODEL,
    }


@router.get("/conversations")
def list_conversations(
    sess: Session = Depends(get_session),
) -> list[DesignerConversationSummary]:
    rows = sess.exec(
        select(DesignerConversation).order_by(
            DesignerConversation.updated_at.desc()
        )
    ).all()
    return [_convo_summary(r) for r in rows]


@router.post("/conversations", status_code=201)
def create_conversation(
    payload: DesignerConversationCreate,
    sess: Session = Depends(get_session),
) -> DesignerConversationOut:
    name = (payload.name or "").strip()[:128]
    now = datetime.utcnow()
    row = DesignerConversation(
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
) -> DesignerConversationOut:
    row = sess.get(DesignerConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    return _convo_to_out(row)


@router.patch("/conversations/{cid}")
def rename_conversation(
    cid: int,
    payload: DesignerConversationRename,
    sess: Session = Depends(get_session),
) -> DesignerConversationOut:
    row = sess.get(DesignerConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    row.name = payload.name
    row.updated_at = datetime.utcnow()
    sess.add(row)
    sess.commit()
    sess.refresh(row)
    return _convo_to_out(row)


@router.delete("/conversations/{cid}", status_code=204, response_model=None)
def delete_conversation(
    cid: int, sess: Session = Depends(get_session)
) -> None:
    row = sess.get(DesignerConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    sess.delete(row)
    sess.commit()


# ---------------------------------------------------------------------------
# Proposal lookup + Apply + Save
# ---------------------------------------------------------------------------
def _find_proposal(
    row: DesignerConversation, proposal_id: str
) -> dict[str, Any]:
    lp = row.last_proposal
    if not isinstance(lp, dict):
        raise HTTPException(404, "no proposals available in this conversation")
    for p in lp.get("proposals") or []:
        if isinstance(p, dict) and str(p.get("proposal_id")) == proposal_id:
            return p
    raise HTTPException(404, f"unknown proposal_id '{proposal_id}'")


def _proposal_to_state_entry(pl: dict[str, Any]) -> dict[str, Any]:
    """Shape a DesignerProposalLight dict for apply_state_to_light."""
    return {
        "light_id": int(pl["light_id"]),
        "r": int(pl.get("r", 0)),
        "g": int(pl.get("g", 0)),
        "b": int(pl.get("b", 0)),
        "w": int(pl.get("w", 0)),
        "a": int(pl.get("a", 0)),
        "uv": int(pl.get("uv", 0)),
        "dimmer": int(pl.get("dimmer", 255)),
        "on": bool(pl.get("on", True)),
        "zone_state": dict(pl.get("zone_state") or {}),
        "motion_state": dict(pl.get("motion_state") or {}),
    }


@router.post("/conversations/{cid}/apply")
def apply_proposal(
    cid: int,
    payload: DesignerApplyRequest,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    row = sess.get(DesignerConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    prop = _find_proposal(row, payload.proposal_id)
    entries = [
        _proposal_to_state_entry(pl)
        for pl in prop.get("lights") or []
        if isinstance(pl, dict) and isinstance(pl.get("light_id"), int)
    ]
    by_id = {e["light_id"]: e for e in entries}
    if not by_id:
        return {"ok": True, "applied": 0}

    effect_engine.stop_affecting(set(by_id.keys()))

    lights = sess.exec(select(Light).where(Light.id.in_(list(by_id.keys())))).all()
    applied = 0
    for light in lights:
        entry = by_id.get(light.id)
        if entry is None:
            continue
        apply_state_to_light(light, entry)
        sess.add(light)
        applied += 1
    sess.commit()
    for light in lights:
        push_light(light)
    return {"ok": True, "applied": applied}


@router.post("/conversations/{cid}/save")
def save_proposal(
    cid: int,
    payload: DesignerSaveRequest,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    row = sess.get(DesignerConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")
    prop = _find_proposal(row, payload.proposal_id)
    name = (payload.name or prop.get("name") or "").strip()[:128]
    if not name:
        raise HTTPException(400, "save requires a non-empty name")

    entries = [
        _proposal_to_state_entry(pl)
        for pl in prop.get("lights") or []
        if isinstance(pl, dict) and isinstance(pl.get("light_id"), int)
    ]
    if not entries:
        raise HTTPException(400, "proposal has no lights to save")

    kind = prop.get("kind")
    if kind == "scene":
        cid_target = prop.get("controller_id")
        if not isinstance(cid_target, int):
            raise HTTPException(400, "scene proposal missing controller_id")
        ctrl = sess.get(Controller, cid_target)
        if ctrl is None:
            raise HTTPException(400, "scene controller no longer exists")
        scene = Scene(
            name=name,
            controller_id=cid_target,
            cross_controller=False,
            lights=entries,
        )
        sess.add(scene)
        sess.commit()
        sess.refresh(scene)
        return {"ok": True, "kind": "scene", "id": scene.id, "name": name}

    # kind == 'state'
    state = State(name=name, lights=entries)
    sess.add(state)
    sess.commit()
    sess.refresh(state)
    return {"ok": True, "kind": "state", "id": state.id, "name": name}


# ---------------------------------------------------------------------------
# Streaming send-message endpoint (SSE)
# ---------------------------------------------------------------------------
def _build_messages_for_api(
    stored: Iterable[dict[str, Any]], new_user_text: str
) -> list[dict[str, Any]]:
    """Clone the stored Anthropic-shaped history and append the new user turn.

    Truncate to the most recent ``_MAX_TURNS_HISTORY`` turns to keep the
    request within Claude's context window on very long conversations."""
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
    payload: DesignerMessageIn,
    request: Request,
    sess: Session = Depends(get_session),
) -> StreamingResponse:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "Claude is not configured on this server")

    row = sess.get(DesignerConversation, cid)
    if row is None:
        raise HTTPException(404, "conversation not found")

    try:
        import anthropic  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            503, "anthropic package is not installed on the server"
        ) from exc

    rig = _build_rig_context(sess)
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
            """Run in a worker thread: stream Claude and push events to queue."""
            try:
                client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                with client.messages.stream(
                    model=ANTHROPIC_MODEL,
                    max_tokens=8192,
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
                                        (
                                            "tool_delta",
                                            {"partial_json": pj},
                                        ),
                                    )
                        # content_block_stop / message_stop are ignored;
                        # we collect the final message after the loop.
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
                                        "name": getattr(blk, "name", "")
                                        or "",
                                        "input": inp
                                        if isinstance(inp, dict)
                                        else {},
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
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("designer stream failed")
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"message": f"Claude request failed: {exc}"}),
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("__done__", None)
                )

        producer_task = loop.run_in_executor(None, producer)

        yield _sse_event(
            "start",
            {"conversation_id": conversation_id},
        )

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

        if errored or stop_flag["cancelled"] and not final_content_blocks:
            return

        # Persist the turn and emit proposal + done.
        assistant_msg = {"role": "assistant", "content": final_content_blocks}
        user_msg = {"role": "user", "content": user_text}
        proposals_clean: list[dict[str, Any]] = []
        summary_text = ""
        for block in final_content_blocks:
            if block.get("type") == "tool_use" and block.get("name") == _TOOL_NAME:
                summary_text, proposals_clean = _sanitize_tool_payload(
                    block.get("input"), sess
                )
                break

        # Rewrite the tool_use input in place with the sanitized payload so
        # the stored history matches what we'll later Apply/Save on.
        for block in final_content_blocks:
            if block.get("type") == "tool_use" and block.get("name") == _TOOL_NAME:
                block["input"] = {
                    "summary": summary_text,
                    "proposals": proposals_clean,
                }
                break

        try:
            refreshed = sess.get(DesignerConversation, conversation_id)
            if refreshed is not None:
                msgs = list(refreshed.messages or [])
                msgs.append(user_msg)
                msgs.append(assistant_msg)
                refreshed.messages = msgs
                refreshed.last_proposal = {
                    "summary": summary_text,
                    "proposals": proposals_clean,
                }
                if not refreshed.name:
                    refreshed.name = user_text.strip().splitlines()[0][:64]
                refreshed.updated_at = datetime.utcnow()
                sess.add(refreshed)
                sess.commit()
                sess.refresh(refreshed)
                out = _convo_to_out(refreshed)
                yield _sse_event(
                    "proposal",
                    [p.model_dump() for p in out.last_proposals],
                )
                yield _sse_event(
                    "done",
                    {"conversation": out.model_dump()},
                )
        except Exception as exc:
            log.exception("failed to persist designer turn")
            yield _sse_event(
                "error",
                {"message": f"failed to persist turn: {exc}"},
            )

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        stream_gen(), media_type="text/event-stream", headers=headers
    )
