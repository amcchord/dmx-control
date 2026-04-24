import React, { useMemo, useState } from "react";
import type { FixtureLayout, FixtureZone, ParsedManual } from "../../api";
import { MOTION_AXES, orderedZones } from "../../fixtureLayout";
import type { Form } from "./types";

/** Diff-style review of parsed modes returned by Claude. Lets the user
 * pick which mode names should be replaced and which new modes should
 * be added, then fires ``onApply`` once they confirm. */
export default function ScanMergePreview({
  form,
  parsed,
  onApply,
  onDismiss,
}: {
  form: Form;
  parsed: ParsedManual;
  onApply: (accept: {
    name: boolean;
    replace: Set<string>;
    add: Set<string>;
  }) => void;
  onDismiss: () => void;
}) {
  const existingByLower = useMemo(
    () =>
      new Map(
        form.modes.map((m) => [m.name.trim().toLowerCase(), m] as const),
      ),
    [form.modes],
  );

  const [acceptName, setAcceptName] = useState(
    !!parsed.suggested_name && parsed.suggested_name !== form.name,
  );
  const [replace, setReplace] = useState<Set<string>>(
    () =>
      new Set(
        parsed.modes
          .filter((pm) => existingByLower.has(pm.name.trim().toLowerCase()))
          .map((pm) => pm.name.trim().toLowerCase()),
      ),
  );
  const [add, setAdd] = useState<Set<string>>(
    () =>
      new Set(
        parsed.modes
          .filter((pm) => !existingByLower.has(pm.name.trim().toLowerCase()))
          .map((pm) => pm.name.trim().toLowerCase()),
      ),
  );

  const toggle = (s: Set<string>, k: string, set: (s: Set<string>) => void) => {
    const next = new Set(s);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    set(next);
  };

  return (
    <div className="space-y-2 rounded-lg bg-bg-elev p-3 ring-1 ring-accent/40">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">
          Claude found {parsed.modes.length} mode
          {parsed.modes.length === 1 ? "" : "s"}
        </div>
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 text-xs"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      </div>
      {parsed.suggested_name && parsed.suggested_name !== form.name && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-line bg-bg-card text-accent"
            checked={acceptName}
            onChange={(e) => setAcceptName(e.target.checked)}
          />
          Replace name with{" "}
          <span className="font-mono">{parsed.suggested_name}</span>
        </label>
      )}
      <div className="space-y-1">
        {parsed.modes.map((pm) => {
          const lower = pm.name.trim().toLowerCase();
          const existing = existingByLower.get(lower);
          const checked = existing ? replace.has(lower) : add.has(lower);
          return (
            <label
              key={lower + pm.channels.join(",")}
              className="flex items-start gap-2 rounded-md bg-bg-card p-2 text-xs ring-1 ring-line"
            >
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-line bg-bg-elev text-accent"
                checked={checked}
                onChange={() =>
                  existing
                    ? toggle(replace, lower, setReplace)
                    : toggle(add, lower, setAdd)
                }
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold">{pm.name}</span>
                  <span className="pill text-[10px]">
                    {existing ? "replace" : "add"}
                  </span>
                  <span className="text-[10px] text-muted">
                    {pm.channels.length}ch
                  </span>
                  {pm.layout && (
                    <span
                      className="pill text-[10px] bg-accent/10 text-accent ring-accent/40"
                      title={layoutSummary(pm.layout)}
                    >
                      {layoutBadge(pm.layout)}
                    </span>
                  )}
                </div>
                {pm.layout && (
                  <div className="mt-1">
                    <ParsedLayoutThumb layout={pm.layout} />
                  </div>
                )}
                <div className="mt-0.5 font-mono text-muted">
                  {pm.channels.join(", ")}
                </div>
                {pm.notes && (
                  <div className="mt-0.5 text-muted">{pm.notes}</div>
                )}
              </div>
            </label>
          );
        })}
      </div>
      <div className="flex justify-end">
        <button
          type="button"
          className="btn-primary"
          onClick={() => onApply({ name: acceptName, replace, add })}
        >
          Apply changes
        </button>
      </div>
    </div>
  );
}

function layoutSummary(layout: FixtureLayout): string {
  const parts: string[] = [];
  if (layout.zones.length > 0) {
    parts.push(
      `${layout.zones.length} zone${layout.zones.length === 1 ? "" : "s"}`,
    );
  }
  if (layout.motion) {
    const axes: string[] = [];
    for (const axis of MOTION_AXES) {
      if (typeof layout.motion[axis] === "number") axes.push(axis);
    }
    if (axes.length) parts.push(axes.join("+"));
  }
  return parts.join(" • ") || "empty layout";
}

function layoutBadge(layout: FixtureLayout): string {
  const n = layout.zones.length;
  if (n === 0) {
    const axes: string[] = [];
    for (const axis of MOTION_AXES) {
      if (typeof layout.motion?.[axis] === "number") axes.push(axis);
    }
    return axes.length ? axes.join("/") : layout.shape;
  }
  if (layout.shape === "linear") return `${n} px linear`;
  if (layout.shape === "grid")
    return `${layout.cols ?? "?"}×${layout.rows ?? "?"} grid`;
  if (layout.shape === "ring") return `${n} ring`;
  if (layout.shape === "cluster") return `${n} zones`;
  return `${n} zones`;
}

function ParsedLayoutThumb({ layout }: { layout: FixtureLayout }) {
  const zones = orderedZones(layout);
  if (zones.length === 0) {
    return (
      <div className="text-[10px] text-muted">
        Motion only ({layoutSummary(layout)})
      </div>
    );
  }
  const fill = (z: FixtureZone) => {
    if (z.colors.r != null && z.colors.g != null && z.colors.b != null) {
      return "linear-gradient(135deg,#ff4d4d,#4dff6a,#4d6aff)";
    }
    if (z.colors.w != null) return "#f5f5f5";
    if (z.colors.a != null) return "#ffb23d";
    if (z.colors.uv != null) return "#b44dff";
    return "#8791a7";
  };
  if (layout.shape === "grid") {
    const cols = layout.cols ?? Math.ceil(Math.sqrt(zones.length));
    return (
      <div
        className="grid gap-px rounded-sm"
        style={{
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          width: Math.min(12 * cols, 160),
        }}
      >
        {zones.map((z) => (
          <div
            key={z.id}
            className="h-2.5 rounded-[1px]"
            style={{ background: fill(z) }}
          />
        ))}
      </div>
    );
  }
  if (layout.shape === "ring") {
    return (
      <div className="text-[10px] text-muted">
        ring of {zones.length} cells
      </div>
    );
  }
  return (
    <div className="flex items-center gap-px">
      {zones.slice(0, 32).map((z) => (
        <div
          key={z.id}
          className="h-2.5 min-w-[4px] flex-1 max-w-[10px]"
          style={{ background: fill(z) }}
        />
      ))}
    </div>
  );
}
