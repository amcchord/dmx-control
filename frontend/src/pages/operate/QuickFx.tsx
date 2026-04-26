import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Api,
  Effect,
  LayerBlendMode,
  LAYER_BLEND_MODES,
  Palette,
} from "../../api";
import PaletteSwatch from "../../components/PaletteSwatch";
import { useLayerStore } from "../../state/layers";
import { useSelection } from "../../state/selection";
import { useToast } from "../../toast";

/** Curated grid of saved effect presets.
 *
 * Designed for tap-to-launch: short tap pushes the preset onto the top
 * of the layer stack with default blend (normal/100%); long-press opens
 * a small inspector to set blend mode + opacity + mask before launch.
 *
 * The Lua editor lives on the desktop Effects Composer, not here. */
export default function QuickFx() {
  const { selection } = useSelection();
  const { layers } = useLayerStore();
  const { push: notify } = useToast();
  const [effects, setEffects] = useState<Effect[]>([]);
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [filter, setFilter] = useState<"all" | "rgb" | "aux">("all");
  const [search, setSearch] = useState("");
  const [pendingPreset, setPendingPreset] = useState<Effect | null>(null);

  useEffect(() => {
    void Promise.all([
      Api.listEffects().then(setEffects),
      Api.listPalettes().then(setPalettes),
    ]);
  }, []);

  const palettesById = useMemo(
    () => new Map(palettes.map((p) => [p.id, p])),
    [palettes],
  );

  const filtered = useMemo(() => {
    let out = effects;
    if (filter === "rgb")
      out = out.filter((e) => e.target_channels.includes("rgb"));
    if (filter === "aux")
      out = out.filter(
        (e) =>
          !e.target_channels.includes("rgb") ||
          e.target_channels.length > 1,
      );
    if (search.trim()) {
      const q = search.toLowerCase();
      out = out.filter(
        (e) =>
          e.name.toLowerCase().includes(q) ||
          (e.description ?? "").toLowerCase().includes(q),
      );
    }
    return out;
  }, [effects, filter, search]);

  const launch = async (
    e: Effect,
    overrides?: {
      blend_mode?: LayerBlendMode;
      opacity?: number;
      mask?: number[];
    },
  ) => {
    try {
      const mask = overrides?.mask ?? [...selection.lightIds];
      await Api.createLayer({
        effect_id: e.id,
        blend_mode: overrides?.blend_mode ?? "normal",
        opacity: overrides?.opacity ?? 1.0,
        mask_light_ids: mask,
      });
      notify(`+ ${e.name}`, "success");
    } catch (err) {
      notify(String(err), "error");
    }
  };

  const activeIds = new Set(
    layers.map((l) => l.effect_id).filter((x): x is number => x != null),
  );

  return (
    <div className="flex flex-col gap-3 pb-24">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          placeholder="Search presets..."
          className="input flex-1 min-w-[10rem]"
        />
        <div className="flex rounded-md ring-1 ring-line">
          {(["all", "rgb", "aux"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={
                "px-2.5 py-1 text-xs uppercase tracking-wider " +
                (filter === f
                  ? "bg-bg-elev text-white"
                  : "text-muted hover:text-white")
              }
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {selection.lightIds.size > 0 && (
        <div className="rounded-md bg-emerald-950/40 px-3 py-2 text-xs text-emerald-200 ring-1 ring-emerald-800">
          Launching with mask: {selection.lightIds.size} selected light
          {selection.lightIds.size === 1 ? "" : "s"}
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
        {filtered.map((e) => (
          <PresetCard
            key={e.id}
            effect={e}
            palette={
              e.palette_id != null ? palettesById.get(e.palette_id) : undefined
            }
            running={activeIds.has(e.id)}
            onLaunch={() => launch(e)}
            onConfigure={() => setPendingPreset(e)}
          />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="rounded-lg border border-dashed border-line p-8 text-center text-sm text-muted">
          No presets match. Build one in the{" "}
          <Link to="/author/effects" className="text-accent hover:underline">
            Effects Composer
          </Link>
          .
        </div>
      )}

      {pendingPreset && (
        <PresetSheet
          effect={pendingPreset}
          onClose={() => setPendingPreset(null)}
          onLaunch={(overrides) => {
            void launch(pendingPreset, overrides);
            setPendingPreset(null);
          }}
        />
      )}
    </div>
  );
}

function PresetCard({
  effect,
  palette,
  running,
  onLaunch,
  onConfigure,
}: {
  effect: Effect;
  palette?: Palette;
  running: boolean;
  onLaunch: () => void;
  onConfigure: () => void;
}) {
  return (
    <button
      onClick={onLaunch}
      onContextMenu={(e) => {
        e.preventDefault();
        onConfigure();
      }}
      className={
        "card flex flex-col gap-1.5 p-3 text-left transition hover:bg-bg-elev " +
        (running ? "ring-2 ring-emerald-600" : "")
      }
      title="Tap to launch • Right-click / long-press to configure"
    >
      <div className="flex items-center justify-between">
        <span className="truncate text-sm font-semibold">{effect.name}</span>
        {running && (
          <span className="rounded-full bg-emerald-900/60 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-emerald-200">
            live
          </span>
        )}
      </div>
      {effect.description && (
        <div className="line-clamp-2 text-[11px] text-muted">
          {effect.description}
        </div>
      )}
      {palette && <PaletteSwatch colors={palette.colors.slice(0, 8)} />}
      <div className="flex flex-wrap gap-1 pt-1">
        {effect.target_channels.map((tc) => (
          <span
            key={tc}
            className="rounded bg-bg-card px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-muted ring-1 ring-line"
          >
            {tc}
          </span>
        ))}
      </div>
    </button>
  );
}

function PresetSheet({
  effect,
  onClose,
  onLaunch,
}: {
  effect: Effect;
  onClose: () => void;
  onLaunch: (overrides: {
    blend_mode: LayerBlendMode;
    opacity: number;
  }) => void;
}) {
  const [blend, setBlend] = useState<LayerBlendMode>("normal");
  const [opacity, setOpacity] = useState(1.0);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
      <div className="w-full max-w-md rounded-t-2xl bg-bg-card p-4 ring-1 ring-line sm:rounded-2xl">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-semibold">{effect.name}</div>
          <button
            onClick={onClose}
            className="btn-ghost px-2 py-1 text-xs"
            aria-label="Close"
          >
            {"\u00D7"}
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="label">Blend mode</label>
            <select
              value={blend}
              onChange={(e) =>
                setBlend(e.currentTarget.value as LayerBlendMode)
              }
              className="input"
            >
              {LAYER_BLEND_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">
              Opacity {(opacity * 100).toFixed(0)}%
            </label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={opacity}
              onChange={(e) => setOpacity(parseFloat(e.currentTarget.value))}
              className="w-full accent-accent"
            />
          </div>
          <button
            onClick={() => onLaunch({ blend_mode: blend, opacity })}
            className="btn-primary w-full"
          >
            Launch as new layer
          </button>
        </div>
      </div>
    </div>
  );
}
