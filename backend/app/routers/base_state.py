"""Read-only endpoint for the base-state change log.

The log tracks recent manual color sets, scene/state applies, palette
applies, and blackouts so the layers panel can answer "why is this
light red?" even when no effect layer is running. Live updates are
fanned out through the existing ``/api/layers/ws`` connection (see
:mod:`.layers`); this endpoint is the cold-start snapshot.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..auth import AuthDep
from ..base_state_log import log as base_state_log

router = APIRouter(
    prefix="/api/base-state",
    tags=["base-state"],
    dependencies=[AuthDep],
)


@router.get("/log")
def get_log() -> list[dict]:
    """Return the recent base-state change log, newest first."""
    return base_state_log.snapshot()
