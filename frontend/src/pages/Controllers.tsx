import React, { useEffect, useMemo, useState } from "react";
import { Api, Controller, Light, LightModel } from "../api";
import Modal from "../components/Modal";
import { useToast } from "../toast";

type CtrlForm = Omit<Controller, "id">;
type LightForm = {
  name: string;
  controller_id: number;
  model_id: number;
  start_address: number;
};

const emptyCtrl: CtrlForm = {
  name: "",
  ip: "",
  port: 6454,
  net: 0,
  subnet: 0,
  universe: 0,
  enabled: true,
};

export default function Controllers() {
  const toast = useToast();
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [models, setModels] = useState<LightModel[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Controller | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<CtrlForm>(emptyCtrl);
  const [lightFor, setLightFor] = useState<Controller | null>(null);

  const refresh = async () => {
    try {
      const [c, m, l] = await Promise.all([
        Api.listControllers(),
        Api.listModels(),
        Api.listLights(),
      ]);
      setControllers(c);
      setModels(m);
      setLights(l);
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
    setForm(emptyCtrl);
    setEditing(null);
    setCreating(true);
  };

  const openEdit = (c: Controller) => {
    const { id: _id, ...rest } = c;
    setForm(rest);
    setEditing(c);
    setCreating(true);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (editing) {
        await Api.updateController(editing.id, form);
        toast.push("Controller updated", "success");
      } else {
        await Api.createController(form);
        toast.push("Controller created", "success");
      }
      setCreating(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const remove = async (c: Controller) => {
    if (!confirm(`Delete controller "${c.name}"? Its lights will also be removed.`)) return;
    try {
      const cLights = lights.filter((l) => l.controller_id === c.id);
      for (const l of cLights) await Api.deleteLight(l.id);
      await Api.deleteController(c.id);
      toast.push("Controller deleted", "success");
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Controllers</h1>
          <p className="text-sm text-muted">Art-Net nodes that receive DMX.</p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          Add controller
        </button>
      </div>

      {loading ? (
        <div className="text-muted">Loading...</div>
      ) : controllers.length === 0 ? (
        <div className="card p-8 text-center">
          <div className="font-medium">No controllers yet</div>
          <div className="mt-1 text-sm text-muted">
            Add your first Art-Net controller to start sending DMX.
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {controllers.map((c) => {
            const clights = lights.filter((l) => l.controller_id === c.id);
            return (
              <div key={c.id} className="card p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate font-semibold">{c.name}</h3>
                      {!c.enabled && (
                        <span className="pill bg-rose-950 text-rose-300 ring-rose-900">
                          disabled
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1 text-xs text-muted">
                      <span className="pill">{c.ip}:{c.port}</span>
                      <span className="pill">net {c.net}</span>
                      <span className="pill">subnet {c.subnet}</span>
                      <span className="pill">universe {c.universe}</span>
                      <span className="pill">{clights.length} lights</span>
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button className="btn-ghost" onClick={() => openEdit(c)}>
                      Edit
                    </button>
                    <button
                      className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
                      onClick={() => remove(c)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                <div className="mt-3">
                  <button
                    className="btn-secondary w-full"
                    onClick={() => setLightFor(c)}
                  >
                    Manage {clights.length} light{clights.length === 1 ? "" : "s"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title={editing ? `Edit ${editing.name}` : "Add controller"}
        size="md"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button className="btn-primary" form="ctrl-form" type="submit">
              Save
            </button>
          </>
        }
      >
        <form id="ctrl-form" onSubmit={submit} className="space-y-3">
          <Field label="Name">
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="IP" className="col-span-2">
              <input
                className="input"
                value={form.ip}
                onChange={(e) => setForm({ ...form, ip: e.target.value })}
                placeholder="192.168.1.100"
                required
              />
            </Field>
            <Field label="Port">
              <input
                className="input"
                type="number"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
              />
            </Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Net">
              <input
                className="input"
                type="number"
                min={0}
                max={127}
                value={form.net}
                onChange={(e) => setForm({ ...form, net: Number(e.target.value) })}
              />
            </Field>
            <Field label="Subnet">
              <input
                className="input"
                type="number"
                min={0}
                max={15}
                value={form.subnet}
                onChange={(e) => setForm({ ...form, subnet: Number(e.target.value) })}
              />
            </Field>
            <Field label="Universe">
              <input
                className="input"
                type="number"
                min={0}
                max={15}
                value={form.universe}
                onChange={(e) => setForm({ ...form, universe: Number(e.target.value) })}
              />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-200">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-line bg-bg-elev text-accent"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            />
            Enabled
          </label>
        </form>
      </Modal>

      <LightManagerModal
        controller={lightFor}
        onClose={() => setLightFor(null)}
        models={models}
        lights={lights.filter((l) => lightFor && l.controller_id === lightFor.id)}
        onChanged={refresh}
      />
    </div>
  );
}

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={"block " + className}>
      <span className="label mb-1 block">{label}</span>
      {children}
    </label>
  );
}

function LightManagerModal({
  controller,
  onClose,
  models,
  lights,
  onChanged,
}: {
  controller: Controller | null;
  onClose: () => void;
  models: LightModel[];
  lights: Light[];
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState<Light | null>(null);
  const [form, setForm] = useState<LightForm>({
    name: "",
    controller_id: 0,
    model_id: models[0]?.id ?? 0,
    start_address: 1,
  });
  const [showForm, setShowForm] = useState(false);
  const [count, setCount] = useState(1);

  const reset = () => {
    setForm({
      name: "",
      controller_id: controller?.id ?? 0,
      model_id: models[0]?.id ?? 0,
      start_address: nextFreeAddress(lights, models),
    });
    setEditing(null);
    setCount(1);
  };

  const openCreate = () => {
    reset();
    setShowForm(true);
  };

  const openEdit = (l: Light) => {
    setForm({
      name: l.name,
      controller_id: l.controller_id,
      model_id: l.model_id,
      start_address: l.start_address,
    });
    setCount(1);
    setEditing(l);
    setShowForm(true);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!controller) return;
    try {
      if (editing) {
        await Api.updateLight(editing.id, { ...form, controller_id: controller.id });
        toast.push("Light updated", "success");
      } else {
        const model = models.find((m) => m.id === form.model_id);
        const chanCount = model?.channel_count ?? 3;
        const n = Math.max(1, Math.min(128, count));
        for (let i = 0; i < n; i++) {
          const addr = form.start_address + i * chanCount;
          if (addr + chanCount - 1 > 512) break;
          await Api.createLight({
            name: n > 1 ? `${form.name} ${i + 1}` : form.name,
            controller_id: controller.id,
            model_id: form.model_id,
            start_address: addr,
          });
        }
        toast.push(`${n} light${n === 1 ? "" : "s"} created`, "success");
      }
      setShowForm(false);
      await onChanged();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const remove = async (l: Light) => {
    if (!confirm(`Delete "${l.name}"?`)) return;
    try {
      await Api.deleteLight(l.id);
      await onChanged();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  useEffect(() => {
    if (controller) reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller?.id, models.length, lights.length]);

  return (
    <Modal
      open={controller !== null}
      onClose={onClose}
      title={controller ? `Lights on ${controller.name}` : ""}
      size="lg"
    >
      {showForm ? (
        <form onSubmit={submit} className="space-y-3">
          <Field label="Name">
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Model">
              <select
                className="input"
                value={form.model_id}
                onChange={(e) => setForm({ ...form, model_id: Number(e.target.value) })}
              >
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} ({m.channel_count}ch)
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Start address (1-512)">
              <input
                className="input"
                type="number"
                min={1}
                max={512}
                value={form.start_address}
                onChange={(e) =>
                  setForm({ ...form, start_address: Number(e.target.value) })
                }
              />
            </Field>
          </div>
          {!editing && (
            <Field label="Count (auto-numbered & spaced by channel count)">
              <input
                className="input"
                type="number"
                min={1}
                max={128}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
              />
            </Field>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={() => setShowForm(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              {editing ? "Save" : "Create"}
            </button>
          </div>
        </form>
      ) : (
        <div className="space-y-3">
          <div className="flex justify-end">
            <button className="btn-primary" onClick={openCreate}>
              Add light
            </button>
          </div>
          {lights.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">
              No lights on this controller.
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg ring-1 ring-line">
              <table className="w-full text-sm">
                <thead className="bg-bg-elev text-left text-xs uppercase tracking-wide text-muted">
                  <tr>
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">Model</th>
                    <th className="px-3 py-2">Address</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {lights
                    .slice()
                    .sort((a, b) => a.start_address - b.start_address)
                    .map((l) => {
                      const m = models.find((x) => x.id === l.model_id);
                      return (
                        <tr key={l.id} className="border-t border-line">
                          <td className="px-3 py-2">{l.name}</td>
                          <td className="px-3 py-2 text-muted">
                            {m?.name ?? "?"}
                          </td>
                          <td className="px-3 py-2 font-mono">
                            {l.start_address}
                            {m ? `-${l.start_address + m.channel_count - 1}` : ""}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <button
                              className="btn-ghost mr-1"
                              onClick={() => openEdit(l)}
                            >
                              Edit
                            </button>
                            <button
                              className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
                              onClick={() => remove(l)}
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function nextFreeAddress(lights: Light[], models: LightModel[]): number {
  if (lights.length === 0) return 1;
  const byId = new Map(models.map((m) => [m.id, m.channel_count] as const));
  let max = 0;
  for (const l of lights) {
    const count = byId.get(l.model_id) ?? 3;
    max = Math.max(max, l.start_address + count - 1);
  }
  return Math.min(512, max + 1);
}
