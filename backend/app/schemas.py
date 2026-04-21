from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    "tilt",
    "other",
}


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


class LightModelIn(BaseModel):
    name: str
    channels: list[str]

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("channels must be non-empty")
        if len(v) > 64:
            raise ValueError("too many channels")
        bad = [c for c in v if c not in CHANNEL_ROLES]
        if bad:
            raise ValueError(f"unknown channel role(s): {bad}")
        return v


class LightModelOut(BaseModel):
    id: int
    name: str
    channels: list[str]
    channel_count: int
    builtin: bool


class LightIn(BaseModel):
    model_config = _BASE_CONFIG

    name: str
    controller_id: int
    model_id: int
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


class ColorRequest(BaseModel):
    r: int = 0
    g: int = 0
    b: int = 0
    w: Optional[int] = None
    a: Optional[int] = None
    uv: Optional[int] = None
    dimmer: Optional[int] = None
    on: Optional[bool] = None

    @field_validator("r", "g", "b")
    @classmethod
    def _byte(cls, v: int) -> int:
        if not (0 <= v <= 255):
            raise ValueError("must be 0..255")
        return v


class BulkColorRequest(ColorRequest):
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


class ApplyPaletteRequest(BaseModel):
    light_ids: list[int]
    mode: Literal["cycle", "random", "gradient"] = "cycle"
