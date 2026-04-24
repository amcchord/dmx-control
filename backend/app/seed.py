"""Seed curated palettes and common light models.

Safe to run multiple times: palettes and models are keyed by name and will be
updated (for builtins) or left alone (for user entries).
"""

from __future__ import annotations

from sqlmodel import Session, select

from .db import engine, init_db
from .models import Effect, LightModel, LightModelMode, Palette

BUILTIN_MODELS: list[tuple[str, list[str]]] = [
    ("RGB 3ch", ["r", "g", "b"]),
    ("RGBW 4ch", ["r", "g", "b", "w"]),
    ("RGBWA 5ch", ["r", "g", "b", "w", "a"]),
    ("RGBWA+UV 6ch", ["r", "g", "b", "w", "a", "uv"]),
    ("Par 7ch", ["dimmer", "r", "g", "b", "strobe", "macro", "speed"]),
]


"""Per-entry shape for built-in palettes.

Each entry is either a 6-digit hex string (no aux values) or a dict
``{r, g, b, w?, a?, uv?}`` for palettes that intentionally drive the
auxiliary channels. ``_upsert_palette`` normalizes both into the
structured ``entries`` storage and the legacy ``colors`` hex list."""

BUILTIN_PALETTE_ENTRY = str | dict
BUILTIN_PALETTES: list[tuple[str, list]] = [
    (
        "Cyberpunk Neon",
        ["#FF2DAA", "#00E5FF", "#7C4DFF", "#2D1B69", "#C9D1D9"],
    ),
    (
        "Synthwave Sunset",
        ["#FF3B7F", "#FF7A59", "#FFB36B", "#7C4DFF", "#2D1B69"],
    ),
    (
        "Vaporwave",
        ["#F62E97", "#94167F", "#E93479", "#F9AC53", "#153CB4"],
    ),
    (
        "Aurora Borealis",
        ["#00FF9F", "#00B8FF", "#7C4DFF", "#2EF9B6", "#001A33"],
    ),
    (
        "Deep Ocean",
        ["#011F4B", "#03396C", "#005B96", "#6497B1", "#B3CDE0"],
    ),
    (
        "Forest Canopy",
        ["#0B3D0B", "#1B5E20", "#2E7D32", "#7CB342", "#C5E1A5"],
    ),
    (
        "Ember and Ash",
        ["#1A0A00", "#4A1500", "#B23A00", "#FF6B1A", "#FFD199"],
    ),
    (
        "Candlelight",
        ["#2B1400", "#7A3C00", "#FF8A3D", "#FFB26B", "#FFD19A"],
    ),
    (
        "Ice and Fire",
        ["#E8F6FF", "#66D3FA", "#0077B6", "#FF5B1F", "#FFB36B"],
    ),
    (
        "Blood Moon",
        ["#2B0A0A", "#6E0F0F", "#B01E1E", "#FF3B30", "#FFB36B"],
    ),
    (
        "Pastel Dream",
        ["#FFB5E8", "#B28DFF", "#AFCBFF", "#BFFCC6", "#FFC9DE", "#FFFFD1"],
    ),
    (
        "Halloween",
        ["#FF6A00", "#8A2BE2", "#1B1B1B", "#39FF14", "#FFD300"],
    ),
    (
        "Bioluminescence",
        ["#001018", "#003049", "#00B4D8", "#90E0EF", "#CAFFBF"],
    ),
    (
        "Desert Sunset",
        ["#2E0F0A", "#7A1F0F", "#C1440E", "#E57B3A", "#F6C28B"],
    ),
    (
        "Rainbow Spectrum",
        [
            "#FF0000",
            "#FF7F00",
            "#FFD500",
            "#7FFF00",
            "#00FF00",
            "#00FF7F",
            "#00FFFF",
            "#007FFF",
            "#0000FF",
            "#7F00FF",
            "#FF00FF",
            "#FF007F",
        ],
    ),
    (
        "UV Blacklight",
        [
            # Black base + explicit UV punch — use on fixtures that expose
            # a UV channel to kick the UV LEDs independently of RGB.
            {"r": 0, "g": 0, "b": 0, "uv": 255},
            {"r": 24, "g": 0, "b": 48, "uv": 200},
            {"r": 48, "g": 0, "b": 96, "uv": 220},
            {"r": 124, "g": 77, "b": 255, "uv": 255},
        ],
    ),
    (
        "Warm Amber Wash",
        [
            # Warm tungsten-style palette. Explicit amber values push
            # fixtures that have an amber LED toward the classic warm
            # color you can't hit with pure RGB.
            {"r": 255, "g": 170, "b": 80, "a": 255, "w": 180},
            {"r": 255, "g": 120, "b": 40, "a": 220},
            {"r": 200, "g": 80, "b": 20, "a": 180},
            {"r": 120, "g": 40, "b": 10, "a": 120},
        ],
    ),
]


def _upsert_model(sess: Session, name: str, channels: list[str]) -> None:
    mode_name = f"{len(channels)}ch"
    existing = sess.exec(select(LightModel).where(LightModel.name == name)).first()
    if existing is None:
        model = LightModel(
            name=name,
            channels=channels,
            channel_count=len(channels),
            builtin=True,
        )
        sess.add(model)
        sess.flush()
        sess.add(
            LightModelMode(
                model_id=model.id,
                name=mode_name,
                channels=list(channels),
                channel_count=len(channels),
                is_default=True,
            )
        )
        return

    existing.channels = channels
    existing.channel_count = len(channels)
    existing.builtin = True
    sess.add(existing)

    modes = sess.exec(
        select(LightModelMode).where(LightModelMode.model_id == existing.id)
    ).all()
    if not modes:
        sess.add(
            LightModelMode(
                model_id=existing.id,
                name=mode_name,
                channels=list(channels),
                channel_count=len(channels),
                is_default=True,
            )
        )
        return

    # Keep the built-in's default mode aligned with the canonical channel list.
    default = next((m for m in modes if m.is_default), None)
    if default is None:
        default = modes[0]
        default.is_default = True
    default.channels = list(channels)
    default.channel_count = len(channels)
    sess.add(default)


# Built-in effects. light_ids + targets are intentionally left empty so the
# engine resolves them to "every light on the rig" at play time, which
# means these effects are immediately useful on any fixture configuration.
BUILTIN_EFFECTS: list[dict] = [
    {
        "name": "Rainbow Wash",
        "effect_type": "rainbow",
        "palette_name": None,
        "spread": "across_lights",
        "params": {
            "speed_hz": 0.15,
            "direction": "forward",
            "offset": 0.15,
            "intensity": 1.0,
            "size": 1.0,
            "softness": 0.5,
            "fade_in_s": 0.5,
            "fade_out_s": 0.5,
        },
    },
    {
        "name": "Breathing Amber",
        "effect_type": "pulse",
        "palette_name": "Candlelight",
        "spread": "across_lights",
        "params": {
            "speed_hz": 0.25,
            "direction": "forward",
            "offset": 0.0,
            "intensity": 1.0,
            "size": 1.0,
            "softness": 0.5,
            "fade_in_s": 1.0,
            "fade_out_s": 1.0,
        },
    },
    {
        "name": "Cyberpunk Chase",
        "effect_type": "chase",
        "palette_name": "Cyberpunk Neon",
        "spread": "across_lights",
        "params": {
            "speed_hz": 1.5,
            "direction": "forward",
            "offset": 0.15,
            "intensity": 1.0,
            "size": 1.5,
            "softness": 0.6,
            "fade_in_s": 0.3,
            "fade_out_s": 0.3,
        },
    },
    {
        "name": "Aurora Fade",
        "effect_type": "fade",
        "palette_name": "Aurora Borealis",
        "spread": "across_fixture",
        "params": {
            "speed_hz": 0.1,
            "direction": "forward",
            "offset": 0.05,
            "intensity": 1.0,
            "size": 1.0,
            "softness": 0.5,
            "fade_in_s": 1.0,
            "fade_out_s": 1.0,
        },
    },
    {
        "name": "Halloween Strobe",
        "effect_type": "strobe",
        "palette_name": "Halloween",
        "spread": "across_lights",
        "params": {
            "speed_hz": 6.0,
            "direction": "forward",
            "offset": 0.0,
            "intensity": 1.0,
            "size": 0.4,
            "softness": 0.0,
            "fade_in_s": 0.1,
            "fade_out_s": 0.2,
        },
    },
    {
        "name": "Pastel Sparkle",
        "effect_type": "sparkle",
        "palette_name": "Pastel Dream",
        "spread": "across_zones",
        "params": {
            "speed_hz": 2.0,
            "direction": "forward",
            "offset": 0.0,
            "intensity": 1.0,
            "size": 1.0,
            "softness": 0.5,
            "fade_in_s": 0.3,
            "fade_out_s": 0.5,
        },
    },
    {
        # Chase only the white LED without touching RGB. Use this on top
        # of any static color palette to add a moving white accent.
        "name": "White LED Chase",
        "effect_type": "chase",
        "palette_name": None,
        "spread": "across_lights",
        "target_channels": ["w"],
        "params": {
            "speed_hz": 1.2,
            "direction": "forward",
            "offset": 0.2,
            "intensity": 1.0,
            "size": 1.2,
            "softness": 0.4,
            "fade_in_s": 0.2,
            "fade_out_s": 0.4,
        },
    },
    {
        "name": "Strobe Pulse (Strobe Channel)",
        "effect_type": "pulse",
        "palette_name": None,
        "spread": "across_lights",
        "target_channels": ["strobe"],
        "params": {
            "speed_hz": 0.5,
            "direction": "forward",
            "offset": 0.0,
            "intensity": 1.0,
            "size": 1.0,
            "softness": 0.3,
            "fade_in_s": 0.1,
            "fade_out_s": 0.2,
        },
    },
    {
        "name": "UV Accent Wave",
        "effect_type": "wave",
        "palette_name": None,
        "spread": "across_lights",
        "target_channels": ["uv"],
        "params": {
            "speed_hz": 0.3,
            "direction": "forward",
            "offset": 0.1,
            "intensity": 1.0,
            "size": 1.0,
            "softness": 0.5,
            "fade_in_s": 0.5,
            "fade_out_s": 0.5,
        },
    },
]


def _normalize_palette_items(
    items: list,
) -> tuple[list[str], list[dict]]:
    """Split a built-in palette list into ``(colors, entries)``.

    Accepts mixed entries: hex strings (RGB only) or dicts carrying
    explicit aux values. The hex form is always regenerated from each
    entry's RGB so the two lists stay in sync on disk."""
    entries: list[dict] = []
    for item in items:
        if isinstance(item, str):
            s = item.strip().lstrip("#")
            if len(s) != 6:
                continue
            try:
                r = int(s[0:2], 16)
                g = int(s[2:4], 16)
                b = int(s[4:6], 16)
            except ValueError:
                continue
            entries.append({"r": r, "g": g, "b": b})
        elif isinstance(item, dict):
            try:
                entry = {
                    "r": int(item["r"]),
                    "g": int(item["g"]),
                    "b": int(item["b"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
            for aux in ("w", "a", "uv"):
                if item.get(aux) is not None:
                    entry[aux] = int(item[aux])
            entries.append(entry)
    colors = [f"#{e['r']:02X}{e['g']:02X}{e['b']:02X}" for e in entries]
    return colors, entries


def _upsert_palette(sess: Session, name: str, items: list) -> None:
    colors, entries = _normalize_palette_items(items)
    existing = sess.exec(select(Palette).where(Palette.name == name)).first()
    if existing is None:
        sess.add(
            Palette(name=name, colors=colors, entries=entries, builtin=True)
        )
    else:
        existing.colors = colors
        existing.entries = entries
        existing.builtin = True
        sess.add(existing)


def _upsert_effect(sess: Session, spec: dict) -> None:
    name = spec["name"]
    palette_id: int | None = None
    if spec.get("palette_name"):
        pal = sess.exec(
            select(Palette).where(Palette.name == spec["palette_name"])
        ).first()
        if pal is not None:
            palette_id = pal.id
    target_channels = list(spec.get("target_channels") or ["rgb"])
    existing = sess.exec(select(Effect).where(Effect.name == name)).first()
    if existing is None:
        sess.add(
            Effect(
                name=name,
                effect_type=spec["effect_type"],
                palette_id=palette_id,
                light_ids=[],
                targets=[],
                spread=spec["spread"],
                params=dict(spec["params"]),
                target_channels=target_channels,
                is_active=False,
                builtin=True,
            )
        )
        return
    # Keep builtin effects in sync with the canonical definition.
    existing.effect_type = spec["effect_type"]
    existing.palette_id = palette_id
    existing.spread = spec["spread"]
    existing.params = dict(spec["params"])
    existing.target_channels = target_channels
    existing.builtin = True
    sess.add(existing)


def seed() -> None:
    init_db()
    with Session(engine) as sess:
        for name, chans in BUILTIN_MODELS:
            _upsert_model(sess, name, chans)
        for name, items in BUILTIN_PALETTES:
            _upsert_palette(sess, name, items)
        # Palettes must be present before effects so palette_id resolves.
        sess.commit()
        for spec in BUILTIN_EFFECTS:
            _upsert_effect(sess, spec)
        sess.commit()


if __name__ == "__main__":
    seed()
    print("Seeded built-in models and palettes.")
