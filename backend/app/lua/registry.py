"""Builtin Lua effect script registry.

The seeder reads these scripts from disk and upserts a builtin
:class:`Effect` row per file. Filenames double as the canonical builtin
name; we use that for legacy ``effect_type`` migration so a saved row
with ``effect_type='fade'`` adopts ``backend/app/lua/builtins/fade.lua``
on first boot of the new build.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_BUILTINS_DIR = Path(__file__).with_name("builtins")


@lru_cache(maxsize=1)
def builtin_sources() -> dict[str, str]:
    """Return ``{name: source}`` for every ``builtins/*.lua`` shipped."""
    out: dict[str, str] = {}
    if not _BUILTINS_DIR.is_dir():
        return out
    for path in sorted(_BUILTINS_DIR.glob("*.lua")):
        name = path.stem
        try:
            out[name] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def get_builtin_source(name: str) -> str | None:
    """Lookup a builtin source by name. Returns None if missing."""
    return builtin_sources().get(name)
