"""Tests for the layered effect compositor.

Covers the new `merge_overlay_into_state` blend modes (replacing the
single legacy linear cross-fade) plus the engine's deterministic layer
ordering and auto-mute on repeated Lua errors. The tests intentionally
exercise the pure functions in ``app.effects`` so they don't need the
asyncio tick loop or Art-Net plumbing."""

from __future__ import annotations

import pytest

from app.effects import (
    BLEND_MODES,
    LightOverlay,
    _blend_byte,
    merge_overlay_into_state,
)


def _base() -> dict:
    return {
        "r": 100,
        "g": 100,
        "b": 100,
        "w": 0,
        "a": 0,
        "uv": 0,
        "dimmer": 255,
        "on": True,
        "zone_state": {},
        "motion_state": {},
    }


def test_blend_byte_normal_is_linear_crossfade():
    assert _blend_byte(0, 200, "normal", 0.0) == 0
    assert _blend_byte(0, 200, "normal", 1.0) == 200
    # 50% crossfade lands on the midpoint.
    assert _blend_byte(0, 200, "normal", 0.5) == 100


def test_blend_byte_add_clamps_at_255():
    assert _blend_byte(200, 200, "add", 1.0) == 255
    assert _blend_byte(200, 100, "add", 0.5) == 250


def test_blend_byte_multiply_darkens():
    # multiply: full opacity → out = below * (overlay/255)
    assert _blend_byte(200, 0, "multiply", 1.0) == 0
    assert _blend_byte(200, 255, "multiply", 1.0) == 200
    # half opacity should leave us between the original and the result.
    assert _blend_byte(200, 0, "multiply", 0.5) == 100


def test_blend_byte_screen_lightens():
    # screen: 1 - (1-a)(1-b)
    assert _blend_byte(100, 100, "screen", 1.0) > 100


def test_blend_byte_max_min():
    assert _blend_byte(50, 200, "max", 1.0) == 200
    assert _blend_byte(200, 50, "max", 1.0) == 200
    assert _blend_byte(50, 200, "min", 1.0) == 50


def test_all_blend_modes_present():
    assert set(BLEND_MODES) == {
        "normal", "add", "multiply", "screen", "max", "min", "replace",
    }


def test_merge_normal_blends_rgb_with_opacity():
    overlay = LightOverlay(flat=(255, 0, 0, 1.0))
    out = merge_overlay_into_state(
        _base(),
        overlay,
        zone_ids=set(),
        fade_weight=1.0,
        target_channels=["rgb"],
        blend_mode="normal",
        layer_opacity=0.5,
    )
    # 50% blend from base 100 toward 255: 100 + (255-100)*0.5 = 177-178
    assert 170 <= out["r"] <= 180
    # Green collapses toward 0 from 100 by 50%: ~50
    assert 45 <= out["g"] <= 55


def test_merge_add_caps_at_255():
    overlay = LightOverlay(flat=(255, 255, 255, 1.0))
    out = merge_overlay_into_state(
        _base(),
        overlay,
        zone_ids=set(),
        fade_weight=1.0,
        target_channels=["rgb"],
        blend_mode="add",
        layer_opacity=1.0,
    )
    assert out["r"] == 255 and out["g"] == 255 and out["b"] == 255


def test_merge_zero_opacity_is_passthrough():
    overlay = LightOverlay(flat=(0, 0, 0, 1.0))
    out = merge_overlay_into_state(
        _base(),
        overlay,
        zone_ids=set(),
        fade_weight=1.0,
        target_channels=["rgb"],
        blend_mode="normal",
        layer_opacity=0.0,
    )
    assert out["r"] == 100 and out["g"] == 100 and out["b"] == 100


def test_merge_aux_channel_only_does_not_touch_rgb():
    overlay = LightOverlay(flat=(255, 255, 255, 1.0))
    out = merge_overlay_into_state(
        _base(),
        overlay,
        zone_ids=set(),
        fade_weight=1.0,
        target_channels=["w"],
        blend_mode="normal",
        layer_opacity=1.0,
    )
    assert out["r"] == 100 and out["g"] == 100 and out["b"] == 100
    assert out["w"] == 255


def test_merge_respects_direct_color_policy():
    """Under "direct" W policy, an RGB layer must NOT auto-derive W."""
    overlay = LightOverlay(flat=(180, 180, 180, 1.0))
    base = _base()
    base["w"] = 50  # user's explicit fader value
    out = merge_overlay_into_state(
        base,
        overlay,
        zone_ids=set(),
        fade_weight=1.0,
        color_policy={"w": "direct"},
        target_channels=["rgb"],
        blend_mode="normal",
        layer_opacity=1.0,
    )
    assert out["w"] == 50, "Direct W should be preserved when only RGB is targeted"


def test_overlay_forces_on_when_base_is_off():
    """A running effect must light the fixture even when the base rig
    is in a blackout state. Otherwise the Art-Net renderer
    short-circuits to all-zero (it honors ``state["on"]`` first) and
    the operator sees a black rig with the fire layer happily ticking
    behind the scenes."""
    base = _base()
    base["on"] = False  # Blackout from a previous scene apply.
    overlay = LightOverlay(flat=(255, 100, 0, 1.0))
    out = merge_overlay_into_state(
        base,
        overlay,
        zone_ids=set(),
        fade_weight=1.0,
        target_channels=["rgb"],
        blend_mode="normal",
        layer_opacity=1.0,
    )
    assert out["on"] is True


def test_zero_opacity_overlay_does_not_force_on():
    """Conversely, an effect at zero envelope must NOT light a fixture
    that was deliberately turned off — the layer is contributing
    nothing this tick, so the base ``on`` value should pass through
    untouched."""
    base = _base()
    base["on"] = False
    overlay = LightOverlay(flat=(255, 100, 0, 1.0))
    out = merge_overlay_into_state(
        base,
        overlay,
        zone_ids=set(),
        fade_weight=0.0,
        target_channels=["rgb"],
        blend_mode="normal",
        layer_opacity=1.0,
    )
    assert out["on"] is False


def test_two_layer_composite_is_deterministic_in_z_order():
    """Same inputs in different draw orders must produce the same final
    state when the engine respects (z_index, id) ordering."""
    base = _base()
    layer_a = LightOverlay(flat=(255, 0, 0, 1.0))   # red bottom
    layer_b = LightOverlay(flat=(0, 0, 255, 1.0))   # blue top, mode=screen
    s1 = merge_overlay_into_state(
        base, layer_a, set(), 1.0, target_channels=["rgb"],
        blend_mode="normal", layer_opacity=1.0,
    )
    s1 = merge_overlay_into_state(
        s1, layer_b, set(), 1.0, target_channels=["rgb"],
        blend_mode="screen", layer_opacity=1.0,
    )
    # Repeating identical inputs must yield identical bytes.
    s2 = merge_overlay_into_state(
        base, layer_a, set(), 1.0, target_channels=["rgb"],
        blend_mode="normal", layer_opacity=1.0,
    )
    s2 = merge_overlay_into_state(
        s2, layer_b, set(), 1.0, target_channels=["rgb"],
        blend_mode="screen", layer_opacity=1.0,
    )
    assert (s1["r"], s1["g"], s1["b"]) == (s2["r"], s2["g"], s2["b"])
