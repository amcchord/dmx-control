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


BUILTIN_PALETTES: list[tuple[str, list[str]]] = [
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
]


def _upsert_palette(sess: Session, name: str, colors: list[str]) -> None:
    existing = sess.exec(select(Palette).where(Palette.name == name)).first()
    if existing is None:
        sess.add(Palette(name=name, colors=colors, builtin=True))
    else:
        existing.colors = colors
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
    existing.builtin = True
    sess.add(existing)


def seed() -> None:
    init_db()
    with Session(engine) as sess:
        for name, chans in BUILTIN_MODELS:
            _upsert_model(sess, name, chans)
        for name, colors in BUILTIN_PALETTES:
            _upsert_palette(sess, name, colors)
        # Palettes must be present before effects so palette_id resolves.
        sess.commit()
        for spec in BUILTIN_EFFECTS:
            _upsert_effect(sess, spec)
        sess.commit()


if __name__ == "__main__":
    seed()
    print("Seeded built-in models and palettes.")
