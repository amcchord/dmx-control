from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_BASE_CONFIG = ConfigDict(protected_namespaces=())


CHANNEL_ROLES = {
    "r",
    "g",
    "b",
    "w",
    "a",  # amber
    "uv",
    "dimmer",
    "strobe",
    "macro",
    "speed",
    "pan",
    "pan_fine",
    "tilt",
    "tilt_fine",
    "zoom",
    "focus",
    "other",
}


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


class LightModelModeOut(BaseModel):
    id: int
    name: str
    channels: list[str]
    channel_count: int
    is_default: bool
    layout: Optional[dict] = None


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

    @field_validator("start_address")
    @classmethod
    def _addr(cls, v: int) -> int:
        if not (1 <= v <= 512):
            raise ValueError("start_address must be 1..512")
        return v


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
    # Per-zone colors for compound fixtures (empty dict for simple pars).
    zone_state: dict = Field(default_factory=dict)
    # Motion axes as floats in [0, 1]; empty when the fixture has no motion.
    motion_state: dict = Field(default_factory=dict)


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


class PaletteIn(BaseModel):
    name: str
    colors: list[str]

    @field_validator("colors")
    @classmethod
    def _hex(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("colors must be non-empty")
        out: list[str] = []
        for c in v:
            s = c.strip()
            if not s.startswith("#"):
                s = "#" + s
            if len(s) != 7:
                raise ValueError(f"invalid hex color: {c}")
            try:
                int(s[1:], 16)
            except ValueError as e:
                raise ValueError(f"invalid hex color: {c}") from e
            out.append(s.upper())
        return out


class PaletteOut(BaseModel):
    id: int
    name: str
    colors: list[str]
    builtin: bool


EFFECT_TYPES = {
    "static",
    "fade",
    "cycle",
    "chase",
    "pulse",
    "rainbow",
    "strobe",
    "sparkle",
    "wave",
}

SPREAD_MODES = {"across_lights", "across_fixture", "across_zones"}

DIRECTIONS = {"forward", "reverse", "pingpong"}


class EffectParams(BaseModel):
    """Runtime parameters shared by every effect primitive.

    All effects interpret the same field set; individual effects ignore
    fields that don't apply (e.g. ``size`` is meaningless for ``pulse``)."""

    speed_hz: float = 0.5
    direction: Literal["forward", "reverse", "pingpong"] = "forward"
    offset: float = 0.0
    intensity: float = 1.0
    size: float = 1.0
    softness: float = 0.5
    fade_in_s: float = 0.25
    fade_out_s: float = 0.25

    @field_validator("speed_hz")
    @classmethod
    def _speed(cls, v: float) -> float:
        if not (0.0 <= v <= 60.0):
            raise ValueError("speed_hz must be in [0, 60]")
        return float(v)

    @field_validator("offset", "intensity", "softness")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("must be in [0, 1]")
        return float(v)

    @field_validator("size")
    @classmethod
    def _size(cls, v: float) -> float:
        if not (0.0 <= v <= 64.0):
            raise ValueError("size must be in [0, 64]")
        return float(v)

    @field_validator("fade_in_s", "fade_out_s")
    @classmethod
    def _fade(cls, v: float) -> float:
        if not (0.0 <= v <= 60.0):
            raise ValueError("fade must be in [0, 60] seconds")
        return float(v)


class SceneIn(BaseModel):
    name: str
    effect_type: Literal[
        "static", "fade", "cycle", "chase", "pulse",
        "rainbow", "strobe", "sparkle", "wave",
    ]
    palette_id: Optional[int] = None
    light_ids: list[int] = Field(default_factory=list)
    targets: Optional[list[BulkTarget]] = None
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )
    params: EffectParams = Field(default_factory=EffectParams)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must be non-empty")
        if len(s) > 128:
            raise ValueError("name too long")
        return s


class LiveSceneIn(BaseModel):
    """Same as SceneIn but name is optional (generated server-side)."""

    name: Optional[str] = None
    effect_type: Literal[
        "static", "fade", "cycle", "chase", "pulse",
        "rainbow", "strobe", "sparkle", "wave",
    ]
    palette_id: Optional[int] = None
    light_ids: list[int] = Field(default_factory=list)
    targets: Optional[list[BulkTarget]] = None
    spread: Literal["across_lights", "across_fixture", "across_zones"] = (
        "across_lights"
    )
    params: EffectParams = Field(default_factory=EffectParams)


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


class SceneOut(BaseModel):
    id: int
    name: str
    effect_type: str
    palette_id: Optional[int] = None
    light_ids: list[int]
    targets: list[BulkTarget]
    spread: str
    params: EffectParams
    is_active: bool
    builtin: bool


class ActiveScene(BaseModel):
    """Entry in ``GET /api/scenes/active``.

    ``id`` is null for live (transient) scenes that have not been promoted
    to a saved preset yet. ``handle`` is a stable opaque id the client can
    use to stop that exact scene."""

    id: Optional[int] = None
    handle: str
    name: str
    effect_type: str
    runtime_s: float


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
