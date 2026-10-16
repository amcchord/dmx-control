"""Current Claude request settings shared by every AI feature.

Verified against the provider catalog and Opus/Sonnet 5.5 migration guides
on 2026-10-16. Thinking signatures must survive tool-use round trips.
"""
from typing import Any


def request_options(effort: str = "low") -> dict:
    return {"tool_choice": {"type": "auto"},
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": effort}}


def serialize_content(message) -> list[dict[str, Any]]:
    blocks = []
    for block in getattr(message, "content", []) or []:
        if hasattr(block, "model_dump"):
            blocks.append(block.model_dump(mode="json", exclude_none=True))
        elif isinstance(block, dict):
            blocks.append(dict(block))
        else:
            blocks.append({k: v for k, v in vars(block).items() if not k.startswith("_")})
    return blocks
