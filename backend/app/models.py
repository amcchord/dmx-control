from __future__ import annotations

from typing import Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
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


class LightModel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    channel_count: int
    # Ordered list of channel roles, e.g. ["r","g","b","w","a","uv"].
    channels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    builtin: bool = False


class Light(SQLModel, table=True):
    model_config = {"protected_namespaces": ()}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    controller_id: int = Field(foreign_key="controller.id", index=True)
    model_id: int = Field(foreign_key="lightmodel.id", index=True)
    start_address: int  # 1..512
    position: int = 0

    # Persisted RGBW state so we can restore on restart.
    r: int = 0
    g: int = 0
    b: int = 0
    w: int = 0
    a: int = 0
    uv: int = 0
    dimmer: int = 255
    on: bool = True


class Palette(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    colors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    builtin: bool = False
