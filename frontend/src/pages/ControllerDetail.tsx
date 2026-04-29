import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Api, Controller, Light, LightModel, LightModelMode } from "../api";
import { useToast } from "../toast";
import { rgbToHex } from "../util";
import LightColorPicker from "../components/LightColorPicker";
import Modal from "../components/Modal";

type LightForm = {
  name: string;
  controller_id: number;
  model_id: number;
  mode_id: number | null;
  start_address: number;
  notes: string | null;
};

function pickDefaultMode(m?: LightModel | null): LightModelMode | null {
  if (!m) return null;
  const def = m.modes.find((x) => x.is_default);
  if (def) return def;
  if (m.modes.length > 0) return m.modes[0];
  return null;
}

function nextFreeAddress(lights: Light[], models: LightModel[]): number {
  if (lights.length === 0) return 1;
  const modelById = new Map(models.map((m) => [m.id, m] as const));
  let max = 0;
  for (const l of lights) {
    const m = modelById.get(l.model_id);
    let mode: LightModelMode | undefined;
    if (m) {
      mode = m.modes.find((x) => x.id === l.mode_id);
      if (!mode) mode = m.modes.find((x) => x.is_default);
      if (!mode) mode = m.modes[0];
    }
    let count = 3;
    if (mode) count = mode.channel_count;
    else if (m) count = m.channel_count;
    max = Math.max(max, l.start_address + count - 1);
  }
  return Math.min(512, max + 1);
}

function nextPosition(lights: Light[]): number {
  let max = -1;
  for (const l of lights) {
    if (l.position > max) max = l.position;
  }
  return max + 1;
}

function moveItem<T>(arr: T[], fromIdx: number, toIdx: number): T[] {
  if (fromIdx === toIdx) return arr;
  const out = arr.slice();
  const [item] = out.splice(fromIdx, 1);
  out.splice(toIdx, 0, item);
  return out;
}

function BulbIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 18h6" />
      <path d="M10 21h4" />
      <path d="M12 3a7 7 0 0 1 4 12.7V17H8v-1.3A7 7 0 0 1 12 3z" />
    </svg>
  );
}

function DragHandleIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
    >
      <circle cx="7" cy="4" r="1.4" />
      <circle cx="7" cy="10" r="1.4" />
      <circle cx="7" cy="16" r="1.4" />
      <circle cx="13" cy="4" r="1.4" />
      <circle cx="13" cy="10" r="1.4" />
      <circle cx="13" cy="16" r="1.4" />
    </svg>
  );
}

function ModelThumb({
  model,
  size = "md",
}: {
  model?: LightModel | null;
  size?: "sm" | "md" | "lg";
}) {
  let cls = "h-10 w-10";
  if (size === "sm") cls = "h-8 w-8";
  if (size === "lg") cls = "h-16 w-16";
  if (model?.image_url) {
    return (
      <img
        src={model.image_url}
        alt=""
        className={
          cls +
          " shrink-0 rounded-md object-cover ring-1 ring-line bg-bg-elev"
        }
      />
    );
  }
  return (
    <div
      className={
        cls +
        " shrink-0 grid place-items-center rounded-md ring-1 ring-line bg-bg-elev text-muted"
      }
    >
      <BulbIcon className="h-1/2 w-1/2" />
    </div>
  );
}

function ColorSwatch({ light }: { light: Light }) {
  const hex = rgbToHex(light.r, light.g, light.b);
  let opacity = 1;
  if (!light.on) opacity = 0.25;
  else {
    const d = light.dimmer / 255;
    if (d < 0.15) opacity = 0.25;
    else opacity = d;
  }
  let title = hex;
  if (!light.on) title = `${hex} (off)`;
  return (
    <span
      className="inline-block h-5 w-5 rounded ring-1 ring-line"
      style={{ backgroundColor: hex, opacity }}
      title={title}
    />
  );
}

export default function ControllerDetail() {
  const params = useParams<{ id: string }>();
  const controllerId = Number(params.id);
  const toast = useToast();
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [models, setModels] = useState<LightModel[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [loading, setLoading] = useState(true);
  const [colorOpen, setColorOpen] = useState(false);

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

  const controller = useMemo(
    () => controllers.find((c) => c.id === controllerId) ?? null,
    [controllers, controllerId],
  );

  const controllerLights = useMemo(
    () => lights.filter((l) => l.controller_id === controllerId),
    [lights, controllerId],
  );

  if (loading) {
    return <div className="text-muted">Loading...</div>;
  }

  if (!controller) {
    return (
      <div className="card p-8 text-center">
        <div className="font-medium">Controller not found</div>
        <div className="mt-2">
          <Link className="btn-secondary" to="/controllers">
            Back to controllers
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/controllers"
          className="text-sm text-muted hover:text-slate-200"
        >
          &larr; Controllers
        </Link>
      </div>

      <div className="card p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-xl font-semibold">
                {controller.name}
              </h1>
              {!controller.enabled && (
                <span className="pill bg-rose-950 text-rose-300 ring-rose-900">
                  disabled
                </span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-1 text-xs text-muted">
              <span className="pill">
                {controller.ip}:{controller.port}
              </span>
              <span className="pill">net {controller.net}</span>
              <span className="pill">subnet {controller.subnet}</span>
              <span className="pill">universe {controller.universe}</span>
              <span className="pill">{controllerLights.length} lights</span>
            </div>
            {controller.notes && (
              <p className="mt-2 whitespace-pre-wrap text-xs text-muted">
                {controller.notes}
              </p>
            )}
          </div>
          <div className="shrink-0">
            <button
              className="btn-primary text-sm"
              disabled={controllerLights.length === 0}
              onClick={() => setColorOpen(true)}
              title="Color every fixture on this controller"
            >
              Set color
            </button>
          </div>
        </div>
      </div>

      <LightsPanel
        controller={controller}
        models={models}
        lights={controllerLights}
        onChanged={refresh}
      />

      <Modal
        open={colorOpen}
        onClose={() => setColorOpen(false)}
        title={`Color · ${controller.name} (${controllerLights.length} ${
          controllerLights.length === 1 ? "light" : "lights"
        })`}
        size="md"
      >
        {controllerLights.length > 0 && (
          <LightColorPicker
            lights={controllerLights}
            models={models}
            notify={(msg, kind) => toast.push(msg, kind)}
            onApplied={(updated) => {
              const byId = new Map(updated.map((l) => [l.id, l]));
              setLights((prev) => prev.map((l) => byId.get(l.id) ?? l));
            }}
          />
        )}
      </Modal>
    </div>
  );
}

function LightsPanel({
  controller,
  models,
  lights,
  onChanged,
}: {
  controller: Controller;
  models: LightModel[];
  lights: Light[];
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState<Light | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [count, setCount] = useState(1);
  const [lastAutoName, setLastAutoName] = useState<string>("");

  const firstModel = models[0];
  const firstMode = pickDefaultMode(firstModel);

  const [form, setForm] = useState<LightForm>({
    name: "",
    controller_id: controller.id,
    model_id: firstModel?.id ?? 0,
    mode_id: firstMode?.id ?? null,
    start_address: 1,
    notes: null,
  });

  const lightsById = useMemo(() => {
    const map = new Map<number, Light>();
    for (const l of lights) map.set(l.id, l);
    return map;
  }, [lights]);

  const baseOrderIds = useMemo(() => {
    const sorted = lights.slice().sort((a, b) => {
      if (a.position !== b.position) return a.position - b.position;
      return a.id - b.id;
    });
    return sorted.map((l) => l.id);
  }, [lights]);

  const [orderIds, setOrderIds] = useState<number[]>(baseOrderIds);
  const [dragId, setDragId] = useState<number | null>(null);
  const dragChanged = useRef(false);

  useEffect(() => {
    if (dragId !== null) return;
    setOrderIds(baseOrderIds);
  }, [baseOrderIds, dragId]);

  let activeModel: LightModel | undefined = models.find(
    (m) => m.id === form.model_id,
  );
  let activeMode: LightModelMode | undefined;
  if (activeModel) {
    activeMode = activeModel.modes.find((x) => x.id === form.mode_id);
    if (!activeMode) activeMode = activeModel.modes.find((x) => x.is_default);
    if (!activeMode) activeMode = activeModel.modes[0];
  }

  const resetForCreate = () => {
    const m0 = models[0];
    const defaultMode = pickDefaultMode(m0);
    const suggestedName = m0?.name ?? "";
    setForm({
      name: suggestedName,
      controller_id: controller.id,
      model_id: m0?.id ?? 0,
      mode_id: defaultMode?.id ?? null,
      start_address: nextFreeAddress(lights, models),
      notes: null,
    });
    setLastAutoName(suggestedName);
    setEditing(null);
    setCount(1);
  };

  const openCreate = () => {
    resetForCreate();
    setShowForm(true);
  };

  const openEdit = (l: Light) => {
    setForm({
      name: l.name,
      controller_id: l.controller_id,
      model_id: l.model_id,
      mode_id: l.mode_id,
      start_address: l.start_address,
      notes: l.notes ?? null,
    });
    setLastAutoName("");
    setCount(1);
    setEditing(l);
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    setEditing(null);
  };

  const onModelChange = (modelId: number) => {
    const m = models.find((x) => x.id === modelId);
    const defaultMode = pickDefaultMode(m);
    let nextName = form.name;
    if (!editing) {
      if (form.name === "" || form.name === lastAutoName) {
        nextName = m?.name ?? "";
        setLastAutoName(nextName);
      }
    }
    setForm({
      ...form,
      name: nextName,
      model_id: modelId,
      mode_id: defaultMode?.id ?? null,
    });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (editing) {
        await Api.updateLight(editing.id, {
          ...form,
          controller_id: controller.id,
          position: editing.position,
        });
        toast.push("Light updated", "success");
      } else {
        let chanCount = 3;
        if (activeMode) chanCount = activeMode.channel_count;
        else if (activeModel) chanCount = activeModel.channel_count;
        const n = Math.max(1, Math.min(128, count));
        const basePos = nextPosition(lights);
        for (let i = 0; i < n; i++) {
          const addr = form.start_address + i * chanCount;
          if (addr + chanCount - 1 > 512) break;
          let name = form.name;
          if (n > 1) name = `${form.name} ${i + 1}`;
          await Api.createLight({
            name,
            controller_id: controller.id,
            model_id: form.model_id,
            mode_id: form.mode_id,
            start_address: addr,
            position: basePos + i,
          });
        }
        let plural = "s";
        if (n === 1) plural = "";
        toast.push(`${n} light${plural} created`, "success");
      }
      closeForm();
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

  const handleDragStart = (id: number) => (e: React.DragEvent) => {
    setDragId(id);
    dragChanged.current = false;
    e.dataTransfer.effectAllowed = "move";
    try {
      e.dataTransfer.setData("text/plain", String(id));
    } catch {
      // Some browsers throw on setData during synthetic events; safe to ignore.
    }
  };

  const handleDragOver = (overId: number) => (e: React.DragEvent) => {
    if (dragId === null) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragId === overId) return;
    const fromIdx = orderIds.indexOf(dragId);
    const toIdx = orderIds.indexOf(overId);
    if (fromIdx < 0 || toIdx < 0) return;
    setOrderIds(moveItem(orderIds, fromIdx, toIdx));
    dragChanged.current = true;
  };

  const handleDragEnd = async () => {
    const draggedId = dragId;
    setDragId(null);
    if (!dragChanged.current || draggedId === null) return;
    dragChanged.current = false;
    const snapshot = orderIds.slice();
    try {
      await Api.reorderLights(snapshot);
      await onChanged();
    } catch (e) {
      toast.push(String(e), "error");
      await onChanged();
    }
  };

  const orderedLights = orderIds
    .map((id) => lightsById.get(id))
    .filter((l): l is Light => l !== undefined);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Lights</h2>
        {!showForm && (
          <button className="btn-primary" onClick={openCreate}>
            Add light
          </button>
        )}
      </div>

      {showForm && (
        <form onSubmit={submit} className="card space-y-3 p-4">
          <div className="flex items-center justify-between">
            <div className="font-medium">
              {editing && `Edit ${editing.name}`}
              {!editing && "Add light"}
            </div>
            <button type="button" className="btn-ghost" onClick={closeForm}>
              Cancel
            </button>
          </div>

          <div className="flex items-start gap-3">
            <ModelThumb model={activeModel} size="lg" />
            <div className="flex-1 space-y-3">
              <Field label="Model">
                <select
                  className="input"
                  value={form.model_id}
                  onChange={(e) => onModelChange(Number(e.target.value))}
                >
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Mode">
                <select
                  className="input"
                  value={form.mode_id ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    let mode_id: number | null = null;
                    if (v) mode_id = Number(v);
                    setForm({ ...form, mode_id });
                  }}
                  disabled={!activeModel || activeModel.modes.length === 0}
                >
                  {activeModel?.modes.map((mode) => (
                    <option key={mode.id} value={mode.id}>
                      {mode.is_default && "★ "}
                      {mode.name} ({mode.channel_count}ch)
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </div>

          <Field label="Name">
            <input
              className="input"
              value={form.name}
              onChange={(e) => {
                setForm({ ...form, name: e.target.value });
                setLastAutoName("");
              }}
              required
            />
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
          {editing && (
            <Field label="Notes (for the AI Designer)">
              <textarea
                className="input"
                rows={3}
                value={form.notes ?? ""}
                placeholder="e.g. Lead vocalist key light. Point slightly stage-right."
                onChange={(e) =>
                  setForm({
                    ...form,
                    notes: e.target.value ? e.target.value : null,
                  })
                }
              />
            </Field>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={closeForm}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              {editing && "Save"}
              {!editing && "Create"}
            </button>
          </div>
        </form>
      )}

      {orderedLights.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">
          No lights on this controller.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg ring-1 ring-line">
          <div className="hidden bg-bg-elev px-3 py-2 text-xs uppercase tracking-wide text-muted sm:grid sm:grid-cols-[24px_48px_1fr_180px_24px_120px_160px] sm:items-center sm:gap-3">
            <span />
            <span />
            <span>Name</span>
            <span>Model / Mode</span>
            <span>Color</span>
            <span>Address</span>
            <span className="text-right">Actions</span>
          </div>
          <ul className="divide-y divide-line">
            {orderedLights.map((l) => {
              const m = models.find((x) => x.id === l.model_id);
              let mode: LightModelMode | undefined;
              if (m) {
                mode = m.modes.find((x) => x.id === l.mode_id);
                if (!mode) mode = m.modes.find((x) => x.is_default);
                if (!mode) mode = m.modes[0];
              }
              let chanCount = 0;
              if (mode) chanCount = mode.channel_count;
              else if (m) chanCount = m.channel_count;
              const isDragging = dragId === l.id;
              let rowCls =
                "grid grid-cols-[24px_48px_1fr_auto] items-center gap-3 px-3 py-2 sm:grid-cols-[24px_48px_1fr_180px_24px_120px_160px]";
              if (isDragging) rowCls += " opacity-50";
              return (
                <li
                  key={l.id}
                  className={rowCls}
                  draggable
                  onDragStart={handleDragStart(l.id)}
                  onDragOver={handleDragOver(l.id)}
                  onDragEnd={handleDragEnd}
                  onDrop={(e) => e.preventDefault()}
                >
                  <span
                    className="cursor-grab text-muted hover:text-slate-200 active:cursor-grabbing"
                    title="Drag to reorder"
                  >
                    <DragHandleIcon className="h-4 w-4" />
                  </span>
                  <ModelThumb model={m} size="sm" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-1">
                      <span className="truncate font-medium">{l.name}</span>
                      {l.notes && (
                        <span
                          className="pill text-[9px]"
                          title={l.notes}
                        >
                          notes
                        </span>
                      )}
                    </div>
                    <div className="truncate text-xs text-muted sm:hidden">
                      {m?.name ?? "?"} &middot; {mode?.name ?? "?"} &middot;{" "}
                      <span className="font-mono">
                        {l.start_address}
                        {chanCount > 0 && `-${l.start_address + chanCount - 1}`}
                      </span>
                    </div>
                  </div>
                  <div className="hidden min-w-0 text-sm text-muted sm:block">
                    <div className="truncate">{m?.name ?? "?"}</div>
                    <div className="truncate text-xs">{mode?.name ?? "?"}</div>
                  </div>
                  <div className="hidden sm:block">
                    <ColorSwatch light={l} />
                  </div>
                  <div className="hidden font-mono text-sm sm:block">
                    {l.start_address}
                    {chanCount > 0 && `-${l.start_address + chanCount - 1}`}
                  </div>
                  <div className="flex justify-end gap-1">
                    <button
                      className="btn-ghost"
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
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
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
