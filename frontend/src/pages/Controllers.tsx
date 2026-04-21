import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Api, Controller, Light } from "../api";
import Modal from "../components/Modal";
import { useToast } from "../toast";

type CtrlForm = Omit<Controller, "id">;

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
  const [lights, setLights] = useState<Light[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Controller | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<CtrlForm>(emptyCtrl);

  const refresh = async () => {
    try {
      const [c, l] = await Promise.all([
        Api.listControllers(),
        Api.listLights(),
      ]);
      setControllers(c);
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
            let plural = "s";
            if (clights.length === 1) plural = "";
            return (
              <div key={c.id} className="card p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/controllers/${c.id}`}
                        className="truncate font-semibold hover:text-accent"
                      >
                        {c.name}
                      </Link>
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
                  <Link
                    to={`/controllers/${c.id}`}
                    className="btn-secondary w-full justify-center"
                  >
                    Open ({clights.length} light{plural})
                  </Link>
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
