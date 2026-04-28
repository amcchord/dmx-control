"""Tests for indexed-color (``color`` role) rendering and AI parse.

Covers:
* ``artnet._pick_color_byte`` — nearest-match snapping in RGB Euclidean
  space, off-entry handling, off-below threshold on dimmerless modes.
* ``artnet._compute_channel_values`` — flat-mode rendering of a single
  ``color`` slot.
* ``artnet._compute_layout_values`` — Blizzard StormChaser 20CH golden
  (16 cells, one shared palette).
* ``artnet._byte_to_color_rgb`` + ``_decode_binding`` — dashboard
  preview reverses wheel bytes back to representative RGB.
* ``schemas.ColorTable`` validation — overlapping ranges rejected,
  ``_normalize_color_table`` drops the table when the mode has no
  ``color`` slot.
* ``routers.ai._sanitize_color_table`` — Claude payload roundtrip.
"""

from __future__ import annotations

import pytest

from app.artnet import (
    LightBinding,
    _byte_to_color_rgb,
    _compute_channel_values,
    _compute_layout_values,
    _decode_binding,
    _pick_color_byte,
)
from app.routers.ai import _sanitize, _sanitize_color_table
from app.schemas import (
    ColorTable,
    ColorTableEntry,
    LightModelModeIn,
    _normalize_color_table,
)


# ---------------------------------------------------------------------------
# StormChaser palette: shared fixture data
# ---------------------------------------------------------------------------
# The full 14-entry palette documented in the Blizzard StormChaser Supercell
# manual (20CH mode CH3-18, 52CH mode CH51 effects). Used as the canonical
# golden across the renderer + sanitize tests below.

STORMCHASER_PALETTE: list[dict] = [
    {"lo": 0, "hi": 15, "name": "Off", "r": 0, "g": 0, "b": 0},
    {"lo": 16, "hi": 31, "name": "Red", "r": 255, "g": 0, "b": 0},
    {"lo": 32, "hi": 47, "name": "Green", "r": 0, "g": 255, "b": 0},
    {"lo": 48, "hi": 63, "name": "Blue", "r": 0, "g": 0, "b": 255},
    {"lo": 64, "hi": 79, "name": "Yellow", "r": 255, "g": 255, "b": 0},
    {"lo": 80, "hi": 95, "name": "Lime", "r": 128, "g": 255, "b": 0},
    {"lo": 96, "hi": 111, "name": "Orange", "r": 255, "g": 128, "b": 0},
    {"lo": 112, "hi": 127, "name": "Purple", "r": 128, "g": 0, "b": 128},
    {"lo": 128, "hi": 143, "name": "Magenta", "r": 255, "g": 0, "b": 255},
    {"lo": 144, "hi": 159, "name": "Pink", "r": 255, "g": 128, "b": 192},
    {"lo": 160, "hi": 175, "name": "Med. Blue", "r": 32, "g": 64, "b": 192},
    {"lo": 176, "hi": 191, "name": "Aqua", "r": 0, "g": 192, "b": 192},
    {"lo": 192, "hi": 207, "name": "Lt. Blue", "r": 128, "g": 192, "b": 255},
    {"lo": 208, "hi": 255, "name": "White", "r": 255, "g": 255, "b": 255},
]


def _palette_table(off_below: int = 0) -> dict:
    return {"entries": [dict(e) for e in STORMCHASER_PALETTE], "off_below": off_below}


# ---------------------------------------------------------------------------
# _pick_color_byte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rgb,expected_lo,expected_hi",
    [
        ((255, 0, 0), 16, 31),  # exact red
        ((0, 255, 0), 32, 47),  # exact green
        ((0, 0, 255), 48, 63),  # exact blue
        ((255, 255, 255), 208, 255),  # white
        ((0, 0, 0), 0, 15),  # off
        ((250, 5, 5), 16, 31),  # near-red snaps to red
        ((10, 250, 10), 32, 47),  # near-green
        ((128, 0, 128), 112, 127),  # exact purple
    ],
)
def test_pick_color_byte_nearest_match(rgb, expected_lo, expected_hi):
    r, g, b = rgb
    out = _pick_color_byte(r, g, b, _palette_table(), True, has_dimmer=True)
    assert expected_lo <= out <= expected_hi, (
        f"({r},{g},{b}) -> byte {out} not in [{expected_lo},{expected_hi}]"
    )


def test_pick_color_byte_emits_midpoint():
    """The renderer emits the midpoint of the matching entry's range."""
    out = _pick_color_byte(255, 0, 0, _palette_table(), True, has_dimmer=True)
    assert out == (16 + 31) // 2


def test_pick_color_byte_off_when_not_on():
    """When the light is off, return the off-entry's midpoint regardless
    of RGB."""
    out = _pick_color_byte(255, 255, 255, _palette_table(), False, has_dimmer=True)
    assert out == (0 + 15) // 2


def test_pick_color_byte_no_table_returns_zero():
    """Without a table the wheel byte stays at 0 (matches today's macro
    slot behavior for legacy fixtures)."""
    assert _pick_color_byte(255, 0, 0, None, True, has_dimmer=True) == 0
    assert _pick_color_byte(255, 0, 0, {"entries": []}, True, has_dimmer=True) == 0


def test_pick_color_byte_off_below_only_when_dimmerless():
    """``off_below`` only forces off when the mode has no dimmer
    channel — modes with a dimmer leave RGB at full and dim via the
    dedicated channel instead.

    We pick (130,0,0) which is 5 units closer to red (255,0,0) than to
    off (0,0,0); without the off_below override the nearest-match
    chooses red. With ``off_below=132`` on a dimmerless fixture the
    threshold short-circuits to off."""
    table = _palette_table(off_below=132)
    dimmerless = _pick_color_byte(130, 0, 0, table, True, has_dimmer=False)
    assert dimmerless == (0 + 15) // 2
    # Same RGB on a mode WITH a dimmer: nearest-match runs and lands on
    # red (the renderer dims the fixture via the dedicated channel).
    with_dim = _pick_color_byte(130, 0, 0, table, True, has_dimmer=True)
    assert 16 <= with_dim <= 31


def test_pick_color_byte_preserves_when_threshold_met():
    """Above ``off_below`` we always pick the closest non-off entry, even
    on a dimmerless fixture."""
    table = _palette_table(off_below=64)
    out = _pick_color_byte(200, 0, 0, table, True, has_dimmer=False)
    assert 16 <= out <= 31


# ---------------------------------------------------------------------------
# _compute_channel_values (flat path)
# ---------------------------------------------------------------------------


def test_flat_color_role_drives_byte_from_rgb():
    """A flat mode whose channels are ['dimmer','color'] emits the wheel
    byte for the requested logical RGB."""
    channels = ["dimmer", "color"]
    state = {"r": 255, "g": 0, "b": 0, "on": True, "dimmer": 255}
    values = _compute_channel_values(channels, state, None, _palette_table())
    assert values[0] == 255  # dimmer channel
    assert 16 <= values[1] <= 31  # red entry midpoint


def test_flat_color_role_zero_without_table():
    """Without a color_table the slot stays at 0 (no regression for
    legacy macro-tagged fixtures)."""
    channels = ["dimmer", "color"]
    state = {"r": 255, "g": 0, "b": 0, "on": True, "dimmer": 255}
    values = _compute_channel_values(channels, state, None, None)
    assert values[1] == 0


def test_flat_color_role_off_when_not_on():
    """``on=False`` zeros the entire flat output as before — the color
    slot follows along."""
    channels = ["dimmer", "color"]
    state = {"r": 255, "g": 0, "b": 0, "on": False, "dimmer": 255}
    values = _compute_channel_values(channels, state, None, _palette_table())
    assert values == [0, 0]


# ---------------------------------------------------------------------------
# _compute_layout_values (Blizzard StormChaser 20CH golden)
# ---------------------------------------------------------------------------


def _stormchaser_20ch_layout() -> dict:
    """One ``color`` slot per cell, plus globals for dimmer/strobe/macro/speed.

    Channel order: [dimmer, strobe, color×16, macro, speed] -> 20 channels.
    Cell N's byte sits at index 2 + (N-1)."""
    zones = [
        {
            "id": f"c{i}",
            "label": f"Cell {i + 1}",
            "kind": "pixel",
            "row": 0,
            "col": i,
            "colors": {"color": 2 + i},
        }
        for i in range(16)
    ]
    return {
        "shape": "linear",
        "cols": 16,
        "zones": zones,
        "globals": {"dimmer": 0, "strobe": 1, "macro": 18, "speed": 19},
    }


def _stormchaser_channels() -> list[str]:
    return (
        ["dimmer", "strobe"] + ["color"] * 16 + ["macro", "speed"]
    )


def _stormchaser_state(zone_state: dict) -> dict:
    return {
        "r": 0,
        "g": 0,
        "b": 0,
        "on": True,
        "dimmer": 255,
        "zone_state": zone_state,
        "motion_state": {},
    }


def test_stormchaser_20ch_renders_per_zone_color():
    """Setting cell 7 to red and cell 13 to blue should produce wheel
    bytes in the documented red and blue ranges at the right offsets."""
    channels = _stormchaser_channels()
    layout = _stormchaser_20ch_layout()
    zone_state = {
        "c6": {"r": 255, "g": 0, "b": 0, "on": True, "dimmer": 255},
        "c12": {"r": 0, "g": 0, "b": 255, "on": True, "dimmer": 255},
    }
    values = _compute_layout_values(
        channels, layout, _stormchaser_state(zone_state), None, _palette_table()
    )
    assert len(values) == 20
    # Red on cell 7 (index 2 + 6 = 8): byte in 16..31.
    assert 16 <= values[8] <= 31
    # Blue on cell 13 (index 2 + 12 = 14): byte in 48..63.
    assert 48 <= values[14] <= 63
    # Cell 1 (no zone state) defaults to fallback flat RGB (0,0,0) -> off.
    assert 0 <= values[2] <= 15
    # Globals: dimmer at full, macro/speed/strobe forced 0.
    assert values[0] == 255
    assert values[1] == 0
    assert values[18] == 0
    assert values[19] == 0


def test_stormchaser_20ch_off_zone_emits_off_byte():
    """A zone with on=False emits the off-entry byte regardless of its
    stored RGB. This is what makes black "really go dark" on indexed-
    color modes that would otherwise stay on the closest hue."""
    channels = _stormchaser_channels()
    layout = _stormchaser_20ch_layout()
    zone_state = {
        "c0": {"r": 255, "g": 0, "b": 0, "on": False, "dimmer": 255},
    }
    values = _compute_layout_values(
        channels, layout, _stormchaser_state(zone_state), None, _palette_table()
    )
    # Cell 1 byte: in the off entry's range.
    assert 0 <= values[2] <= 15


# ---------------------------------------------------------------------------
# _byte_to_color_rgb + _decode_binding (dashboard preview)
# ---------------------------------------------------------------------------


def test_byte_to_color_rgb_reverses_each_entry():
    table = _palette_table()
    for entry in STORMCHASER_PALETTE:
        midpoint = (entry["lo"] + entry["hi"]) // 2
        rgb = _byte_to_color_rgb(midpoint, table)
        assert rgb == (entry["r"], entry["g"], entry["b"])


def test_decode_binding_layout_zone_swatch_uses_table():
    """End-to-end: render a StormChaser 20CH state, write the bytes into
    a 512-byte universe buffer, and then decode them back. The decoded
    zone state should reflect the representative RGB of the matching
    entries (so the dashboard swatch shows actual colors, not raw
    bytes)."""
    channels = _stormchaser_channels()
    layout = _stormchaser_20ch_layout()
    color_table = _palette_table()
    zone_state = {
        "c6": {"r": 255, "g": 0, "b": 0, "on": True, "dimmer": 255},
        "c12": {"r": 0, "g": 0, "b": 255, "on": True, "dimmer": 255},
    }
    values = _compute_layout_values(
        channels, layout, _stormchaser_state(zone_state), None, color_table
    )
    buf = bytearray(512)
    for i, v in enumerate(values):
        buf[i] = v
    binding = LightBinding(
        light_id=1,
        start_index=0,
        channels=channels,
        layout=layout,
        color_policy={},
        color_table=color_table,
    )
    decoded = _decode_binding(binding, buf)
    assert decoded["zone_state"]["c6"] == {
        "r": 255,
        "g": 0,
        "b": 0,
        "on": True,
    }
    assert decoded["zone_state"]["c12"] == {
        "r": 0,
        "g": 0,
        "b": 255,
        "on": True,
    }
    assert decoded["on"] is True


def test_decode_binding_flat_mode_uses_table():
    """A flat indexed-color-only fixture decodes wheel bytes back to
    representative RGB."""
    channels = ["dimmer", "color"]
    color_table = _palette_table()
    binding = LightBinding(
        light_id=1,
        start_index=0,
        channels=channels,
        layout=None,
        color_policy={},
        color_table=color_table,
    )
    buf = bytearray(512)
    buf[0] = 255  # dimmer
    buf[1] = 24   # midpoint of 16-31 (Red)
    decoded = _decode_binding(binding, buf)
    assert decoded["r"] == 255
    assert decoded["g"] == 0
    assert decoded["b"] == 0


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_color_table_rejects_overlapping_ranges():
    with pytest.raises(Exception):
        ColorTable(
            entries=[
                ColorTableEntry(lo=0, hi=15, name="Off", r=0, g=0, b=0),
                ColorTableEntry(lo=10, hi=31, name="Red", r=255, g=0, b=0),
            ]
        )


def test_color_table_rejects_lo_gt_hi():
    with pytest.raises(Exception):
        ColorTableEntry(lo=64, hi=32, name="Bad", r=0, g=0, b=0)


def test_color_table_rejects_byte_out_of_range():
    with pytest.raises(Exception):
        ColorTableEntry(lo=0, hi=300, name="Bad", r=0, g=0, b=0)


def test_color_table_sorts_entries():
    """Out-of-order entries are sorted by lo for stable storage."""
    table = ColorTable(
        entries=[
            ColorTableEntry(lo=16, hi=31, name="Red", r=255, g=0, b=0),
            ColorTableEntry(lo=0, hi=15, name="Off", r=0, g=0, b=0),
        ]
    )
    assert [e.lo for e in table.entries] == [0, 16]


def test_normalize_color_table_drops_when_no_color_slot():
    """A table is silently dropped when the mode has no ``color`` slot
    for it to drive (it would never apply at render time)."""
    table = {"entries": [dict(e) for e in STORMCHASER_PALETTE]}
    out = _normalize_color_table(table, ["r", "g", "b"])
    assert out is None


def test_normalize_color_table_keeps_when_color_slot_present():
    table = {"entries": [dict(e) for e in STORMCHASER_PALETTE]}
    out = _normalize_color_table(table, ["dimmer", "color"])
    assert out is not None
    assert len(out["entries"]) == len(STORMCHASER_PALETTE)


def test_mode_in_normalizes_color_table():
    """``LightModelModeIn`` runs both the policy and color-table
    normalizers; a table on a mode without a ``color`` slot vanishes
    while the rest of the input survives."""
    payload = {
        "name": "rgb 3ch",
        "channels": ["r", "g", "b"],
        "is_default": True,
        "color_table": {"entries": [dict(e) for e in STORMCHASER_PALETTE]},
    }
    mode = LightModelModeIn.model_validate(payload)
    assert mode.color_table is None


def test_mode_in_keeps_color_table_when_slot_present():
    payload = {
        "name": "stormchaser 20ch",
        "channels": ["dimmer", "strobe"]
        + ["color"] * 16
        + ["macro", "speed"],
        "is_default": True,
        "color_table": {"entries": [dict(e) for e in STORMCHASER_PALETTE]},
    }
    mode = LightModelModeIn.model_validate(payload)
    assert mode.color_table is not None
    assert len(mode.color_table["entries"]) == 14


# ---------------------------------------------------------------------------
# AI parser sanitize roundtrip
# ---------------------------------------------------------------------------


def test_sanitize_color_table_strips_invalid_entries():
    """Out-of-range bytes / missing fields are dropped silently."""
    raw = {
        "entries": [
            {"lo": 0, "hi": 15, "name": "Off", "r": 0, "g": 0, "b": 0},
            {"lo": -5, "hi": 10, "name": "Bad", "r": 0, "g": 0, "b": 0},
            {"lo": 16, "hi": 31, "name": "Red", "r": 999, "g": 0, "b": 0},
            {"lo": 32, "hi": 47, "name": "Green", "r": 0, "g": 255, "b": 0},
        ]
    }
    out = _sanitize_color_table(raw, ["dimmer", "color"])
    assert out is not None
    names = [e["name"] for e in out["entries"]]
    assert names == ["Off", "Green"]


def test_sanitize_color_table_drops_when_no_color_slot():
    raw = {"entries": [dict(e) for e in STORMCHASER_PALETTE]}
    out = _sanitize_color_table(raw, ["r", "g", "b"])
    assert out is None


def test_sanitize_color_table_drops_overlaps():
    """Overlapping ranges keep the earlier entry and drop later ones."""
    raw = {
        "entries": [
            {"lo": 0, "hi": 31, "name": "Off+Red", "r": 0, "g": 0, "b": 0},
            {"lo": 10, "hi": 31, "name": "Overlap", "r": 255, "g": 0, "b": 0},
            {"lo": 32, "hi": 47, "name": "Green", "r": 0, "g": 255, "b": 0},
        ]
    }
    out = _sanitize_color_table(raw, ["dimmer", "color"])
    assert out is not None
    assert [e["name"] for e in out["entries"]] == ["Off+Red", "Green"]


def test_sanitize_full_stormchaser_payload():
    """End-to-end Claude payload roundtrip: a 20CH StormChaser-shaped
    response should survive ``_sanitize`` with the per-cell color
    layout intact and the shared color_table preserved."""
    raw = {
        "suggested_name": "Blizzard StormChaser Supercell",
        "modes": [
            {
                "name": "20CH",
                "channels": (
                    ["dimmer", "strobe"]
                    + ["color"] * 16
                    + ["macro", "speed"]
                ),
                "layout": {
                    "shape": "linear",
                    "cols": 16,
                    "zones": [
                        {
                            "id": f"c{i}",
                            "label": f"Cell {i + 1}",
                            "kind": "pixel",
                            "row": 0,
                            "col": i,
                            "colors": {"color": 2 + i},
                        }
                        for i in range(16)
                    ],
                    "globals": {
                        "dimmer": 0,
                        "strobe": 1,
                        "macro": 18,
                        "speed": 19,
                    },
                },
                "color_table": {
                    "entries": [dict(e) for e in STORMCHASER_PALETTE]
                },
            }
        ],
    }
    out = _sanitize(raw)
    assert out["suggested_name"] == "Blizzard StormChaser Supercell"
    modes = out["modes"]
    assert len(modes) == 1
    mode = modes[0]
    assert mode["channels"].count("color") == 16
    assert mode["layout"]["shape"] == "linear"
    assert len(mode["layout"]["zones"]) == 16
    assert mode["color_table"] is not None
    assert len(mode["color_table"]["entries"]) == 14
    # First zone's color slot points at CH3 (index 2 in the 0-based list).
    assert mode["layout"]["zones"][0]["colors"]["color"] == 2
