"""Minimal async-friendly Art-Net DMX sender.

We speak the Art-Net protocol directly over UDP. Each controller owns one or
more universe buffers (512 bytes) that we maintain in memory. Any change to a
light patches its channel slice into the buffer and re-sends the universe.

Art-Net ArtDmx packet layout (see https://art-net.org.uk/ spec):

    Offset  Field          Size  Notes
    0       ID             8     "Art-Net\0"
    8       OpCode         2     0x5000 little-endian (ArtDmx)
    10      ProtVerHi      1     0
    11      ProtVer        1     14
    12      Sequence       1     0 = disabled
    13      Physical       1     0
    14      SubUni         1     (Subnet << 4) | Universe
    15      Net            1     0..127
    16      LengthHi       1     (length >> 8)
    17      Length         1     (length & 0xFF), even, 2..512
    18+     Data           N     DMX channel data
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from dataclasses import dataclass
from typing import Iterable

from .models import Controller, Light, LightModel

log = logging.getLogger(__name__)

ART_NET_HEADER = b"Art-Net\0"
OP_DMX = 0x5000
PROT_VER = 14
UNIVERSE_SIZE = 512


def build_artdmx_packet(net: int, subnet: int, universe: int, data: bytes) -> bytes:
    if len(data) != UNIVERSE_SIZE:
        raise ValueError("data must be exactly 512 bytes")
    header = bytearray()
    header += ART_NET_HEADER
    header += OP_DMX.to_bytes(2, "little")
    header += bytes([0, PROT_VER])  # prot ver hi/lo (big endian)
    header += bytes([0, 0])  # sequence, physical
    header += bytes([((subnet & 0x0F) << 4) | (universe & 0x0F)])
    header += bytes([net & 0x7F])
    header += bytes([(UNIVERSE_SIZE >> 8) & 0xFF, UNIVERSE_SIZE & 0xFF])
    return bytes(header) + data


@dataclass
class LightBinding:
    """Materialized placement of a Light on a universe buffer."""

    light_id: int
    start_index: int  # 0-based position in the 512-byte DMX buffer
    channels: list[str]  # role per channel, e.g. ["r","g","b","w"]


class UniverseBuffer:
    def __init__(self, net: int, subnet: int, universe: int) -> None:
        self.net = net
        self.subnet = subnet
        self.universe = universe
        self.data = bytearray(UNIVERSE_SIZE)
        self.bindings: dict[int, LightBinding] = {}  # light_id -> binding

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.net, self.subnet, self.universe)


def _compute_channel_values(channels: list[str], state: dict) -> list[int]:
    """Map a light's logical state dict into DMX channel values ordered by role."""
    r = int(state.get("r", 0))
    g = int(state.get("g", 0))
    b = int(state.get("b", 0))
    w = state.get("w")
    a = state.get("a")
    uv = state.get("uv")
    dimmer = int(state.get("dimmer", 255))
    on = bool(state.get("on", True))
    if not on:
        return [0] * len(channels)

    # If the model has no dedicated dimmer channel, bake brightness into RGB.
    has_dimmer = "dimmer" in channels
    scale = 1.0 if has_dimmer else max(0, min(255, dimmer)) / 255.0

    # Reasonable defaults for white/amber/uv derived from RGB if unspecified.
    if w is None:
        w = min(r, g, b)
    if a is None:
        a = min(r, g) // 2
    if uv is None:
        uv = 0

    values: list[int] = []
    for role in channels:
        if role == "r":
            values.append(int(round(r * scale)))
        elif role == "g":
            values.append(int(round(g * scale)))
        elif role == "b":
            values.append(int(round(b * scale)))
        elif role == "w":
            values.append(int(round(w * scale)))
        elif role == "a":
            values.append(int(round(a * scale)))
        elif role == "uv":
            values.append(int(round(uv * scale)))
        elif role == "dimmer":
            values.append(max(0, min(255, dimmer)))
        elif role == "strobe":
            values.append(0)  # no strobe in v1
        elif role == "macro":
            values.append(0)
        elif role == "speed":
            values.append(0)
        elif role == "pan":
            values.append(128)
        elif role == "tilt":
            values.append(128)
        else:  # other / unknown
            values.append(0)
    # clamp
    return [max(0, min(255, v)) for v in values]


class ArtNetManager:
    """Thread-safe, send-on-demand Art-Net manager.

    One UDP socket is shared across all controllers. State is kept in
    per-controller universe buffers so any restart or CRUD change can rebuild
    them deterministically from the database.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # controller_id -> (Controller, UniverseBuffer)
        self._controllers: dict[int, tuple[Controller, UniverseBuffer]] = {}
        # light_id -> controller_id, for quick routing on color updates
        self._light_to_controller: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle / sync helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def rebuild(
        self,
        controllers: Iterable[Controller],
        lights: Iterable[Light],
        models: Iterable[LightModel],
    ) -> None:
        """Rebuild all in-memory state from database snapshots."""
        models_by_id = {m.id: m for m in models}
        lights_list = list(lights)

        with self._lock:
            self._controllers.clear()
            self._light_to_controller.clear()
            for ctrl in controllers:
                buf = UniverseBuffer(ctrl.net, ctrl.subnet, ctrl.universe)
                self._controllers[ctrl.id] = (ctrl, buf)

            for light in lights_list:
                model = models_by_id.get(light.model_id)
                if model is None:
                    continue
                if light.controller_id not in self._controllers:
                    continue
                ctrl, buf = self._controllers[light.controller_id]
                start = light.start_address - 1  # DMX is 1-indexed; buffer is 0-indexed
                if start < 0 or start + model.channel_count > UNIVERSE_SIZE:
                    log.warning(
                        "Light %s does not fit in universe (start=%s, count=%s)",
                        light.id,
                        light.start_address,
                        model.channel_count,
                    )
                    continue
                binding = LightBinding(
                    light_id=light.id,
                    start_index=start,
                    channels=list(model.channels),
                )
                buf.bindings[light.id] = binding
                self._light_to_controller[light.id] = ctrl.id

                values = _compute_channel_values(
                    binding.channels,
                    {
                        "r": light.r,
                        "g": light.g,
                        "b": light.b,
                        "w": light.w,
                        "a": light.a,
                        "uv": light.uv,
                        "dimmer": light.dimmer,
                        "on": light.on,
                    },
                )
                for i, v in enumerate(values):
                    buf.data[start + i] = v

            # Push initial state to all controllers.
            for cid, (ctrl, buf) in self._controllers.items():
                if ctrl.enabled:
                    self._send_buffer(ctrl, buf)

    def set_light_state(self, light_id: int, state: dict) -> bool:
        """Update a light's values and push a packet. Returns True on success."""
        with self._lock:
            ctrl_id = self._light_to_controller.get(light_id)
            if ctrl_id is None:
                return False
            ctrl, buf = self._controllers[ctrl_id]
            binding = buf.bindings.get(light_id)
            if binding is None:
                return False
            values = _compute_channel_values(binding.channels, state)
            for i, v in enumerate(values):
                buf.data[binding.start_index + i] = v
            if ctrl.enabled:
                self._send_buffer(ctrl, buf)
            return True

    def blackout(self, controller_id: int) -> bool:
        """Zero out every channel on a controller and send."""
        with self._lock:
            entry = self._controllers.get(controller_id)
            if entry is None:
                return False
            ctrl, buf = entry
            for i in range(UNIVERSE_SIZE):
                buf.data[i] = 0
            if ctrl.enabled:
                self._send_buffer(ctrl, buf)
            return True

    def send_all(self) -> None:
        """Re-send every controller's buffer (useful after a reload)."""
        with self._lock:
            for ctrl, buf in self._controllers.values():
                if ctrl.enabled:
                    self._send_buffer(ctrl, buf)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _send_buffer(self, ctrl: Controller, buf: UniverseBuffer) -> None:
        packet = build_artdmx_packet(
            ctrl.net, ctrl.subnet, ctrl.universe, bytes(buf.data)
        )
        try:
            self._sock.sendto(packet, (ctrl.ip, ctrl.port))
        except OSError as e:
            log.warning("Failed to send Art-Net to %s:%s: %s", ctrl.ip, ctrl.port, e)


manager = ArtNetManager()


async def rebuild_manager_async() -> None:
    """Reload manager state from DB in a worker thread."""
    from sqlmodel import Session, select

    from .db import engine
    from .models import Controller, Light, LightModel

    def _work() -> None:
        with Session(engine) as sess:
            controllers = list(sess.exec(select(Controller)))
            lights = list(sess.exec(select(Light)))
            models = list(sess.exec(select(LightModel)))
        manager.rebuild(controllers, lights, models)

    await asyncio.to_thread(_work)


def rebuild_manager_sync() -> None:
    from sqlmodel import Session, select

    from .db import engine
    from .models import Controller, Light, LightModel

    with Session(engine) as sess:
        controllers = list(sess.exec(select(Controller)))
        lights = list(sess.exec(select(Light)))
        models = list(sess.exec(select(LightModel)))
    manager.rebuild(controllers, lights, models)
