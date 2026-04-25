"""Tests for the per-mode W/A/UV color policy.

These cover the three places the policy has to be honored:

* ``artnet._compute_channel_values`` — flat renderer; "direct" roles must
  not be auto-derived from RGB.
* ``routers.palettes._paint_light_flat`` / ``_paint_zone`` — palette paint
  must leave "direct" W/A alone so the user's explicit value is preserved
  across palette application.
* ``schemas._normalize_color_policy`` — unknown roles silently dropped,
  invalid values rejected.
"""

from __future__ import annotations

import pytest

from app.artnet import _compute_channel_values
from app.routers.palettes import (
    _apply_entry_flat,
    _apply_entry_zone,
    _paint_light_flat,
    _paint_zone,
)
from app.schemas import PaletteEntry, _normalize_color_policy


# ---------------------------------------------------------------------------
# _compute_channel_values
# ---------------------------------------------------------------------------


def _channels() -> list[str]:
    return ["r", "g", "b", "w", "a", "uv"]


def _state(**overrides):
    base = {"r": 200, "g": 150, "b": 100, "on": True, "dimmer": 255}
    base.update(overrides)
    return base


def test_mix_policy_derives_w_from_min_rgb():
    """Default "mix" policy keeps today's W = min(R,G,B) derivation."""
    values = _compute_channel_values(_channels(), _state())
    r, g, b, w, a, uv = values
    assert (r, g, b) == (200, 150, 100)
    assert w == 100, "W should equal min(R,G,B) under mix policy"
    assert a == 75, "A should equal min(R,G)//2 under mix policy"
    assert uv == 0


def test_direct_policy_zeroes_unset_w_a():
    """Direct W/A must NOT be auto-derived; unset means 0."""
    policy = {"w": "direct", "a": "direct"}
    values = _compute_channel_values(_channels(), _state(), policy)
    _r, _g, _b, w, a, _uv = values
    assert w == 0
    assert a == 0


def test_direct_policy_preserves_explicit_value():
    """When the state dict sets W/A explicitly, direct policy passes
    those values through unchanged (subject only to the global dimmer
    scale when there is no dedicated dimmer channel)."""
    policy = {"w": "direct", "a": "direct"}
    state = _state(w=222, a=111)
    values = _compute_channel_values(_channels(), state, policy)
    _r, _g, _b, w, a, _uv = values
    assert w == 222
    assert a == 111


def test_policy_only_applies_to_roles_present_on_fixture():
    """Channels outside the mode's channel list should not be affected
    by the policy at all; the renderer simply doesn't emit them."""
    # RGBW (no amber). Setting an amber policy shouldn't change anything.
    channels = ["r", "g", "b", "w"]
    policy = {"w": "direct", "a": "direct"}
    values = _compute_channel_values(channels, _state(), policy)
    assert len(values) == 4
    assert values[3] == 0, "direct W with unset state should be 0"


def test_off_still_blanks_everything():
    """``on=False`` must blank every channel regardless of policy."""
    policy = {"w": "direct"}
    state = _state(on=False, w=255)
    values = _compute_channel_values(_channels(), state, policy)
    assert all(v == 0 for v in values)


# ---------------------------------------------------------------------------
# palettes._paint_light_flat / _paint_zone
# ---------------------------------------------------------------------------


class _FakeLight:
    """Minimal stand-in for :class:`app.models.Light` — the palette paint
    helpers only touch attribute assignments, never session state."""

    def __init__(self):
        self.r = 0
        self.g = 0
        self.b = 0
        self.w = 42
        self.a = 11
        self.on = False
        self.zone_state: dict = {"existing": "ignored"}


def test_paint_light_mix_overwrites_w_and_a():
    light = _FakeLight()
    _paint_light_flat(light, "#FFAA55")  # default policy = all "mix"
    assert light.r == 0xFF
    assert light.g == 0xAA
    assert light.b == 0x55
    assert light.w == 0x55  # min(r,g,b)
    assert light.a == (0xFF + 0) // 2 or light.a == (0xAA // 2)
    # a = min(r,g)//2 = min(0xFF, 0xAA)//2 = 0xAA//2 = 0x55
    assert light.a == 0x55


def test_paint_light_direct_preserves_w_and_a():
    light = _FakeLight()
    _paint_light_flat(light, "#FFAA55", {"w": "direct", "a": "direct"})
    assert light.r == 0xFF
    assert light.g == 0xAA
    assert light.b == 0x55
    # User-owned direct channels must be left alone.
    assert light.w == 42
    assert light.a == 11


def test_palette_entry_sets_explicit_uv_on_mix_policy():
    """The migration removed the old UV gap: a palette entry that supplies
    an explicit UV value must be written through even when the mode is
    ``mix`` (the historical default). Previously ``_paint_light_flat``
    never touched UV at all."""
    light = _FakeLight()
    entry = PaletteEntry(r=0, g=0, b=0, uv=255)
    _apply_entry_flat(light, entry)
    assert light.uv == 255, "explicit UV must be written"


def test_palette_entry_respects_direct_policy_with_explicit_value():
    """When the mode marks UV as ``direct``, an explicit entry still
    wins (palette authors can opt in to overriding the user's fader)."""
    light = _FakeLight()
    light.uv = 10
    entry = PaletteEntry(r=10, g=20, b=30, uv=200)
    _apply_entry_flat(light, entry, {"uv": "direct"})
    assert light.uv == 200


def test_palette_entry_direct_uv_without_explicit_preserves_fader():
    """Direct UV with no entry value must leave the user's fader alone."""
    light = _FakeLight()
    light.uv = 77
    entry = PaletteEntry(r=10, g=20, b=30)  # no uv
    _apply_entry_flat(light, entry, {"uv": "direct"})
    assert light.uv == 77


def test_palette_entry_explicit_w_and_a_override_derivation():
    """Explicit aux values always win over RGB-derived ones."""
    light = _FakeLight()
    entry = PaletteEntry(r=200, g=150, b=100, w=33, a=77)
    _apply_entry_flat(light, entry)
    assert light.w == 33
    assert light.a == 77


def test_palette_entry_zone_sets_uv_on_mix():
    zs_map: dict = {}
    entry = PaletteEntry(r=0, g=0, b=0, uv=180)
    _apply_entry_zone(zs_map, "z1", entry)
    zs = zs_map["z1"]
    assert zs["uv"] == 180


def test_paint_zone_direct_preserves_existing_w():
    zs_map: dict = {"z1": {"w": 200, "a": 50, "on": False}}
    _paint_zone(zs_map, "z1", "#FFAA55", {"w": "direct"})
    zs = zs_map["z1"]
    assert zs["r"] == 0xFF
    assert zs["w"] == 200  # preserved; direct
    assert zs["a"] == 0xAA // 2  # still mixed (not marked direct)
    assert zs["on"] is True


# ---------------------------------------------------------------------------
# schemas._normalize_color_policy
# ---------------------------------------------------------------------------


def test_normalize_drops_roles_not_present_in_channels():
    """Policy entries for channels the mode doesn't have must be dropped
    silently so the stored policy stays tight against the real roles."""
    out = _normalize_color_policy(
        {"w": "direct", "a": "direct", "uv": "mix"},
        ["r", "g", "b", "w"],
    )
    assert out == {"w": "direct"}


def test_normalize_drops_unknown_roles():
    out = _normalize_color_policy(
        {"dimmer": "direct", "w": "direct"},
        ["r", "g", "b", "w"],
    )
    assert out == {"w": "direct"}


def test_normalize_rejects_invalid_value():
    with pytest.raises(ValueError):
        _normalize_color_policy({"w": "bogus"}, ["r", "g", "b", "w"])


def test_normalize_empty_input_is_empty():
    assert _normalize_color_policy(None, ["r", "g", "b", "w"]) == {}
    assert _normalize_color_policy({}, ["r", "g", "b", "w"]) == {}


# ---------------------------------------------------------------------------
# Multi-white / extra aux roles (w2, w3, a2, uv2)
# ---------------------------------------------------------------------------


def test_multi_white_emits_independent_values():
    """A mode with ``w`` (mix) and ``w2`` (always direct) must emit them
    separately: W derived from min(R,G,B), W2 exactly as supplied."""
    channels = ["r", "g", "b", "w", "w2", "a2"]
    state = {
        "r": 200,
        "g": 150,
        "b": 100,
        "on": True,
        "dimmer": 255,
        "w2": 50,
        "a2": 25,
    }
    values = _compute_channel_values(channels, state)
    assert values[0] == 200
    assert values[1] == 150
    assert values[2] == 100
    assert values[3] == 100, "W uses default mix-from-RGB (min of R,G,B)"
    assert values[4] == 50, "W2 passes through the explicit state value"
    assert values[5] == 25, "A2 passes through the explicit state value"


def test_extras_default_to_zero_when_omitted():
    """Extras must never be derived from RGB; missing state -> 0."""
    channels = ["r", "g", "b", "w2", "a2", "uv2"]
    state = {"r": 200, "g": 150, "b": 100, "on": True, "dimmer": 255}
    values = _compute_channel_values(channels, state)
    assert values[:3] == [200, 150, 100]
    assert values[3] == 0
    assert values[4] == 0
    assert values[5] == 0


def test_extras_respect_dimmer_when_no_dedicated_dimmer_channel():
    """When the fixture has no ``dimmer`` channel the brightness slider
    bakes into every output byte — including the aux extras."""
    channels = ["r", "g", "b", "w2"]
    state = {"r": 100, "g": 100, "b": 100, "w2": 200, "on": True, "dimmer": 128}
    values = _compute_channel_values(channels, state)
    scale = 128 / 255.0
    assert values[3] == round(200 * scale)


def test_channel_roles_include_extras():
    """Sanity: the role enum exposes the new aux roles so the API and
    frontend can rely on them."""
    from app.schemas import CHANNEL_ROLES, EXTRA_COLOR_ROLES, POLICY_ROLES

    for role in ("w2", "w3", "a2", "uv2"):
        assert role in CHANNEL_ROLES
        assert role in POLICY_ROLES
        assert role in EXTRA_COLOR_ROLES


# ---------------------------------------------------------------------------
# seed built-ins
# ---------------------------------------------------------------------------


def test_seed_builtins_default_to_empty_policy():
    """Built-in models must not carry a policy override — the default
    empty dict is what gives the historical "mix" behavior."""
    from app import seed

    for _name, channels in seed.BUILTIN_MODELS:
        # Sanity-check that the seed tuples are the shape we expect.
        assert isinstance(channels, list)
        for role in channels:
            assert isinstance(role, str)
    # No seed entry declares a color_policy today; updating it here would
    # change user-visible behavior for everyone.
    assert not any(len(row) > 2 for row in seed.BUILTIN_MODELS)
