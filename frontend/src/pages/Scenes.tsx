import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api, Controller, Scene } from "../api";
import Modal from "../components/Modal";
import { useToast } from "../toast";

type CreateForm = {
  name: string;
  controllerId: number | null;
  crossController: boolean;
  fromRendered: boolean;
};

const EMPTY_FORM: CreateForm = {
  name: "",
  controllerId: null,
  crossController: false,
  fromRendered: false,
};

export default function Scenes() {
  const toast = useToast();
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>({ ...EMPTY_FORM });
  const [editing, setEditing] = useState<Scene | null>(null);
  const [editName, setEditName] = useState("");
  const [editController, setEditController] = useState<number | null>(null);
  const [editCross, setEditCross] = useState(false);

  const refresh = async () => {
    try {
      const [c, s] = await Promise.all([Api.listControllers(), Api.listScenes()]);
      setControllers(c);
      setScenes(s);
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

  const controllerById = useMemo(() => {
    const m = new Map<number, Controller>();
    controllers.forEach((c) => m.set(c.id, c));
    return m;
  }, [controllers]);

  const scenesByController = useMemo(() => {
    const byCtrl = new Map<number, Scene[]>();
    for (const c of controllers) byCtrl.set(c.id, []);
    const blackouts: Scene[] = [];
    const saved: Scene[] = [];
    for (const s of scenes) {
      if (s.builtin && s.id === null) {
        blackouts.push(s);
      } else {
        saved.push(s);
      }
    }
    for (const s of blackouts) {
      const list = byCtrl.get(s.controller_id);
      if (list) list.push(s);
    }
    for (const s of saved) {
      const list = byCtrl.get(s.controller_id) ?? [];
      list.push(s);
      byCtrl.set(s.controller_id, list);
    }
    return byCtrl;
  }, [scenes, controllers]);

  const openCreate = () => {
    const firstCtrl = controllers[0]?.id ?? null;
    setCreateForm({ ...EMPTY_FORM, controllerId: firstCtrl });
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const name = createForm.name.trim();
    if (!name) return;
    if (createForm.controllerId === null) {
      toast.push("Pick a controller", "error");
      return;
    }
    try {
      await Api.createScene({
        name,
        controller_id: createForm.controllerId,
        cross_controller: createForm.crossController,
        from_rendered: createForm.fromRendered,
      });
      toast.push(`Saved "${name}"`, "success");
      setCreateOpen(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const openEdit = (s: Scene) => {
    if (s.id === null) return;
    setEditing(s);
    setEditName(s.name);
    setEditController(s.controller_id);
    setEditCross(s.cross_controller);
  };

  const submitEdit = async () => {
    if (editing === null || editing.id === null) return;
    const name = editName.trim();
    if (!name) return;
    try {
      await Api.updateScene(editing.id, {
        name,
        controller_id: editController ?? undefined,
        cross_controller: editCross,
      });
      toast.push("Scene updated", "success");
      setEditing(null);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const recapture = async (s: Scene) => {
    if (s.id === null) return;
    if (!confirm(`Replace "${s.name}" with the current state?`)) return;
    try {
      await Api.updateScene(s.id, { recapture: true });
      toast.push(`Re-captured "${s.name}"`, "success");
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const remove = async (s: Scene) => {
    if (s.id === null) return;
    if (!confirm(`Delete scene "${s.name}"?`)) return;
    try {
      await Api.deleteScene(s.id);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const apply = async (s: Scene) => {
    const controllerName =
      controllerById.get(s.controller_id)?.name ?? `controller ${s.controller_id}`;
    if (!confirm(`Apply "${s.name}" to ${controllerName}?`)) return;
    try {
      if (s.id === null) {
        await Api.applyBlackoutScene(s.controller_id);
      } else {
        await Api.applyScene(s.id);
      }
      toast.push(`Applied "${s.name}"`, "success");
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  if (loading) {
    return <div className="text-muted">Loading...</div>;
  }

  if (controllers.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-3 p-8 text-center">
        <div className="text-lg font-semibold">No controllers yet</div>
        <p className="text-sm text-muted">
          Scenes are saved per controller. Add a controller first.
        </p>
        <Link to="/controllers" className="btn-primary">
          Add controller
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">Scenes</h1>
          <p className="text-sm text-muted">
            Snapshots of light state you can recall from the Lights page.
          </p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          Save current state…
        </button>
      </div>

      {controllers.map((c) => {
        const list = scenesByController.get(c.id) ?? [];
        return (
          <section key={c.id} className="space-y-2">
            <header className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">{c.name}</h2>
              <span className="pill">
                {c.ip}:{c.port}
              </span>
              <span className="pill">
                U {c.net}:{c.subnet}:{c.universe}
              </span>
            </header>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {list.length === 0 && (
                <div className="card p-3 text-xs text-muted">
                  No scenes for this controller yet.
                </div>
              )}
              {list.map((s) => (
                <div
                  key={s.id === null ? `blackout-${c.id}` : `scene-${s.id}`}
                  className="card flex items-center justify-between gap-2 p-3"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{s.name}</span>
                      {s.builtin && (
                        <span className="pill text-[10px]">built-in</span>
                      )}
                      {s.cross_controller && (
                        <span className="pill text-[10px]">multi-controller</span>
                      )}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted">
                      {s.lights.length} light
                      {s.lights.length === 1 ? "" : "s"} captured
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      className="btn-primary text-xs"
                      onClick={() => apply(s)}
                    >
                      Apply
                    </button>
                    {!s.builtin && (
                      <>
                        <button
                          className="btn-ghost text-xs"
                          onClick={() => recapture(s)}
                          title="Replace the snapshot with the current state"
                        >
                          Re-capture
                        </button>
                        <button
                          className="btn-ghost text-xs"
                          onClick={() => openEdit(s)}
                        >
                          Edit
                        </button>
                        <button
                          className="btn-ghost text-xs text-rose-300 hover:bg-rose-950"
                          onClick={() => remove(s)}
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        );
      })}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Save current state"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={submitCreate}
              disabled={
                !createForm.name.trim() || createForm.controllerId === null
              }
            >
              Save
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="label mb-1 block !text-xs normal-case tracking-normal">
              Name
            </label>
            <input
              className="input w-full"
              value={createForm.name}
              autoFocus
              placeholder="Evening wash"
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, name: e.target.value }))
              }
            />
          </div>
          <div>
            <label className="label mb-1 block !text-xs normal-case tracking-normal">
              Controller
            </label>
            <select
              className="input w-full"
              value={createForm.controllerId ?? ""}
              onChange={(e) =>
                setCreateForm((f) => ({
                  ...f,
                  controllerId:
                    e.target.value === "" ? null : Number(e.target.value),
                }))
              }
            >
              {controllers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={createForm.crossController}
              onChange={(e) =>
                setCreateForm((f) => ({
                  ...f,
                  crossController: e.target.checked,
                }))
              }
            />
            Include lights on every controller (multi-controller scene)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={createForm.fromRendered}
              onChange={(e) =>
                setCreateForm((f) => ({
                  ...f,
                  fromRendered: e.target.checked,
                }))
              }
            />
            Capture the live rendered output (freezes running effects)
          </label>
        </div>
      </Modal>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Edit "${editing.name}"` : "Edit scene"}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={submitEdit}
              disabled={!editName.trim()}
            >
              Save
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="label mb-1 block !text-xs normal-case tracking-normal">
              Name
            </label>
            <input
              className="input w-full"
              value={editName}
              autoFocus
              onChange={(e) => setEditName(e.target.value)}
            />
          </div>
          <div>
            <label className="label mb-1 block !text-xs normal-case tracking-normal">
              Controller
            </label>
            <select
              className="input w-full"
              value={editController ?? ""}
              onChange={(e) =>
                setEditController(
                  e.target.value === "" ? null : Number(e.target.value),
                )
              }
            >
              {controllers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={editCross}
              onChange={(e) => setEditCross(e.target.checked)}
            />
            Multi-controller scene
          </label>
        </div>
      </Modal>
    </div>
  );
}
