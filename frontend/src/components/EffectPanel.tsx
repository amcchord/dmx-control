import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Api,
  BulkTarget,
  DEFAULT_EFFECT_PARAMS,
  EffectDirection,
  EffectParams,
  EffectType,
  Palette,
  PaletteSpread,
} from "../api";
import PaletteSwatch from "./PaletteSwatch";

type Selection = {
  light_ids: number[];
  targets: BulkTarget[];
};

type Props = {
  selection: Selection;
  palettes: Palette[];
  onLiveStateChange?: (live: LiveInfo | null) => void;
  /** Called after a live scene is promoted to a saved Scene, so the parent
   * can refresh its scenes list. */
  onSaved?: () => void;
  /** Called whenever the panel starts or stops a live scene so the parent
   * can refresh the active-scenes indicator. */
  onActiveChanged?: () => void;
  notify?: (msg: string, kind?: "success" | "error" | "info") => void;
};

export type LiveInfo = {
  handle: string;
  name: string;
};

type EffectMeta = {
  key: EffectType;
  label: string;
  icon: string;
  needsPalette: boolean;
  needsSize: boolean;
  needsSoftness: boolean;
  description: string;
};

const EFFECTS: EffectMeta[] = [
  {
    key: "static",
    label: "Static",
    icon: "\u25A0",
    needsPalette: true,
    needsSize: false,
    needsSoftness: false,
    description: "Distribute palette once; no motion.",
  },
  {
    key: "fade",
    label: "Fade",
    icon: "\u25C9",
    needsPalette: true,
    needsSize: false,
    needsSoftness: false,
    description: "Smooth palette crossfade per target.",
  },
  {
    key: "cycle",
    label: "Cycle",
    icon: "\u21BB",
    needsPalette: true,
    needsSize: false,
    needsSoftness: false,
    description: "Step through palette colors.",
  },
  {
    key: "chase",
    label: "Chase",
    icon: "\u27A4",
    needsPalette: true,
    needsSize: true,
    needsSoftness: true,
    description: "Moving window of color across targets.",
  },
  {
    key: "pulse",
    label: "Pulse",
    icon: "\u2665",
    needsPalette: true,
    needsSize: false,
    needsSoftness: false,
    description: "Brightness breathing on palette color.",
  },
  {
    key: "rainbow",
    label: "Rainbow",
    icon: "\u2728",
    needsPalette: false,
    needsSize: false,
    needsSoftness: false,
    description: "Full-hue sweep; ignores palette.",
  },
  {
    key: "strobe",
    label: "Strobe",
    icon: "\u26A1",
    needsPalette: true,
    needsSize: true,
    needsSoftness: false,
    description: "Fast on/off flashes.",
  },
  {
    key: "sparkle",
    label: "Sparkle",
    icon: "\u2734",
    needsPalette: true,
    needsSize: false,
    needsSoftness: false,
    description: "Random per-target flashes.",
  },
  {
    key: "wave",
    label: "Wave",
    icon: "\u223F",
    needsPalette: true,
    needsSize: false,
    needsSoftness: false,
    description: "Smooth sinusoidal brightness wave.",
  },
];

const SPREADS: {
  key: PaletteSpread;
  label: string;
  hint: string;
  icon: string;
}[] = [
  {
    key: "across_lights",
    label: "Across lights",
    hint: "One step per fixture; chase sweeps the rig.",
    icon: "\u25EF\u25EF\u25EF",
  },
  {
    key: "across_fixture",
    label: "Across fixture",
    hint: "Each fixture runs the effect over its own zones.",
    icon: "\u25B0\u25B0\u25B0",
  },
  {
    key: "across_zones",
    label: "Across zones",
    hint: "Flatten every zone into one long strip end-to-end.",
    icon: "\u25A0\u25A0\u25A0\u25A0\u25A0",
  },
];

function findEffect(type: EffectType): EffectMeta {
  return EFFECTS.find((e) => e.key === type) ?? EFFECTS[1];
}

function speedLabel(hz: number): string {
  if (hz <= 0) return "Stopped";
  if (hz >= 1) return `${(hz * 60).toFixed(0)} BPM`;
  return `${(60 * hz).toFixed(1)} BPM`;
}

function offsetLabel(offset: number, targetCount: number): string {
  const per = targetCount > 0 ? 1 / targetCount : 0;
  const delta = Math.abs(offset - per);
  if (offset === 0) return "In sync";
  if (delta < 0.02) return "Perfect chase";
  if (Math.abs(offset - 0.5) < 0.02) return "Alternating";
  return `Phase ${Math.round(offset * 100)}%`;
}

export default function EffectPanel({
  selection,
  palettes,
  onLiveStateChange,
  onSaved,
  onActiveChanged,
  notify,
}: Props) {
  const [effectType, setEffectType] = useState<EffectType>("fade");
  const [paletteId, setPaletteId] = useState<number | null>(
    palettes[0]?.id ?? null,
  );
  const [spread, setSpread] = useState<PaletteSpread>("across_lights");
  const [params, setParams] = useState<EffectParams>({
    ...DEFAULT_EFFECT_PARAMS,
  });
  const [live, setLive] = useState<LiveInfo | null>(null);
  const [expandAdv, setExpandAdv] = useState(false);
  const [savePromptOpen, setSavePromptOpen] = useState(false);
  const [saveName, setSaveName] = useState("");

  // Default-select the first non-builtin palette, falling back to any palette.
  useEffect(() => {
    if (paletteId !== null) return;
    const fav = palettes.find((p) => p.builtin) ?? palettes[0];
    if (fav) setPaletteId(fav.id);
  }, [palettes, paletteId]);

  const meta = findEffect(effectType);

  const targetCount = useMemo(() => {
    if (spread === "across_lights") return selection.light_ids.length;
    // For across_fixture/across_zones the UI can only estimate (since we
    // don't have modes here). Use targets length as a proxy.
    return (
      selection.light_ids.length + selection.targets.length
    );
  }, [selection, spread]);

  const hasSelection =
    selection.light_ids.length + selection.targets.length > 0;

  // Auto-restart live scene on param/selection change, debounced so that
  // drag-scrubbing a slider does not hammer the engine.
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!live) return;
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      void restartLive();
    }, 140);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    effectType,
    paletteId,
    spread,
    params.speed_hz,
    params.direction,
    params.offset,
    params.intensity,
    params.size,
    params.softness,
    params.fade_in_s,
    params.fade_out_s,
    selection,
  ]);

  // Sync external "live state" subscribers whenever it changes.
  useEffect(() => {
    onLiveStateChange?.(live);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  async function play() {
    if (!hasSelection) {
      notify?.("Select one or more lights first", "error");
      return;
    }
    try {
      const res = await Api.playLive({
        effect_type: effectType,
        palette_id: meta.needsPalette ? paletteId : null,
        light_ids: selection.light_ids,
        targets: selection.targets,
        spread,
        params,
      });
      setLive({ handle: res.handle, name: res.name });
      onActiveChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    }
  }

  async function stop() {
    if (!live) return;
    const current = live;
    setLive(null);
    try {
      await Api.stopLive(current.handle);
    } catch (e) {
      notify?.(String(e), "error");
    }
    onActiveChanged?.();
  }

  async function restartLive() {
    if (!live) return;
    const old = live;
    try {
      const res = await Api.playLive({
        effect_type: effectType,
        palette_id: meta.needsPalette ? paletteId : null,
        light_ids: selection.light_ids,
        targets: selection.targets,
        spread,
        params: { ...params, fade_in_s: 0 },
      });
      setLive({ handle: res.handle, name: res.name });
      // Only stop the old one after starting the new so DMX overlaps.
      await Api.stopLive(old.handle);
      onActiveChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    }
  }

  async function savePreset() {
    if (!live) return;
    const name = saveName.trim();
    if (!name) return;
    try {
      await Api.saveLive(live.handle, name);
      notify?.(`Saved "${name}" to scenes`, "success");
      setSavePromptOpen(false);
      setSaveName("");
      onSaved?.();
    } catch (e) {
      notify?.(String(e), "error");
    }
  }

  const playing = live !== null;

  return (
    <div className="card space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Effect</div>
          <div className="text-xs text-muted">
            {meta.description}
          </div>
        </div>
        {playing && (
          <span className="pill bg-emerald-950 text-emerald-300 ring-emerald-900">
            Live
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {EFFECTS.map((e) => (
          <button
            key={e.key}
            onClick={() => setEffectType(e.key)}
            title={e.description}
            className={
              "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs ring-1 transition " +
              (effectType === e.key
                ? "bg-accent text-white ring-accent"
                : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
            }
          >
            <span aria-hidden className="text-sm leading-none">
              {e.icon}
            </span>
            <span>{e.label}</span>
          </button>
        ))}
      </div>

      {meta.needsPalette && (
        <div>
          <div className="label mb-1.5 !text-[11px] normal-case tracking-normal">
            Palette
          </div>
          <div className="max-h-36 overflow-y-auto pr-1">
            <div className="grid grid-cols-1 gap-1.5">
              {palettes.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPaletteId(p.id)}
                  className={
                    "flex items-center gap-2 rounded-md p-1.5 text-left text-xs ring-1 transition " +
                    (paletteId === p.id
                      ? "bg-bg-elev ring-accent"
                      : "bg-bg-elev/50 ring-line hover:bg-bg-elev")
                  }
                >
                  <div className="w-24 flex-shrink-0 truncate">
                    {p.name}
                  </div>
                  <div className="flex-1">
                    <PaletteSwatch colors={p.colors} />
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      <div>
        <div className="label mb-1.5 !text-[11px] normal-case tracking-normal">
          Spread
        </div>
        <div className="grid grid-cols-3 gap-1.5">
          {SPREADS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSpread(s.key)}
              title={s.hint}
              className={
                "rounded-md px-2 py-1.5 text-[11px] ring-1 transition " +
                (spread === s.key
                  ? "bg-accent text-white ring-accent"
                  : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
              }
            >
              <div className="font-medium">{s.label}</div>
              <div
                className="mt-1 font-mono text-[10px] opacity-70"
                aria-hidden
              >
                {s.icon}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <Slider
          label="Speed"
          value={params.speed_hz}
          min={0}
          max={10}
          step={0.01}
          suffix={speedLabel(params.speed_hz)}
          onChange={(v) => setParams((p) => ({ ...p, speed_hz: v }))}
        />
        <Slider
          label="Offset"
          value={params.offset}
          min={0}
          max={1}
          step={0.01}
          suffix={offsetLabel(params.offset, targetCount)}
          onChange={(v) => setParams((p) => ({ ...p, offset: v }))}
        />
        <Slider
          label="Intensity"
          value={params.intensity}
          min={0}
          max={1}
          step={0.01}
          suffix={`${Math.round(params.intensity * 100)}%`}
          onChange={(v) => setParams((p) => ({ ...p, intensity: v }))}
        />
      </div>

      <div>
        <button
          onClick={() => setExpandAdv((x) => !x)}
          className="text-[11px] text-muted underline-offset-2 hover:text-slate-200 hover:underline"
        >
          {expandAdv ? "Hide advanced" : "More\u2026"}
        </button>
        {expandAdv && (
          <div className="mt-3 space-y-3">
            {meta.needsSize && (
              <Slider
                label={effectType === "strobe" ? "Duty" : "Window"}
                value={params.size}
                min={0.05}
                max={effectType === "strobe" ? 0.95 : 8}
                step={0.05}
                suffix={
                  effectType === "strobe"
                    ? `${Math.round(params.size * 100)}%`
                    : params.size.toFixed(2)
                }
                onChange={(v) => setParams((p) => ({ ...p, size: v }))}
              />
            )}
            {meta.needsSoftness && (
              <Slider
                label="Softness"
                value={params.softness}
                min={0}
                max={1}
                step={0.01}
                suffix={`${Math.round(params.softness * 100)}%`}
                onChange={(v) => setParams((p) => ({ ...p, softness: v }))}
              />
            )}
            <div>
              <div className="label mb-1 !text-[11px] normal-case tracking-normal">
                Direction
              </div>
              <div className="flex gap-1">
                {(["forward", "reverse", "pingpong"] as EffectDirection[]).map(
                  (d) => (
                    <button
                      key={d}
                      onClick={() => setParams((p) => ({ ...p, direction: d }))}
                      className={
                        "rounded-md px-2 py-1 text-[11px] ring-1 " +
                        (params.direction === d
                          ? "bg-accent text-white ring-accent"
                          : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                      }
                    >
                      {d}
                    </button>
                  ),
                )}
              </div>
            </div>
            <Slider
              label="Fade in"
              value={params.fade_in_s}
              min={0}
              max={10}
              step={0.1}
              suffix={`${params.fade_in_s.toFixed(1)}s`}
              onChange={(v) => setParams((p) => ({ ...p, fade_in_s: v }))}
            />
            <Slider
              label="Fade out"
              value={params.fade_out_s}
              min={0}
              max={10}
              step={0.1}
              suffix={`${params.fade_out_s.toFixed(1)}s`}
              onChange={(v) => setParams((p) => ({ ...p, fade_out_s: v }))}
            />
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2 pt-1">
        {!playing ? (
          <button
            className="btn-primary flex-1"
            onClick={play}
            disabled={!hasSelection}
          >
            Play effect
          </button>
        ) : (
          <>
            <button className="btn-danger flex-1" onClick={stop}>
              Stop
            </button>
            <button
              className="btn-secondary"
              onClick={() => {
                setSaveName(`${meta.label} ${Date.now() % 10000}`);
                setSavePromptOpen(true);
              }}
            >
              Save as preset
            </button>
          </>
        )}
      </div>

      {savePromptOpen && (
        <div className="mt-2 rounded-md bg-bg-elev p-2 ring-1 ring-line">
          <div className="label mb-1 !text-[11px] normal-case tracking-normal">
            Preset name
          </div>
          <div className="flex gap-2">
            <input
              className="input"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="My scene"
              autoFocus
            />
            <button
              className="btn-primary"
              onClick={savePreset}
              disabled={!saveName.trim()}
            >
              Save
            </button>
            <button
              className="btn-ghost"
              onClick={() => setSavePromptOpen(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between">
        <span className="label !text-[11px] normal-case tracking-normal">
          {label}
        </span>
        <span className="text-[11px] text-muted">{suffix}</span>
      </div>
      <input
        type="range"
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-bg-elev accent-accent"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
    </div>
  );
}
