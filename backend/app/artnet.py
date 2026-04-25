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
from typing import Iterable, Optional

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
    # W/A/UV policy ({"w":"direct", ...}). Missing keys default to "mix"
    # which preserves the historical "derive from RGB" renderer behavior.
    color_policy: dict | None = None


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


def _policy_for(policy: dict | None, role: str) -> str:
    """Return the resolved policy ("mix" or "direct") for one W/A/UV role.

    Any value other than "direct" resolves to "mix" so that missing or
    unknown entries behave like today's default."""
    if not policy:
        return "mix"
    v = policy.get(role)
    if v == "direct":
        return "direct"
    return "mix"


def _resolve_aux(
    role: str,
    raw: object,
    r: int,
    g: int,
    b: int,
    policy: dict | None,
) -> int:
    """Resolve one of W / A / UV, honoring the per-role policy.

    For "direct" roles we never auto-derive from RGB: a ``None`` state
    value becomes 0 so the channel acts like an independent fader. For
    "mix" roles we preserve the historical derivations when unspecified.
    """
    if raw is not None:
        return int(raw)
    if _policy_for(policy, role) == "direct":
        return 0
    if role == "w":
        return min(r, g, b)
    if role == "a":
        return min(r, g) // 2
    return 0


def _compute_channel_values(
    channels: list[str], state: dict, policy: dict | None = None
) -> list[int]:
    """Map a light's logical state dict into DMX channel values ordered by role.

    Flat (non-compound) path: iterates ``channels`` and emits one byte per
    slot. For compound fixtures use :func:`_compute_layout_values` instead.
    ``policy`` is an optional {role: "mix"|"direct"} map from the mode;
    see :func:`_resolve_aux`.
    """
    r = int(state.get("r", 0))
    g = int(state.get("g", 0))
    b = int(state.get("b", 0))
    w = _resolve_aux("w", state.get("w"), r, g, b, policy)
    a = _resolve_aux("a", state.get("a"), r, g, b, policy)
    uv = _resolve_aux("uv", state.get("uv"), r, g, b, policy)
    # Extra aux channels are always direct faders (never derived from RGB,
    # never touched by palette paint). Missing state entries default to 0.
    w2 = int(state.get("w2", 0) or 0)
    w3 = int(state.get("w3", 0) or 0)
    a2 = int(state.get("a2", 0) or 0)
    uv2 = int(state.get("uv2", 0) or 0)
    dimmer = int(state.get("dimmer", 255))
    on = bool(state.get("on", True))
    if not on:
        return [0] * len(channels)

    # If the model has no dedicated dimmer channel, bake brightness into RGB.
    has_dimmer = "dimmer" in channels
    scale = 1.0 if has_dimmer else max(0, min(255, dimmer)) / 255.0

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
        elif role == "w2":
            values.append(int(round(w2 * scale)))
        elif role == "w3":
            values.append(int(round(w3 * scale)))
        elif role == "a2":
            values.append(int(round(a2 * scale)))
        elif role == "uv2":
            values.append(int(round(uv2 * scale)))
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
    zs: dict, global_dimmer: int, policy: dict | None = None
) -> tuple[int, int, int, int, int, int, dict, int, bool]:
    """Return (r, g, b, w, a, uv, extras, dimmer, on) with fallback derivation.

    ``extras`` is a dict of the optional aux roles (``w2``, ``w3``,
    ``a2``, ``uv2``) pulled from the zone state. Keys present in the
    zone dict are copied verbatim (as ints); missing keys are absent
    from the returned dict so callers can cheaply test membership."""
    r = int(zs.get("r", 0))
    g = int(zs.get("g", 0))
    b = int(zs.get("b", 0))
    w = _resolve_aux("w", zs.get("w"), r, g, b, policy)
    a = _resolve_aux("a", zs.get("a"), r, g, b, policy)
    uv = _resolve_aux("uv", zs.get("uv"), r, g, b, policy)
    extras: dict[str, int] = {}
    for role in ("w2", "w3", "a2", "uv2"):
        raw = zs.get(role)
        if raw is None:
            continue
        extras[role] = max(0, min(255, int(raw)))
    dim = int(zs.get("dimmer", global_dimmer))
    on = bool(zs.get("on", True))
    return r, g, b, w, a, uv, extras, dim, on


def _compute_layout_values(
    channels: list[str], layout: dict, state: dict, policy: dict | None = None
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
    flat_extras = state.get("extra_colors") or {}
    fallback = {
        "r": state.get("r", 0),
        "g": state.get("g", 0),
        "b": state.get("b", 0),
        "w": state.get("w"),
        "a": state.get("a"),
        "uv": state.get("uv"),
        "w2": flat_extras.get("w2"),
        "w3": flat_extras.get("w3"),
        "a2": flat_extras.get("a2"),
        "uv2": flat_extras.get("uv2"),
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

        zr, zg, zb, zw, za, zuv, zextras, zdim, zon = _derive_zone_defaults(
            zs, global_dimmer, policy
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
            # Aux extras default to 0 when not set on the zone.
            "w2": zextras.get("w2", 0),
            "w3": zextras.get("w3", 0),
            "a2": zextras.get("a2", 0),
            "uv2": zextras.get("uv2", 0),
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
        return _compute_layout_values(
            binding.channels, binding.layout, state, binding.color_policy
        )
    return _compute_channel_values(
        binding.channels, state, binding.color_policy
    )


def _decode_binding(binding: "LightBinding", buf: bytearray) -> dict:
    """Reverse of :func:`_render_binding`: peek at the raw DMX bytes and
    extract a light's current RGB output.

    The decode is best-effort. We report the byte values exactly as they
    sit on the wire, including any brightness-baked-in RGB (for fixtures
    without a dedicated dimmer channel). ``on`` is true when *any* channel
    has a non-zero value — that matches how the UI interprets colour
    swatches (an all-zero fixture shows the hatched "off" pattern)."""
    start = binding.start_index
    channels = binding.channels

    def _get(off: Optional[int]) -> Optional[int]:
        if off is None or not isinstance(off, int):
            return None
        if off < 0 or off >= len(channels):
            return None
        idx = start + off
        if idx < 0 or idx >= len(buf):
            return None
        return int(buf[idx])

    zone_state: dict[str, dict] = {}
    flat_r = 0
    flat_g = 0
    flat_b = 0
    any_nonzero = False

    layout = binding.layout
    if layout:
        zones = layout.get("zones") or []
        zone_bris: list[int] = []
        for zone in zones:
            zid = zone.get("id")
            if not isinstance(zid, str):
                continue
            colors = zone.get("colors") or {}
            r = _get(colors.get("r")) or 0
            g = _get(colors.get("g")) or 0
            b = _get(colors.get("b")) or 0
            dim = _get(zone.get("dimmer"))
            # Bake a per-zone dimmer into RGB so the on-screen swatch
            # reflects what the fixture actually emits.
            if dim is not None:
                scale = dim / 255.0
                r = int(round(r * scale))
                g = int(round(g * scale))
                b = int(round(b * scale))
            on = (r | g | b) > 0
            if on:
                any_nonzero = True
            zone_state[zid] = {"r": r, "g": g, "b": b, "on": on}
            zone_bris.append(max(r, g, b))
        if zone_state:
            # Surface the brightest zone as the flat fallback so the
            # whole-card swatch also animates.
            bright = max(zone_state.values(), key=lambda s: max(s["r"], s["g"], s["b"]))
            flat_r, flat_g, flat_b = bright["r"], bright["g"], bright["b"]
        # Apply the global dimmer to the flat fallback if present.
        globals_ = layout.get("globals") or {}
        gdim = _get(globals_.get("dimmer"))
        if gdim is not None:
            scale = gdim / 255.0
            flat_r = int(round(flat_r * scale))
            flat_g = int(round(flat_g * scale))
            flat_b = int(round(flat_b * scale))
            for zs in zone_state.values():
                zs["r"] = int(round(zs["r"] * scale))
                zs["g"] = int(round(zs["g"] * scale))
                zs["b"] = int(round(zs["b"] * scale))
                zs["on"] = (zs["r"] | zs["g"] | zs["b"]) > 0
    else:
        # Flat fixture: walk channels and pick up the first r/g/b. Apply
        # any dimmer we see.
        dim = 255
        for i, role in enumerate(channels):
            v = int(buf[start + i]) if 0 <= start + i < len(buf) else 0
            if role == "r" and flat_r == 0:
                flat_r = v
            elif role == "g" and flat_g == 0:
                flat_g = v
            elif role == "b" and flat_b == 0:
                flat_b = v
            elif role == "dimmer":
                dim = v
            if v != 0:
                any_nonzero = True
        if dim != 255 and "dimmer" in channels:
            # The "dimmer" channel already scales the fixture at the
            # hardware level; reflect that brightness in the swatch too.
            scale = dim / 255.0
            flat_r = int(round(flat_r * scale))
            flat_g = int(round(flat_g * scale))
            flat_b = int(round(flat_b * scale))

    return {
        "r": flat_r,
        "g": flat_g,
        "b": flat_b,
        "on": any_nonzero,
        "zone_state": zone_state,
    }


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
        # Controllers with pending writes awaiting flush_dirty().
        self._dirty: set[int] = set()

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
            self._dirty.clear()
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
                    policy = (
                        dict(mode.color_policy)
                        if isinstance(mode.color_policy, dict)
                        else {}
                    )
                else:
                    channels = list(model.channels)
                    channel_count = model.channel_count
                    layout = None
                    policy = {}
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
                    color_policy=policy,
                )
                buf.bindings[light.id] = binding
                self._light_to_controller[light.id] = ctrl.id

                extras = getattr(light, "extra_colors", {}) or {}
                values = _render_binding(
                    binding,
                    {
                        "r": light.r,
                        "g": light.g,
                        "b": light.b,
                        "w": light.w,
                        "a": light.a,
                        "uv": light.uv,
                        "w2": extras.get("w2"),
                        "w3": extras.get("w3"),
                        "a2": extras.get("a2"),
                        "uv2": extras.get("uv2"),
                        "extra_colors": extras,
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
            changed = False
            for i, v in enumerate(values):
                if buf.data[binding.start_index + i] != v:
                    buf.data[binding.start_index + i] = v
                    changed = True
            if changed and ctrl.enabled:
                self._send_buffer(ctrl, buf)
            self._dirty.discard(ctrl_id)
            return True

    def set_light_state_deferred(self, light_id: int, state: dict) -> bool:
        """Like :meth:`set_light_state` but does not send. The affected
        controller is marked dirty and will be flushed by
        :meth:`flush_dirty`. Used by the effect engine to coalesce many
        per-frame writes into one UDP packet per controller per tick."""
        with self._lock:
            ctrl_id = self._light_to_controller.get(light_id)
            if ctrl_id is None:
                return False
            ctrl, buf = self._controllers[ctrl_id]
            binding = buf.bindings.get(light_id)
            if binding is None:
                return False
            values = _render_binding(binding, state)
            changed = False
            for i, v in enumerate(values):
                if buf.data[binding.start_index + i] != v:
                    buf.data[binding.start_index + i] = v
                    changed = True
            if changed:
                self._dirty.add(ctrl_id)
            return True

    def flush_dirty(self) -> int:
        """Send one packet per controller whose buffer changed since the
        last flush. Returns the number of packets sent."""
        with self._lock:
            sent = 0
            for ctrl_id in list(self._dirty):
                entry = self._controllers.get(ctrl_id)
                if entry is None:
                    self._dirty.discard(ctrl_id)
                    continue
                ctrl, buf = entry
                if ctrl.enabled:
                    self._send_buffer(ctrl, buf)
                    sent += 1
            self._dirty.clear()
            return sent

    def mark_dirty(self, controller_id: int) -> None:
        """Force a flush-send of the given controller on next flush_dirty()."""
        with self._lock:
            if controller_id in self._controllers:
                self._dirty.add(controller_id)

    def controller_id_for_light(self, light_id: int) -> Optional[int]:
        with self._lock:
            return self._light_to_controller.get(light_id)

    def snapshot_rendered(self) -> dict[int, dict]:
        """Decode the current universe buffers back into per-light RGB state.

        Returns ``{light_id: {r,g,b, on, zone_state: {zone_id: {r,g,b, on}}}}``.
        Used by the Dashboard to render a realtime preview of what the rig
        is outputting right now (so animations visibly animate the
        on-screen cards, not just the physical fixtures)."""
        out: dict[int, dict] = {}
        with self._lock:
            for ctrl_id, (_ctrl, buf) in self._controllers.items():
                for lid, binding in buf.bindings.items():
                    out[lid] = _decode_binding(binding, buf.data)
        return out

    def blackout(self, controller_id: int) -> bool:
        """Zero out every channel on a controller and send."""
        with self._lock:
            entry = self._controllers.get(controller_id)
            if entry is None:
                return False
            ctrl, buf = entry
            for i in range(UNIVERSE_SIZE):
                buf.data[i] = 0
            self._dirty.discard(controller_id)
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
