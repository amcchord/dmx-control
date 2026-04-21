"""Seed curated palettes and common light models.

Safe to run multiple times: palettes and models are keyed by name and will be
updated (for builtins) or left alone (for user entries).
"""

from __future__ import annotations

from sqlmodel import Session, select

from .db import engine, init_db
from .models import LightModel, Palette

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
    existing = sess.exec(select(LightModel).where(LightModel.name == name)).first()
    if existing is None:
        sess.add(
            LightModel(
                name=name,
                channels=channels,
                channel_count=len(channels),
                builtin=True,
            )
        )
    else:
        existing.channels = channels
        existing.channel_count = len(channels)
        existing.builtin = True
        sess.add(existing)


def _upsert_palette(sess: Session, name: str, colors: list[str]) -> None:
    existing = sess.exec(select(Palette).where(Palette.name == name)).first()
    if existing is None:
        sess.add(Palette(name=name, colors=colors, builtin=True))
    else:
        existing.colors = colors
        existing.builtin = True
        sess.add(existing)


def seed() -> None:
    init_db()
    with Session(engine) as sess:
        for name, chans in BUILTIN_MODELS:
            _upsert_model(sess, name, chans)
        for name, colors in BUILTIN_PALETTES:
            _upsert_palette(sess, name, colors)
        sess.commit()


if __name__ == "__main__":
    seed()
    print("Seeded built-in models and palettes.")
