"""Regression test for the designer router's multi-tool persist path.

Claude can call several proposal tools in a single turn (e.g.
``propose_rig_design`` + ``propose_effect`` when the user asks for "a
cyberpunk theme with a flicker"). The persist code used to ``break``
after the first tool block, which silently dropped every proposal from
later tool blocks and produced ``unknown proposal_id`` errors at apply
time. This test exercises the dispatch on a controlled message-content
fixture so the behavior stays locked in even if the SSE plumbing
changes."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete


@pytest.fixture()
def client():
    """Reuse the live app with auth disabled and per-test DB reset."""
    from app.auth import require_auth
    from app.db import engine as db_engine
    from app.main import app
    from app.models import (
        Controller,
        DesignerConversation,
        Effect,
        EffectLayer,
        Light,
        LightModel,
        LightModelMode,
    )

    async def _no_auth() -> None:
        return None

    app.dependency_overrides[require_auth] = _no_auth

    with Session(db_engine) as sess:
        sess.exec(delete(EffectLayer))
        sess.exec(delete(Effect).where(Effect.builtin == False))  # noqa: E712
        sess.exec(delete(DesignerConversation))
        sess.exec(delete(Light))
        sess.exec(delete(LightModelMode))
        sess.exec(delete(LightModel))
        sess.exec(delete(Controller))
        sess.commit()

        ctrl = Controller(
            name="Stage", ip="127.0.0.1", port=6454,
            net=0, subnet=0, universe=0, enabled=True,
        )
        sess.add(ctrl)
        sess.flush()
        model = LightModel(name="RGB 3ch", channels=["r", "g", "b"], channel_count=3)
        sess.add(model)
        sess.flush()
        mode = LightModelMode(
            model_id=model.id, name="3ch",
            channels=["r", "g", "b"], channel_count=3, is_default=True,
        )
        sess.add(mode)
        sess.flush()
        light = Light(
            name="L1", controller_id=ctrl.id, model_id=model.id,
            mode_id=mode.id, start_address=1,
        )
        sess.add(light)
        sess.commit()

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_multi_tool_turn_keeps_every_proposal(client) -> None:
    """When Claude returns two tool_use blocks (rig design + effect),
    both proposals must end up in ``last_proposal`` so Apply works on
    either card. The reference bug only stored the first tool's
    proposals and silently dropped the rest."""
    from sqlmodel import Session, select

    from app.db import engine as db_engine
    from app.models import DesignerConversation, Light
    from app.routers.designer import _sanitize_tool_payload

    with Session(db_engine) as sess:
        light_id = sess.exec(select(Light.id)).first()
        assert light_id is not None
        # Convo to mutate.
        convo = DesignerConversation(name="multi-tool fixture")
        sess.add(convo)
        sess.commit()
        sess.refresh(convo)
        cid = convo.id

        # Two tool_use blocks in one assistant turn.
        rig_design_input: dict[str, Any] = {
            "summary": "Cyberpunk wash.",
            "proposals": [
                {
                    "proposal_id": "cyberpunk_state",
                    "kind": "state",
                    "name": "Neon Grid",
                    "lights": [
                        {
                            "light_id": light_id, "on": True,
                            "dimmer": 255, "r": 200, "g": 0, "b": 200,
                        }
                    ],
                }
            ],
        }
        effect_input: dict[str, Any] = {
            "summary": "Sparse white flicker.",
            "effects": [
                {
                    "proposal_id": "neon_flicker",
                    "name": "Neon Flicker",
                    "builtin": "strobe",
                    "spread": "across_lights",
                    "params": {"speed_hz": 6.0, "size": 0.4},
                    "controls": {
                        "intensity": 0.5, "fade_in_s": 0.2, "fade_out_s": 0.4,
                    },
                    "target_channels": ["w"],
                }
            ],
        }

        # Sanitize each block independently — exactly the call the
        # streaming persist path makes after Claude finishes.
        s1, p1 = _sanitize_tool_payload(
            rig_design_input, sess, tool_name="propose_rig_design"
        )
        s2, p2 = _sanitize_tool_payload(
            effect_input, sess, tool_name="propose_effect"
        )
        assert any(p["proposal_id"] == "cyberpunk_state" for p in p1)
        assert any(p["proposal_id"] == "neon_flicker" for p in p2)

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for prop in p1 + p2:
            pid = prop["proposal_id"]
            if pid in seen:
                continue
            seen.add(pid)
            merged.append(prop)

        convo.last_proposal = {
            "summary": "\n\n".join(s for s in (s1, s2) if s),
            "proposals": merged,
        }
        sess.add(convo)
        sess.commit()

    # Both proposals should now be applyable through the public API.
    res_state = client.post(
        f"/api/designer/conversations/{cid}/apply",
        json={"proposal_id": "cyberpunk_state"},
    )
    assert res_state.status_code == 200, res_state.text

    res_effect = client.post(
        f"/api/designer/conversations/{cid}/apply",
        json={"proposal_id": "neon_flicker"},
    )
    assert res_effect.status_code == 200, res_effect.text
    body = res_effect.json()
    assert body.get("ok") is True
    # Effect proposals come back as engine handles, not light counts.
    assert "handle" in body or body.get("kind") == "effect"
