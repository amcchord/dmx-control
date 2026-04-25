import React, { useEffect, useState } from "react";
import { ActiveEffect, Api } from "../api";

type Props = {
  activeEffects: ActiveEffect[];
  onChanged?: () => void;
  notify?: (msg: string, kind?: "success" | "error" | "info") => void;
};

export default function ActiveEffectsBar({
  activeEffects,
  onChanged,
  notify,
}: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [, setTick] = useState(0);

  // Advance the runtime display once per second without re-polling.
  useEffect(() => {
    if (activeEffects.length === 0) return;
    const h = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(h);
  }, [activeEffects.length]);

  if (activeEffects.length === 0) return null;

  async function stopOne(a: ActiveEffect) {
    setBusy(a.handle);
    try {
      if (a.id != null) {
        await Api.stopEffect(a.id);
      } else {
        await Api.stopLive(a.handle);
      }
      onChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    } finally {
      setBusy(null);
    }
  }

  async function stopAll() {
    setBusy("all");
    try {
      await Api.stopAllEffects();
      onChanged?.();
    } catch (e) {
      notify?.(String(e), "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 rounded-lg bg-emerald-950/40 px-2 py-1.5 ring-1 ring-emerald-900/60">
      <div className="flex items-center gap-1.5 pl-1">
        <span className="relative inline-flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
        </span>
        <span className="text-xs font-semibold text-emerald-200">
          {activeEffects.length} running
        </span>
      </div>
      <div className="flex flex-1 flex-wrap gap-1.5">
        {activeEffects.map((a) => {
          const isBusy = busy === a.handle;
          return (
            <div
              key={a.handle}
              className={
                "flex items-center gap-1.5 rounded-full bg-bg-card py-0.5 pl-2 pr-0.5 text-xs ring-1 " +
                (a.id == null ? "ring-amber-800" : "ring-emerald-800")
              }
              title={`running ${formatRuntime(a.runtime_s)}`}
            >
              <span className="text-sm leading-none" aria-hidden>
                {"\u25C9"}
              </span>
              <span className="max-w-[14ch] truncate font-medium text-slate-100">
                {a.name}
              </span>
              {a.id == null && (
                <span className="rounded bg-amber-900/50 px-1 py-px text-[9px] font-semibold uppercase tracking-wider text-amber-200">
                  live
                </span>
              )}
              <span className="font-mono text-[10px] text-muted">
                {formatRuntime(a.runtime_s)}
              </span>
              <button
                onClick={() => stopOne(a)}
                disabled={isBusy}
                className="ml-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-rose-900/40 text-rose-200 ring-1 ring-rose-800 transition hover:bg-rose-700 hover:text-white disabled:opacity-50"
                title="Stop this effect"
                aria-label={`Stop ${a.name}`}
              >
                {"\u00D7"}
              </button>
            </div>
          );
        })}
      </div>
      {activeEffects.length > 1 && (
        <button
          onClick={stopAll}
          disabled={busy === "all"}
          className="btn-danger px-2.5 py-1 text-xs"
          title="Stop every running effect"
        >
          Stop all
        </button>
      )}
    </div>
  );
}

function formatRuntime(s: number): string {
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}:${rem.toString().padStart(2, "0")}`;
}
