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

from .models import Controller, Light, LightModel, LightModelMode

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
    layout: dict | None = None  # compound zone/motion overlay (see docs)


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
    """Map a light's logical state dict into DMX channel values ordered by role.

    Flat (non-compound) path: iterates ``channels`` and emits one byte per
    slot. For compound fixtures use :func:`_compute_layout_values` instead.
    """
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

    # Motion defaults (floats in [0, 1]; default centered).
    motion = state.get("motion") or {}
    mpan = float(motion.get("pan", 0.5))
    mtilt = float(motion.get("tilt", 0.5))
    mzoom = float(motion.get("zoom", 0.5))
    mfocus = float(motion.get("focus", 0.5))

    def _fine_byte(v: float) -> int:
        i16 = max(0, min(65535, int(round(max(0.0, min(1.0, v)) * 65535))))
        return i16 & 0xFF

    def _coarse_byte(v: float) -> int:
        return max(0, min(255, int(round(max(0.0, min(1.0, v)) * 255))))

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
            values.append(_coarse_byte(mpan))
        elif role == "pan_fine":
            values.append(_fine_byte(mpan))
        elif role == "tilt":
            values.append(_coarse_byte(mtilt))
        elif role == "tilt_fine":
            values.append(_fine_byte(mtilt))
        elif role == "zoom":
            values.append(_coarse_byte(mzoom))
        elif role == "focus":
            values.append(_coarse_byte(mfocus))
        else:  # other / unknown
            values.append(0)
    return [max(0, min(255, v)) for v in values]


def _derive_zone_defaults(
    zs: dict, global_dimmer: int
) -> tuple[int, int, int, int, int, int, int, bool]:
    """Return (r, g, b, w, a, uv, dimmer, on) with fallback derivation."""
    r = int(zs.get("r", 0))
    g = int(zs.get("g", 0))
    b = int(zs.get("b", 0))
    w = zs.get("w")
    a = zs.get("a")
    uv = zs.get("uv")
    dim = int(zs.get("dimmer", global_dimmer))
    on = bool(zs.get("on", True))
    if w is None:
        w = min(r, g, b)
    if a is None:
        a = min(r, g) // 2
    if uv is None:
        uv = 0
    return r, g, b, int(w), int(a), int(uv), dim, on


def _compute_layout_values(
    channels: list[str], layout: dict, state: dict
) -> list[int]:
    """Compound-fixture renderer: writes zone/motion/global values into a
    bytes-like list sized to the mode's channel count."""
    n = len(channels)
    vals = [0] * n

    on = bool(state.get("on", True))
    if not on:
        return vals

    global_dimmer = int(state.get("dimmer", 255))
    zone_state = state.get("zone_state") or {}
    motion_state = state.get("motion_state") or {}

    # Fallback "flat" state for zones that don't have explicit state yet.
    fallback = {
        "r": state.get("r", 0),
        "g": state.get("g", 0),
        "b": state.get("b", 0),
        "w": state.get("w"),
        "a": state.get("a"),
        "uv": state.get("uv"),
        "dimmer": global_dimmer,
        "on": True,
    }

    def _in_range(off) -> bool:
        return isinstance(off, int) and 0 <= off < n

    zones = layout.get("zones") or []
    for zone in zones:
        zid = zone.get("id")
        colors = zone.get("colors") or {}
        dimmer_off = zone.get("dimmer")
        strobe_off = zone.get("strobe")

        zs = zone_state.get(zid) if zid is not None else None
        if not isinstance(zs, dict):
            zs = fallback

        zr, zg, zb, zw, za, zuv, zdim, zon = _derive_zone_defaults(
            zs, global_dimmer
        )
        if not zon:
            if _in_range(dimmer_off):
                vals[dimmer_off] = 0
            continue

        has_zone_dim = _in_range(dimmer_off)
        scale = 1.0 if has_zone_dim else max(0, min(255, zdim)) / 255.0
        role_vals = {
            "r": zr, "g": zg, "b": zb,
            "w": zw, "a": za, "uv": zuv,
        }
        for role, off in colors.items():
            if not _in_range(off):
                continue
            v = role_vals.get(role, 0)
            vals[off] = max(0, min(255, int(round(v * scale))))
        if has_zone_dim:
            vals[dimmer_off] = max(0, min(255, zdim))
        if _in_range(strobe_off):
            vals[strobe_off] = 0  # no strobe animation in v1

    # Motion axes — floats in [0, 1]; split to coarse/fine when both exist.
    motion = layout.get("motion") or {}
    for axis in ("pan", "tilt", "zoom", "focus"):
        coarse_off = motion.get(axis)
        fine_off = motion.get(f"{axis}_fine")
        has_coarse = _in_range(coarse_off)
        has_fine = _in_range(fine_off)
        if not (has_coarse or has_fine):
            continue
        raw = motion_state.get(axis)
        if raw is None:
            raw = 0.5
        v = max(0.0, min(1.0, float(raw)))
        if has_coarse and has_fine:
            i16 = int(round(v * 65535))
            i16 = max(0, min(65535, i16))
            vals[coarse_off] = (i16 >> 8) & 0xFF
            vals[fine_off] = i16 & 0xFF
        elif has_coarse:
            vals[coarse_off] = max(0, min(255, int(round(v * 255))))
        else:
            vals[fine_off] = max(0, min(255, int(round(v * 255))))

    # Globals — dimmer/strobe/macro/speed at explicit offsets.
    globals_ = layout.get("globals") or {}
    dim_off = globals_.get("dimmer")
    if _in_range(dim_off):
        vals[dim_off] = max(0, min(255, global_dimmer))
    for role in ("strobe", "macro", "speed"):
        off = globals_.get(role)
        if _in_range(off):
            vals[off] = 0

    return vals


def _render_binding(binding: LightBinding, state: dict) -> list[int]:
    """Top-level dispatch: layout-aware renderer if a layout is present,
    otherwise fall back to the flat channel emission."""
    if binding.layout:
        return _compute_layout_values(binding.channels, binding.layout, state)
    return _compute_channel_values(binding.channels, state)


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
        modes: Iterable[LightModelMode] = (),
    ) -> None:
        """Rebuild all in-memory state from database snapshots."""
        models_by_id = {m.id: m for m in models}
        modes_by_id: dict[int, LightModelMode] = {}
        default_mode_by_model: dict[int, LightModelMode] = {}
        for mode in modes:
            modes_by_id[mode.id] = mode
            if mode.is_default:
                default_mode_by_model[mode.model_id] = mode
            else:
                default_mode_by_model.setdefault(mode.model_id, mode)
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
                # Prefer the light's explicit mode, fall back to the model's
                # default, then to the cached channel list on LightModel.
                mode = None
                if light.mode_id is not None:
                    mode = modes_by_id.get(light.mode_id)
                if mode is None:
                    mode = default_mode_by_model.get(light.model_id)
                if mode is not None:
                    channels = list(mode.channels)
                    channel_count = mode.channel_count
                    layout = mode.layout if isinstance(mode.layout, dict) else None
                else:
                    channels = list(model.channels)
                    channel_count = model.channel_count
                    layout = None
                ctrl, buf = self._controllers[light.controller_id]
                start = light.start_address - 1  # DMX is 1-indexed; buffer is 0-indexed
                if start < 0 or start + channel_count > UNIVERSE_SIZE:
                    log.warning(
                        "Light %s does not fit in universe (start=%s, count=%s)",
                        light.id,
                        light.start_address,
                        channel_count,
                    )
                    continue
                binding = LightBinding(
                    light_id=light.id,
                    start_index=start,
                    channels=channels,
                    layout=layout,
                )
                buf.bindings[light.id] = binding
                self._light_to_controller[light.id] = ctrl.id

                values = _render_binding(
                    binding,
                    {
                        "r": light.r,
                        "g": light.g,
                        "b": light.b,
                        "w": light.w,
                        "a": light.a,
                        "uv": light.uv,
                        "dimmer": light.dimmer,
                        "on": light.on,
                        "zone_state": getattr(light, "zone_state", {}) or {},
                        "motion_state": getattr(light, "motion_state", {}) or {},
                    },
                )
                for i, v in enumerate(values):
                    buf.data[start + i] = v

            # Push initial state to all controllers.
            for cid, (ctrl, buf) in self._controllers.items():
                if ctrl.enabled:
                    self._send_buffer(ctrl, buf)

    def set_light_state(self, light_id: int, state: dict) -> bool:
        """Update a light's values and push a packet. Returns True on success.

        ``state`` should include the flat color fields and may include
        ``zone_state`` / ``motion_state`` dicts for compound fixtures.
        """
        with self._lock:
            ctrl_id = self._light_to_controller.get(light_id)
            if ctrl_id is None:
                return False
            ctrl, buf = self._controllers[ctrl_id]
            binding = buf.bindings.get(light_id)
            if binding is None:
                return False
            values = _render_binding(binding, state)
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
    from .models import Controller, Light, LightModel, LightModelMode

    def _work() -> None:
        with Session(engine) as sess:
            controllers = list(sess.exec(select(Controller)))
            lights = list(sess.exec(select(Light)))
            models = list(sess.exec(select(LightModel)))
            modes = list(sess.exec(select(LightModelMode)))
        manager.rebuild(controllers, lights, models, modes)

    await asyncio.to_thread(_work)


def rebuild_manager_sync() -> None:
    from sqlmodel import Session, select

    from .db import engine
    from .models import Controller, Light, LightModel, LightModelMode

    with Session(engine) as sess:
        controllers = list(sess.exec(select(Controller)))
        lights = list(sess.exec(select(Light)))
        models = list(sess.exec(select(LightModel)))
        modes = list(sess.exec(select(LightModelMode)))
    manager.rebuild(controllers, lights, models, modes)
