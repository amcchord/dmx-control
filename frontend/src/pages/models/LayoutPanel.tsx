import React, { useMemo, useState } from "react";
import type {
  ColorRole,
  FixtureLayout,
  FixtureZone,
  LayoutShape,
} from "../../api";
import {
  COLOR_ROLES,
  MOTION_AXES,
  MotionAxis,
  SHAPES,
  channelOwners,
  detectZones,
  makeZoneId,
  orderedZones,
} from "../../fixtureLayout";

/** Top-level compound-fixture (zones + motion + globals) editor. */
export default function LayoutPanel({
  channels,
  layout,
  onChange,
}: {
  channels: string[];
  layout: FixtureLayout | null;
  onChange: (next: FixtureLayout | null) => void;
}) {
  const [open, setOpen] = useState(
    () => layout != null && (layout.zones.length > 0 || !!layout.motion),
  );

  const enable = () => {
    setOpen(true);
    if (layout == null) onChange(detectZones(channels));
  };

  const disable = () => {
    setOpen(false);
    onChange(null);
  };

  const autoDetect = () => {
    onChange(detectZones(channels));
  };

  const owners = useMemo(() => channelOwners(layout), [layout]);

  return (
    <div className="rounded-lg bg-bg-elev p-3 ring-1 ring-line">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Fixture layout</div>
          <div className="text-xs text-muted">
            Describe zones (pixels, rings, eyes, heads) and motion axes so the
            console can address each one individually and roll palettes across
            the fixture.
          </div>
        </div>
        {layout == null ? (
          <button type="button" className="btn-secondary" onClick={enable}>
            Add layout
          </button>
        ) : (
          <div className="flex gap-1">
            <button
              type="button"
              className="btn-ghost !px-2 !py-1 text-xs"
              onClick={autoDetect}
              title="Detect zones from the channel list"
            >
              Auto-detect
            </button>
            <button
              type="button"
              className="btn-ghost !px-2 !py-1 text-xs text-rose-300 hover:bg-rose-950 hover:text-rose-200"
              onClick={disable}
            >
              Remove layout
            </button>
          </div>
        )}
      </div>
      {layout != null && open && (
        <LayoutBody
          channels={channels}
          layout={layout}
          owners={owners}
          onChange={onChange}
        />
      )}
    </div>
  );
}

function LayoutBody({
  channels,
  layout,
  owners,
  onChange,
}: {
  channels: string[];
  layout: FixtureLayout;
  owners: Map<number, string>;
  onChange: (next: FixtureLayout) => void;
}) {
  const setShape = (shape: LayoutShape) => {
    const next: FixtureLayout = { ...layout, shape };
    if (shape === "single") next.zones = [];
    onChange(next);
  };

  const setCols = (cols: number | null) =>
    onChange({ ...layout, cols: cols ?? null });
  const setRows = (rows: number | null) =>
    onChange({ ...layout, rows: rows ?? null });

  const setZones = (zones: FixtureZone[]) =>
    onChange({ ...layout, zones });

  const addZone = () => {
    const taken = new Set(layout.zones.map((z) => z.id));
    const id = makeZoneId("z", taken);
    const zones: FixtureZone[] = [
      ...layout.zones,
      {
        id,
        label: `Zone ${layout.zones.length + 1}`,
        kind: "pixel",
        row: 0,
        col: layout.zones.length,
        colors: {},
      },
    ];
    setZones(zones);
  };

  const updateZone = (idx: number, patch: Partial<FixtureZone>) => {
    const zones = layout.zones.map((z, i) =>
      i === idx ? { ...z, ...patch, colors: patch.colors ?? z.colors } : z,
    );
    setZones(zones);
  };

  const removeZone = (idx: number) => {
    setZones(layout.zones.filter((_, i) => i !== idx));
  };

  const setMotion = (patch: Partial<NonNullable<FixtureLayout["motion"]>>) => {
    const next: NonNullable<FixtureLayout["motion"]> = {
      ...(layout.motion ?? {}),
      ...patch,
    };
    onChange({ ...layout, motion: next });
  };

  const setGlobal = (
    key: "dimmer" | "strobe" | "macro" | "speed",
    val: number | null,
  ) => {
    const next: NonNullable<FixtureLayout["globals"]> = {
      ...(layout.globals ?? {}),
    };
    if (val == null) delete next[key];
    else next[key] = val;
    onChange({ ...layout, globals: next });
  };

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label !text-[10px]">Shape</span>
        {SHAPES.map((s) => (
          <button
            type="button"
            key={s}
            className={
              "rounded-full px-3 py-1 text-xs ring-1 transition " +
              (layout.shape === s
                ? "bg-accent text-white ring-accent"
                : "bg-bg-card text-slate-300 ring-line hover:bg-bg-elev")
            }
            onClick={() => setShape(s)}
          >
            {s}
          </button>
        ))}
        {(layout.shape === "linear" ||
          layout.shape === "grid" ||
          layout.shape === "ring") && (
          <>
            <label className="ml-2 flex items-center gap-1 text-xs">
              <span className="text-muted">cols</span>
              <input
                type="number"
                min={1}
                className="input !w-16 !py-0.5 text-xs"
                value={layout.cols ?? ""}
                onChange={(e) =>
                  setCols(e.target.value ? Number(e.target.value) : null)
                }
              />
            </label>
            {layout.shape === "grid" && (
              <label className="flex items-center gap-1 text-xs">
                <span className="text-muted">rows</span>
                <input
                  type="number"
                  min={1}
                  className="input !w-16 !py-0.5 text-xs"
                  value={layout.rows ?? ""}
                  onChange={(e) =>
                    setRows(e.target.value ? Number(e.target.value) : null)
                  }
                />
              </label>
            )}
          </>
        )}
      </div>

      <LayoutPreview layout={layout} owners={owners} />

      {layout.shape !== "single" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="label !text-[10px]">
              Zones ({layout.zones.length})
            </div>
            <button
              type="button"
              className="btn-ghost !px-2 !py-1 text-xs"
              onClick={addZone}
            >
              + add zone
            </button>
          </div>
          <div className="space-y-2">
            {layout.zones.length === 0 && (
              <div className="rounded-md bg-bg-card px-3 py-2 text-xs text-muted ring-1 ring-line">
                No zones yet. Click "Auto-detect" above or add them manually.
              </div>
            )}
            {layout.zones.map((z, i) => (
              <ZoneRow
                key={z.id + ":" + i}
                channels={channels}
                zone={z}
                onChange={(patch) => updateZone(i, patch)}
                onRemove={() => removeZone(i)}
              />
            ))}
          </div>
        </div>
      )}

      <MotionBlock
        channels={channels}
        layout={layout}
        onMotion={setMotion}
        onDegrees={(k, v) =>
          setMotion({ [k]: v } as Partial<NonNullable<FixtureLayout["motion"]>>)
        }
      />

      <GlobalsBlock
        channels={channels}
        globals={layout.globals ?? {}}
        onChange={setGlobal}
      />
    </div>
  );
}

function LayoutPreview({
  layout,
  owners,
}: {
  layout: FixtureLayout;
  owners: Map<number, string>;
}) {
  const zones = orderedZones(layout);
  const count = zones.length;

  const fill = (z: FixtureZone): string => {
    const hasRGB = z.colors.r != null && z.colors.g != null && z.colors.b != null;
    if (hasRGB) return "linear-gradient(135deg,#ff4d4d,#4dff6a,#4d6aff)";
    if (z.colors.w != null) return "#f5f5f5";
    if (z.colors.a != null) return "#ffb23d";
    if (z.colors.uv != null) return "#b44dff";
    return "#8791a7";
  };

  let shapeView: React.ReactNode = null;
  if (layout.shape === "single" || count === 0) {
    shapeView = (
      <div className="flex h-16 items-center justify-center text-xs text-muted">
        Single zone — fixture controlled as a single color.
      </div>
    );
  } else if (layout.shape === "linear") {
    shapeView = (
      <div className="flex h-16 items-center gap-1 overflow-x-auto">
        {zones.map((z) => (
          <div
            key={z.id}
            title={z.label}
            className="h-12 min-w-[16px] flex-1 rounded-sm ring-1 ring-line"
            style={{ background: fill(z) }}
          />
        ))}
      </div>
    );
  } else if (layout.shape === "grid") {
    const cols = layout.cols ?? Math.ceil(Math.sqrt(count));
    shapeView = (
      <div
        className="grid gap-1"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      >
        {zones.map((z) => (
          <div
            key={z.id}
            title={z.label}
            className="aspect-square rounded-sm ring-1 ring-line"
            style={{ background: fill(z) }}
          />
        ))}
      </div>
    );
  } else if (layout.shape === "ring") {
    const size = 96;
    const radius = size / 2 - 8;
    shapeView = (
      <div
        className="relative mx-auto"
        style={{ width: size, height: size }}
      >
        {zones.map((z, i) => {
          const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
          const x = size / 2 + Math.cos(angle) * radius - 6;
          const y = size / 2 + Math.sin(angle) * radius - 6;
          return (
            <div
              key={z.id}
              title={z.label}
              className="absolute h-3 w-3 rounded-full ring-1 ring-line"
              style={{ left: x, top: y, background: fill(z) }}
            />
          );
        })}
      </div>
    );
  } else {
    shapeView = (
      <div className="flex flex-wrap gap-2">
        {zones.map((z) => (
          <div
            key={z.id}
            title={z.label}
            className="flex items-center gap-2 rounded-md bg-bg-card px-2 py-1 text-xs ring-1 ring-line"
          >
            <span
              className="h-3 w-3 rounded-sm"
              style={{ background: fill(z) }}
            />
            <span className="truncate">{z.label}</span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-bg-card p-2 ring-1 ring-line">
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-muted">
        <span>Preview</span>
        <span>{owners.size} channels assigned</span>
      </div>
      {shapeView}
    </div>
  );
}

function ZoneRow({
  channels,
  zone,
  onChange,
  onRemove,
}: {
  channels: string[];
  zone: FixtureZone;
  onChange: (patch: Partial<FixtureZone>) => void;
  onRemove: () => void;
}) {
  const setColorOffset = (role: ColorRole, value: number | null) => {
    const next = { ...zone.colors };
    if (value == null) delete next[role];
    else next[role] = value;
    onChange({ colors: next });
  };

  return (
    <div className="rounded-md bg-bg-card p-2 ring-1 ring-line">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="input !w-40 !py-1"
          value={zone.label}
          onChange={(e) => onChange({ label: e.target.value })}
          placeholder="Zone label"
        />
        <select
          className="input !w-28 !py-1 text-xs"
          value={zone.kind ?? "pixel"}
          onChange={(e) =>
            onChange({ kind: e.target.value as FixtureZone["kind"] })
          }
        >
          {(
            [
              "pixel",
              "segment",
              "ring",
              "panel",
              "eye",
              "head",
              "beam",
              "global",
              "other",
            ] as const
          ).map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-xs text-muted">
          row
          <input
            type="number"
            min={0}
            className="input !w-14 !py-0.5 text-xs"
            value={zone.row ?? 0}
            onChange={(e) => onChange({ row: Number(e.target.value) })}
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          col
          <input
            type="number"
            min={0}
            className="input !w-14 !py-0.5 text-xs"
            value={zone.col ?? 0}
            onChange={(e) => onChange({ col: Number(e.target.value) })}
          />
        </label>
        <span className="ml-auto font-mono text-[10px] text-muted">
          id: {zone.id}
        </span>
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 text-xs text-rose-300"
          onClick={onRemove}
        >
          remove
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {COLOR_ROLES.map((role) => (
          <ChannelPicker
            key={role}
            label={role.toUpperCase()}
            channels={channels}
            value={zone.colors[role] ?? null}
            filterRole={role}
            onChange={(v) => setColorOffset(role, v)}
          />
        ))}
        <ChannelPicker
          label="Dim"
          channels={channels}
          value={zone.dimmer ?? null}
          filterRole="dimmer"
          onChange={(v) => onChange({ dimmer: v == null ? undefined : v })}
        />
        <ChannelPicker
          label="Str"
          channels={channels}
          value={zone.strobe ?? null}
          filterRole="strobe"
          onChange={(v) => onChange({ strobe: v == null ? undefined : v })}
        />
      </div>
      {Object.keys(zone.colors).length === 0 && zone.dimmer == null && (
        <div className="mt-1 text-[11px] text-amber-300/80">
          Warning: zone has no color or dimmer channels assigned.
        </div>
      )}
    </div>
  );
}

function ChannelPicker({
  label,
  channels,
  value,
  filterRole,
  onChange,
}: {
  label: string;
  channels: string[];
  value: number | null;
  filterRole?: string;
  onChange: (v: number | null) => void;
}) {
  return (
    <label className="flex items-center gap-1 rounded-md bg-bg-elev px-2 py-0.5 text-xs ring-1 ring-line">
      <span className="font-mono text-muted">{label}</span>
      <select
        className="bg-transparent text-xs outline-none"
        value={value == null ? "" : String(value)}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" ? null : Number(v));
        }}
      >
        <option value="">—</option>
        {channels.map((role, i) => {
          const match = filterRole ? role === filterRole : true;
          return (
            <option key={i} value={i}>
              {match ? "★ " : ""}
              {i + 1}. {role}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function MotionBlock({
  channels,
  layout,
  onMotion,
  onDegrees,
}: {
  channels: string[];
  layout: FixtureLayout;
  onMotion: (patch: Partial<NonNullable<FixtureLayout["motion"]>>) => void;
  onDegrees: (k: "pan_degrees" | "tilt_degrees", v: number | null) => void;
}) {
  const motion = layout.motion ?? {};
  const has = (axis: MotionAxis) =>
    typeof motion[axis] === "number" ||
    typeof motion[`${axis}_fine` as keyof typeof motion] === "number";
  const anyMotion = MOTION_AXES.some((a) => has(a));

  return (
    <div className="rounded-md bg-bg-card p-2 ring-1 ring-line">
      <div className="mb-2 flex items-center justify-between">
        <div className="label !text-[10px]">
          Motion (pan / tilt / zoom / focus)
        </div>
        <div className="text-[10px] text-muted">
          {anyMotion ? "—" : "optional"}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {MOTION_AXES.map((axis) => {
          const coarseRole = axis;
          const fineRole = `${axis}_fine` as keyof typeof motion;
          const has16 = axis === "pan" || axis === "tilt";
          return (
            <div
              key={axis}
              className="flex flex-wrap items-center gap-2 rounded-md bg-bg-elev p-2 ring-1 ring-line"
            >
              <span className="label !text-[10px] w-12 capitalize">
                {axis}
              </span>
              <ChannelPicker
                label="ch"
                channels={channels}
                value={(motion[coarseRole] as number | null | undefined) ?? null}
                filterRole={coarseRole}
                onChange={(v) =>
                  onMotion({
                    [coarseRole]: v,
                  } as Partial<NonNullable<FixtureLayout["motion"]>>)
                }
              />
              {has16 && (
                <ChannelPicker
                  label="fine"
                  channels={channels}
                  value={(motion[fineRole] as number | null | undefined) ?? null}
                  filterRole={axis === "pan" ? "pan_fine" : "tilt_fine"}
                  onChange={(v) =>
                    onMotion({
                      [fineRole]: v,
                    } as Partial<NonNullable<FixtureLayout["motion"]>>)
                  }
                />
              )}
              {has16 && (
                <label className="flex items-center gap-1 text-[11px] text-muted">
                  range
                  <input
                    type="number"
                    min={0}
                    max={1080}
                    className="input !w-16 !py-0.5 text-xs"
                    value={
                      axis === "pan"
                        ? motion.pan_degrees ?? ""
                        : motion.tilt_degrees ?? ""
                    }
                    onChange={(e) =>
                      onDegrees(
                        axis === "pan" ? "pan_degrees" : "tilt_degrees",
                        e.target.value ? Number(e.target.value) : null,
                      )
                    }
                  />
                  °
                </label>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GlobalsBlock({
  channels,
  globals,
  onChange,
}: {
  channels: string[];
  globals: NonNullable<FixtureLayout["globals"]>;
  onChange: (
    key: "dimmer" | "strobe" | "macro" | "speed",
    val: number | null,
  ) => void;
}) {
  return (
    <div className="rounded-md bg-bg-card p-2 ring-1 ring-line">
      <div className="mb-2 label !text-[10px]">Globals (fixture-wide)</div>
      <div className="flex flex-wrap gap-2">
        <ChannelPicker
          label="Master Dim"
          channels={channels}
          value={(globals.dimmer as number | null | undefined) ?? null}
          filterRole="dimmer"
          onChange={(v) => onChange("dimmer", v)}
        />
        <ChannelPicker
          label="Strobe"
          channels={channels}
          value={(globals.strobe as number | null | undefined) ?? null}
          filterRole="strobe"
          onChange={(v) => onChange("strobe", v)}
        />
        <ChannelPicker
          label="Macro"
          channels={channels}
          value={(globals.macro as number | null | undefined) ?? null}
          filterRole="macro"
          onChange={(v) => onChange("macro", v)}
        />
        <ChannelPicker
          label="Speed"
          channels={channels}
          value={(globals.speed as number | null | undefined) ?? null}
          filterRole="speed"
          onChange={(v) => onChange("speed", v)}
        />
      </div>
    </div>
  );
}
