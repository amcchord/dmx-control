import type {
  ColorRole,
  FixtureLayout,
  FixtureZone,
  LayoutShape,
  Light,
  LightModelMode,
  ZoneColorState,
} from "./api";

export const COLOR_ROLES: ColorRole[] = [
  "r",
  "g",
  "b",
  "w",
  "w2",
  "w3",
  "a",
  "a2",
  "uv",
  "uv2",
  "color",
];

export const SHAPES: LayoutShape[] = [
  "single",
  "linear",
  "grid",
  "ring",
  "cluster",
];

export const MOTION_AXES = ["pan", "tilt", "zoom", "focus"] as const;
export type MotionAxis = (typeof MOTION_AXES)[number];

export function makeZoneId(prefix: string, taken: Set<string>): string {
  let i = taken.size;
  while (true) {
    const id = `${prefix}${i}`;
    if (!taken.has(id)) {
      taken.add(id);
      return id;
    }
    i += 1;
  }
}

/**
 * Greedy detector: walk the channel list and carve out a zone whenever we
 * see a fresh r/g/b[+ w/a/uv][+ dimmer] run. Global slots (pan, tilt, zoom,
 * focus, strobe, macro, speed, dimmer before any zone, "other") are left
 * unassigned and reported separately.
 */
export function detectZones(channels: string[]): FixtureLayout {
  const zones: FixtureZone[] = [];
  const globals: FixtureLayout["globals"] = {};
  const motion: FixtureLayout["motion"] = {};
  const takenIds = new Set<string>();

  // When a mode has multiple bare ``color`` channels (one per cell, e.g.
  // Blizzard StormChaser 20CH), each becomes its own zone. A single
  // standalone ``color`` is stashed as ``globals.color`` so the wheel
  // drives the whole fixture from the flat RGB state.
  const colorSlotCount = channels.filter((c) => c === "color").length;
  const colorSlotsAsZones = colorSlotCount >= 2;

  let i = 0;
  let hasAnyRGB = false;
  while (i < channels.length) {
    const role = channels[i];
    // Indexed-color slot — emit a zone (multi-cell case) or fall through
    // to be picked up as globals.color below.
    if (role === "color" && colorSlotsAsZones) {
      const id = makeZoneId("c", takenIds);
      zones.push({
        id,
        label: `Cell ${zones.length + 1}`,
        kind: "pixel",
        row: 0,
        col: zones.length,
        colors: { color: i },
      });
      i += 1;
      continue;
    }
    // Try to start a color zone if we see an r at this position.
    if (role === "r") {
      const colors: Partial<Record<ColorRole, number>> = { r: i };
      let j = i + 1;
      const seen = new Set<string>(["r"]);
      while (j < channels.length) {
        const nr = channels[j];
        if (nr === "g" && !seen.has("g")) {
          colors.g = j;
          seen.add("g");
          j++;
          continue;
        }
        if (nr === "b" && !seen.has("b")) {
          colors.b = j;
          seen.add("b");
          j++;
          continue;
        }
        if (nr === "w" && !seen.has("w")) {
          colors.w = j;
          seen.add("w");
          j++;
          continue;
        }
        if (nr === "a" && !seen.has("a")) {
          colors.a = j;
          seen.add("a");
          j++;
          continue;
        }
        if (nr === "uv" && !seen.has("uv")) {
          colors.uv = j;
          seen.add("uv");
          j++;
          continue;
        }
        break;
      }
      if (seen.has("g") && seen.has("b")) {
        hasAnyRGB = true;
        // If the channel immediately after this color block is a dimmer
        // and no global dimmer has claimed it yet, treat as per-zone dimmer.
        let dimmer: number | undefined;
        if (channels[j] === "dimmer") {
          dimmer = j;
          j += 1;
        }
        const id = makeZoneId("p", takenIds);
        zones.push({
          id,
          label: `Pixel ${zones.length + 1}`,
          kind: "pixel",
          row: 0,
          col: zones.length,
          colors,
          ...(dimmer !== undefined ? { dimmer } : {}),
        });
        i = j;
        continue;
      }
      // Fallback: only saw a lone "r"; skip.
      i += 1;
      continue;
    }
    // Motion / globals / unmapped.
    if (role === "pan" && motion.pan == null) motion.pan = i;
    else if (role === "pan_fine" && motion.pan_fine == null)
      motion.pan_fine = i;
    else if (role === "tilt" && motion.tilt == null) motion.tilt = i;
    else if (role === "tilt_fine" && motion.tilt_fine == null)
      motion.tilt_fine = i;
    else if (role === "zoom" && motion.zoom == null) motion.zoom = i;
    else if (role === "focus" && motion.focus == null) motion.focus = i;
    else if (role === "dimmer" && globals.dimmer == null) globals.dimmer = i;
    else if (role === "strobe" && globals.strobe == null) globals.strobe = i;
    else if (role === "macro" && globals.macro == null) globals.macro = i;
    else if (role === "speed" && globals.speed == null) globals.speed = i;
    else if (role === "color" && globals.color == null) globals.color = i;
    i += 1;
  }

  let shape: LayoutShape = "single";
  if (zones.length > 1) shape = "linear";
  if (!hasAnyRGB && zones.length === 0) shape = "single";

  const layout: FixtureLayout = {
    shape,
    zones,
  };
  if (shape === "linear" && zones.length > 0) {
    layout.cols = zones.length;
    layout.rows = 1;
  }
  if (Object.keys(motion).length > 0) layout.motion = motion;
  if (Object.keys(globals).length > 0) layout.globals = globals;
  return layout;
}

/** Return true when a layout actually describes more than one zone or any
 * motion axis — i.e. when it should render as compound. */
export function isCompoundLayout(
  layout: FixtureLayout | null | undefined,
): boolean {
  if (!layout) return false;
  if ((layout.zones?.length ?? 0) > 1) return true;
  const m = layout.motion;
  if (m) {
    for (const axis of MOTION_AXES) {
      const coarse = m[axis];
      const fine = m[`${axis}_fine` as keyof typeof m];
      if (
        (typeof coarse === "number" && coarse >= 0) ||
        (typeof fine === "number" && fine >= 0)
      )
        return true;
    }
  }
  return false;
}

/** Resolve the mode's layout for a given light. */
export function resolveMode(
  light: Light,
  mode: LightModelMode | undefined | null,
): FixtureLayout | null {
  if (!mode) return null;
  return mode.layout ?? null;
}

/** Return the hex swatch for a zone using the light's per-zone state with
 * fallback to the flat fields. */
export function zoneHex(
  light: Light,
  zoneId: string,
): { hex: string; on: boolean; dimmer: number } {
  const zs: ZoneColorState | undefined = light.zone_state?.[zoneId];
  const r = zs?.r ?? light.r;
  const g = zs?.g ?? light.g;
  const b = zs?.b ?? light.b;
  const on = zs?.on ?? light.on;
  const dimmer = zs?.dimmer ?? light.dimmer;
  const hex = `#${[r, g, b]
    .map((c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, "0"))
    .join("")}`.toUpperCase();
  return { hex, on, dimmer };
}

/** Sort zones by (row, col) then declaration index. */
export function orderedZones(layout: FixtureLayout): FixtureZone[] {
  const rows = layout.zones.map((z, i) => ({ z, i }));
  rows.sort((a, b) => {
    const ra = a.z.row ?? 0;
    const rb = b.z.row ?? 0;
    if (ra !== rb) return ra - rb;
    const ca = a.z.col ?? 0;
    const cb = b.z.col ?? 0;
    if (ca !== cb) return ca - cb;
    return a.i - b.i;
  });
  return rows.map((r) => r.z);
}

/** Determine which channel offsets are claimed by a layout and by what. */
export function channelOwners(
  layout: FixtureLayout | null | undefined,
): Map<number, string> {
  const out = new Map<number, string>();
  if (!layout) return out;
  for (const z of layout.zones) {
    for (const [role, off] of Object.entries(z.colors)) {
      if (typeof off === "number") out.set(off, `${z.id}:${role}`);
    }
    if (typeof z.dimmer === "number") out.set(z.dimmer, `${z.id}:dim`);
    if (typeof z.strobe === "number") out.set(z.strobe, `${z.id}:str`);
  }
  if (layout.motion) {
    for (const axis of MOTION_AXES) {
      const coarse = layout.motion[axis];
      const fine = layout.motion[`${axis}_fine` as keyof typeof layout.motion];
      if (typeof coarse === "number") out.set(coarse, `motion:${axis}`);
      if (typeof fine === "number") out.set(fine, `motion:${axis}_fine`);
    }
  }
  if (layout.globals) {
    for (const key of [
      "dimmer",
      "strobe",
      "macro",
      "speed",
      "color",
    ] as const) {
      const off = layout.globals[key];
      if (typeof off === "number") out.set(off, `global:${key}`);
    }
  }
  return out;
}

/** Does this light's mode expose any motion axes? */
export function hasMotion(
  layout: FixtureLayout | null | undefined,
): boolean {
  if (!layout?.motion) return false;
  for (const axis of MOTION_AXES) {
    if (typeof layout.motion[axis] === "number") return true;
    const fine = layout.motion[`${axis}_fine` as keyof typeof layout.motion];
    if (typeof fine === "number") return true;
  }
  return false;
}
