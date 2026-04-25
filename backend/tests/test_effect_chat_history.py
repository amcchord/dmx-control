"""Tests for ``_build_messages_for_api`` in the effect chat router.

The Anthropic API rejects multi-turn flows where an assistant ``tool_use``
block is not immediately followed by a matching ``tool_result`` in the
next user message. Our chat UI doesn't surface tool results back to
Claude, so the helper has to synthesize placeholder acknowledgments.
"""

from __future__ import annotations


def test_history_without_tool_use_passes_through():
    from app.routers.effect_chat import _build_messages_for_api

    stored = [
        {"role": "user", "content": "make a chase"},
        {"role": "assistant", "content": "ok"},
    ]
    out = _build_messages_for_api(stored, "now faster")
    assert out == [
        {"role": "user", "content": "make a chase"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "now faster"},
    ]


def test_assistant_tool_use_gets_synthetic_tool_result():
    from app.routers.effect_chat import _build_messages_for_api

    stored = [
        {"role": "user", "content": "make a chase"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Sure."},
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "propose_effect",
                    "input": {"name": "Chase"},
                },
            ],
        },
    ]
    out = _build_messages_for_api(stored, "now faster")
    assert len(out) == 3
    assert out[0] == {"role": "user", "content": "make a chase"}
    assert out[1]["role"] == "assistant"
    last = out[-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    # First block is the tool_result acknowledgment for the prior tool_use.
    assert last["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_abc",
        "content": "applied",
    }
    # Followed by the user's new text.
    assert last["content"][1] == {"type": "text", "text": "now faster"}


def test_multiple_tool_uses_in_one_turn_are_all_acknowledged():
    from app.routers.effect_chat import _build_messages_for_api

    stored = [
        {"role": "user", "content": "two effects"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "x", "input": {}},
            ],
        },
    ]
    out = _build_messages_for_api(stored, "tweak")
    last = out[-1]
    ids = [
        b["tool_use_id"]
        for b in last["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert ids == ["t1", "t2"]


def test_pending_tool_uses_fold_into_next_stored_user_turn():
    """Ensure the placeholders ride along on whichever user turn comes
    next, not just on the brand-new one we're appending."""
    from app.routers.effect_chat import _build_messages_for_api

    stored = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            ],
        },
        {"role": "user", "content": "second"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t2", "name": "x", "input": {}},
            ],
        },
    ]
    out = _build_messages_for_api(stored, "third")
    # second user turn should have ack for t1 prepended.
    second_user = out[2]
    assert second_user["role"] == "user"
    assert isinstance(second_user["content"], list)
    assert second_user["content"][0]["type"] == "tool_result"
    assert second_user["content"][0]["tool_use_id"] == "t1"
    # third user turn should ack t2.
    last = out[-1]
    assert last["content"][0]["tool_use_id"] == "t2"
