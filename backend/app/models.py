from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON, Text
from sqlmodel import Field, SQLModel


class Controller(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    ip: str
    port: int = 6454
    net: int = 0
    subnet: int = 0
    universe: int = 0
    enabled: bool = True
    # Free-text description the designer AI reads for rig context
    # (e.g. "stage-left wash bar, front-of-house"). Optional.
    notes: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )


class LightModel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # Channel fields are a cached mirror of the model's default LightModelMode.
    # Kept for backwards compatibility and simpler code paths that only need
    # a quick summary; authoritative channel layouts live in LightModelMode.
    channel_count: int
    channels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    builtin: bool = False
    image_filename: Optional[str] = None


class LightModelMode(SQLModel, table=True):
    model_config = {"protected_namespaces": ()}

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(foreign_key="lightmodel.id", index=True)
    name: str
    channels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    channel_count: int
    is_default: bool = False
    # Optional structural overlay on top of `channels`. When null the mode
    # behaves as a single global zone (today's behavior for simple pars).
    # Shape documented in docs; channel references are 0-based indices into
    # the flat `channels` list.
    layout: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    # Per-role policy for the auxiliary color channels W / A / UV.
    # Keys are a subset of {"w","a","uv"} and values are either "mix" (the
    # channel's value is derived from RGB by the renderer when unspecified,
    # matching the historical default) or "direct" (the channel is treated
    # as an independent fader — never auto-derived, never overwritten by
    # palette painting or effect RGB blending). Missing keys imply "mix"
    # so existing rows behave identically after the migration.
    color_policy: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Light(SQLModel, table=True):
    model_config = {"protected_namespaces": ()}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    controller_id: int = Field(foreign_key="controller.id", index=True)
    model_id: int = Field(foreign_key="lightmodel.id", index=True)
    mode_id: Optional[int] = Field(
        default=None, foreign_key="lightmodelmode.id", index=True
    )
    start_address: int  # 1..512
    position: int = 0

    r: int = 0
    g: int = 0
    b: int = 0
    w: int = 0
    a: int = 0
    uv: int = 0
    dimmer: int = 255
    on: bool = True

    # Extra aux color channels for fixtures with multiple whites / ambers /
    # UVs. Keyed by role ("w2", "w3", "a2", "uv2"); missing keys behave as
    # 0. Kept as JSON so we can extend without a schema change.
    extra_colors: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Per-zone color state for compound fixtures.
    # { zone_id: {r,g,b,w,a,uv,dimmer,on} }. Empty/missing => all zones
    # inherit the flat r/g/b/w/a/uv/dimmer/on fallback.
    zone_state: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Motion state for fixtures that have pan/tilt/zoom/focus.
    # All values are floats in [0, 1]; the DMX renderer splits into
    # coarse/fine bytes if both offsets are present in the layout.
    motion_state: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Free-text description the designer AI reads for rig context
    # (e.g. "lead vocalist key light"). Optional.
    notes: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )


class Palette(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # Legacy hex-only view of the palette. Kept for backward compatibility
    # with consumers that only care about RGB (e.g. the simulated preview
    # / the designer's rig summary); authoritative per-entry W/A/UV live
    # in ``entries``.
    colors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Structured per-color entries. Each entry is a dict with 0-255 ints:
    #   {"r": int, "g": int, "b": int, "w"?: int, "a"?: int, "uv"?: int}
    # Missing aux keys mean "derive from RGB (for mix policy) or leave the
    # fader alone (for direct policy)". When the palette was created before
    # the aux migration, entries are backfilled from ``colors`` with RGB
    # only so behavior is identical to today.
    entries: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    builtin: bool = False


class Effect(SQLModel, table=True):
    """A named animated effect over a set of targets.

    Effects are sandboxed Lua scripts. Each script declares its own
    parameters via a top-level ``PARAMS`` table; the user's chosen values
    are stored on this row in ``params``. The engine ticks the script
    every frame and merges the result onto each target's base color (the
    fixture's persistent DB state is never written by the engine).

    ``effect_type`` is retained as a nullable column for backwards
    compatibility with pre-Lua databases. New rows should leave it null;
    the migration in :mod:`.seed` resolves legacy values to the matching
    builtin script source on first boot of the new build."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # Lua source for this effect. Empty string only on legacy rows that
    # haven't been migrated yet (the seeder fills these in).
    source: str = Field(default="", sa_column=Column(Text))
    # Cached parameter schema parsed out of the script's top-level
    # ``PARAMS`` table. The lint endpoint refreshes this on save so the
    # auto-generated form stays in sync with the source.
    param_schema: list[dict] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # Legacy column - left in place to avoid a destructive migration on
    # existing rows. SQLite originally created this with ``NOT NULL`` and
    # we can't drop the constraint with ALTER TABLE, so default to a
    # placeholder ``"lua"`` for new rows. Not part of the public API.
    effect_type: str = Field(default="lua", nullable=True)
    palette_id: Optional[int] = Field(
        default=None, foreign_key="palette.id", index=True
    )
    light_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # Each target: {"light_id": int, "zone_id": str | None}
    targets: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    # across_lights | across_fixture | across_zones
    spread: str = "across_lights"
    # User-customized parameter values; clamped against ``param_schema``
    # at save time. Engine reads from here every tick.
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Which logical channels the overlay animates. Valid entries are
    # "rgb", "w", "a", "uv", "dimmer", "strobe". Default ["rgb"] matches
    # historical behavior. When "rgb" is absent the base fixture color is
    # left untouched and the effect only modulates the listed aux channel
    # via a scalar brightness envelope (useful for chases on the white /
    # strobe / UV channel while keeping the palette on RGB).
    target_channels: list[str] = Field(
        default_factory=lambda: ["rgb"], sa_column=Column(JSON)
    )
    is_active: bool = False
    builtin: bool = False


class Scene(SQLModel, table=True):
    """A snapshot of light state that can be saved and re-applied.

    Scenes belong to a primary ``controller_id`` (used when listing scenes
    in the per-controller dropdown on the Lights page). When
    ``cross_controller`` is true, the snapshot may cover lights on other
    controllers as well. ``lights`` is a list of per-light state dicts
    captured at save time; each dict mirrors the writable fields on
    :class:`Light` (r/g/b/w/a/uv/dimmer/on + zone_state + motion_state)
    plus the ``light_id`` key used to restore."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    controller_id: int = Field(foreign_key="controller.id", index=True)
    cross_controller: bool = False
    lights: list[dict] = Field(default_factory=list, sa_column=Column(JSON))


class DesignerConversation(SQLModel, table=True):
    """A multi-turn chat with the designer AI (Claude Opus).

    ``messages`` is the raw Anthropic-shaped conversation log: each entry
    is ``{role, content}`` where ``content`` is a list of content blocks
    (``text`` / ``tool_use`` / ``tool_result``). We persist the full raw
    log so every follow-up request can replay prior turns without losing
    any tool plumbing.

    ``last_proposal`` caches the most recent structured output so Apply/
    Save can target proposals by ``proposal_id`` even after the page has
    been reloaded."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    messages: list[dict] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    last_proposal: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )


class EffectConversation(SQLModel, table=True):
    """A multi-turn chat iteratively refining an effect definition.

    Same shape as :class:`DesignerConversation` but the tool Claude is
    forced to call is ``propose_effect`` and the persisted
    ``last_proposal`` always holds one ``EffectIn``-shaped dict under a
    ``proposal`` key. Kept separate from designer conversations to keep
    the two contracts independent."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    messages: list[dict] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    last_proposal: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )


class State(SQLModel, table=True):
    """A rig-wide snapshot of light state covering every controller.

    States are conceptually similar to :class:`Scene` but are always
    rig-wide: they capture every :class:`Light` in the system regardless
    of which controller it lives on, and have no primary ``controller_id``.
    ``lights`` has the same shape as ``Scene.lights`` - a list of dicts
    mirroring the writable fields on :class:`Light` plus the ``light_id``
    key used to restore."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    lights: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
