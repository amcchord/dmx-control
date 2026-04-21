import React, { useEffect, useState } from "react";
import { Api, LightModel } from "../api";
import Modal from "../components/Modal";
import { useToast } from "../toast";

const ROLES = [
  "r",
  "g",
  "b",
  "w",
  "a",
  "uv",
  "dimmer",
  "strobe",
  "macro",
  "speed",
  "pan",
  "tilt",
  "other",
] as const;

const ROLE_COLORS: Record<string, string> = {
  r: "#ff4d4d",
  g: "#4dff6a",
  b: "#4d6aff",
  w: "#f5f5f5",
  a: "#ffb23d",
  uv: "#b44dff",
  dimmer: "#cfcfcf",
  strobe: "#fff566",
  macro: "#8791a7",
  speed: "#8791a7",
  pan: "#8791a7",
  tilt: "#8791a7",
  other: "#8791a7",
};

type Form = { name: string; channels: string[] };

export default function Models() {
  const toast = useToast();
  const [models, setModels] = useState<LightModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<LightModel | null>(null);
  const [form, setForm] = useState<Form>({ name: "", channels: ["r", "g", "b"] });

  const refresh = async () => {
    try {
      setModels(await Api.listModels());
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
    setForm({ name: "", channels: ["r", "g", "b"] });
    setEditing(null);
    setOpen(true);
  };

  const openEdit = (m: LightModel) => {
    setForm({ name: m.name, channels: [...m.channels] });
    setEditing(m);
    setOpen(true);
  };

  const clone = async (m: LightModel) => {
    try {
      await Api.cloneModel(m.id);
      await refresh();
      toast.push("Cloned", "success");
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const remove = async (m: LightModel) => {
    if (!confirm(`Delete model "${m.name}"?`)) return;
    try {
      await Api.deleteModel(m.id);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (editing) {
        await Api.updateModel(editing.id, form);
        toast.push("Model updated", "success");
      } else {
        await Api.createModel(form);
        toast.push("Model created", "success");
      }
      setOpen(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const moveChannel = (index: number, dir: -1 | 1) => {
    const next = [...form.channels];
    const j = index + dir;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j], next[index]];
    setForm({ ...form, channels: next });
  };

  const removeChannel = (index: number) => {
    const next = [...form.channels];
    next.splice(index, 1);
    setForm({ ...form, channels: next });
  };

  const addChannel = (role: string) => {
    setForm({ ...form, channels: [...form.channels, role] });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Light Models</h1>
          <p className="text-sm text-muted">Channel layouts for your fixtures.</p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          Add model
        </button>
      </div>

      {loading ? (
        <div className="text-muted">Loading...</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {models.map((m) => (
            <div key={m.id} className="card p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold">{m.name}</h3>
                    {m.builtin && <span className="pill text-[10px]">built-in</span>}
                  </div>
                  <div className="mt-1 text-xs text-muted">
                    {m.channel_count} channel{m.channel_count === 1 ? "" : "s"}
                  </div>
                </div>
                <div className="flex gap-1">
                  {m.builtin ? (
                    <button className="btn-ghost" onClick={() => clone(m)}>
                      Clone
                    </button>
                  ) : (
                    <>
                      <button className="btn-ghost" onClick={() => openEdit(m)}>
                        Edit
                      </button>
                      <button
                        className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
                        onClick={() => remove(m)}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {m.channels.map((c, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded-md bg-bg-elev px-2 py-1 text-xs ring-1 ring-line"
                  >
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ background: ROLE_COLORS[c] ?? "#8791a7" }}
                    />
                    <span className="font-mono">
                      {i + 1}. {c}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? `Edit ${editing.name}` : "New model"}
        size="lg"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-primary"
              type="submit"
              form="model-form"
              disabled={!form.name || form.channels.length === 0}
            >
              Save
            </button>
          </>
        }
      >
        <form id="model-form" onSubmit={submit} className="space-y-4">
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
            <div className="label mb-1">Channels ({form.channels.length})</div>
            <div className="space-y-1 rounded-lg bg-bg-elev p-2 ring-1 ring-line">
              {form.channels.length === 0 && (
                <div className="px-2 py-1 text-xs text-muted">No channels yet.</div>
              )}
              {form.channels.map((c, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-md bg-bg-card px-2 py-1.5 ring-1 ring-line"
                >
                  <span className="w-8 text-right font-mono text-xs text-muted">
                    {i + 1}
                  </span>
                  <span
                    className="h-3 w-3 rounded-full"
                    style={{ background: ROLE_COLORS[c] ?? "#8791a7" }}
                  />
                  <select
                    className="input !py-1"
                    value={c}
                    onChange={(e) => {
                      const next = [...form.channels];
                      next[i] = e.target.value;
                      setForm({ ...form, channels: next });
                    }}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn-ghost !px-2 !py-1"
                    onClick={() => moveChannel(i, -1)}
                    disabled={i === 0}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn-ghost !px-2 !py-1"
                    onClick={() => moveChannel(i, 1)}
                    disabled={i === form.channels.length - 1}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="btn-ghost !px-2 !py-1 text-rose-300"
                    onClick={() => removeChannel(i)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {ROLES.map((r) => (
                <button
                  type="button"
                  key={r}
                  className="rounded-full bg-bg-elev px-2.5 py-1 text-xs ring-1 ring-line hover:bg-bg-card"
                  onClick={() => addChannel(r)}
                >
                  + {r}
                </button>
              ))}
            </div>
          </div>
        </form>
      </Modal>
    </div>
  );
}
