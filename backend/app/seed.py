"""Seed curated palettes, common light models, and Lua builtin effects.

Safe to run multiple times: palettes and models are keyed by name and
will be updated (for builtins) or left alone (for user entries). Builtin
effects are seeded directly from the Lua scripts shipped under
``backend/app/lua/builtins/``; the ``seed_lua_builtins`` step also runs
a one-shot migration that fills in ``Effect.source`` for any non-builtin
row that still has the legacy ``effect_type`` set.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from .db import engine, init_db
from .lua import builtin_sources, compile_script
from .models import Effect, LightModel, LightModelMode, Palette

log = logging.getLogger(__name__)

BUILTIN_MODELS: list[tuple[str, list[str]]] = [
    ("RGB 3ch", ["r", "g", "b"]),
    ("RGBW 4ch", ["r", "g", "b", "w"]),
    ("RGBWA 5ch", ["r", "g", "b", "w", "a"]),
    ("RGBWA+UV 6ch", ["r", "g", "b", "w", "a", "uv"]),
    ("Par 7ch", ["dimmer", "r", "g", "b", "strobe", "macro", "speed"]),
]


BUILTIN_PALETTE_ENTRY = str | dict
BUILTIN_PALETTES: list[tuple[str, list]] = [
    ("Cyberpunk Neon", ["#FF2DAA", "#00E5FF", "#7C4DFF", "#2D1B69", "#C9D1D9"]),
    ("Synthwave Sunset", ["#FF3B7F", "#FF7A59", "#FFB36B", "#7C4DFF", "#2D1B69"]),
    ("Vaporwave", ["#F62E97", "#94167F", "#E93479", "#F9AC53", "#153CB4"]),
    ("Aurora Borealis", ["#00FF9F", "#00B8FF", "#7C4DFF", "#2EF9B6", "#001A33"]),
    ("Deep Ocean", ["#011F4B", "#03396C", "#005B96", "#6497B1", "#B3CDE0"]),
    ("Forest Canopy", ["#0B3D0B", "#1B5E20", "#2E7D32", "#7CB342", "#C5E1A5"]),
    ("Ember and Ash", ["#1A0A00", "#4A1500", "#B23A00", "#FF6B1A", "#FFD199"]),
    ("Candlelight", ["#2B1400", "#7A3C00", "#FF8A3D", "#FFB26B", "#FFD19A"]),
    ("Ice and Fire", ["#E8F6FF", "#66D3FA", "#0077B6", "#FF5B1F", "#FFB36B"]),
    ("Blood Moon", ["#2B0A0A", "#6E0F0F", "#B01E1E", "#FF3B30", "#FFB36B"]),
    ("Pastel Dream", ["#FFB5E8", "#B28DFF", "#AFCBFF", "#BFFCC6", "#FFC9DE", "#FFFFD1"]),
    ("Halloween", ["#FF6A00", "#8A2BE2", "#1B1B1B", "#39FF14", "#FFD300"]),
    ("Bioluminescence", ["#001018", "#003049", "#00B4D8", "#90E0EF", "#CAFFBF"]),
    ("Desert Sunset", ["#2E0F0A", "#7A1F0F", "#C1440E", "#E57B3A", "#F6C28B"]),
    (
        "Rainbow Spectrum",
        [
            "#FF0000", "#FF7F00", "#FFD500", "#7FFF00", "#00FF00",
            "#00FF7F", "#00FFFF", "#007FFF", "#0000FF", "#7F00FF",
            "#FF00FF", "#FF007F",
        ],
    ),
    (
        "UV Blacklight",
        [
            {"r": 0, "g": 0, "b": 0, "uv": 255},
            {"r": 24, "g": 0, "b": 48, "uv": 200},
            {"r": 48, "g": 0, "b": 96, "uv": 220},
            {"r": 124, "g": 77, "b": 255, "uv": 255},
        ],
    ),
    (
        "Warm Amber Wash",
        [
            {"r": 255, "g": 170, "b": 80, "a": 255, "w": 180},
            {"r": 255, "g": 120, "b": 40, "a": 220},
            {"r": 200, "g": 80, "b": 20, "a": 180},
            {"r": 120, "g": 40, "b": 10, "a": 120},
        ],
    ),
]


# Each builtin effect references a Lua script by basename (sourced from
# ``backend/app/lua/builtins/<script>.lua``) and supplies its name,
# default palette, default param overrides, spread, and target channels.
# Any param values omitted here fall back to the script's declared
# defaults via the schema merge.
BUILTIN_EFFECTS: list[dict] = [
    {
        "name": "Rainbow Wash",
        "script": "rainbow",
        "palette_name": None,
        "spread": "across_lights",
        "params": {"speed_hz": 0.15, "offset": 0.15},
        "controls": {"intensity": 1.0, "fade_in_s": 0.5, "fade_out_s": 0.5},
    },
    {
        "name": "Breathing Amber",
        "script": "pulse",
        "palette_name": "Candlelight",
        "spread": "across_lights",
        "params": {"speed_hz": 0.25},
        "controls": {"intensity": 1.0, "fade_in_s": 1.0, "fade_out_s": 1.0},
    },
    {
        "name": "Cyberpunk Chase",
        "script": "chase",
        "palette_name": "Cyberpunk Neon",
        "spread": "across_lights",
        "params": {"speed_hz": 1.5, "offset": 0.15, "size": 1.5, "softness": 0.6},
        "controls": {"intensity": 1.0, "fade_in_s": 0.3, "fade_out_s": 0.3},
    },
    {
        "name": "Aurora Fade",
        "script": "fade",
        "palette_name": "Aurora Borealis",
        "spread": "across_fixture",
        "params": {"speed_hz": 0.1, "offset": 0.05},
        "controls": {"intensity": 1.0, "fade_in_s": 1.0, "fade_out_s": 1.0},
    },
    {
        "name": "Halloween Strobe",
        "script": "strobe",
        "palette_name": "Halloween",
        "spread": "across_lights",
        "params": {"speed_hz": 6.0, "size": 0.4},
        "controls": {"intensity": 1.0, "fade_in_s": 0.1, "fade_out_s": 0.2},
    },
    {
        "name": "Pastel Sparkle",
        "script": "sparkle",
        "palette_name": "Pastel Dream",
        "spread": "across_zones",
        "params": {"speed_hz": 2.0},
        "controls": {"intensity": 1.0, "fade_in_s": 0.3, "fade_out_s": 0.5},
    },
    {
        "name": "White LED Chase",
        "script": "chase",
        "palette_name": None,
        "spread": "across_lights",
        "target_channels": ["w"],
        "params": {"speed_hz": 1.2, "offset": 0.2, "size": 1.2, "softness": 0.4},
        "controls": {"intensity": 1.0, "fade_in_s": 0.2, "fade_out_s": 0.4},
    },
    {
        "name": "Strobe Pulse (Strobe Channel)",
        "script": "pulse",
        "palette_name": None,
        "spread": "across_lights",
        "target_channels": ["strobe"],
        "params": {"speed_hz": 0.5},
        "controls": {"intensity": 1.0, "fade_in_s": 0.1, "fade_out_s": 0.2},
    },
    {
        "name": "UV Accent Wave",
        "script": "wave",
        "palette_name": None,
        "spread": "across_lights",
        "target_channels": ["uv"],
        "params": {"speed_hz": 0.3, "offset": 0.1},
        "controls": {"intensity": 1.0, "fade_in_s": 0.5, "fade_out_s": 0.5},
    },
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
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

    default = next((m for m in modes if m.is_default), None)
    if default is None:
        default = modes[0]
        default.is_default = True
    default.channels = list(channels)
    default.channel_count = len(channels)
    sess.add(default)


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------
def _normalize_palette_items(items: list) -> tuple[list[str], list[dict]]:
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


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------
def _upsert_effect(sess: Session, spec: dict, sources: dict[str, str]) -> None:
    name = spec["name"]
    script_name = spec["script"]
    source = sources.get(script_name)
    if source is None:
        log.warning("builtin effect %s references missing script %s",
                    name, script_name)
        return
    palette_id: Optional[int] = None
    if spec.get("palette_name"):
        pal = sess.exec(
            select(Palette).where(Palette.name == spec["palette_name"])
        ).first()
        if pal is not None:
            palette_id = pal.id
    target_channels = list(spec.get("target_channels") or ["rgb"])
    # Combine script knobs + engine controls in the persisted ``params``
    # dict (the engine splits them at play time).
    params = dict(spec.get("params") or {})
    controls = dict(spec.get("controls") or {})
    params.update(controls)
    try:
        compiled = compile_script(source, chunkname=f"=builtins/{script_name}")
    except Exception as exc:
        log.warning("builtin script %s failed to compile: %s", script_name, exc)
        return
    schema = list(compiled.meta.param_schema)

    existing = sess.exec(select(Effect).where(Effect.name == name)).first()
    if existing is None:
        sess.add(
            Effect(
                name=name,
                source=source,
                param_schema=schema,
                effect_type=script_name,  # legacy mirror; harmless
                palette_id=palette_id,
                light_ids=[],
                targets=[],
                spread=spec["spread"],
                params=params,
                target_channels=target_channels,
                is_active=False,
                builtin=True,
            )
        )
        return
    existing.source = source
    existing.param_schema = schema
    existing.effect_type = script_name
    existing.palette_id = palette_id
    existing.spread = spec["spread"]
    existing.params = params
    existing.target_channels = target_channels
    existing.builtin = True
    sess.add(existing)


def _migrate_legacy_effects(
    sess: Session, sources: dict[str, str]
) -> None:
    """Backfill ``Effect.source`` for non-builtin rows still on the legacy
    ``effect_type`` flow. Existing user-customized ``params`` are kept as
    is so the script (which reads the same param ids) keeps working."""
    rows = sess.exec(select(Effect)).all()
    for row in rows:
        if row.builtin:
            continue
        if row.source and row.source.strip():
            continue
        legacy = (row.effect_type or "").strip()
        if not legacy:
            continue
        src = sources.get(legacy)
        if src is None:
            log.warning(
                "effect %s (%s) references unknown legacy effect_type %r",
                row.id, row.name, legacy,
            )
            continue
        try:
            compiled = compile_script(src, chunkname=f"=migrate/{legacy}")
        except Exception as exc:
            log.warning(
                "failed to compile migration source for %s: %s",
                row.name, exc,
            )
            continue
        row.source = src
        row.param_schema = list(compiled.meta.param_schema)
        sess.add(row)


def seed() -> None:
    init_db()
    sources = builtin_sources()
    with Session(engine) as sess:
        for name, chans in BUILTIN_MODELS:
            _upsert_model(sess, name, chans)
        for name, items in BUILTIN_PALETTES:
            _upsert_palette(sess, name, items)
        sess.commit()
        for spec in BUILTIN_EFFECTS:
            _upsert_effect(sess, spec, sources)
        _migrate_legacy_effects(sess, sources)
        sess.commit()


if __name__ == "__main__":
    seed()
    print("Seeded built-in models, palettes, and Lua effects.")
