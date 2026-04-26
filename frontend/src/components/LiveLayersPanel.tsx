import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  EffectLayer,
  LAYER_BLEND_MODES,
  LayerBlendMode,
} from "../api";
import { useLayerStore } from "../state/layers";
import { useToast } from "../toast";

type Props = {
  /** Renders inline (mobile sticky pill) or expanded (desktop side rail). */
  variant?: "compact" | "full";
  /** Hide the per-layer blend-mode dropdown on small screens to keep
   *  the row tappable. */
  showBlend?: boolean;
};

/** Unified "what's on stage" panel.
 *
 * Reads from :func:`useLayerStore`, so both the mobile Now Playing pill
 * and the desktop Live rail show identical state without any extra
 * prop wiring. Each layer row offers mute/solo, opacity, blend mode,
 * and remove; auto-muted layers (engine bailed after repeated Lua
 * errors) flag a banner inline so the operator notices. */
export default function LiveLayersPanel({
  variant = "full",
  showBlend = true,
}: Props) {
  const { layers, health, connected, patchLayer, removeLayer, clearAll } =
    useLayerStore();
  const { push: notify } = useToast();
  const [busy, setBusy] = useState<number | null>(null);

  const visible = layers.filter((l) => l.layer_id != null);
  const anySolo = visible.some((l) => l.solo);

  const onPatch = async (layerId: number, patch: Record<string, unknown>) => {
    try {
      await patchLayer(layerId, patch);
    } catch (e) {
      notify(String(e), "error");
    }
  };

  const onRemove = async (layerId: number) => {
    setBusy(layerId);
    try {
      await removeLayer(layerId);
    } catch (e) {
      notify(String(e), "error");
    } finally {
      setBusy(null);
    }
  };

  const onClearAll = async () => {
    try {
      await clearAll();
      notify("Cleared all layers", "success");
    } catch (e) {
      notify(String(e), "error");
    }
  };

  if (variant === "compact") {
    return (
      <div className="flex items-center gap-2 overflow-x-auto py-1">
        {visible.length === 0 ? (
          <span className="text-xs text-muted">No layers running.</span>
        ) : (
          visible.map((l) => (
            <CompactPill
              key={l.handle}
              layer={l}
              onPatch={onPatch}
              onRemove={onRemove}
              busy={busy}
            />
          ))
        )}
        {visible.length > 0 && (
          <button
            className="btn-danger ml-auto whitespace-nowrap text-xs"
            onClick={onClearAll}
          >
            Clear
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="card flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">
            Live layers
          </div>
          <div className="text-sm font-semibold">
            {visible.length === 0
              ? "Nothing running"
              : `${visible.length} layer${visible.length === 1 ? "" : "s"}`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={
              "inline-block h-2 w-2 rounded-full " +
              (connected ? "bg-emerald-400" : "bg-amber-400")
            }
            title={connected ? "Connected" : "Reconnecting..."}
          />
          {visible.length > 0 && (
            <button
              className="btn-danger text-xs"
              onClick={onClearAll}
            >
              Clear all
            </button>
          )}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {visible.length === 0 ? (
          <div className="rounded-md border border-dashed border-line p-6 text-center text-xs text-muted">
            No layers running.
            <br />
            Pick a preset on{" "}
            <Link to="/quick-fx" className="text-accent hover:underline">
              Quick FX
            </Link>{" "}
            or build one in the{" "}
            <Link
              to="/author/effects"
              className="text-accent hover:underline"
            >
              Effects Composer
            </Link>
            .
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {visible
              .slice()
              .reverse() // top of stack first in the list
              .map((l) => (
                <li key={l.handle}>
                  <FullRow
                    layer={l}
                    soloActive={anySolo}
                    showBlend={showBlend}
                    busy={busy === l.layer_id}
                    onPatch={onPatch}
                    onRemove={onRemove}
                  />
                </li>
              ))}
          </ul>
        )}
      </div>
      {health && (
        <div className="grid grid-cols-3 gap-2 border-t border-line px-3 py-2 text-[10px] text-muted">
          <span>tick {health.tick_hz.toFixed(0)} Hz</span>
          <span>{health.last_tick_ms.toFixed(1)} ms</span>
          <span>dropped {health.dropped_frames}</span>
        </div>
      )}
    </div>
  );
}

function CompactPill({
  layer,
  onPatch,
  onRemove,
  busy,
}: {
  layer: EffectLayer;
  onPatch: (id: number, patch: Record<string, unknown>) => void;
  onRemove: (id: number) => void;
  busy: number | null;
}) {
  const id = layer.layer_id!;
  const muted = layer.mute || layer.auto_muted;
  const danger = !!layer.error;
  return (
    <div
      className={
        "flex shrink-0 items-center gap-1.5 rounded-full pl-2.5 pr-1 py-1 ring-1 " +
        (danger
          ? "bg-rose-950/40 ring-rose-800"
          : muted
            ? "bg-bg-card ring-line"
            : "bg-emerald-950/30 ring-emerald-800")
      }
      title={layer.error ?? ""}
    >
      <span className="text-xs font-medium text-slate-100">
        {layer.name}
      </span>
      <span className="text-[10px] text-muted">
        z{layer.z_index}
      </span>
      <button
        onClick={() => onPatch(id, { mute: !layer.mute })}
        className={
          "rounded-full px-1.5 text-[10px] " +
          (layer.mute ? "bg-amber-700/40 text-amber-200" : "text-muted")
        }
        title="Mute"
      >
        M
      </button>
      <button
        onClick={() => onRemove(id)}
        disabled={busy === id}
        className="ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full bg-rose-900/40 text-rose-200 ring-1 ring-rose-800 hover:bg-rose-700 hover:text-white disabled:opacity-50"
        aria-label="Remove layer"
      >
        {"\u00D7"}
      </button>
    </div>
  );
}

function FullRow({
  layer,
  soloActive,
  showBlend,
  busy,
  onPatch,
  onRemove,
}: {
  layer: EffectLayer;
  soloActive: boolean;
  showBlend: boolean;
  busy: boolean;
  onPatch: (id: number, patch: Record<string, unknown>) => void;
  onRemove: (id: number) => void;
}) {
  const id = layer.layer_id!;
  const stale = soloActive && !layer.solo;
  const danger = !!layer.error;
  return (
    <div
      className={
        "rounded-md p-2 ring-1 " +
        (danger
          ? "bg-rose-950/30 ring-rose-800"
          : layer.auto_muted
            ? "bg-amber-950/30 ring-amber-800"
            : stale
              ? "bg-bg-elev ring-line opacity-70"
              : "bg-bg-elev ring-line")
      }
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-muted">
          z{layer.z_index}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {layer.name}
        </span>
        {layer.auto_muted && (
          <span className="rounded bg-amber-900/60 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-amber-200">
            auto-muted
          </span>
        )}
        <button
          onClick={() => onPatch(id, { mute: !layer.mute })}
          className={
            "rounded px-2 py-0.5 text-[10px] uppercase tracking-wider ring-1 " +
            (layer.mute
              ? "bg-amber-700/40 text-amber-200 ring-amber-700"
              : "text-muted ring-line hover:text-white")
          }
          aria-pressed={layer.mute}
        >
          mute
        </button>
        <button
          onClick={() => onPatch(id, { solo: !layer.solo })}
          className={
            "rounded px-2 py-0.5 text-[10px] uppercase tracking-wider ring-1 " +
            (layer.solo
              ? "bg-accent/80 text-white ring-accent"
              : "text-muted ring-line hover:text-white")
          }
          aria-pressed={layer.solo}
        >
          solo
        </button>
        <button
          onClick={() => onRemove(id)}
          disabled={busy}
          className="ml-1 inline-flex h-6 w-6 items-center justify-center rounded-full bg-rose-900/40 text-rose-200 ring-1 ring-rose-800 hover:bg-rose-700 hover:text-white disabled:opacity-50"
          aria-label="Remove layer"
        >
          {"\u00D7"}
        </button>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={layer.opacity}
          onChange={(e) =>
            onPatch(id, { opacity: parseFloat(e.currentTarget.value) })
          }
          className="flex-1 accent-accent"
          aria-label="Opacity"
        />
        <span className="w-10 text-right font-mono text-[10px] text-muted">
          {(layer.opacity * 100).toFixed(0)}%
        </span>
        {showBlend && (
          <select
            value={layer.blend_mode}
            onChange={(e) =>
              onPatch(id, {
                blend_mode: e.currentTarget.value as LayerBlendMode,
              })
            }
            className="rounded bg-bg-card px-1.5 py-0.5 text-[10px] ring-1 ring-line"
            aria-label="Blend mode"
          >
            {LAYER_BLEND_MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>
      {layer.error && (
        <div className="mt-1.5 truncate text-[10px] text-rose-300" title={layer.error}>
          {layer.error}
        </div>
      )}
    </div>
  );
}
