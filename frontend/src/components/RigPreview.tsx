import React, { useEffect, useState } from "react";
import { Api, Controller, Light, RenderedLight } from "../api";

type Props = {
  /** Lights to render; usually every light in the rig but pages may
   *  filter (e.g. drill into one controller). */
  lights: Light[];
  /** Optional click handler — when set, cells become buttons. */
  onSelect?: (lightId: number) => void;
  /** Light ids currently selected; gets a ring overlay. */
  selected?: Set<number>;
  /** Polling cadence. Defaults to 4 Hz which is plenty for visual sync
   *  with the engine's 30 Hz tick without spamming the server. */
  hz?: number;
  /** Optional override on the cell shape; defaults to a flat square. */
  size?: "sm" | "md" | "lg";
  className?: string;
  /** Hide the legend strip (used in dense desktop rails). */
  compact?: boolean;
  /** When provided, lights group into one section per controller and
   *  the section header shows the controller name. */
  controllers?: Controller[];
  /** Show the fixture name underneath each cell. Hover always shows it
   *  via the native title attribute regardless of this flag. */
  showLabels?: boolean;
};

/** Live rig preview shown across both shells.
 *
 * Polls ``GET /api/lights/rendered`` to stay in sync with the engine's
 * DMX output (the same numbers that go out over Art-Net), and decodes
 * the per-light/per-zone payload into colored cells. The component is
 * deliberately small and self-contained so it can drop into the mobile
 * Now Playing hero, the desktop Live rail, the Effects Composer, and
 * the Scene Composer. */
export default function RigPreview({
  lights,
  onSelect,
  selected,
  hz = 4,
  size = "md",
  className = "",
  compact = false,
  controllers,
  showLabels = false,
}: Props) {
  const [rendered, setRendered] = useState<Record<string, RenderedLight>>({});

  useEffect(() => {
    let cancelled = false;
    const period = Math.max(50, Math.floor(1000 / Math.max(1, hz)));
    let timer: number | null = null;

    const tick = async () => {
      try {
        const data = await Api.listRenderedLights();
        if (!cancelled) setRendered(data);
      } catch {
        // Ignore transient failures; the next tick will retry.
      } finally {
        if (!cancelled) timer = window.setTimeout(tick, period);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [hz]);

  const cellClass =
    size === "sm"
      ? "h-6 w-6"
      : size === "lg"
        ? "h-12 w-12"
        : "h-9 w-9";

  if (lights.length === 0) {
    return (
      <div
        className={
          "rounded-lg border border-dashed border-line p-6 text-center text-xs text-muted " +
          className
        }
      >
        No lights configured yet.
      </div>
    );
  }

  // Group when controllers prop is supplied, else render the whole rig
  // as one block.
  if (controllers && controllers.length > 0) {
    const byCtrl = new Map<number, Light[]>();
    for (const l of lights) {
      const arr = byCtrl.get(l.controller_id) ?? [];
      arr.push(l);
      byCtrl.set(l.controller_id, arr);
    }
    const ordered = controllers
      .map((c) => ({ ctrl: c, arr: byCtrl.get(c.id) ?? [] }))
      .filter((g) => g.arr.length > 0);
    // Append any orphan controller_ids not present in the list (data
    // mismatch) so we never silently drop fixtures.
    for (const [cid, arr] of byCtrl) {
      if (!controllers.some((c) => c.id === cid)) {
        ordered.push({
          ctrl: { id: cid, name: `Controller #${cid}` } as Controller,
          arr,
        });
      }
    }
    return (
      <div className={"flex flex-col gap-3 " + className}>
        {ordered.map(({ ctrl, arr }) => (
          <section
            key={ctrl.id}
            className="rounded-lg bg-bg-elev/60 p-3 ring-1 ring-line"
          >
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted">
                {ctrl.name}
              </div>
              <div className="text-[10px] text-muted">
                {arr.length} fixture{arr.length === 1 ? "" : "s"}
              </div>
            </div>
            <CellGrid
              lights={arr}
              rendered={rendered}
              cellClass={cellClass}
              onSelect={onSelect}
              selected={selected}
              showLabels={showLabels}
            />
          </section>
        ))}
        {!compact && (
          <div className="flex items-center justify-between text-[10px] text-muted">
            <span>{lights.length} fixtures</span>
            <span>~{hz} Hz preview</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={
        "flex flex-col gap-2 rounded-lg bg-bg-elev/60 p-3 ring-1 ring-line " +
        className
      }
    >
      <CellGrid
        lights={lights}
        rendered={rendered}
        cellClass={cellClass}
        onSelect={onSelect}
        selected={selected}
        showLabels={showLabels}
      />
      {!compact && (
        <div className="flex items-center justify-between text-[10px] text-muted">
          <span>{lights.length} fixtures</span>
          <span>~{hz} Hz preview</span>
        </div>
      )}
    </div>
  );
}

function CellGrid({
  lights,
  rendered,
  cellClass,
  onSelect,
  selected,
  showLabels,
}: {
  lights: Light[];
  rendered: Record<string, RenderedLight>;
  cellClass: string;
  onSelect?: (id: number) => void;
  selected?: Set<number>;
  showLabels: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {lights.map((light) => {
        const r = rendered[String(light.id)];
        const swatch = r
          ? `rgb(${r.r}, ${r.g}, ${r.b})`
          : `rgb(${light.r}, ${light.g}, ${light.b})`;
        const isOn = r ? r.on : light.on;
        const isSelected = selected?.has(light.id);
        // Render every zone the renderer reports, capped at a sensible
        // ceiling so a 112-channel pixel bar still fits inside a 36-px
        // cell. Off zones show as solid black (not 40% opacity) so they
        // don't visually mute the surrounding lit zones.
        const allZones = r?.zone_state ? Object.entries(r.zone_state) : [];
        const PREVIEW_ZONE_CAP = 64;
        const sub = allZones.slice(0, PREVIEW_ZONE_CAP);
        const cols = sub.length <= 1 ? 1 : Math.ceil(Math.sqrt(sub.length));
        const swatchEl = (
          <div
            className={
              "relative " +
              cellClass +
              " overflow-hidden rounded-md ring-1 transition " +
              (isSelected
                ? "ring-2 ring-accent shadow-[0_0_0_2px_rgba(124,77,255,0.4)]"
                : "ring-line") +
              (isOn ? "" : " opacity-40")
            }
            style={{ background: swatch }}
            title={`${light.name} (#${light.id})`}
          >
            {sub.length > 1 && (
              <div
                className="absolute inset-0 grid"
                style={{
                  gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
                }}
              >
                {sub.map(([zid, z]) => (
                  <div
                    key={zid}
                    className="h-full w-full"
                    style={{
                      background: z.on
                        ? `rgb(${z.r}, ${z.g}, ${z.b})`
                        : "#000",
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        );

        const label = showLabels ? (
          <span className="block max-w-[5rem] truncate text-center text-[10px] text-muted">
            {light.name}
          </span>
        ) : null;

        if (onSelect) {
          return (
            <button
              key={light.id}
              onClick={() => onSelect(light.id)}
              className="group flex flex-col items-center gap-1 rounded-md focus:outline-none focus:ring-2 focus:ring-accent"
              aria-label={`${light.name}`}
              aria-pressed={isSelected ? true : false}
              title={`${light.name} (#${light.id})`}
            >
              {swatchEl}
              {label}
            </button>
          );
        }

        return (
          <div
            key={light.id}
            className="flex flex-col items-center gap-1"
            title={`${light.name} (#${light.id})`}
          >
            {swatchEl}
            {label}
          </div>
        );
      })}
    </div>
  );
}
