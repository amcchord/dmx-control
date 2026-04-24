/** Tiny TS port of the backend effect math used only for the preview.
 *
 * The authoritative math lives in ``backend/app/effects.py``. This
 * module reproduces just enough of it to render the simulated preview
 * grid on the Effects page without a DMX round-trip. It intentionally
 * only handles the flat (no-zones) path: every preview cell is one
 * slot in a single "across_lights" group. */

import type {
  EffectDirection,
  EffectParams,
  EffectTargetChannel,
  EffectType,
  PaletteEntry,
} from "../api";

const TAU = Math.PI * 2;

const fract = (v: number) => v - Math.floor(v);
const clamp = (v: number, lo: number, hi: number) =>
  v < lo ? lo : v > hi ? hi : v;

function hexToRgb(hex: string): [number, number, number] {
  const s = hex.startsWith("#") ? hex.slice(1) : hex;
  const r = parseInt(s.slice(0, 2), 16) || 0;
  const g = parseInt(s.slice(2, 4), 16) || 0;
  const b = parseInt(s.slice(4, 6), 16) || 0;
  return [r, g, b];
}

function entryToRgb(entry: PaletteEntry): [number, number, number] {
  return [entry.r, entry.g, entry.b];
}

function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  h = fract(h);
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  let r: number;
  let g: number;
  let b: number;
  switch (((i % 6) + 6) % 6) {
    case 0:
      [r, g, b] = [v, t, p];
      break;
    case 1:
      [r, g, b] = [q, v, p];
      break;
    case 2:
      [r, g, b] = [p, v, t];
      break;
    case 3:
      [r, g, b] = [p, q, v];
      break;
    case 4:
      [r, g, b] = [t, p, v];
      break;
    default:
      [r, g, b] = [v, p, q];
  }
  return [
    Math.round(r * 255),
    Math.round(g * 255),
    Math.round(b * 255),
  ];
}

function sampleSmooth(
  stops: PaletteEntry[],
  phase: number,
): [number, number, number] {
  if (!stops.length) return [0, 0, 0];
  if (stops.length === 1) return entryToRgb(stops[0]);
  phase = fract(phase);
  const n = stops.length;
  const pos = phase * n;
  const lo = Math.floor(pos) % n;
  const hi = (lo + 1) % n;
  const frac = pos - Math.floor(pos);
  const [r1, g1, b1] = entryToRgb(stops[lo]);
  const [r2, g2, b2] = entryToRgb(stops[hi]);
  return [
    Math.round(r1 + (r2 - r1) * frac),
    Math.round(g1 + (g2 - g1) * frac),
    Math.round(b1 + (b2 - b1) * frac),
  ];
}

function sampleStep(
  stops: PaletteEntry[],
  phase: number,
): [number, number, number] {
  if (!stops.length) return [0, 0, 0];
  phase = fract(phase);
  const idx = Math.floor(phase * stops.length) % stops.length;
  return entryToRgb(stops[idx]);
}

function applyDirection(
  phase: number,
  direction: EffectDirection,
  cyclesDone: number,
): number {
  phase = fract(phase);
  if (direction === "reverse") return fract(1 - phase);
  if (direction === "pingpong") {
    if (Math.floor(cyclesDone) % 2 === 1) return fract(1 - phase);
  }
  return phase;
}

function envelopeChase(
  phase: number,
  size: number,
  softness: number,
): number {
  const d = Math.min(phase, 1 - phase);
  const width = Math.max(0.001, 0.5 * size);
  if (d >= width) return 0;
  const t = 1 - d / width;
  if (softness <= 0) return t > 0 ? 1 : 0;
  if (t >= 1 - softness) return 1;
  return t / Math.max(0.001, 1 - softness);
}

const envelopePulse = (phase: number) =>
  0.5 + 0.5 * Math.cos(TAU * phase);

function envelopeStrobe(phase: number, duty: number): number {
  const d = clamp(duty, 0.02, 0.98);
  return phase < d ? 1 : 0;
}

const envelopeWave = (phase: number) => 0.5 + 0.5 * Math.sin(TAU * phase);

export type PreviewCell = {
  /** CSS color for the base RGB layer. */
  rgb: string;
  /** Scalar brightness 0-1 applied on top of rgb when target_channels!=rgb.
   *  When rgb is targeted, brightness bakes into rgb already and this
   *  value is always 1. */
  brightness: number;
  /** Which aux channel the scalar is driving (when any). Used to tint
   *  the overlay indicator so the user can see whether it's white/amber/
   *  UV/strobe. */
  auxTint: string | null;
};

function auxTintFor(channels: EffectTargetChannel[]): string | null {
  if (channels.includes("rgb")) return null;
  if (channels.includes("w")) return "#FFFFFF";
  if (channels.includes("a")) return "#FF9F3A";
  if (channels.includes("uv")) return "#7C4DFF";
  if (channels.includes("strobe")) return "#FFFFFF";
  if (channels.includes("dimmer")) return "#FFE27A";
  return null;
}

export function computePreview(
  type: EffectType,
  entries: PaletteEntry[],
  params: EffectParams,
  targetChannels: EffectTargetChannel[],
  cellCount: number,
  t: number,
): PreviewCell[] {
  const speed = params.speed_hz;
  const direction = params.direction;
  const offset = params.offset;
  const intensity = clamp(params.intensity, 0, 1);
  const size = params.size;
  const softness = params.softness;

  const perIndex = offset <= 1 ? offset / Math.max(1, cellCount) : offset;
  const cyclesDone = t * speed;

  const touchesRgb = targetChannels.includes("rgb");
  const auxTint = auxTintFor(targetChannels);

  const cells: PreviewCell[] = [];
  for (let i = 0; i < cellCount; i++) {
    const rawPhase = cyclesDone + i * perIndex;
    const phase = applyDirection(rawPhase, direction, cyclesDone);

    let rgb: [number, number, number] = [0, 0, 0];
    let bri = 1;
    let active = true;

    switch (type) {
      case "static": {
        const pick = cellCount <= 1 ? 0 : i / cellCount;
        rgb = sampleSmooth(entries, pick);
        break;
      }
      case "fade": {
        rgb = sampleSmooth(entries, phase);
        break;
      }
      case "cycle": {
        rgb = sampleStep(entries, phase);
        break;
      }
      case "chase": {
        bri = envelopeChase(
          phase,
          Math.max(0.05, (size / Math.max(1, cellCount)) * 2),
          softness,
        );
        active = bri > 0;
        rgb = active ? sampleSmooth(entries, cyclesDone * 0.5) : [0, 0, 0];
        break;
      }
      case "pulse": {
        bri = envelopePulse(phase);
        rgb = sampleSmooth(
          entries,
          cyclesDone * 0.25 + i / Math.max(1, cellCount),
        );
        break;
      }
      case "rainbow": {
        rgb = hsvToRgb(phase, 1, 1);
        break;
      }
      case "strobe": {
        const duty = clamp(size, 0.02, 0.98);
        bri = envelopeStrobe(phase, duty);
        active = bri > 0;
        rgb = active ? sampleSmooth(entries, cyclesDone * 0.1) : [0, 0, 0];
        break;
      }
      case "sparkle": {
        // Deterministic pseudo-random per cell/bucket.
        const rate = Math.max(0.5, speed * 4);
        const bucket = Math.floor(t * rate);
        const seed = ((i * 2654435761) ^ (bucket * 0x9e3779b9)) >>> 0;
        const on = (seed & 0xff) < 96;
        if (on) {
          const decay = Math.max(0, 1 - fract(t * rate));
          bri = decay;
          const idx = seed % Math.max(1, entries.length);
          rgb = entries.length ? entryToRgb(entries[idx]) : [255, 255, 255];
        } else {
          bri = 0;
          active = false;
          rgb = [0, 0, 0];
        }
        break;
      }
      case "wave": {
        bri = envelopeWave(phase);
        rgb = sampleSmooth(entries, cyclesDone * 0.25);
        break;
      }
      default:
        rgb = [0, 0, 0];
        bri = 0;
        break;
    }

    const eff = (active ? Math.max(0, Math.min(1, bri)) : 0) * intensity;

    let display: [number, number, number];
    let displayBri = 1;
    if (touchesRgb) {
      display = [
        Math.round(rgb[0] * eff),
        Math.round(rgb[1] * eff),
        Math.round(rgb[2] * eff),
      ];
    } else {
      // Aux-only: base color stays off for preview purposes, and the
      // aux-channel scalar is surfaced via brightness + tint.
      display = [0, 0, 0];
      displayBri = eff;
    }

    cells.push({
      rgb: `rgb(${display[0]}, ${display[1]}, ${display[2]})`,
      brightness: displayBri,
      auxTint: touchesRgb ? null : auxTint,
    });
  }
  return cells;
}
