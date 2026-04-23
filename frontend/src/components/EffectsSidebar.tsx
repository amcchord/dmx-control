import React, { useEffect, useState } from "react";
import { Api, ActiveEffect, Effect, Palette } from "../api";
import PaletteSwatch from "./PaletteSwatch";

type Props = {
  effects: Effect[];
  activeEffects: ActiveEffect[];
  palettes: Palette[];
  onEffectSelected?: (effect: Effect) => void;
  onChanged?: () => void;
  notify?: (msg: string, kind?: "success" | "error" | "info") => void;
};

export default function EffectsSidebar({
  effects,
  activeEffects,
  palettes,
  onEffectSelected,
  onChanged,
  notify,
}: Props) {
  const [busy, setBusy] = useState<number | null>(null);
  const [, setTick] = useState(0);

  // Re-render once per second so the running-time counters advance.
  useEffect(() => {
    const h = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(h);
  }, []);

  const palettesById = new Map(palettes.map((p) => [p.id, p]));
  const activeByEffect = new Map<number, ActiveEffect>();
  for (const a of activeEffects) {
    if (a.id != null) activeByEffect.set(a.id, a);
  }
  const liveOnlyCount = activeEffects.filter((a) => a.id == null).length;
  const hasAnyActive = activeEffects.length > 0;

  async function play(e: Effect) {
    setBusy(e.id);
    try {
      await Api.playEffect(e.id);
      onChanged?.();
    } catch (err) {
      notify?.(String(err), "error");
    } finally {
      setBusy(null);
    }
  }

  async function stop(e: Effect) {
    setBusy(e.id);
    try {
      await Api.stopEffect(e.id);
      onChanged?.();
    } catch (err) {
      notify?.(String(err), "error");
    } finally {
      setBusy(null);
    }
  }

  async function remove(e: Effect) {
    if (!confirm(`Delete effect "${e.name}"?`)) return;
    try {
      await Api.deleteEffect(e.id);
      onChanged?.();
    } catch (err) {
      notify?.(String(err), "error");
    }
  }

  async function stopAll() {
    try {
      await Api.stopAllEffects();
      onChanged?.();
    } catch (err) {
      notify?.(String(err), "error");
    }
  }

  return (
    <div className="card flex h-full flex-col p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Effects</div>
          <div className="text-[11px] text-muted">
            {effects.length} preset{effects.length === 1 ? "" : "s"}
            {hasAnyActive ? ` \u2022 ${activeEffects.length} running` : ""}
            {liveOnlyCount > 0 ? ` (${liveOnlyCount} live)` : ""}
          </div>
        </div>
        <button
          className="btn-ghost text-xs"
          onClick={stopAll}
          disabled={!hasAnyActive}
          title="Stop every running effect"
        >
          Stop all
        </button>
      </div>

      {effects.length === 0 ? (
        <div className="rounded-md bg-bg-elev p-3 text-center text-xs text-muted">
          No saved effects yet. Play a live effect and hit "Save as preset" to
          pin it here.
        </div>
      ) : (
        <div className="flex-1 space-y-1.5 overflow-y-auto pr-1">
          {effects.map((e) => {
            const active = activeByEffect.get(e.id);
            const palette = e.palette_id
              ? palettesById.get(e.palette_id)
              : undefined;
            return (
              <div
                key={e.id}
                className={
                  "flex items-center gap-2 rounded-md p-1.5 text-xs ring-1 " +
                  (active
                    ? "bg-emerald-950/40 ring-emerald-800"
                    : "bg-bg-elev ring-line")
                }
              >
                <div className="min-w-0 flex-1">
                  <button
                    className="block w-full truncate text-left font-medium hover:underline"
                    onClick={() => onEffectSelected?.(e)}
                    title="Load this effect into the editor"
                  >
                    {e.name}
                    {e.builtin && (
                      <span className="ml-1 text-[10px] text-muted">
                        built-in
                      </span>
                    )}
                  </button>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-wide text-muted">
                      {e.effect_type}
                    </span>
                    {palette && (
                      <div className="max-w-[100px] flex-1">
                        <PaletteSwatch colors={palette.colors.slice(0, 6)} />
                      </div>
                    )}
                    {active && (
                      <span className="text-[10px] text-emerald-300">
                        {formatRuntime(active.runtime_s)}
                      </span>
                    )}
                  </div>
                </div>
                {active ? (
                  <button
                    className="btn-ghost text-rose-300"
                    onClick={() => stop(e)}
                    disabled={busy === e.id}
                    title="Stop"
                  >
                    {"\u25A0"}
                  </button>
                ) : (
                  <button
                    className="btn-ghost text-emerald-300"
                    onClick={() => play(e)}
                    disabled={busy === e.id}
                    title="Play"
                  >
                    {"\u25B6"}
                  </button>
                )}
                {!e.builtin && (
                  <button
                    className="btn-ghost text-muted hover:text-rose-300"
                    onClick={() => remove(e)}
                    title="Delete"
                  >
                    {"\u00D7"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatRuntime(s: number): string {
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m${rem.toString().padStart(2, "0")}s`;
}
