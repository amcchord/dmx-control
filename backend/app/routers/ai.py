"""Claude-based fixture manual parser.

Takes a PDF or image upload of a DMX fixture manual and returns a structured
list of channel modes. We use a forced ``tool_use`` Messages API call for
reliable structured output.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile

from ..auth import AuthDep
from ..config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from ..schemas import CHANNEL_ROLES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ai"], dependencies=[AuthDep])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ACCEPTED_TYPES = {
    "application/pdf": "document",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/webp": "image",
}

_ROLE_LIST = sorted(CHANNEL_ROLES)
_TOOL_NAME = "record_fixture_modes"


_LAYOUT_SHAPES = ["single", "linear", "grid", "ring", "cluster"]
_ZONE_KINDS = [
    "pixel",
    "segment",
    "ring",
    "panel",
    "eye",
    "head",
    "beam",
    "global",
    "other",
]
_COLOR_ROLES = ["r", "g", "b", "w", "a", "uv"]


def _build_tool_schema() -> dict[str, Any]:
    zone_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {
                "type": "string",
                "description": (
                    "Stable identifier for this zone (e.g. 'p0', 'beam', "
                    "'board1'). Must be unique within the mode."
                ),
            },
            "label": {
                "type": "string",
                "description": (
                    "Human label (e.g. 'Pixel 1', 'LED Ball', 'Halo Ring')."
                ),
            },
            "kind": {"type": "string", "enum": _ZONE_KINDS},
            "row": {"type": "integer", "minimum": 0},
            "col": {"type": "integer", "minimum": 0},
            "colors": {
                "type": "object",
                "additionalProperties": False,
                "description": (
                    "Map of color role -> 0-based channel index within this "
                    "mode's channel list. Include only the roles this zone "
                    "actually exposes."
                ),
                "properties": {
                    role: {"type": "integer", "minimum": 0}
                    for role in _COLOR_ROLES
                },
            },
            "dimmer": {
                "type": "integer",
                "minimum": 0,
                "description": "Per-zone dimmer channel index, if any.",
            },
            "strobe": {
                "type": "integer",
                "minimum": 0,
                "description": "Per-zone strobe channel index, if any.",
            },
        },
        "required": ["id", "label", "colors"],
    }

    motion_schema = {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Motion axes channel offsets (0-based within the mode). Include "
            "only the axes the mode actually exposes. Use the *_fine fields "
            "for 16-bit high-resolution variants."
        ),
        "properties": {
            "pan": {"type": "integer", "minimum": 0},
            "pan_fine": {"type": "integer", "minimum": 0},
            "tilt": {"type": "integer", "minimum": 0},
            "tilt_fine": {"type": "integer", "minimum": 0},
            "zoom": {"type": "integer", "minimum": 0},
            "focus": {"type": "integer", "minimum": 0},
            "pan_degrees": {"type": "number"},
            "tilt_degrees": {"type": "number"},
        },
    }

    globals_schema = {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Fixture-wide channel offsets (0-based within the mode) for roles "
            "that don't belong to any single zone."
        ),
        "properties": {
            "dimmer": {"type": "integer", "minimum": 0},
            "strobe": {"type": "integer", "minimum": 0},
            "macro": {"type": "integer", "minimum": 0},
            "speed": {"type": "integer", "minimum": 0},
        },
    }

    layout_schema = {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Optional structural overlay describing how this mode's channels "
            "break down into independently-addressable zones and motion axes. "
            "Include only when the mode exposes more than one zone, or when "
            "PTZ axes are present."
        ),
        "properties": {
            "shape": {"type": "string", "enum": _LAYOUT_SHAPES},
            "cols": {"type": "integer", "minimum": 1},
            "rows": {"type": "integer", "minimum": 1},
            "zones": {"type": "array", "items": zone_schema},
            "motion": motion_schema,
            "globals": globals_schema,
        },
        "required": ["shape", "zones"],
    }

    return {
        "name": _TOOL_NAME,
        "description": (
            "Record the fixture's name and every channel mode documented in "
            "the manual. Use exactly one entry per mode."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "suggested_name": {
                    "type": "string",
                    "description": "Make and model of the fixture.",
                },
                "modes": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "Short mode label, e.g. '3ch', '7ch', "
                                    "'Extended', or the manufacturer's name."
                                ),
                            },
                            "channels": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "string",
                                    "enum": _ROLE_LIST,
                                },
                                "description": (
                                    "Ordered DMX channel roles, one per DMX "
                                    "slot. Use 'other' for channels without a "
                                    "clear color/intensity purpose."
                                ),
                            },
                            "layout": layout_schema,
                            "notes": {"type": "string"},
                        },
                        "required": ["name", "channels"],
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Any top-level caveats or observations.",
                },
            },
            "required": ["suggested_name", "modes"],
        },
    }


_SYSTEM_PROMPT = (
    "You are a lighting technician helping configure DMX fixtures. Given a "
    "fixture manual or spec sheet, extract the make/model and every documented "
    "DMX channel mode. For each mode, list the channel roles in the exact "
    "order they occupy DMX slots.\n\n"
    f"Only use these role tokens: {', '.join(_ROLE_LIST)}. Pick the closest "
    "match: 'r','g','b' for RGB color channels; 'w' for white; 'a' for amber; "
    "'uv' for ultraviolet; 'dimmer' for an intensity/master dimmer channel; "
    "'strobe' for strobe/shutter; 'macro' for color macros or built-in "
    "programs; 'speed' for program/strobe speed; 'pan'/'tilt' for moving-head "
    "position (use 'pan_fine'/'tilt_fine' for 16-bit fine-adjustment bytes); "
    "'zoom'/'focus' for moving-head optics; "
    "'other' for anything that doesn't fit (e.g. sound sensitivity, "
    "dimmer curve). Always record the channel count and order faithfully even "
    "if you have to use 'other'. Return a ``notes`` field for any caveats you "
    "couldn't express in structured form. Do not invent modes — only include "
    "ones explicitly documented.\n\n"
    "Compound fixtures: whenever a mode addresses multiple physical "
    "sub-elements independently (pixel bars, rings, moving-head boards, "
    "multi-eye fixtures), also populate the per-mode ``layout`` object. Each "
    "independently-controllable sub-element is a ``zone``; its ``colors`` "
    "map records the 0-based channel index (within this mode's channel "
    "list) for each color role it exposes. Use ``shape: 'linear'`` with "
    "``cols`` for pixel bars, ``shape: 'grid'`` with ``rows``/``cols`` for "
    "matrix panels, ``shape: 'ring'`` for halos, and ``shape: 'cluster'`` "
    "for heterogeneous moving heads (name zones like 'Beam', 'Ball', 'Board "
    "1'). Use ``shape: 'single'`` (or omit ``layout`` entirely) for fixtures "
    "that address all their LEDs as one block. Record motion axes under "
    "``layout.motion`` (with ``pan_degrees``/``tilt_degrees`` when the "
    "manual documents the range) and fixture-wide channels (master dimmer, "
    "global strobe, macro/program, speed) under ``layout.globals``. All "
    "channel indices are 0-based offsets within the mode's channel list."
)


@router.get("/ai/status")
def ai_status() -> dict[str, Any]:
    return {"enabled": bool(ANTHROPIC_API_KEY), "model": ANTHROPIC_MODEL}


@router.post("/models/parse-manual")
async def parse_manual(file: UploadFile) -> dict[str, Any]:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "Claude is not configured on this server")

    ctype = (file.content_type or "").lower()
    # Some browsers send the generic octet-stream; fall back on the filename.
    if ctype not in ACCEPTED_TYPES and file.filename:
        lower = file.filename.lower()
        if lower.endswith(".pdf"):
            ctype = "application/pdf"
        elif lower.endswith(".png"):
            ctype = "image/png"
        elif lower.endswith(".jpg") or lower.endswith(".jpeg"):
            ctype = "image/jpeg"
        elif lower.endswith(".webp"):
            ctype = "image/webp"
    if ctype not in ACCEPTED_TYPES:
        raise HTTPException(
            400, f"unsupported file type '{file.content_type}'"
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (max 25 MB)")

    block_kind = ACCEPTED_TYPES[ctype]
    media_type = "application/pdf" if block_kind == "document" else ctype
    data_b64 = base64.standard_b64encode(raw).decode("ascii")
    content_block: dict[str, Any] = {
        "type": block_kind,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data_b64,
        },
    }

    try:
        import anthropic
    except ImportError as exc:
        raise HTTPException(
            503, "anthropic package is not installed on the server"
        ) from exc

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        message = await _run_anthropic(
            client,
            [
                content_block,
                {
                    "type": "text",
                    "text": (
                        "Record every channel mode documented in this manual "
                        "using the record_fixture_modes tool."
                    ),
                },
            ],
        )
    except anthropic.APIStatusError as exc:
        log.warning("Anthropic API error: %s", exc)
        raise HTTPException(502, f"Claude API error: {exc.message}") from exc
    except anthropic.APIError as exc:
        log.warning("Anthropic error: %s", exc)
        raise HTTPException(502, "Claude request failed") from exc

    payload = _extract_tool_use(message)
    if payload is None:
        raise HTTPException(
            502,
            "Claude did not return structured output. Try a clearer manual "
            "or a different page.",
        )

    return _sanitize(payload)


async def _run_anthropic(client, content: list[dict[str, Any]]):
    import anthropic  # noqa: F401 - ensure module is importable here too
    import asyncio

    def _do_call():
        return client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[_build_tool_schema()],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": content}],
        )

    return await asyncio.to_thread(_do_call)


def _extract_tool_use(message) -> dict[str, Any] | None:
    for block in getattr(message, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                return inp
    return None


_MAX_CHANNELS = 512


def _clean_index(val: Any, upper: int) -> int | None:
    if not isinstance(val, int) or isinstance(val, bool):
        return None
    if val < 0 or val >= upper:
        return None
    return val


def _sanitize_layout(
    raw_layout: Any, channel_count: int
) -> dict[str, Any] | None:
    """Best-effort pass-through of Claude's layout with index bounds checked.

    Returns None if the layout doesn't describe any zones or motion axes."""
    if not isinstance(raw_layout, dict):
        return None
    shape = raw_layout.get("shape")
    if not isinstance(shape, str) or shape not in _LAYOUT_SHAPES:
        shape = "single"

    cols = _clean_index(raw_layout.get("cols"), channel_count + 1)
    rows = _clean_index(raw_layout.get("rows"), channel_count + 1)

    cleaned_zones: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_zone in raw_layout.get("zones") or []:
        if not isinstance(raw_zone, dict):
            continue
        zid = str(raw_zone.get("id") or "").strip()[:32]
        if not zid or zid in seen_ids:
            continue
        label = str(raw_zone.get("label") or zid).strip()[:64]
        kind = raw_zone.get("kind")
        if not isinstance(kind, str) or kind not in _ZONE_KINDS:
            kind = "other"
        row = _clean_index(raw_zone.get("row"), 1024)
        col = _clean_index(raw_zone.get("col"), 1024)

        colors_raw = raw_zone.get("colors") or {}
        colors: dict[str, int] = {}
        if isinstance(colors_raw, dict):
            for role, idx in colors_raw.items():
                if role not in _COLOR_ROLES:
                    continue
                ci = _clean_index(idx, channel_count)
                if ci is None:
                    continue
                colors[role] = ci
        if not colors and raw_zone.get("dimmer") is None:
            # A zone with neither color nor dimmer is meaningless.
            continue

        zone: dict[str, Any] = {
            "id": zid,
            "label": label,
            "kind": kind,
            "colors": colors,
        }
        if row is not None:
            zone["row"] = row
        if col is not None:
            zone["col"] = col
        dim_idx = _clean_index(raw_zone.get("dimmer"), channel_count)
        if dim_idx is not None:
            zone["dimmer"] = dim_idx
        strobe_idx = _clean_index(raw_zone.get("strobe"), channel_count)
        if strobe_idx is not None:
            zone["strobe"] = strobe_idx
        cleaned_zones.append(zone)
        seen_ids.add(zid)

    motion: dict[str, Any] = {}
    raw_motion = raw_layout.get("motion") or {}
    if isinstance(raw_motion, dict):
        for axis in ("pan", "pan_fine", "tilt", "tilt_fine", "zoom", "focus"):
            idx = _clean_index(raw_motion.get(axis), channel_count)
            if idx is not None:
                motion[axis] = idx
        for deg_key in ("pan_degrees", "tilt_degrees"):
            val = raw_motion.get(deg_key)
            if isinstance(val, (int, float)) and 0 < val < 10_000:
                motion[deg_key] = float(val)

    globals_: dict[str, Any] = {}
    raw_globals = raw_layout.get("globals") or {}
    if isinstance(raw_globals, dict):
        for key in ("dimmer", "strobe", "macro", "speed"):
            idx = _clean_index(raw_globals.get(key), channel_count)
            if idx is not None:
                globals_[key] = idx

    if not cleaned_zones and not motion and not globals_:
        return None

    layout: dict[str, Any] = {"shape": shape, "zones": cleaned_zones}
    if cols is not None:
        layout["cols"] = cols
    if rows is not None:
        layout["rows"] = rows
    if motion:
        layout["motion"] = motion
    if globals_:
        layout["globals"] = globals_
    return layout


def _sanitize(raw: dict[str, Any]) -> dict[str, Any]:
    suggested = str(raw.get("suggested_name") or "").strip()[:128]
    top_notes = str(raw.get("notes") or "").strip()[:2000] or None

    cleaned_modes: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for raw_mode in raw.get("modes") or []:
        if not isinstance(raw_mode, dict):
            continue
        name = str(raw_mode.get("name") or "").strip()[:64]
        if not name:
            continue
        channels_raw = raw_mode.get("channels") or []
        if not isinstance(channels_raw, list):
            continue
        channels: list[str] = []
        for role in channels_raw:
            if not isinstance(role, str):
                continue
            r = role.strip().lower()
            if r in CHANNEL_ROLES:
                channels.append(r)
            if len(channels) >= _MAX_CHANNELS:
                break
        if not channels:
            continue
        key = (name.lower(), tuple(channels))
        if key in seen:
            continue
        seen.add(key)
        notes = raw_mode.get("notes")
        notes_str = str(notes).strip()[:500] if notes else None
        layout = _sanitize_layout(raw_mode.get("layout"), len(channels))
        cleaned_modes.append(
            {
                "name": name,
                "channels": channels,
                "notes": notes_str or None,
                "layout": layout,
            }
        )

    return {
        "suggested_name": suggested,
        "modes": cleaned_modes,
        "notes": top_notes,
    }
