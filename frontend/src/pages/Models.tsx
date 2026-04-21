import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Api,
  ColorRole,
  FixtureLayout,
  FixtureZone,
  LayoutShape,
  LightModel,
  LightModelMode,
  LightModelModeInput,
  ParsedManual,
  UploadProgress,
} from "../api";
import Modal from "../components/Modal";
import { useToast } from "../toast";
import {
  COLOR_ROLES,
  MOTION_AXES,
  MotionAxis,
  SHAPES,
  channelOwners,
  detectZones,
  makeZoneId,
  orderedZones,
} from "../fixtureLayout";

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
  "pan_fine",
  "tilt",
  "tilt_fine",
  "zoom",
  "focus",
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
  pan: "#6ba2ff",
  pan_fine: "#4e7bc9",
  tilt: "#6ba2ff",
  tilt_fine: "#4e7bc9",
  zoom: "#b28df4",
  focus: "#b28df4",
  other: "#8791a7",
};

type ModeDraft = {
  key: string; // stable key for React list rendering (id if persisted, else synthetic)
  id?: number;
  name: string;
  channels: string[];
  is_default: boolean;
  layout: FixtureLayout | null;
};

type Form = {
  name: string;
  modes: ModeDraft[];
  activeKey: string;
};

let _modeKeyCounter = 0;
const newModeKey = () => `m-${Date.now()}-${++_modeKeyCounter}`;

const toDraft = (m: LightModelMode): ModeDraft => ({
  key: `m-${m.id}`,
  id: m.id,
  name: m.name,
  channels: [...m.channels],
  is_default: m.is_default,
  layout: m.layout ?? null,
});

const blankForm = (): Form => {
  const key = newModeKey();
  return {
    name: "",
    modes: [
      {
        key,
        name: "3ch",
        channels: ["r", "g", "b"],
        is_default: true,
        layout: null,
      },
    ],
    activeKey: key,
  };
};

const fromModel = (m: LightModel): Form => {
  const modes = m.modes.length
    ? m.modes.map(toDraft)
    : [
        {
          key: newModeKey(),
          name: `${m.channel_count || m.channels.length}ch`,
          channels: [...m.channels],
          is_default: true,
          layout: null,
        },
      ];
  return {
    name: m.name,
    modes,
    activeKey: modes[0].key,
  };
};

const draftsToPayload = (drafts: ModeDraft[]): LightModelModeInput[] =>
  drafts.map((d) => ({
    id: d.id,
    name: d.name.trim(),
    channels: [...d.channels],
    is_default: d.is_default,
    layout: d.layout ?? null,
  }));

export default function Models() {
  const toast = useToast();
  const [models, setModels] = useState<LightModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<LightModel | null>(null);
  const [form, setForm] = useState<Form>(blankForm());
  const [formNotes, setFormNotes] = useState<string | null>(null);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [manualPickerFor, setManualPickerFor] = useState<"create" | "edit" | null>(
    null,
  );
  const [manualState, setManualState] = useState<ManualState>({
    phase: "idle",
  });
  const [pendingScan, setPendingScan] = useState<ParsedManual | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const [imageBusy, setImageBusy] = useState(false);

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
    Api.aiStatus()
      .then((s) => setAiEnabled(s.enabled))
      .catch(() => setAiEnabled(false));
  }, []);

  const activeMode = useMemo(() => {
    return form.modes.find((m) => m.key === form.activeKey) ?? form.modes[0];
  }, [form]);

  const openCreate = () => {
    setForm(blankForm());
    setFormNotes(null);
    setPendingScan(null);
    setEditing(null);
    setOpen(true);
  };

  const openEdit = (m: LightModel) => {
    setForm(fromModel(m));
    setFormNotes(null);
    setPendingScan(null);
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
    const trimmedName = form.name.trim();
    if (!trimmedName) {
      toast.push("Name is required", "error");
      return;
    }
    if (form.modes.length === 0) {
      toast.push("At least one mode is required", "error");
      return;
    }
    for (const m of form.modes) {
      if (!m.name.trim()) {
        toast.push("Every mode needs a name", "error");
        return;
      }
      if (m.channels.length === 0) {
        toast.push(`Mode "${m.name}" has no channels`, "error");
        return;
      }
    }
    const payload = {
      name: trimmedName,
      modes: draftsToPayload(form.modes),
    };
    try {
      if (editing) {
        await Api.updateModel(editing.id, payload);
        toast.push("Model updated", "success");
      } else {
        await Api.createModel(payload);
        toast.push("Model created", "success");
      }
      setOpen(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  // ---- mode list operations (inside the editor) ----
  const addMode = () => {
    const key = newModeKey();
    setForm((f) => {
      const nextModes: ModeDraft[] = [
        ...f.modes,
        {
          key,
          name: `Mode ${f.modes.length + 1}`,
          channels: ["r", "g", "b"],
          is_default: f.modes.length === 0,
          layout: null,
        },
      ];
      return { ...f, modes: nextModes, activeKey: key };
    });
  };

  const removeMode = (key: string) => {
    setForm((f) => {
      if (f.modes.length <= 1) return f;
      const next = f.modes.filter((m) => m.key !== key);
      const removed = f.modes.find((m) => m.key === key);
      if (removed?.is_default && next.length) next[0].is_default = true;
      const activeKey =
        f.activeKey === key ? next[0].key : f.activeKey;
      return { ...f, modes: next, activeKey };
    });
  };

  const setActiveKey = (key: string) =>
    setForm((f) => ({ ...f, activeKey: key }));

  const setModeField = <K extends keyof ModeDraft>(
    key: string,
    field: K,
    value: ModeDraft[K],
  ) =>
    setForm((f) => ({
      ...f,
      modes: f.modes.map((m) =>
        m.key === key ? { ...m, [field]: value } : m,
      ),
    }));

  const setDefault = (key: string) =>
    setForm((f) => ({
      ...f,
      modes: f.modes.map((m) => ({ ...m, is_default: m.key === key })),
    }));

  const moveChannel = (index: number, dir: -1 | 1) => {
    const mode = activeMode;
    if (!mode) return;
    const next = [...mode.channels];
    const j = index + dir;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j], next[index]];
    setModeField(mode.key, "channels", next);
  };

  const removeChannel = (index: number) => {
    const mode = activeMode;
    if (!mode) return;
    const next = [...mode.channels];
    next.splice(index, 1);
    setModeField(mode.key, "channels", next);
  };

  const addChannel = (role: string) => {
    const mode = activeMode;
    if (!mode) return;
    setModeField(mode.key, "channels", [...mode.channels, role]);
  };

  const setChannel = (index: number, role: string) => {
    const mode = activeMode;
    if (!mode) return;
    const next = [...mode.channels];
    next[index] = role;
    setModeField(mode.key, "channels", next);
  };

  // ---- image upload ----
  const triggerImagePick = () => {
    if (!editing) return;
    imageInputRef.current?.click();
  };

  const handleImageFile = async (file: File | null) => {
    if (!file || !editing) return;
    setImageBusy(true);
    try {
      const updated = await Api.uploadModelImage(editing.id, file);
      setModels((prev) =>
        prev.map((m) => (m.id === updated.id ? updated : m)),
      );
      setEditing(updated);
      toast.push("Image uploaded", "success");
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setImageBusy(false);
      if (imageInputRef.current) imageInputRef.current.value = "";
    }
  };

  const removeImage = async () => {
    if (!editing) return;
    if (!confirm("Remove image?")) return;
    setImageBusy(true);
    try {
      const updated = await Api.deleteModelImage(editing.id);
      setModels((prev) =>
        prev.map((m) => (m.id === updated.id ? updated : m)),
      );
      setEditing(updated);
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setImageBusy(false);
    }
  };

  // ---- AI manual parsing ----
  const handleManualFile = async (file: File | null) => {
    if (!file) return;
    const startedAt = Date.now();
    setManualState({
      phase: "uploading",
      file: { name: file.name, size: file.size },
      percent: 0,
      startedAt,
    });
    try {
      const parsed = await Api.parseManual(file, (p: UploadProgress) => {
        setManualState((s) => {
          if (s.phase === "idle" || s.phase === "error") return s;
          if (p.phase === "uploading") {
            return {
              ...s,
              phase: "uploading",
              percent: p.percent ?? s.percent,
            };
          }
          return {
            ...s,
            phase: "processing",
            percent: 1,
            processingStartedAt:
              s.phase === "processing" ? s.processingStartedAt : Date.now(),
          };
        });
      });
      if (manualPickerFor === "create") {
        applyParsedToCreate(parsed);
      } else if (manualPickerFor === "edit") {
        setPendingScan(parsed);
      }
      setManualPickerFor(null);
      setManualState({ phase: "idle" });
    } catch (e) {
      setManualState({ phase: "error", message: String(e) });
    }
  };

  const dismissManualPicker = () => {
    if (manualState.phase === "uploading" || manualState.phase === "processing")
      return;
    setManualPickerFor(null);
    setManualState({ phase: "idle" });
  };

  const applyParsedToCreate = (parsed: ParsedManual) => {
    const modes: ModeDraft[] = parsed.modes.map((pm, i) => ({
      key: newModeKey(),
      name: pm.name || `Mode ${i + 1}`,
      channels: [...pm.channels],
      is_default: i === 0,
      layout: pm.layout ?? null,
    }));
    if (modes.length === 0) {
      toast.push("Claude returned no modes", "error");
      return;
    }
    setEditing(null);
    setForm({
      name: parsed.suggested_name || "",
      modes,
      activeKey: modes[0].key,
    });
    setFormNotes(parsed.notes || null);
    setPendingScan(null);
    setOpen(true);
  };

  const applyScanMergeAndClear = (accept: {
    name: boolean;
    replace: Set<string>;
    add: Set<string>;
  }) => {
    if (!pendingScan) return;
    setForm((f) => {
      let nextModes = [...f.modes];
      const byLower = new Map(
        nextModes.map((m) => [m.name.trim().toLowerCase(), m] as const),
      );
      for (const pm of pendingScan.modes) {
        const lower = pm.name.trim().toLowerCase();
        const existing = byLower.get(lower);
        if (existing && accept.replace.has(lower)) {
          nextModes = nextModes.map((m) =>
            m === existing
              ? {
                  ...m,
                  channels: [...pm.channels],
                  layout: pm.layout ?? m.layout,
                }
              : m,
          );
        } else if (!existing && accept.add.has(lower)) {
          nextModes.push({
            key: newModeKey(),
            name: pm.name,
            channels: [...pm.channels],
            is_default: false,
            layout: pm.layout ?? null,
          });
        }
      }
      // Ensure at least one default
      if (!nextModes.some((m) => m.is_default) && nextModes.length) {
        nextModes[0].is_default = true;
      }
      return {
        ...f,
        name: accept.name && pendingScan.suggested_name
          ? pendingScan.suggested_name
          : f.name,
        modes: nextModes,
      };
    });
    setFormNotes(pendingScan.notes || null);
    setPendingScan(null);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Light Models</h1>
          <p className="text-sm text-muted">Channel layouts for your fixtures.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {aiEnabled && (
            <button
              className="btn-secondary"
              onClick={() => setManualPickerFor("create")}
            >
              Create from manual…
            </button>
          )}
          <button className="btn-primary" onClick={openCreate}>
            Add model
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-muted">Loading...</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {models.map((m) => (
            <div key={m.id} className="card p-4">
              <div className="flex items-start gap-3">
                <ModelThumbnail model={m} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate font-semibold">{m.name}</h3>
                    {m.builtin && (
                      <span className="pill text-[10px]">built-in</span>
                    )}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1 text-xs text-muted">
                    {m.modes.length === 0 ? (
                      <span>
                        {m.channel_count} channel
                        {m.channel_count === 1 ? "" : "s"}
                      </span>
                    ) : (
                      m.modes.map((mode) => (
                        <span
                          key={mode.id}
                          className={
                            "pill " +
                            (mode.is_default
                              ? "bg-accent/20 text-accent ring-accent/40"
                              : "")
                          }
                          title={mode.channels.join(", ")}
                        >
                          {mode.is_default ? "★ " : ""}
                          {mode.name}
                        </span>
                      ))
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
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
                {(m.modes.find((x) => x.is_default) ?? m.modes[0])?.channels.map(
                  (c, i) => (
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
                  ),
                )}
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
              disabled={
                !form.name ||
                form.modes.length === 0 ||
                form.modes.some((m) => m.channels.length === 0)
              }
            >
              Save
            </button>
          </>
        }
      >
        <form id="model-form" onSubmit={submit} className="space-y-4">
          {editing && (
            <div className="flex items-start gap-3 rounded-lg bg-bg-elev p-3 ring-1 ring-line">
              <div className="h-20 w-20 shrink-0 overflow-hidden rounded-md bg-bg-card ring-1 ring-line">
                {editing.image_url ? (
                  <img
                    src={editing.image_url}
                    alt=""
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center text-[10px] uppercase tracking-wider text-muted">
                    no image
                  </div>
                )}
              </div>
              <div className="flex-1 space-y-2">
                <div className="text-xs text-muted">
                  Reference image for pickers. PNG / JPG / WEBP, up to 5 MB.
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={triggerImagePick}
                    disabled={imageBusy}
                  >
                    {imageBusy ? "Uploading…" : editing.image_url ? "Replace" : "Upload"}
                  </button>
                  {editing.image_url && (
                    <button
                      type="button"
                      className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
                      onClick={removeImage}
                      disabled={imageBusy}
                    >
                      Remove
                    </button>
                  )}
                  {aiEnabled && (
                    <button
                      type="button"
                      className="btn-secondary ml-auto"
                      onClick={() => setManualPickerFor("edit")}
                    >
                      Re-scan manual…
                    </button>
                  )}
                </div>
                <input
                  ref={imageInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(e) => handleImageFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </div>
          )}

          {formNotes && (
            <div className="rounded-md bg-accent/10 p-2 text-xs text-slate-300 ring-1 ring-accent/30">
              <span className="font-semibold">Claude notes: </span>
              {formNotes}
            </div>
          )}

          {pendingScan && (
            <ScanMergePreview
              form={form}
              parsed={pendingScan}
              onApply={applyScanMergeAndClear}
              onDismiss={() => setPendingScan(null)}
            />
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

          <div className="grid grid-cols-1 gap-3 md:grid-cols-[200px_1fr]">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="label">Modes ({form.modes.length})</span>
                <button
                  type="button"
                  className="btn-ghost !px-2 !py-1 text-xs"
                  onClick={addMode}
                >
                  + add
                </button>
              </div>
              <div className="space-y-1 rounded-lg bg-bg-elev p-1 ring-1 ring-line">
                {form.modes.map((mode) => (
                  <div
                    key={mode.key}
                    className={
                      "flex items-center gap-1 rounded-md px-2 py-1 text-sm transition " +
                      (mode.key === form.activeKey
                        ? "bg-bg-card ring-1 ring-accent"
                        : "hover:bg-bg-card")
                    }
                  >
                    <button
                      type="button"
                      className="flex-1 truncate text-left"
                      onClick={() => setActiveKey(mode.key)}
                    >
                      {mode.is_default && <span className="text-accent">★ </span>}
                      <span className="font-medium">{mode.name || "(unnamed)"}</span>
                      <span className="ml-1 text-xs text-muted">
                        {mode.channels.length}ch
                      </span>
                    </button>
                    <button
                      type="button"
                      className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
                      onClick={() => setDefault(mode.key)}
                      disabled={mode.is_default}
                      title="Set as default"
                    >
                      ★
                    </button>
                    <button
                      type="button"
                      className="btn-ghost !px-1.5 !py-0.5 text-[10px] text-rose-300"
                      onClick={() => removeMode(mode.key)}
                      disabled={form.modes.length <= 1}
                      title="Delete mode"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-3">
              {activeMode && (
                <>
                  <div className="grid grid-cols-[1fr_auto] gap-2">
                    <label className="block">
                      <span className="label mb-1 block">Mode name</span>
                      <input
                        className="input"
                        value={activeMode.name}
                        onChange={(e) =>
                          setModeField(activeMode.key, "name", e.target.value)
                        }
                        required
                      />
                    </label>
                    <label className="flex items-end gap-2 pb-1.5 text-sm text-slate-200">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-line bg-bg-elev text-accent"
                        checked={activeMode.is_default}
                        onChange={() => setDefault(activeMode.key)}
                      />
                      Default
                    </label>
                  </div>
                  <div>
                    <div className="label mb-1">
                      Channels ({activeMode.channels.length})
                    </div>
                    <div className="space-y-1 rounded-lg bg-bg-elev p-2 ring-1 ring-line">
                      {activeMode.channels.length === 0 && (
                        <div className="px-2 py-1 text-xs text-muted">
                          No channels yet.
                        </div>
                      )}
                      {activeMode.channels.map((c, i) => (
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
                            onChange={(e) => setChannel(i, e.target.value)}
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
                            disabled={i === activeMode.channels.length - 1}
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
                  <LayoutPanel
                    channels={activeMode.channels}
                    layout={activeMode.layout}
                    onChange={(next) =>
                      setModeField(activeMode.key, "layout", next)
                    }
                  />
                </>
              )}
            </div>
          </div>
        </form>
      </Modal>

      <ManualPicker
        open={manualPickerFor !== null}
        state={manualState}
        title={
          manualPickerFor === "edit" ? "Re-scan manual" : "Create from manual"
        }
        onClose={dismissManualPicker}
        onFile={handleManualFile}
        onReset={() => setManualState({ phase: "idle" })}
      />
    </div>
  );
}

type ManualState =
  | { phase: "idle" }
  | {
      phase: "uploading";
      file: { name: string; size: number };
      percent: number;
      startedAt: number;
    }
  | {
      phase: "processing";
      file: { name: string; size: number };
      percent: 1;
      startedAt: number;
      processingStartedAt: number;
    }
  | { phase: "error"; message: string };

function ModelThumbnail({ model }: { model: LightModel }) {
  if (model.image_url) {
    return (
      <div className="h-16 w-16 shrink-0 overflow-hidden rounded-md bg-bg-elev ring-1 ring-line">
        <img
          src={model.image_url}
          alt=""
          className="h-full w-full object-cover"
        />
      </div>
    );
  }
  const channels =
    (model.modes.find((x) => x.is_default) ?? model.modes[0])?.channels ??
    model.channels;
  return (
    <div className="flex h-16 w-16 shrink-0 items-center justify-center gap-0.5 rounded-md bg-bg-elev p-1 ring-1 ring-line">
      {channels.slice(0, 6).map((c, i) => (
        <span
          key={i}
          className="h-6 w-1.5 rounded-sm"
          style={{ background: ROLE_COLORS[c] ?? "#8791a7" }}
        />
      ))}
    </div>
  );
}

function ManualPicker({
  open,
  state,
  title,
  onClose,
  onFile,
  onReset,
}: {
  open: boolean;
  state: ManualState;
  title: string;
  onClose: () => void;
  onFile: (file: File | null) => void;
  onReset: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());

  // Keep a ticking "now" so elapsed-time displays refresh while busy.
  useEffect(() => {
    if (state.phase !== "uploading" && state.phase !== "processing") return;
    const id = setInterval(() => setNowTick(Date.now()), 250);
    return () => clearInterval(id);
  }, [state.phase]);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (state.phase !== "idle" && state.phase !== "error") return;
    const f = e.dataTransfer.files?.[0] ?? null;
    onFile(f);
  };

  const busy = state.phase === "uploading" || state.phase === "processing";

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      size="md"
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (state.phase === "idle" || state.phase === "error") {
            setDragging(true);
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={
          "flex flex-col items-stretch gap-3 rounded-lg border-2 border-dashed p-6 text-sm transition " +
          (dragging
            ? "border-accent bg-accent/10"
            : "border-line bg-bg-elev")
        }
      >
        {state.phase === "idle" && (
          <IdleBody
            onPick={() => inputRef.current?.click()}
            inputRef={inputRef}
            onFile={onFile}
          />
        )}

        {state.phase === "error" && (
          <ErrorBody
            message={state.message}
            onRetry={onReset}
            onPick={() => inputRef.current?.click()}
            inputRef={inputRef}
            onFile={onFile}
          />
        )}

        {(state.phase === "uploading" || state.phase === "processing") && (
          <BusyBody state={state} now={nowTick} />
        )}
      </div>
    </Modal>
  );
}

function IdleBody({
  onPick,
  inputRef,
  onFile,
}: {
  onPick: () => void;
  inputRef: React.MutableRefObject<HTMLInputElement | null>;
  onFile: (f: File | null) => void;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-4 text-center">
      <div className="text-slate-200">
        Drop a PDF or screenshot of the fixture manual here.
      </div>
      <div className="text-xs text-muted">
        PDF, PNG, JPG, or WEBP — up to 10 MB. Claude extracts the name and
        every channel mode.
      </div>
      <button type="button" className="btn-primary" onClick={onPick}>
        Choose file
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function ErrorBody({
  message,
  onRetry,
  onPick,
  inputRef,
  onFile,
}: {
  message: string;
  onRetry: () => void;
  onPick: () => void;
  inputRef: React.MutableRefObject<HTMLInputElement | null>;
  onFile: (f: File | null) => void;
}) {
  return (
    <div className="flex flex-col items-stretch gap-3 text-center">
      <div className="rounded-md bg-rose-950/60 p-3 text-left text-sm text-rose-200 ring-1 ring-rose-900">
        <div className="mb-1 font-semibold">Something went wrong</div>
        <div className="text-xs text-rose-200/80">{message}</div>
      </div>
      <div className="flex items-center justify-center gap-2">
        <button type="button" className="btn-ghost" onClick={onRetry}>
          Dismiss
        </button>
        <button type="button" className="btn-primary" onClick={onPick}>
          Try another file
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function BusyBody({
  state,
  now,
}: {
  state: Extract<ManualState, { phase: "uploading" | "processing" }>;
  now: number;
}) {
  const uploadPct = Math.round(
    Math.min(1, Math.max(0, state.percent ?? 0)) * 100,
  );
  const overallElapsedS = Math.max(0, (now - state.startedAt) / 1000);
  const processingS =
    state.phase === "processing"
      ? Math.max(0, (now - state.processingStartedAt) / 1000)
      : 0;

  return (
    <div className="flex flex-col gap-4 py-2">
      <div className="flex items-center gap-3">
        <FileIcon />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-slate-100">
            {state.file.name}
          </div>
          <div className="text-xs text-muted">
            {formatBytes(state.file.size)}
          </div>
        </div>
        <div className="text-xs text-muted tabular-nums">
          {overallElapsedS.toFixed(1)}s
        </div>
      </div>

      {/* Step 1: Uploading */}
      <Step
        active={state.phase === "uploading"}
        done={state.phase === "processing"}
        title={
          state.phase === "uploading"
            ? `Uploading… ${uploadPct}%`
            : "Upload complete"
        }
      >
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-card">
          <div
            className="h-full bg-accent transition-all duration-200"
            style={{
              width: state.phase === "uploading" ? `${uploadPct}%` : "100%",
            }}
          />
        </div>
      </Step>

      {/* Step 2: Claude processing */}
      <Step
        active={state.phase === "processing"}
        done={false}
        pending={state.phase === "uploading"}
        title={
          state.phase === "processing"
            ? `Claude is reading the manual… (${processingS.toFixed(1)}s)`
            : "Claude will read the manual next"
        }
      >
        <IndeterminateBar active={state.phase === "processing"} />
      </Step>

      <div className="text-center text-[11px] text-muted">
        Large PDFs can take 10–60s. Please don't close this dialog.
      </div>
    </div>
  );
}

function Step({
  active,
  done,
  pending,
  title,
  children,
}: {
  active: boolean;
  done: boolean;
  pending?: boolean;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={
        "rounded-md p-2 ring-1 transition " +
        (active
          ? "bg-bg-card ring-accent/40"
          : done
            ? "bg-bg-card/60 ring-line"
            : "bg-bg-card/30 ring-line opacity-60")
      }
    >
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <StepGlyph active={active} done={done} pending={pending} />
        <span
          className={
            active
              ? "text-slate-100"
              : done
                ? "text-slate-300"
                : "text-muted"
          }
        >
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function StepGlyph({
  active,
  done,
  pending,
}: {
  active: boolean;
  done: boolean;
  pending?: boolean;
}) {
  if (done) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-accent text-[10px] text-white">
        ✓
      </span>
    );
  }
  if (active) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-bg-card border-t-accent" />
      </span>
    );
  }
  if (pending) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full ring-1 ring-line" />
    );
  }
  return (
    <span className="inline-flex h-4 w-4 items-center justify-center rounded-full ring-1 ring-line" />
  );
}

function IndeterminateBar({ active }: { active: boolean }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-card">
      {active ? (
        <div className="indeterminate-bar h-full rounded-full bg-accent/80" />
      ) : (
        <div className="h-full w-0" />
      )}
    </div>
  );
}

function FileIcon() {
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent/10 text-accent ring-1 ring-accent/30">
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
        <path d="M14 3v6h6" />
      </svg>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function ScanMergePreview({
  form,
  parsed,
  onApply,
  onDismiss,
}: {
  form: Form;
  parsed: ParsedManual;
  onApply: (accept: {
    name: boolean;
    replace: Set<string>;
    add: Set<string>;
  }) => void;
  onDismiss: () => void;
}) {
  const existingByLower = useMemo(
    () =>
      new Map(
        form.modes.map((m) => [m.name.trim().toLowerCase(), m] as const),
      ),
    [form.modes],
  );

  const [acceptName, setAcceptName] = useState(
    !!parsed.suggested_name && parsed.suggested_name !== form.name,
  );
  const [replace, setReplace] = useState<Set<string>>(
    () =>
      new Set(
        parsed.modes
          .filter((pm) => existingByLower.has(pm.name.trim().toLowerCase()))
          .map((pm) => pm.name.trim().toLowerCase()),
      ),
  );
  const [add, setAdd] = useState<Set<string>>(
    () =>
      new Set(
        parsed.modes
          .filter((pm) => !existingByLower.has(pm.name.trim().toLowerCase()))
          .map((pm) => pm.name.trim().toLowerCase()),
      ),
  );

  const toggle = (s: Set<string>, k: string, set: (s: Set<string>) => void) => {
    const next = new Set(s);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    set(next);
  };

  return (
    <div className="space-y-2 rounded-lg bg-bg-elev p-3 ring-1 ring-accent/40">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">
          Claude found {parsed.modes.length} mode
          {parsed.modes.length === 1 ? "" : "s"}
        </div>
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 text-xs"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      </div>
      {parsed.suggested_name && parsed.suggested_name !== form.name && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-line bg-bg-card text-accent"
            checked={acceptName}
            onChange={(e) => setAcceptName(e.target.checked)}
          />
          Replace name with <span className="font-mono">{parsed.suggested_name}</span>
        </label>
      )}
      <div className="space-y-1">
        {parsed.modes.map((pm) => {
          const lower = pm.name.trim().toLowerCase();
          const existing = existingByLower.get(lower);
          const checked = existing ? replace.has(lower) : add.has(lower);
          return (
            <label
              key={lower + pm.channels.join(",")}
              className="flex items-start gap-2 rounded-md bg-bg-card p-2 text-xs ring-1 ring-line"
            >
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-line bg-bg-elev text-accent"
                checked={checked}
                onChange={() =>
                  existing
                    ? toggle(replace, lower, setReplace)
                    : toggle(add, lower, setAdd)
                }
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold">{pm.name}</span>
                  <span className="pill text-[10px]">
                    {existing ? "replace" : "add"}
                  </span>
                  <span className="text-[10px] text-muted">
                    {pm.channels.length}ch
                  </span>
                  {pm.layout && (
                    <span
                      className="pill text-[10px] bg-accent/10 text-accent ring-accent/40"
                      title={layoutSummary(pm.layout)}
                    >
                      {layoutBadge(pm.layout)}
                    </span>
                  )}
                </div>
                {pm.layout && (
                  <div className="mt-1">
                    <ParsedLayoutThumb layout={pm.layout} />
                  </div>
                )}
                <div className="mt-0.5 font-mono text-muted">
                  {pm.channels.join(", ")}
                </div>
                {pm.notes && (
                  <div className="mt-0.5 text-muted">{pm.notes}</div>
                )}
              </div>
            </label>
          );
        })}
      </div>
      <div className="flex justify-end">
        <button
          type="button"
          className="btn-primary"
          onClick={() => onApply({ name: acceptName, replace, add })}
        >
          Apply changes
        </button>
      </div>
    </div>
  );
}

function layoutSummary(layout: FixtureLayout): string {
  const parts: string[] = [];
  if (layout.zones.length > 0) {
    parts.push(
      `${layout.zones.length} zone${layout.zones.length === 1 ? "" : "s"}`,
    );
  }
  if (layout.motion) {
    const axes: string[] = [];
    for (const axis of MOTION_AXES) {
      if (typeof layout.motion[axis] === "number") axes.push(axis);
    }
    if (axes.length) parts.push(axes.join("+"));
  }
  return parts.join(" • ") || "empty layout";
}

function layoutBadge(layout: FixtureLayout): string {
  const n = layout.zones.length;
  if (n === 0) {
    const axes: string[] = [];
    for (const axis of MOTION_AXES) {
      if (typeof layout.motion?.[axis] === "number") axes.push(axis);
    }
    return axes.length ? axes.join("/") : layout.shape;
  }
  if (layout.shape === "linear") return `${n} px linear`;
  if (layout.shape === "grid")
    return `${layout.cols ?? "?"}×${layout.rows ?? "?"} grid`;
  if (layout.shape === "ring") return `${n} ring`;
  if (layout.shape === "cluster") return `${n} zones`;
  return `${n} zones`;
}

function ParsedLayoutThumb({ layout }: { layout: FixtureLayout }) {
  const zones = orderedZones(layout);
  if (zones.length === 0) {
    return (
      <div className="text-[10px] text-muted">
        Motion only ({layoutSummary(layout)})
      </div>
    );
  }
  const fill = (z: FixtureZone) => {
    if (z.colors.r != null && z.colors.g != null && z.colors.b != null) {
      return "linear-gradient(135deg,#ff4d4d,#4dff6a,#4d6aff)";
    }
    if (z.colors.w != null) return "#f5f5f5";
    if (z.colors.a != null) return "#ffb23d";
    if (z.colors.uv != null) return "#b44dff";
    return "#8791a7";
  };
  if (layout.shape === "grid") {
    const cols = layout.cols ?? Math.ceil(Math.sqrt(zones.length));
    return (
      <div
        className="grid gap-px rounded-sm"
        style={{
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          width: Math.min(12 * cols, 160),
        }}
      >
        {zones.map((z) => (
          <div
            key={z.id}
            className="h-2.5 rounded-[1px]"
            style={{ background: fill(z) }}
          />
        ))}
      </div>
    );
  }
  if (layout.shape === "ring") {
    return (
      <div className="text-[10px] text-muted">
        ring of {zones.length} cells
      </div>
    );
  }
  return (
    <div className="flex items-center gap-px">
      {zones.slice(0, 32).map((z) => (
        <div
          key={z.id}
          className="h-2.5 min-w-[4px] flex-1 max-w-[10px]"
          style={{ background: fill(z) }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout (compound-fixture) editor
// ---------------------------------------------------------------------------

function LayoutPanel({
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
      {/* Shape + dimensions */}
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

      {/* Zones */}
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

      {/* Motion */}
      <MotionBlock
        channels={channels}
        layout={layout}
        onMotion={setMotion}
        onDegrees={(k, v) =>
          setMotion({ [k]: v } as Partial<NonNullable<FixtureLayout["motion"]>>)
        }
      />

      {/* Globals */}
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

  // Snapshot a representative color for each zone (mix R+G+B offset roles -> swatch).
  // Since no live state exists at edit time, just use role colors.
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
  const n = channels.length;
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
          onChange={(v) =>
            onChange({ dimmer: v == null ? undefined : v })
          }
        />
        <ChannelPicker
          label="Str"
          channels={channels}
          value={zone.strobe ?? null}
          filterRole="strobe"
          onChange={(v) =>
            onChange({ strobe: v == null ? undefined : v })
          }
        />
      </div>
      {Object.keys(zone.colors).length === 0 && zone.dimmer == null && (
        <div className="mt-1 text-[11px] text-amber-300/80">
          Warning: zone has no color or dimmer channels assigned.
        </div>
      )}
      {n === 0 && null}
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
  // Build option list; highlight slots that match filterRole.
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
        <div className="label !text-[10px]">Motion (pan / tilt / zoom / focus)</div>
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
