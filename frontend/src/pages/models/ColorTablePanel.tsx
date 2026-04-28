import { useMemo, useState } from "react";
import { HexColorPicker } from "react-colorful";
import type { ColorTable, ColorTableEntry } from "../../api";

/** Per-mode "indexed-color lookup" editor.
 *
 * Some fixtures (e.g. Blizzard StormChaser 20CH) expose a single DMX
 * byte per cell that selects from a fixed palette of preset colors.
 * The ``color`` channel role marks those slots; this panel edits the
 * mode-level table that the Art-Net renderer uses to project each
 * frame's logical RGB onto the closest preset's representative byte.
 *
 * The panel is hidden when no ``color`` slot is present. After import
 * (PDF parse) the auto-extracted table appears here for review and
 * edits before save. */
export default function ColorTablePanel({
  channels,
  table,
  onChange,
}: {
  channels: string[];
  table: ColorTable | null;
  onChange: (next: ColorTable | null) => void;
}) {
  const present = useMemo(
    () => channels.includes("color"),
    [channels],
  );
  const colorSlotCount = useMemo(
    () => channels.filter((c) => c === "color").length,
    [channels],
  );

  if (!present) return null;

  const enable = () => onChange(makeStarterTable());
  const disable = () => onChange(null);

  return (
    <div className="rounded-lg bg-bg-elev p-3 ring-1 ring-line">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Indexed color table</div>
          <div className="text-xs text-muted">
            Lookup that drives every <code>color</code> channel in this mode (
            {colorSlotCount} {colorSlotCount === 1 ? "slot" : "slots"}). The
            renderer snaps each frame's logical RGB to the closest entry and
            emits its midpoint byte.
          </div>
        </div>
        {table == null ? (
          <button type="button" className="btn-secondary" onClick={enable}>
            Add table
          </button>
        ) : (
          <button
            type="button"
            className="btn-ghost !px-2 !py-1 text-xs text-rose-300 hover:bg-rose-950 hover:text-rose-200"
            onClick={disable}
          >
            Remove table
          </button>
        )}
      </div>
      {table != null && (
        <ColorTableBody channels={channels} table={table} onChange={onChange} />
      )}
      {table == null && (
        <div className="mt-2 rounded-md bg-bg-card px-3 py-2 text-xs text-muted ring-1 ring-line">
          Without a table, the <code>color</code> channel byte will stay at
          0. Add a table to enable RGB-driven preset selection.
        </div>
      )}
    </div>
  );
}

const STARTER_PRESETS: ColorTableEntry[] = [
  { lo: 0, hi: 15, name: "Off", r: 0, g: 0, b: 0 },
  { lo: 16, hi: 31, name: "Red", r: 255, g: 0, b: 0 },
  { lo: 32, hi: 47, name: "Green", r: 0, g: 255, b: 0 },
  { lo: 48, hi: 63, name: "Blue", r: 0, g: 0, b: 255 },
];

const makeStarterTable = (): ColorTable => ({
  entries: STARTER_PRESETS.map((e) => ({ ...e })),
  off_below: 0,
});

function ColorTableBody({
  table,
  onChange,
}: {
  channels: string[];
  table: ColorTable;
  onChange: (next: ColorTable | null) => void;
}) {
  const updateEntry = (idx: number, patch: Partial<ColorTableEntry>) => {
    const next = table.entries.map((e, i) =>
      i === idx ? { ...e, ...patch } : e,
    );
    onChange({ ...table, entries: next });
  };

  const removeEntry = (idx: number) => {
    const next = table.entries.filter((_, i) => i !== idx);
    onChange({ ...table, entries: next });
  };

  const addEntry = () => {
    const lastHi = table.entries.length
      ? table.entries[table.entries.length - 1].hi
      : -1;
    const lo = Math.min(255, Math.max(0, lastHi + 1));
    const hi = Math.min(255, lo + 7);
    onChange({
      ...table,
      entries: [
        ...table.entries,
        { lo, hi, name: "", r: 255, g: 255, b: 255 },
      ],
    });
  };

  const setOffBelow = (val: number) => {
    if (!Number.isFinite(val)) return;
    const v = Math.max(0, Math.min(255, Math.round(val)));
    onChange({ ...table, off_below: v });
  };

  const sortedEntries = [...table.entries]
    .map((e, i) => ({ entry: e, idx: i }))
    .sort((a, b) => a.entry.lo - b.entry.lo);
  // Detect overlapping ranges (server rejects them; surface the issue here).
  const overlapping = new Set<number>();
  for (let i = 1; i < sortedEntries.length; i++) {
    if (sortedEntries[i].entry.lo <= sortedEntries[i - 1].entry.hi) {
      overlapping.add(sortedEntries[i].idx);
      overlapping.add(sortedEntries[i - 1].idx);
    }
  }

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1 text-muted">
          Off below
          <input
            type="number"
            min={0}
            max={255}
            className="input !w-20 !py-0.5"
            value={table.off_below ?? 0}
            onChange={(e) => setOffBelow(Number(e.target.value))}
          />
          <span className="text-[10px]">
            (0–255, on dimmerless fixtures only)
          </span>
        </label>
      </div>

      <div className="space-y-1">
        <div className="grid grid-cols-[44px_44px_minmax(120px,1fr)_180px_36px] items-center gap-2 px-1 text-[10px] uppercase tracking-wider text-muted">
          <span>lo</span>
          <span>hi</span>
          <span>label</span>
          <span>color</span>
          <span></span>
        </div>
        {table.entries.length === 0 && (
          <div className="rounded-md bg-bg-card px-3 py-2 text-xs text-muted ring-1 ring-line">
            No entries yet — add the manufacturer's documented byte ranges.
          </div>
        )}
        {table.entries.map((entry, idx) => (
          <EntryRow
            key={idx}
            entry={entry}
            warn={overlapping.has(idx)}
            onChange={(patch) => updateEntry(idx, patch)}
            onRemove={() => removeEntry(idx)}
          />
        ))}
      </div>

      <button
        type="button"
        className="btn-ghost !px-2 !py-1 text-xs"
        onClick={addEntry}
      >
        + add entry
      </button>
    </div>
  );
}

const rgbToHex = (r: number, g: number, b: number) =>
  "#" +
  [r, g, b]
    .map((c) =>
      Math.max(0, Math.min(255, Math.round(c)))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("");

const hexToRgb = (
  hex: string,
): { r: number; g: number; b: number } | null => {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return null;
  const v = parseInt(m[1], 16);
  return { r: (v >> 16) & 0xff, g: (v >> 8) & 0xff, b: v & 0xff };
};

function EntryRow({
  entry,
  warn,
  onChange,
  onRemove,
}: {
  entry: ColorTableEntry;
  warn: boolean;
  onChange: (patch: Partial<ColorTableEntry>) => void;
  onRemove: () => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const hex = rgbToHex(entry.r, entry.g, entry.b);

  const setRange = (key: "lo" | "hi", v: number) => {
    if (!Number.isFinite(v)) return;
    const clamped = Math.max(0, Math.min(255, Math.round(v)));
    onChange({ [key]: clamped } as Partial<ColorTableEntry>);
  };

  const setHex = (h: string) => {
    const rgb = hexToRgb(h);
    if (rgb) onChange(rgb);
  };

  const rangeBad = entry.lo > entry.hi;

  return (
    <div
      className={
        "grid grid-cols-[44px_44px_minmax(120px,1fr)_180px_36px] items-center gap-2 rounded-md px-1 py-1 ring-1 " +
        (warn || rangeBad
          ? "bg-amber-950/30 ring-amber-700/40"
          : "bg-bg-card ring-line")
      }
    >
      <input
        type="number"
        min={0}
        max={255}
        className="input !py-0.5 text-xs"
        value={entry.lo}
        onChange={(e) => setRange("lo", Number(e.target.value))}
      />
      <input
        type="number"
        min={0}
        max={255}
        className="input !py-0.5 text-xs"
        value={entry.hi}
        onChange={(e) => setRange("hi", Number(e.target.value))}
      />
      <input
        className="input !py-0.5 text-xs"
        value={entry.name ?? ""}
        placeholder="(unnamed)"
        onChange={(e) => onChange({ name: e.target.value })}
        maxLength={32}
      />
      <div className="relative flex items-center gap-2">
        <button
          type="button"
          className="h-6 w-10 rounded ring-1 ring-line"
          style={{ background: hex }}
          onClick={() => setPickerOpen((o) => !o)}
          aria-label="Edit color"
        />
        <input
          className="input !py-0.5 font-mono text-[11px] uppercase"
          value={hex}
          onChange={(e) => setHex(e.target.value)}
          spellCheck={false}
        />
        {pickerOpen && (
          <div
            className="absolute left-0 top-full z-20 mt-1 rounded-lg bg-bg-elev p-2 shadow-lg ring-1 ring-line"
            onMouseLeave={() => setPickerOpen(false)}
          >
            <HexColorPicker color={hex} onChange={setHex} />
          </div>
        )}
      </div>
      <button
        type="button"
        className="btn-ghost !px-2 !py-1 text-xs text-rose-300"
        onClick={onRemove}
        title="Remove entry"
      >
        ×
      </button>
    </div>
  );
}
