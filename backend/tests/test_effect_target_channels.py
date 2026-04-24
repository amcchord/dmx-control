"""Tests for the effect overlay merge's ``target_channels`` layering.

These lock in the "chase the white channel while keeping colors steady"
contract: when ``target_channels=["w"]``, the overlay must not change
``r/g/b`` and must drive the ``w`` channel scalar from the overlay's
envelope + color.
"""

from __future__ import annotations

from app.effects import LightOverlay, merge_overlay_into_state


def _base() -> dict:
    return {
        "r": 200,
        "g": 50,
        "b": 10,
        "w": 5,
        "a": 0,
        "uv": 0,
        "dimmer": 255,
        "on": True,
        "zone_state": {},
        "motion_state": {},
    }


def _flat_overlay(
    r: int, g: int, b: int, eff: float = 1.0
) -> LightOverlay:
    return LightOverlay(flat=(r, g, b, eff))


def test_rgb_target_is_default_and_changes_color():
    """Baseline: with no target_channels the overlay behaves like before
    (blends into RGB, derives W under mix)."""
    base = _base()
    overlay = _flat_overlay(0, 0, 255, eff=1.0)
    out = merge_overlay_into_state(base, overlay, [], 1.0, None, None)
    assert out["r"] == 0
    assert out["g"] == 0
    assert out["b"] == 255


def test_w_only_target_leaves_rgb_untouched():
    """When only ``w`` is targeted, base RGB must not change."""
    base = _base()
    overlay = _flat_overlay(255, 255, 255, eff=1.0)
    out = merge_overlay_into_state(
        base, overlay, [], 1.0, None, target_channels=["w"]
    )
    assert out["r"] == 200, "RGB should remain untouched when only w is targeted"
    assert out["g"] == 50
    assert out["b"] == 10
    # The scalar from the overlay should push W toward 255 (max of rgb).
    assert out["w"] == 255


def test_w_only_with_partial_envelope_blends_with_base():
    """Partial fade_weight should mix base W with the scalar."""
    base = _base()
    overlay = _flat_overlay(200, 200, 200, eff=0.5)
    out = merge_overlay_into_state(
        base, overlay, [], 1.0, None, target_channels=["w"]
    )
    # base.w=5, scalar=200, eff=0.5 -> around 102
    assert 95 <= out["w"] <= 110
    assert out["r"] == 200
    assert out["g"] == 50
    assert out["b"] == 10


def test_uv_only_target_writes_uv_leaves_rgb():
    base = _base()
    overlay = _flat_overlay(100, 100, 100, eff=1.0)
    out = merge_overlay_into_state(
        base, overlay, [], 1.0, None, target_channels=["uv"]
    )
    assert out["r"] == 200 and out["g"] == 50 and out["b"] == 10
    assert out["uv"] == 100


def test_strobe_only_target_writes_strobe_and_leaves_rgb():
    base = _base()
    base["strobe"] = 0
    overlay = _flat_overlay(255, 255, 255, eff=1.0)
    out = merge_overlay_into_state(
        base, overlay, [], 1.0, None, target_channels=["strobe"]
    )
    assert out["r"] == 200 and out["g"] == 50 and out["b"] == 10
    assert out["strobe"] == 255


def test_rgb_plus_w_touches_both():
    """Multi-target case: both RGB and W get animated."""
    base = _base()
    overlay = _flat_overlay(0, 0, 255, eff=1.0)
    out = merge_overlay_into_state(
        base, overlay, [], 1.0, None, target_channels=["rgb", "w"]
    )
    assert out["r"] == 0 and out["g"] == 0 and out["b"] == 255
    # W is also driven by the scalar (max of rgb = 255 here)
    assert out["w"] == 255


def test_aux_only_skips_per_zone_rgb_mutation():
    """When RGB is not targeted, per-zone overlays should not mutate
    zone RGB either."""
    base = _base()
    base["zone_state"] = {"z1": {"r": 0, "g": 0, "b": 0}}
    overlay = LightOverlay(
        flat=None, zones={"z1": (255, 0, 0, 1.0)}
    )
    out = merge_overlay_into_state(
        base, overlay, ["z1"], 1.0, None, target_channels=["w"]
    )
    # Zone r should remain 0 because target_channels excludes rgb.
    assert out["zone_state"]["z1"].get("r", 0) == 0
