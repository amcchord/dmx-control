import React, { useEffect, useMemo, useState } from "react";
import { Api, Palette, PaletteEntry } from "../api";
import Modal from "../components/Modal";
import PaletteSwatch from "../components/PaletteSwatch";
import ColorPicker from "../components/ColorPicker";
import { useToast } from "../toast";

type Form = {
  name: string;
  entries: PaletteEntry[];
};

function hexFor(entry: PaletteEntry): string {
  const h = (n: number) => n.toString(16).padStart(2, "0").toUpperCase();
  return `#${h(entry.r)}${h(entry.g)}${h(entry.b)}`;
}

function parseHex(hex: string): [number, number, number] {
  const s = hex.startsWith("#") ? hex.slice(1) : hex;
  return [
    parseInt(s.slice(0, 2), 16) || 0,
    parseInt(s.slice(2, 4), 16) || 0,
    parseInt(s.slice(4, 6), 16) || 0,
  ];
}

function formFromPalette(p: Palette): Form {
  if (p.entries && p.entries.length > 0) {
    return {
      name: p.name,
      entries: p.entries.map((e) => ({ ...e })),
    };
  }
  return {
    name: p.name,
    entries: (p.colors || []).map((c) => {
      const [r, g, b] = parseHex(c);
      return { r, g, b };
    }),
  };
}

function hexColors(entries: PaletteEntry[]): string[] {
  return entries.map((e) => hexFor(e));
}

export default function Palettes() {
  const toast = useToast();
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Palette | null>(null);
  const [form, setForm] = useState<Form>({
    name: "",
    entries: [{ r: 124, g: 77, b: 255 }],
  });
  const [activeIdx, setActiveIdx] = useState(0);

  const [aiEnabled, setAiEnabled] = useState(false);
  const [genOpen, setGenOpen] = useState(false);
  const [genPrompt, setGenPrompt] = useState("");
  const [genIncludeAux, setGenIncludeAux] = useState(false);
  const [genNumColors, setGenNumColors] = useState<number | "">("");
  const [genBusy, setGenBusy] = useState(false);
  const [genSummary, setGenSummary] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setPalettes(await Api.listPalettes());
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // Claude status for the "Generate" button.
    Api.designer
      .status()
      .then((s) => setAiEnabled(!!s.enabled))
      .catch(() => setAiEnabled(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openCreate = () => {
    setForm({
      name: "",
      entries: [{ r: 124, g: 77, b: 255 }],
    });
    setEditing(null);
    setActiveIdx(0);
    setGenSummary(null);
    setOpen(true);
  };

  const openEdit = (p: Palette) => {
    setForm(formFromPalette(p));
    setEditing(p);
    setActiveIdx(0);
    setGenSummary(null);
    setOpen(true);
  };

  const clone = async (p: Palette) => {
    try {
      await Api.clonePalette(p.id);
      await refresh();
      toast.push("Cloned", "success");
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const remove = async (p: Palette) => {
    if (!confirm(`Delete palette "${p.name}"?`)) return;
    try {
      await Api.deletePalette(p.id);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const body = {
        name: form.name,
        entries: form.entries,
        colors: hexColors(form.entries),
      };
      if (editing) {
        await Api.updatePalette(editing.id, body);
        toast.push("Palette updated", "success");
      } else {
        await Api.createPalette(body);
        toast.push("Palette created", "success");
      }
      setOpen(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const updateEntry = (idx: number, next: Partial<PaletteEntry>) => {
    setForm((f) => {
      const arr = [...f.entries];
      arr[idx] = { ...arr[idx], ...next };
      return { ...f, entries: arr };
    });
  };

  const updateHex = (idx: number, hex: string) => {
    const [r, g, b] = parseHex(hex);
    updateEntry(idx, { r, g, b });
  };

  const addColor = () => {
    setForm((f) => {
      const arr = [...f.entries, { r: 255, g: 255, b: 255 }];
      setActiveIdx(arr.length - 1);
      return { ...f, entries: arr };
    });
  };

  const removeColor = (idx: number) => {
    if (form.entries.length <= 1) return;
    const arr = form.entries.slice();
    arr.splice(idx, 1);
    setForm({ ...form, entries: arr });
    if (activeIdx >= arr.length) setActiveIdx(arr.length - 1);
  };

  async function runGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!genPrompt.trim()) return;
    setGenBusy(true);
    try {
      const res = await Api.generatePalette({
        prompt: genPrompt.trim(),
        num_colors: typeof genNumColors === "number" ? genNumColors : undefined,
        include_aux: genIncludeAux || undefined,
      });
      setForm({
        name: res.name,
        entries: res.entries.map((e) => ({ ...e })),
      });
      setEditing(null);
      setActiveIdx(0);
      setGenSummary(res.summary ?? null);
      setGenOpen(false);
      setOpen(true);
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setGenBusy(false);
    }
  }

  const active = form.entries[activeIdx] ?? form.entries[0];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Palettes</h1>
          <p className="text-sm text-muted">
            Curated color sets. Each color can carry explicit W / A / UV
            values that drive the aux channels directly.
          </p>
        </div>
        <div className="flex gap-2">
          {aiEnabled && (
            <button
              className="btn-secondary"
              onClick={() => {
                setGenPrompt("");
                setGenSummary(null);
                setGenOpen(true);
              }}
            >
              Generate with Claude
            </button>
          )}
          <button className="btn-primary" onClick={openCreate}>
            New palette
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-muted">Loading...</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {palettes.map((p) => (
            <div key={p.id} className="card p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold">{p.name}</h3>
                  {p.builtin && (
                    <span className="pill text-[10px]">built-in</span>
                  )}
                </div>
                <div className="flex gap-1">
                  <button className="btn-ghost" onClick={() => clone(p)}>
                    Clone
                  </button>
                  {!p.builtin && (
                    <>
                      <button className="btn-ghost" onClick={() => openEdit(p)}>
                        Edit
                      </button>
                      <button
                        className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
                        onClick={() => remove(p)}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="mt-3">
                <PaletteSwatch
                  colors={p.colors}
                  entries={p.entries}
                  className="h-10"
                />
                <div className="mt-2 flex flex-wrap gap-1 text-[11px] font-mono text-muted">
                  {p.colors.map((c, i) => (
                    <span key={i}>{c}</span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? `Edit ${editing.name}` : "New palette"}
        size="lg"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-primary"
              type="submit"
              form="palette-form"
              disabled={!form.name || form.entries.length === 0}
            >
              Save
            </button>
          </>
        }
      >
        <form id="palette-form" onSubmit={submit} className="space-y-4">
          {genSummary && (
            <div className="rounded-md bg-accent/10 p-2 text-xs text-slate-200 ring-1 ring-accent/40">
              {genSummary}
            </div>
          )}
          <label className="block">
            <span className="label mb-1 block">Name</span>
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </label>
          <div>
            <div className="label mb-1">Preview</div>
            <PaletteSwatch
              colors={hexColors(form.entries)}
              entries={form.entries}
              className="h-10"
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_1fr]">
            <div>
              <div className="label mb-1">Colors</div>
              <div className="space-y-1 rounded-lg bg-bg-elev p-2 ring-1 ring-line">
                {form.entries.map((entry, i) => {
                  const hex = hexFor(entry);
                  return (
                    <div
                      key={i}
                      className={
                        "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 ring-1 ring-line " +
                        (i === activeIdx
                          ? "bg-bg-card"
                          : "bg-bg-elev hover:bg-bg-card")
                      }
                      onClick={() => setActiveIdx(i)}
                    >
                      <span
                        className="h-5 w-5 rounded ring-1 ring-line"
                        style={{ background: hex }}
                      />
                      <input
                        className="input !py-1 font-mono uppercase"
                        value={hex}
                        onChange={(e) => updateHex(i, e.target.value)}
                        onFocus={() => setActiveIdx(i)}
                      />
                      <button
                        type="button"
                        className="btn-ghost !px-2 !py-1 text-rose-300"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeColor(i);
                        }}
                        disabled={form.entries.length <= 1}
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
                <button
                  type="button"
                  className="btn-secondary mt-1 w-full"
                  onClick={addColor}
                >
                  + Add color
                </button>
              </div>
            </div>
            <div className="space-y-3">
              <div>
                <div className="label mb-1">Edit color #{activeIdx + 1}</div>
                <ColorPicker
                  value={hexFor(active)}
                  onChange={(hex) => updateHex(activeIdx, hex)}
                />
              </div>
              <AuxRow
                label="White (W)"
                hint="Explicit white-channel value for fixtures that expose a W channel. Leave blank to derive from RGB (mix) or preserve the user's fader (direct)."
                value={active.w}
                onChange={(v) => updateEntry(activeIdx, { w: v })}
              />
              <AuxRow
                label="Amber (A)"
                hint="Explicit amber-channel value. Leave blank to derive from RGB."
                value={active.a}
                onChange={(v) => updateEntry(activeIdx, { a: v })}
              />
              <AuxRow
                label="UV / V"
                hint="Explicit UV (violet) channel value. Historically UV was never written by palette apply — setting it here fixes that."
                value={active.uv}
                onChange={(v) => updateEntry(activeIdx, { uv: v })}
              />
            </div>
          </div>
        </form>
      </Modal>

      <Modal
        open={genOpen}
        onClose={() => setGenOpen(false)}
        title="Generate palette with Claude"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setGenOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-primary"
              type="submit"
              form="gen-form"
              disabled={!genPrompt.trim() || genBusy}
            >
              {genBusy ? "Generating..." : "Generate"}
            </button>
          </>
        }
      >
        <form id="gen-form" className="space-y-3" onSubmit={runGenerate}>
          <label className="block">
            <span className="label mb-1 block">Prompt</span>
            <textarea
              className="input h-24"
              placeholder="e.g. dusky desert sunset with warm amber accents"
              value={genPrompt}
              onChange={(e) => setGenPrompt(e.target.value)}
              required
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="label mb-1 block">Num colors</span>
              <input
                className="input"
                type="number"
                min={2}
                max={16}
                value={genNumColors}
                placeholder="auto"
                onChange={(e) => {
                  const v = e.target.value;
                  setGenNumColors(v === "" ? "" : parseInt(v, 10));
                }}
              />
            </label>
            <label className="mt-6 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={genIncludeAux}
                onChange={(e) => setGenIncludeAux(e.target.checked)}
              />
              Include W / A / UV accents
            </label>
          </div>
          <div className="text-[11px] text-muted">
            You'll be able to edit and save the result before it's persisted.
          </div>
        </form>
      </Modal>
    </div>
  );
}

function AuxRow({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: number | null | undefined;
  onChange: (v: number | null) => void;
}) {
  const isSet = value != null;
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between">
        <span className="label !text-[11px] normal-case tracking-normal">
          {label}
        </span>
        <button
          type="button"
          className="text-[10px] text-muted underline-offset-2 hover:text-slate-200 hover:underline"
          onClick={() => onChange(isSet ? null : 0)}
        >
          {isSet ? "clear" : "set explicit"}
        </button>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="range"
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-bg-elev accent-accent"
          min={0}
          max={255}
          step={1}
          value={isSet ? (value as number) : 0}
          onChange={(e) => onChange(parseInt(e.target.value, 10))}
          disabled={!isSet}
        />
        <input
          type="number"
          className="input w-16 !py-1 text-xs"
          min={0}
          max={255}
          value={isSet ? (value as number) : ""}
          placeholder="auto"
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") onChange(null);
            else onChange(Math.max(0, Math.min(255, parseInt(v, 10) || 0)));
          }}
        />
      </div>
      <div className="text-[10px] text-muted">{hint}</div>
    </div>
  );
}
