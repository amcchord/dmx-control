import React, { useEffect, useState } from "react";
import { Api, ActiveScene, Palette, Scene } from "../api";
import PaletteSwatch from "./PaletteSwatch";

type Props = {
  scenes: Scene[];
  activeScenes: ActiveScene[];
  palettes: Palette[];
  onSceneSelected?: (scene: Scene) => void;
  onChanged?: () => void;
  notify?: (msg: string, kind?: "success" | "error" | "info") => void;
};

export default function ScenesSidebar({
  scenes,
  activeScenes,
  palettes,
  onSceneSelected,
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
  const activeByScene = new Map<number, ActiveScene>();
  for (const a of activeScenes) {
    if (a.id != null) activeByScene.set(a.id, a);
  }
  const liveOnlyCount = activeScenes.filter((a) => a.id == null).length;
  const hasAnyActive = activeScenes.length > 0;

  async function play(s: Scene) {
    setBusy(s.id);
    try {
      await Api.playScene(s.id);
      onChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    } finally {
      setBusy(null);
    }
  }

  async function stop(s: Scene) {
    setBusy(s.id);
    try {
      await Api.stopScene(s.id);
      onChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    } finally {
      setBusy(null);
    }
  }

  async function remove(s: Scene) {
    if (!confirm(`Delete scene "${s.name}"?`)) return;
    try {
      await Api.deleteScene(s.id);
      onChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    }
  }

  async function stopAll() {
    try {
      await Api.stopAllScenes();
      onChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    }
  }

  return (
    <div className="card flex h-full flex-col p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Scenes</div>
          <div className="text-[11px] text-muted">
            {scenes.length} preset{scenes.length === 1 ? "" : "s"}
            {hasAnyActive ? ` \u2022 ${activeScenes.length} running` : ""}
            {liveOnlyCount > 0 ? ` (${liveOnlyCount} live)` : ""}
          </div>
        </div>
        <button
          className="btn-ghost text-xs"
          onClick={stopAll}
          disabled={!hasAnyActive}
          title="Stop every running scene"
        >
          Stop all
        </button>
      </div>

      {scenes.length === 0 ? (
        <div className="rounded-md bg-bg-elev p-3 text-center text-xs text-muted">
          No saved scenes yet. Play a live effect and hit "Save as preset" to
          pin it here.
        </div>
      ) : (
        <div className="flex-1 space-y-1.5 overflow-y-auto pr-1">
          {scenes.map((s) => {
            const active = activeByScene.get(s.id);
            const palette = s.palette_id
              ? palettesById.get(s.palette_id)
              : undefined;
            return (
              <div
                key={s.id}
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
                    onClick={() => onSceneSelected?.(s)}
                    title="Load this scene into the editor"
                  >
                    {s.name}
                    {s.builtin && (
                      <span className="ml-1 text-[10px] text-muted">
                        built-in
                      </span>
                    )}
                  </button>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-wide text-muted">
                      {s.effect_type}
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
                    onClick={() => stop(s)}
                    disabled={busy === s.id}
                    title="Stop"
                  >
                    {"\u25A0"}
                  </button>
                ) : (
                  <button
                    className="btn-ghost text-emerald-300"
                    onClick={() => play(s)}
                    disabled={busy === s.id}
                    title="Play"
                  >
                    {"\u25B6"}
                  </button>
                )}
                {!s.builtin && (
                  <button
                    className="btn-ghost text-muted hover:text-rose-300"
                    onClick={() => remove(s)}
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
