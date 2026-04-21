import React, { useEffect, useState } from "react";
import { Api, Palette } from "../api";
import Modal from "../components/Modal";
import PaletteSwatch from "../components/PaletteSwatch";
import ColorPicker from "../components/ColorPicker";
import { useToast } from "../toast";

type Form = { name: string; colors: string[] };

export default function Palettes() {
  const toast = useToast();
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Palette | null>(null);
  const [form, setForm] = useState<Form>({ name: "", colors: ["#7C4DFF"] });
  const [activeIdx, setActiveIdx] = useState(0);

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
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openCreate = () => {
    setForm({ name: "", colors: ["#7C4DFF"] });
    setEditing(null);
    setActiveIdx(0);
    setOpen(true);
  };

  const openEdit = (p: Palette) => {
    setForm({ name: p.name, colors: [...p.colors] });
    setEditing(p);
    setActiveIdx(0);
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
      if (editing) {
        await Api.updatePalette(editing.id, form);
        toast.push("Palette updated", "success");
      } else {
        await Api.createPalette(form);
        toast.push("Palette created", "success");
      }
      setOpen(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const updateColor = (idx: number, hex: string) => {
    const next = [...form.colors];
    next[idx] = hex.toUpperCase();
    setForm({ ...form, colors: next });
  };

  const addColor = () => {
    const next = [...form.colors, "#FFFFFF"];
    setForm({ ...form, colors: next });
    setActiveIdx(next.length - 1);
  };

  const removeColor = (idx: number) => {
    if (form.colors.length <= 1) return;
    const next = form.colors.slice();
    next.splice(idx, 1);
    setForm({ ...form, colors: next });
    if (activeIdx >= next.length) setActiveIdx(next.length - 1);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Palettes</h1>
          <p className="text-sm text-muted">
            Curated color sets you can apply to selected lights.
          </p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          New palette
        </button>
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
                  {p.builtin && <span className="pill text-[10px]">built-in</span>}
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
                <PaletteSwatch colors={p.colors} className="h-10" />
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
              disabled={!form.name || form.colors.length === 0}
            >
              Save
            </button>
          </>
        }
      >
        <form id="palette-form" onSubmit={submit} className="space-y-4">
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
            <PaletteSwatch colors={form.colors} className="h-10" />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="label mb-1">Colors</div>
              <div className="space-y-1 rounded-lg bg-bg-elev p-2 ring-1 ring-line">
                {form.colors.map((c, i) => (
                  <div
                    key={i}
                    className={
                      "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 ring-1 ring-line " +
                      (i === activeIdx ? "bg-bg-card" : "bg-bg-elev hover:bg-bg-card")
                    }
                    onClick={() => setActiveIdx(i)}
                  >
                    <span
                      className="h-5 w-5 rounded ring-1 ring-line"
                      style={{ background: c }}
                    />
                    <input
                      className="input !py-1 font-mono uppercase"
                      value={c}
                      onChange={(e) => updateColor(i, e.target.value)}
                      onFocus={() => setActiveIdx(i)}
                    />
                    <button
                      type="button"
                      className="btn-ghost !px-2 !py-1 text-rose-300"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeColor(i);
                      }}
                      disabled={form.colors.length <= 1}
                    >
                      ×
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="btn-secondary mt-1 w-full"
                  onClick={addColor}
                >
                  + Add color
                </button>
              </div>
            </div>
            <div>
              <div className="label mb-1">
                Edit color #{activeIdx + 1}
              </div>
              <ColorPicker
                value={form.colors[activeIdx] ?? "#FFFFFF"}
                onChange={(hex) => updateColor(activeIdx, hex)}
              />
            </div>
          </div>
        </form>
      </Modal>
    </div>
  );
}
