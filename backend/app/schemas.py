from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_BASE_CONFIG = ConfigDict(protected_namespaces=())


CHANNEL_ROLES = {
    "r",
    "g",
    "b",
    "w",
    "w2",  # secondary white LED (e.g. warm + cool)
    "w3",
    "a",  # amber
    "a2",
    "uv",
    "uv2",
    "dimmer",
    "strobe",
    "macro",
    "speed",
    "color",  # indexed-color channel; resolved via the mode's color_table
    "pan",
    "pan_fine",
    "tilt",
    "tilt_fine",
    "zoom",
    "focus",
    "other",
}

# Roles whose W/A/UV "mix vs direct" behavior can be configured per mode.
# "mix" (default) = derive the channel's value from RGB when the state
# dict omits it (today's behavior: w = min(r,g,b), a = min(r,g)//2,
# uv = 0). "direct" = never auto-derive; the channel is an independent
# fader controllable via the API / Dashboard sliders and must be set
# explicitly (defaults to 0 otherwise). Palette paint and effect RGB
# blending also skip "direct" roles so the user's explicit value is
# preserved.
POLICY_ROLES = {"w", "w2", "w3", "a", "a2", "uv", "uv2"}
CHANNEL_POLICIES = {"mix", "direct"}
# Extra (non-primary) aux roles — these are always treated as "direct"
# faders by the renderer: there is no meaningful "mix from RGB" for a
# second white LED, so the editor surfaces them as direct-only and the
# policy dict stores an explicit "direct" entry for them.
EXTRA_COLOR_ROLES = {"w2", "w3", "a2", "uv2"}


def _normalize_color_policy(
    policy: Optional[dict], channels: list[str]
) -> dict[str, str]:
    """Validate and clamp a color_policy dict to the modes it applies to.

    Keys not in :data:`POLICY_ROLES` and keys whose role isn't present in
    the mode's channel list are silently dropped (they'd have no effect at
    render time anyway). Unknown values raise :class:`ValueError`.
    """
    if not policy:
        return {}
    if not isinstance(policy, dict):
        raise ValueError("color_policy must be a dict")
    present = set(channels)
    out: dict[str, str] = {}
    for role, mode in policy.items():
        if role not in POLICY_ROLES:
            continue
        if role not in present:
            continue
        if mode not in CHANNEL_POLICIES:
            raise ValueError(
                f"invalid color_policy for '{role}': {mode!r} "
                f"(expected one of {sorted(CHANNEL_POLICIES)})"
            )
        out[role] = mode
    return out


def _validate_channel_list(v: list[str]) -> list[str]:
    if not v:
        raise ValueError("channels must be non-empty")
    # DMX universe is 512 slots. Per-pixel modes (e.g. 112CH bars) are
    # routinely larger than 64, so only cap at the hardware limit.
    if len(v) > 512:
        raise ValueError("too many channels (max 512)")
    bad = [c for c in v if c not in CHANNEL_ROLES]
    if bad:
        raise ValueError(f"unknown channel role(s): {bad}")
    return v


# ---------------------------------------------------------------------------
# Color table (indexed-color channel lookup)
# ---------------------------------------------------------------------------
# Some fixtures expose a single DMX byte that selects from a fixed palette
# of preset colors (e.g. the Blizzard StormChaser's "Cell N" channels with
# 0-15 Off, 16-31 Red, 32-47 Green, ...). We tag those slots with the
# ``color`` channel role and attach a mode-level :class:`ColorTable` that
# the renderer uses to project each frame's logical RGB onto the closest
# preset's representative byte. The same table is shared by every ``color``
# slot in the mode (matching the StormChaser shape: 16 cells, one palette).

MAX_COLOR_TABLE_ENTRIES = 64


class ColorTableEntry(BaseModel):
    """One entry in a fixture's indexed-color lookup table.

    ``lo``/``hi`` are the inclusive byte range that selects this preset
    on the DMX wire (per the manufacturer's documentation); the renderer
    emits the midpoint. ``r``/``g``/``b`` are the entry's representative
    color used for nearest-match snapping from logical RGB."""

    lo: int
    hi: int
    name: str = ""
    r: int
    g: int
    b: int

    @field_validator("lo", "hi", "r", "g", "b")
    @classmethod
    def _byte(cls, v: int) -> int:
        if not (0 <= v <= 255):
            raise ValueError("must be 0..255")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = (v or "").strip()
        if len(s) > 32:
            raise ValueError("entry name too long (max 32 chars)")
        return s

    @model_validator(mode="after")
    def _range(self) -> "ColorTableEntry":
        if self.lo > self.hi:
            raise ValueError("lo must be <= hi")
        return self


class ColorTable(BaseModel):
    """Per-mode indexed-color lookup. Drives every ``color`` slot in the
    mode the same way (see :class:`ColorTableEntry`)."""

    entries: list[ColorTableEntry]
    # When the mode has no separate dimmer channel and the requested
    # logical RGB has ``max(r,g,b) < off_below``, the renderer forces the
    # "off" entry. Lets the existing dimmer-bake-into-RGB path actually
    # go dark on dimmerless wheel fixtures. Default 0 disables.
    off_below: int = 0

    @field_validator("off_below")
    @classmethod
    def _ofb(cls, v: int) -> int:
        if not (0 <= v <= 255):
            raise ValueError("off_below must be 0..255")
        return v

    @field_validator("entries")
    @classmethod
    def _entries(cls, v: list[ColorTableEntry]) -> list[ColorTableEntry]:
        if not v:
            raise ValueError("color table must have at least one entry")
        if len(v) > MAX_COLOR_TABLE_ENTRIES:
            raise ValueError(
                f"too many color table entries "
                f"(max {MAX_COLOR_TABLE_ENTRIES})"
            )
        # Sort by lo for stable storage and cheap overlap checking.
        sorted_entries = sorted(v, key=lambda e: (e.lo, e.hi))
        for prev, cur in zip(sorted_entries, sorted_entries[1:]):
            if cur.lo <= prev.hi:
                raise ValueError(
                    f"overlapping color table ranges: "
                    f"[{prev.lo}-{prev.hi}] and [{cur.lo}-{cur.hi}]"
                )
        return sorted_entries


def _normalize_color_table(
    table: Optional[ColorTable | dict], channels: list[str]
) -> Optional[dict]:
    """Validate a color table against a channel list.

    Returns ``None`` when there is nothing to store: either the table is
    empty/unset or the mode has no ``color`` slot for it to drive (such a
    table would be silently ignored at render time, so don't persist it).
    """
    if table is None:
        return None
    if isinstance(table, dict):
        # Allow callers to pass through Claude's raw payload.
        try:
            table = ColorTable.model_validate(table)
        except Exception as exc:  # pragma: no cover - re-raise as ValueError
            raise ValueError(str(exc)) from exc
    if "color" not in channels:
        return None
    return table.model_dump()


class LoginRequest(BaseModel):
    password: str


class AuthStatus(BaseModel):
    authenticated: bool


class ControllerIn(BaseModel):
    name: str
    ip: str
    port: int = 6454
    net: int = 0
    subnet: int = 0
    universe: int = 0
    enabled: bool = True
    notes: Optional[str] = None

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if len(s) > 2000:
            raise ValueError("notes too long (max 2000 chars)")
        return s

    @field_validator("port")
    @classmethod
    def _port_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("port must be 1..65535")
        return v

    @field_validator("net")
    @classmethod
    def _net_range(cls, v: int) -> int:
        if not (0 <= v <= 127):
            raise ValueError("net must be 0..127")
        return v

    @field_validator("subnet")
    @classmethod
    def _subnet_range(cls, v: int) -> int:
        if not (0 <= v <= 15):
            raise ValueError("subnet must be 0..15")
        return v

    @field_validator("universe")
    @classmethod
    def _universe_range(cls, v: int) -> int:
        if not (0 <= v <= 15):
            raise ValueError("universe must be 0..15")
        return v


class ControllerOut(ControllerIn):
    id: int


class LightModelModeIn(BaseModel):
    # id is optional on input: when PATCHing a model we use it to match
    # existing mode rows; absent => treat as a new mode.
    id: Optional[int] = None
    name: str
    channels: list[str]
    is_default: bool = False
    # Optional compound-fixture overlay. Stored as-is in the mode row.
    layout: Optional[dict] = None
    # Per-role W/A/UV policy — see :func:`_normalize_color_policy`.
    color_policy: Optional[dict] = None
    # Optional indexed-color lookup table — see :class:`ColorTable`.
    # Applies to every ``color`` slot in this mode; ignored (dropped) when
    # the channel list contains no ``color`` entries.
    color_table: Optional[ColorTable | dict] = None

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("mode name must be non-empty")
        if len(s) > 64:
            raise ValueError("mode name too long")
        return s

    @field_validator("channels")
    @classmethod
    def _channels(cls, v: list[str]) -> list[str]:
        return _validate_channel_list(v)

    @model_validator(mode="after")
    def _normalize_policy(self) -> "LightModelModeIn":
        self.color_policy = _normalize_color_policy(
            self.color_policy, self.channels
        )
        # Normalize the color table here too so downstream consumers see a
        # plain dict (or None) and never a Pydantic instance.
        self.color_table = _normalize_color_table(
            self.color_table, self.channels
        )
        return self


class LightModelModeOut(BaseModel):
    id: int
    name: str
    channels: list[str]
    channel_count: int
    is_default: bool
    layout: Optional[dict] = None
    color_policy: dict[str, str] = Field(default_factory=dict)
    color_table: Optional[dict] = None


class LightModelIn(BaseModel):
    """Input schema for creating / updating a LightModel.

    Accepts the new multi-mode shape ``{name, modes:[...]}`` and a legacy
    single-mode shape ``{name, channels:[...]}``. Legacy payloads are
    normalized into a single ``"Default"`` mode so downstream code only has
    to deal with one representation.
    """

    name: str
    modes: Optional[list[LightModelModeIn]] = None
    channels: Optional[list[str]] = None  # legacy shim

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s

    @model_validator(mode="after")
    def _normalize(self) -> "LightModelIn":
        if self.modes and self.channels:
            raise ValueError("provide either modes or channels, not both")
        if not self.modes:
            if not self.channels:
                raise ValueError("must provide modes or channels")
            _validate_channel_list(self.channels)
            self.modes = [
                LightModelModeIn(
                    name=f"{len(self.channels)}ch",
                    channels=list(self.channels),
                    is_default=True,
                )
            ]
            self.channels = None
        if not self.modes:
            raise ValueError("at least one mode required")

        # Deduplicate mode names (case-insensitive) to avoid ambiguous picks.
        seen: set[str] = set()
        for m in self.modes:
            key = m.name.strip().lower()
            if key in seen:
                raise ValueError(f"duplicate mode name: {m.name}")
            seen.add(key)

        # Exactly one default.
        default_idxs = [i for i, m in enumerate(self.modes) if m.is_default]
        if not default_idxs:
            self.modes[0].is_default = True
        elif len(default_idxs) > 1:
            for i, m in enumerate(self.modes):
                m.is_default = i == default_idxs[0]
        return self


class LightModelOut(BaseModel):
    id: int
    name: str
    channels: list[str]
    channel_count: int
    builtin: bool
    image_url: Optional[str] = None
    modes: list[LightModelModeOut] = Field(default_factory=list)


class LightIn(BaseModel):
    model_config = _BASE_CONFIG

    name: str
    controller_id: int
    model_id: int
    mode_id: Optional[int] = None
    start_address: int
    position: int = 0
    notes: Optional[str] = None

    @field_validator("start_address")
    @classmethod
    def _addr(cls, v: int) -> int:
        if not (1 <= v <= 512):
            raise ValueError("start_address must be 1..512")
        return v

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if len(s) > 2000:
            raise ValueError("notes too long (max 2000 chars)")
        return s


class LightOut(BaseModel):
    model_config = _BASE_CONFIG

    id: int
    name: str
    controller_id: int
    model_id: int
    mode_id: Optional[int] = None
    start_address: int
    position: int
    r: int
    g: int
    b: int
    w: int
    a: int
    uv: int
    dimmer: int
    on: bool
    # Extra aux color channels (w2/w3/a2/uv2 -> byte value). Empty dict
    # when the fixture has no extras.
    extra_colors: dict = Field(default_factory=dict)
    # Per-zone colors for compound fixtures (empty dict for simple pars).
    zone_state: dict = Field(default_factory=dict)
    # Motion axes as floats in [0, 1]; empty when the fixture has no motion.
    motion_state: dict = Field(default_factory=dict)
    notes: Optional[str] = None


class MotionRequest(BaseModel):
    """Subset of the motion axes supplied in a color/bulk request.

    Every axis is a float in [0, 1]. Missing axes are left untouched."""

    pan: Optional[float] = None
    tilt: Optional[float] = None
    zoom: Optional[float] = None
    focus: Optional[float] = None

    @field_validator("pan", "tilt", "zoom", "focus")
    @classmethod
    def _unit(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        if not (0.0 <= v <= 1.0):
            raise ValueError("motion axes must be in [0, 1]")
        return v


class ColorRequest(BaseModel):
    r: int = 0
    g: int = 0
    b: int = 0
    w: Optional[int] = None
    a: Optional[int] = None
    uv: Optional[int] = None
    # Extra aux channels for fixtures with multiple whites / ambers / UVs.
    # Each is an independent byte fader; the renderer never derives them
    # from RGB and palette / effect pipelines leave them alone.
    w2: Optional[int] = None
    w3: Optional[int] = None
    a2: Optional[int] = None
    uv2: Optional[int] = None
    dimmer: Optional[int] = None
    on: Optional[bool] = None
    # When present, only the named zone is updated. When omitted, the request
    # targets the whole fixture (all zones + flat color fall back to these
    # values).
    zone_id: Optional[str] = None
    motion: Optional[MotionRequest] = None

    @field_validator("r", "g", "b")
    @classmethod
    def _byte(cls, v: int) -> int:
        if not (0 <= v <= 255):
            raise ValueError("must be 0..255")
        return v

    @field_validator("w2", "w3", "a2", "uv2")
    @classmethod
    def _aux_byte(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        if not (0 <= v <= 255):
            raise ValueError("must be 0..255")
        return v

    @field_validator("zone_id")
    @classmethod
    def _zone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if len(s) > 32:
            raise ValueError("zone_id too long")
        return s


class BulkTarget(BaseModel):
    light_id: int
    zone_id: Optional[str] = None

    @field_validator("zone_id")
    @classmethod
    def _zone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return None
        if len(s) > 32:
            raise ValueError("zone_id too long")
        return s


class BulkColorRequest(ColorRequest):
    light_ids: list[int] = Field(default_factory=list)
    # Zone-aware targets. When set, each entry overrides the top-level
    # zone_id for that light. Lights referenced here should NOT also appear
    # in light_ids. Omit for backward-compatible whole-fixture behavior.
    targets: Optional[list[BulkTarget]] = None


class ReorderLightsRequest(BaseModel):
    light_ids: list[int] = Field(default_factory=list)


class PaletteEntry(BaseModel):
    """One color slot in a palette.

    ``r``/``g``/``b`` are required 0-255 ints. ``w``/``a``/``uv`` are
    optional: when set, palette paint writes them directly (honoring the
    mode's policy); when omitted, W/A are derived from RGB under ``mix``
    policy and UV is left alone. UV is also referred to as "V" in some
    UI labels — they are the same channel role."""

    r: int
    g: int
    b: int
    w: Optional[int] = None
    a: Optional[int] = None
    uv: Optional[int] = None

    @field_validator("r", "g", "b")
    @classmethod
    def _byte(cls, v: int) -> int:
        if not (0 <= v <= 255):
            raise ValueError("must be 0..255")
        return v

    @field_validator("w", "a", "uv")
    @classmethod
    def _aux_byte(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        if not (0 <= v <= 255):
            raise ValueError("aux channel must be 0..255")
        return int(v)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"invalid hex color: {hex_color}")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError as e:
        raise ValueError(f"invalid hex color: {hex_color}") from e


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _normalize_palette_payload(
    *,
    colors: Optional[list[str]],
    entries: Optional[list[PaletteEntry | dict]],
) -> tuple[list[str], list[PaletteEntry]]:
    """Resolve the two accepted input shapes into a consistent pair.

    Callers may supply either ``colors`` (legacy hex list) or ``entries``
    (full per-channel payload); when both are supplied, ``entries`` wins
    and ``colors`` is regenerated from the RGB portion. At least one of
    the two must be non-empty."""
    # Normalize ``entries`` to a list of PaletteEntry.
    entry_models: list[PaletteEntry] = []
    if entries:
        for item in entries:
            if isinstance(item, PaletteEntry):
                entry_models.append(item)
            elif isinstance(item, dict):
                entry_models.append(PaletteEntry(**item))
            else:
                raise ValueError("entries must be PaletteEntry dicts")
    if not entry_models and colors:
        for c in colors:
            r, g, b = _hex_to_rgb(c)
            entry_models.append(PaletteEntry(r=r, g=g, b=b))
    if not entry_models:
        raise ValueError("palette must have at least one color/entry")
    derived_colors = [_rgb_to_hex(e.r, e.g, e.b) for e in entry_models]
    return derived_colors, entry_models


class PaletteIn(BaseModel):
    name: str
    # Either legacy ``colors`` OR the richer ``entries`` list may be
    # provided. If both are provided, ``entries`` wins and ``colors`` is
    # re-derived from the RGB portion so the stored pair stays in sync.
    colors: Optional[list[str]] = None
    entries: Optional[list[PaletteEntry]] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s

    @model_validator(mode="after")
    def _coerce(self) -> "PaletteIn":
        colors, entries = _normalize_palette_payload(
            colors=self.colors, entries=self.entries
        )
        self.colors = colors
        self.entries = entries
        return self


class PaletteOut(BaseModel):
    id: int
    name: str
    colors: list[str]
    entries: list[PaletteEntry] = Field(default_factory=list)
    builtin: bool


SPREAD_MODES = {"across_lights", "across_fixture", "across_zones"}

# Logical "channel groups" an effect overlay may drive. "rgb" is the
# classic path (color animates across the fixture's RGB inputs); the
# others animate a scalar brightness on a single aux channel while
# leaving the base color untouched. Keep this list in sync with the
# merge logic in ``effects.merge_overlay_into_state``.
EFFECT_TARGET_CHANNELS = {"rgb", "w", "a", "uv", "dimmer", "strobe"}

# Limits enforced on engine-applied "system" params (the ones outside the
# script's PARAMS table - the engine still applies fade in/out and the
# global intensity multiplier regardless of what the script declares).
EFFECT_FADE_MAX_S = 30.0
EFFECT_MAX_SOURCE_BYTES = 64 * 1024


# Engine-level controls applied to every effect, regardless of the
# script's PARAMS schema. Scripts read their own knobs out of
# ``ctx.params`` (which is the user's saved ``params`` minus these).
class EffectControls(BaseModel):
    """Per-effect controls the engine applies on top of the script.

    These never reach the script's ``ctx.params``: ``intensity`` is a
    final multiplier on the script's ``brightness``, and the fade-in /
    fade-out seconds shape the engine's start/stop envelope around the
    whole effect."""

    intensity: float = 1.0
    fade_in_s: float = 0.25
    fade_out_s: float = 0.25

    @field_validator("intensity")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("intensity must be in [0, 1]")
        return float(v)

    @field_validator("fade_in_s", "fade_out_s")
    @classmethod
    def _fade(cls, v: float) -> float:
        if not (0.0 <= v <= EFFECT_FADE_MAX_S):
            raise ValueError(
                f"fade must be in [0, {EFFECT_FADE_MAX_S:g}] seconds"
            )
        return float(v)


def _validate_target_channels(v: Optional[list[str]]) -> list[str]:
    if not v:
        return ["rgb"]
    seen: list[str] = []
    for entry in v:
        if not isinstance(entry, str):
            raise ValueError("target_channels must be strings")
        key = entry.strip().lower()
        if key not in EFFECT_TARGET_CHANNELS:
            raise ValueError(
                f"unknown target channel {entry!r}; expected one of "
                f"{sorted(EFFECT_TARGET_CHANNELS)}"
            )
        if key not in seen:
            seen.append(key)
    return seen


def _validate_source(v: str) -> str:
    if not isinstance(v, str):
        raise ValueError("source must be a string")
    if len(v.encode("utf-8")) > EFFECT_MAX_SOURCE_BYTES:
        raise ValueError(
            f"source too large (max {EFFECT_MAX_SOURCE_BYTES} bytes)"
        )
    if not v.strip():
        raise ValueError("source must be non-empty")
    return v


class EffectIn(BaseModel):
    """Save-or-update payload for an effect.

    ``source`` is the Lua script. ``params`` is a free-form dict whose
    keys must match ids declared in the script's ``PARAMS`` table
    (extras are tolerated for forward compatibility). ``controls`` holds
    the engine-level fade/intensity envelope."""

    name: str
    source: str
    palette_id: Optional[int] = None
    light_ids: list[int] = Field(default_factory=list)
    targets: Optional[list[BulkTarget]] = None
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )
    params: dict = Field(default_factory=dict)
    controls: EffectControls = Field(default_factory=EffectControls)
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        return _validate_source(v)

    @field_validator("target_channels")
    @classmethod
    def _channels(cls, v: list[str]) -> list[str]:
        return _validate_target_channels(v)


class LiveEffectIn(BaseModel):
    """Same as EffectIn but name is optional (generated server-side)."""

    name: Optional[str] = None
    source: str
    palette_id: Optional[int] = None
    light_ids: list[int] = Field(default_factory=list)
    targets: Optional[list[BulkTarget]] = None
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )
    params: dict = Field(default_factory=dict)
    controls: EffectControls = Field(default_factory=EffectControls)
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        return _validate_source(v)

    @field_validator("target_channels")
    @classmethod
    def _channels(cls, v: list[str]) -> list[str]:
        return _validate_target_channels(v)


class SaveLiveRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class EffectOut(BaseModel):
    id: int
    name: str
    source: str
    description: str = ""
    param_schema: list[dict] = Field(default_factory=list)
    palette_id: Optional[int] = None
    light_ids: list[int]
    targets: list[BulkTarget]
    spread: str
    params: dict
    controls: EffectControls = Field(default_factory=EffectControls)
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])
    is_active: bool
    builtin: bool


class EffectLintRequest(BaseModel):
    source: str

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        return _validate_source(v)


class EffectLintResponse(BaseModel):
    ok: bool
    name: str = ""
    description: str = ""
    param_schema: list[dict] = Field(default_factory=list)
    has_render: bool = False
    has_tick: bool = False
    error: Optional[dict] = None


class ActiveEffect(BaseModel):
    """Entry in ``GET /api/effects/active``.

    ``id`` is null for live (transient) effects that have not been promoted
    to a saved preset yet. ``handle`` is a stable opaque id the client can
    use to stop that exact effect."""

    id: Optional[int] = None
    handle: str
    name: str
    runtime_s: float


# ---------------------------------------------------------------------------
# Effect layers (Photoshop-style compositing)
# ---------------------------------------------------------------------------
BLEND_MODES_VALID = {"normal", "add", "multiply", "screen", "max", "min", "replace"}


class LayerCreate(BaseModel):
    """Push a new layer onto the rig.

    ``effect_id`` is required; transient/preview layers do not go through
    this endpoint (they ride the existing live-effect path). All
    layer-owned fields are optional and fall back to sensible defaults
    that mirror today's single-effect behavior."""

    effect_id: int
    name: Optional[str] = None
    z_index: Optional[int] = None
    blend_mode: Literal["normal", "add", "multiply", "screen", "max", "min", "replace"] = (
        "normal"
    )
    opacity: float = 1.0
    intensity: float = 1.0
    fade_in_s: float = 0.25
    fade_out_s: float = 0.25
    mute: bool = False
    solo: bool = False
    mask_light_ids: list[int] = Field(default_factory=list)
    target_channels: Optional[list[str]] = None
    spread: Optional[Literal["across_lights", "across_fixture", "across_zones"]] = None
    light_ids: Optional[list[int]] = None
    targets: Optional[list[BulkTarget]] = None
    palette_id: Optional[int] = None
    params_override: dict = Field(default_factory=dict)

    @field_validator("opacity", "intensity")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("must be in [0, 1]")
        return float(v)

    @field_validator("fade_in_s", "fade_out_s")
    @classmethod
    def _fade(cls, v: float) -> float:
        if not (0.0 <= v <= EFFECT_FADE_MAX_S):
            raise ValueError(
                f"fade must be in [0, {EFFECT_FADE_MAX_S:g}] seconds"
            )
        return float(v)

    @field_validator("target_channels")
    @classmethod
    def _channels(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        return _validate_target_channels(v)


class LayerPatch(BaseModel):
    """In-place patch to a running layer.

    Every field is optional. Only sent properties are touched; this maps
    directly onto :meth:`EffectEngine.patch_layer` so opacity sliders,
    mute/solo toggles, blend dropdowns, and reorder operations all reach
    the engine through the same entry point."""

    name: Optional[str] = None
    z_index: Optional[int] = None
    blend_mode: Optional[
        Literal["normal", "add", "multiply", "screen", "max", "min", "replace"]
    ] = None
    opacity: Optional[float] = None
    intensity: Optional[float] = None
    fade_in_s: Optional[float] = None
    fade_out_s: Optional[float] = None
    mute: Optional[bool] = None
    solo: Optional[bool] = None
    mask_light_ids: Optional[list[int]] = None
    target_channels: Optional[list[str]] = None
    params_override: Optional[dict] = None

    @field_validator("opacity", "intensity")
    @classmethod
    def _unit(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        if not (0.0 <= v <= 1.0):
            raise ValueError("must be in [0, 1]")
        return float(v)

    @field_validator("fade_in_s", "fade_out_s")
    @classmethod
    def _fade(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        if not (0.0 <= v <= EFFECT_FADE_MAX_S):
            raise ValueError(
                f"fade must be in [0, {EFFECT_FADE_MAX_S:g}] seconds"
            )
        return float(v)

    @field_validator("target_channels")
    @classmethod
    def _channels(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        return _validate_target_channels(v)


class LayerReorder(BaseModel):
    """Bulk z-index update. Each entry is ``{layer_id, z_index}``."""

    order: list[dict]


class LayerOut(BaseModel):
    """Serialized running layer (used by REST + WS layer store)."""

    handle: str
    layer_id: Optional[int] = None
    effect_id: Optional[int] = None
    name: str
    runtime_s: float = 0.0
    z_index: int = 100
    blend_mode: str = "normal"
    opacity: float = 1.0
    intensity: float = 1.0
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])
    mute: bool = False
    solo: bool = False
    auto_muted: bool = False
    stopping: bool = False
    error: Optional[str] = None
    error_count: int = 0
    last_tick_ms: float = 0.0
    mask_light_ids: list[int] = Field(default_factory=list)


class HealthOut(BaseModel):
    """``GET /api/health`` payload (extended with engine telemetry)."""

    ok: bool = True
    tick_count: int = 0
    dropped_frames: int = 0
    last_tick_ms: float = 0.0
    active_layers: int = 0
    tick_hz: float = 30.0


class SceneLightState(BaseModel):
    """Per-light state captured in a Scene snapshot.

    Mirrors the writable fields on :class:`LightOut` so that a scene can
    be applied by copying these values straight back onto the matching
    Light row and pushing the result to Art-Net."""

    model_config = _BASE_CONFIG

    light_id: int
    r: int = 0
    g: int = 0
    b: int = 0
    w: int = 0
    a: int = 0
    uv: int = 0
    dimmer: int = 255
    on: bool = True
    extra_colors: dict = Field(default_factory=dict)
    zone_state: dict = Field(default_factory=dict)
    motion_state: dict = Field(default_factory=dict)


class SceneCreate(BaseModel):
    """Save the current state of one controller (or the whole rig)."""

    name: str
    controller_id: int
    cross_controller: bool = False
    # When provided, only these lights are captured. Otherwise the snapshot
    # covers every light on ``controller_id`` (or every light at all when
    # ``cross_controller`` is true).
    light_ids: Optional[list[int]] = None
    # When true the snapshot is built from the live rendered buffer
    # (``ArtNetManager.snapshot_rendered()``) rather than the DB state.
    # Useful when an effect is running and the user wants to freeze the
    # visible output.
    from_rendered: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class SceneUpdate(BaseModel):
    """Rename, re-scope, or re-capture an existing scene."""

    name: Optional[str] = None
    controller_id: Optional[int] = None
    cross_controller: Optional[bool] = None
    # When true, re-capture the snapshot from current state (DB by default,
    # or live-rendered when ``from_rendered`` is also set).
    recapture: bool = False
    from_rendered: bool = False
    # Only consulted when ``recapture`` is true.
    light_ids: Optional[list[int]] = None
    # Replace the saved layer stack on this scene. ``None`` (the default)
    # leaves the existing layers untouched; pass ``[]`` to drop them all.
    layers: Optional[list[dict]] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class SceneSavedLayer(BaseModel):
    """One layer spec saved alongside a scene's base snapshot.

    Mirrors :class:`LayerCreate` minus the runtime-only fields. Loaded
    by the Scene Composer and pushed onto the engine in order when the
    scene is applied."""

    effect_id: int
    name: Optional[str] = None
    z_index: Optional[int] = None
    blend_mode: str = "normal"
    opacity: float = 1.0
    intensity: float = 1.0
    fade_in_s: float = 0.25
    fade_out_s: float = 0.25
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])
    spread: str = "across_lights"
    light_ids: list[int] = Field(default_factory=list)
    targets: list[BulkTarget] = Field(default_factory=list)
    mask_light_ids: list[int] = Field(default_factory=list)
    palette_id: Optional[int] = None
    params_override: dict = Field(default_factory=dict)


class SceneOut(BaseModel):
    """Serialized scene.

    ``id`` is nullable so that virtual built-ins (Blackout) can ride on the
    same shape without a persisted row. Virtual entries always have
    ``builtin=True``."""

    model_config = _BASE_CONFIG

    id: Optional[int] = None
    name: str
    controller_id: int
    cross_controller: bool
    lights: list[SceneLightState] = Field(default_factory=list)
    layers: list[dict] = Field(default_factory=list)
    builtin: bool = False


class StateCreate(BaseModel):
    """Save the current state of the entire rig.

    A State is a snapshot of every light on every controller. When
    ``from_rendered`` is true, the snapshot is built from the live
    rendered Art-Net buffer rather than the DB (useful when an effect is
    running and the user wants to freeze the visible output)."""

    name: str
    from_rendered: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class StateUpdate(BaseModel):
    """Rename or re-capture an existing rig-wide state."""

    name: Optional[str] = None
    recapture: bool = False
    from_rendered: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class StateOut(BaseModel):
    """Serialized rig-wide state.

    ``id`` is nullable so that virtual built-ins (Blackout all) can ride
    on the same shape without a persisted row. Virtual entries always
    have ``builtin=True``."""

    model_config = _BASE_CONFIG

    id: Optional[int] = None
    name: str
    lights: list[SceneLightState] = Field(default_factory=list)
    builtin: bool = False


class ApplyPaletteRequest(BaseModel):
    light_ids: list[int]
    mode: Literal["cycle", "random", "gradient"] = "cycle"
    # How the palette is distributed:
    #   across_lights  (default) - one color per fixture
    #   across_fixture           - each fixture gets the palette rolled
    #                              across its own zones (a 16-pixel bar
    #                              becomes a 16-step gradient inside itself)
    #   across_zones             - treat every zone in the selection as a
    #                              flat list and spread one palette across
    #                              them end-to-end
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )


# ---------------------------------------------------------------------------
# Designer (Claude chat → structured rig states / scenes)
# ---------------------------------------------------------------------------


class DesignerMessageIn(BaseModel):
    """One user turn in a designer chat."""

    message: str

    @field_validator("message")
    @classmethod
    def _msg(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("message must be non-empty")
        if len(s) > 10_000:
            raise ValueError("message too long (max 10000 chars)")
        return s


class DesignerProposalLight(BaseModel):
    """One light's target state inside a designer proposal."""

    light_id: int
    on: bool = True
    dimmer: int = 255
    r: int = 0
    g: int = 0
    b: int = 0
    w: Optional[int] = None
    a: Optional[int] = None
    uv: Optional[int] = None
    w2: Optional[int] = None
    w3: Optional[int] = None
    a2: Optional[int] = None
    uv2: Optional[int] = None
    zone_state: dict = Field(default_factory=dict)
    motion_state: dict = Field(default_factory=dict)


class DesignerEffectProposalBody(BaseModel):
    """Claude-facing shape for an effect proposal in the designer chat.

    ``source`` is the Lua script Claude wrote. Optionally ``builtin``
    names a builtin script to clone instead, which Claude tends to use
    when the user just said "give me a fade" without specifying anything
    custom. ``light_ids`` / ``targets`` are resolved client-side against
    the user's current selection."""

    source: Optional[str] = None
    builtin: Optional[str] = None
    palette_id: Optional[int] = None
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )
    params: dict = Field(default_factory=dict)
    controls: EffectControls = Field(default_factory=EffectControls)
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])
    light_ids: list[int] = Field(default_factory=list)
    targets: list[BulkTarget] = Field(default_factory=list)


class DesignerProposal(BaseModel):
    """A named rig design Claude proposes.

    ``kind='state'`` is a rig-wide snapshot (every addressed light);
    ``kind='scene'`` targets one ``controller_id``;
    ``kind='palette'`` is a new palette draft (saveable to /api/palettes);
    ``kind='effect'`` is a new effect spec (saveable to /api/effects and
    playable on the user's current selection)."""

    proposal_id: str
    kind: Literal["state", "scene", "palette", "effect"]
    name: str
    controller_id: Optional[int] = None
    notes: Optional[str] = None
    lights: list[DesignerProposalLight] = Field(default_factory=list)
    # Only set when kind='palette'.
    palette_entries: Optional[list[PaletteEntry]] = None
    # Only set when kind='effect'.
    effect: Optional[DesignerEffectProposalBody] = None


class DesignerMessageOut(BaseModel):
    """One rendered turn in a designer chat (UI-friendly)."""

    role: Literal["user", "assistant"]
    text: str = ""
    proposals: list[DesignerProposal] = Field(default_factory=list)


class DesignerConversationSummary(BaseModel):
    id: int
    name: str
    message_count: int
    updated_at: str


class DesignerConversationOut(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    messages: list[DesignerMessageOut] = Field(default_factory=list)
    last_proposals: list[DesignerProposal] = Field(default_factory=list)
    last_critique: Optional[dict] = None


class DesignerConversationCreate(BaseModel):
    name: Optional[str] = None


class DesignerConversationRename(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class DesignerApplyRequest(BaseModel):
    proposal_id: str


class DesignerSaveRequest(BaseModel):
    proposal_id: str
    name: Optional[str] = None


# ---- Self-critique ("double-check") --------------------------------------

DesignerCritiqueVerdict = Literal[
    "looks_good", "minor_issues", "needs_review", "regenerate"
]
DesignerCritiqueSeverity = Literal["low", "med", "high"]


class DesignerCritiqueCoverage(BaseModel):
    """One requirement extracted from the user's request and whether the
    proposal addresses it."""

    requirement: str
    addressed: bool
    evidence: Optional[str] = None


class DesignerCritiqueRisk(BaseModel):
    issue: str
    severity: DesignerCritiqueSeverity = "low"


class DesignerCritique(BaseModel):
    """Structured QA review of a proposal.

    Filled by a second Anthropic call after each proposal so the UI can
    show "what I think you wanted vs. what was produced" inline."""

    intent_summary: str = ""
    coverage: list[DesignerCritiqueCoverage] = Field(default_factory=list)
    risks: list[DesignerCritiqueRisk] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    verdict: DesignerCritiqueVerdict = "looks_good"
    confidence: float = 0.5
    usage: Optional[dict] = None


class DesignerCritiqueRequest(BaseModel):
    proposal_id: str
    user_request: Optional[str] = None


class DesignerCritiqueResponse(BaseModel):
    ok: bool = True
    proposal_id: str
    critique: DesignerCritique


# ---------------------------------------------------------------------------
# Claude palette generator (one-shot) + effects chat (multi-turn)
# ---------------------------------------------------------------------------


class PaletteGenerateRequest(BaseModel):
    """One-shot palette generation from a free-text prompt."""

    prompt: str
    num_colors: Optional[int] = None
    include_aux: Optional[bool] = None

    @field_validator("prompt")
    @classmethod
    def _p(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("prompt must be non-empty")
        if len(s) > 2000:
            raise ValueError("prompt too long (max 2000 chars)")
        return s

    @field_validator("num_colors")
    @classmethod
    def _n(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        if not (2 <= v <= 16):
            raise ValueError("num_colors must be in [2, 16]")
        return v


class PaletteGenerateResponse(BaseModel):
    name: str
    entries: list[PaletteEntry]
    summary: Optional[str] = None


class EffectMessageIn(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def _msg(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("message must be non-empty")
        if len(s) > 10_000:
            raise ValueError("message too long (max 10000 chars)")
        return s


class EffectProposal(BaseModel):
    """One effect Claude has proposed in a chat.

    The shape mirrors :class:`EffectIn` so the UI can drop it straight
    into the live editor, but ``light_ids`` / ``targets`` are optional
    because Claude typically describes an effect that should run on the
    user's current selection (resolved client-side)."""

    proposal_id: str
    summary: Optional[str] = None
    name: str
    source: str
    description: str = ""
    param_schema: list[dict] = Field(default_factory=list)
    palette_id: Optional[int] = None
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )
    params: dict = Field(default_factory=dict)
    controls: EffectControls = Field(default_factory=EffectControls)
    target_channels: list[str] = Field(default_factory=lambda: ["rgb"])
    light_ids: list[int] = Field(default_factory=list)
    targets: list[BulkTarget] = Field(default_factory=list)

    @field_validator("target_channels")
    @classmethod
    def _channels(cls, v: list[str]) -> list[str]:
        return _validate_target_channels(v)


class EffectChatMessageOut(BaseModel):
    role: Literal["user", "assistant"]
    text: str = ""
    proposal: Optional[EffectProposal] = None


class EffectConversationSummary(BaseModel):
    id: int
    name: str
    message_count: int
    updated_at: str


class EffectConversationOut(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    messages: list[EffectChatMessageOut] = Field(default_factory=list)
    last_proposal: Optional[EffectProposal] = None
    last_critique: Optional[dict] = None


class EffectConversationCreate(BaseModel):
    name: Optional[str] = None


class EffectConversationRename(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s
