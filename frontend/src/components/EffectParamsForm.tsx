import React from "react";
import { EffectParamSchemaEntry, EffectParams } from "../api";

type Props = {
  schema: EffectParamSchemaEntry[];
  values: EffectParams;
  onChange: (next: EffectParams) => void;
};

export default function EffectParamsForm({ schema, values, onChange }: Props) {
  if (schema.length === 0) {
    return (
      <div className="rounded-md bg-bg-elev p-2 text-[11px] text-muted">
        No parameters declared. Add a <code>PARAMS</code> table at the top
        of the script to expose knobs here.
      </div>
    );
  }
  function set(id: string, v: number | string | boolean) {
    onChange({ ...values, [id]: v });
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {schema.map((entry) => (
        <ParamControl
          key={entry.id}
          entry={entry}
          value={values[entry.id] ?? entry.default ?? defaultFor(entry)}
          onChange={(v) => set(entry.id, v)}
        />
      ))}
    </div>
  );
}

function defaultFor(entry: EffectParamSchemaEntry): number | string | boolean {
  switch (entry.type) {
    case "bool":
      return false;
    case "color":
      return "#FFFFFF";
    case "choice":
      return entry.options?.[0] ?? "";
    default:
      return entry.min ?? 0;
  }
}

function ParamControl({
  entry,
  value,
  onChange,
}: {
  entry: EffectParamSchemaEntry;
  value: number | string | boolean;
  onChange: (v: number | string | boolean) => void;
}) {
  const label = entry.label ?? entry.id;
  if (entry.type === "bool") {
    return (
      <label className="flex items-center justify-between gap-2 rounded-md bg-bg-elev p-2 text-xs ring-1 ring-line">
        <span>{label}</span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      </label>
    );
  }
  if (entry.type === "choice") {
    const opts = entry.options ?? [];
    return (
      <div>
        <div className="mb-0.5 flex items-baseline justify-between">
          <span className="label !text-[11px] normal-case tracking-normal">
            {label}
          </span>
        </div>
        <div className="flex flex-wrap gap-1">
          {opts.map((o) => (
            <button
              key={o}
              type="button"
              className={
                "rounded-md px-2 py-1 text-[11px] ring-1 transition " +
                (value === o
                  ? "bg-accent text-white ring-accent"
                  : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
              }
              onClick={() => onChange(o)}
            >
              {o}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (entry.type === "color") {
    return (
      <div>
        <div className="mb-0.5 flex items-baseline justify-between">
          <span className="label !text-[11px] normal-case tracking-normal">
            {label}
          </span>
          <span className="text-[11px] text-muted">{String(value)}</span>
        </div>
        <input
          type="color"
          value={String(value)}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="h-7 w-full rounded-md ring-1 ring-line"
        />
      </div>
    );
  }
  // number / slider
  const num = typeof value === "number" ? value : Number(value) || 0;
  const min = entry.min ?? 0;
  const max = entry.max ?? 1;
  const step = entry.step ?? (max - min < 2 ? 0.01 : 0.05);
  const display =
    Math.abs(num) < 0.01 ? num.toFixed(3) : num.toFixed(num < 1 ? 2 : 1);
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between">
        <span className="label !text-[11px] normal-case tracking-normal">
          {label}
        </span>
        <span className="text-[11px] text-muted">
          {display}
          {entry.suffix ? ` ${entry.suffix}` : ""}
        </span>
      </div>
      <input
        type="range"
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-bg-elev accent-accent"
        min={min}
        max={max}
        step={step}
        value={num}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
    </div>
  );
}
