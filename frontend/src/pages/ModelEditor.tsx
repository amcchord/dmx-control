import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Api,
  LightModel,
  ParsedManual,
  UploadProgress,
} from "../api";
import { useToast } from "../toast";
import ColorPolicySection from "./models/ColorPolicySection";
import ColorTablePanel from "./models/ColorTablePanel";
import LayoutPanel from "./models/LayoutPanel";
import ManualPicker from "./models/ManualPicker";
import ModelThumbnail from "./models/ModelThumbnail";
import ScanMergePreview from "./models/ScanMergePreview";
import {
  Form,
  ManualState,
  ModeDraft,
  ROLES,
  ROLE_COLORS,
  blankForm,
  draftsToPayload,
  fromModel,
  newModeKey,
} from "./models/types";

/** Full-page editor for a single LightModel.
 *
 * Renders at ``/models/new`` (create) and ``/models/:id/edit`` (edit).
 * Replaces the cramped modal form that used to live inside Models.tsx —
 * the new layout has room for the modes list, channel editor, color
 * behavior toggles, and the compound-fixture layout panel side by side
 * without competing with a full-page dialog. */
export default function ModelEditor() {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();

  const isCreate = id === undefined;
  const modelId = isCreate ? null : Number(id);

  const [model, setModel] = useState<LightModel | null>(null);
  const [form, setForm] = useState<Form>(blankForm());
  const [formNotes, setFormNotes] = useState<string | null>(null);
  const [loading, setLoading] = useState(!isCreate);
  const [saving, setSaving] = useState(false);

  const [aiEnabled, setAiEnabled] = useState(false);
  const [manualPickerOpen, setManualPickerOpen] = useState(false);
  const [manualState, setManualState] = useState<ManualState>({ phase: "idle" });
  const [pendingScan, setPendingScan] = useState<ParsedManual | null>(null);

  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const [imageBusy, setImageBusy] = useState(false);

  useEffect(() => {
    Api.aiStatus()
      .then((s) => setAiEnabled(s.enabled))
      .catch(() => setAiEnabled(false));
  }, []);

  // When arriving via "/models/new?manual=1" from the list page, auto-open
  // the manual picker so the user lands straight in the upload drop zone.
  useEffect(() => {
    if (!isCreate) return;
    if (searchParams.get("manual") === "1") {
      setManualPickerOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("manual");
      setSearchParams(next, { replace: true });
    }
  }, [isCreate, searchParams, setSearchParams]);

  useEffect(() => {
    if (isCreate) {
      setForm(blankForm());
      setModel(null);
      setLoading(false);
      return;
    }
    if (modelId == null || Number.isNaN(modelId)) {
      toast.push("Invalid model id", "error");
      navigate("/models", { replace: true });
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const rows = await Api.listModels();
        const m = rows.find((r) => r.id === modelId) ?? null;
        if (cancelled) return;
        if (m == null) {
          toast.push("Model not found", "error");
          navigate("/models", { replace: true });
          return;
        }
        setModel(m);
        setForm(fromModel(m));
      } catch (e) {
        if (!cancelled) toast.push(String(e), "error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isCreate, modelId, navigate, toast]);

  const activeMode = useMemo(
    () => form.modes.find((m) => m.key === form.activeKey) ?? form.modes[0],
    [form],
  );

  const builtin = model?.builtin === true;

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

  const syncPolicyWithChannels = (mode: ModeDraft): ModeDraft => {
    const present = new Set(mode.channels);
    const next = { ...mode.color_policy };
    let changed = false;
    for (const key of Object.keys(next) as (keyof typeof next)[]) {
      if (!present.has(key)) {
        delete next[key];
        changed = true;
      }
    }
    return changed ? { ...mode, color_policy: next } : mode;
  };

  const setChannels = (key: string, channels: string[]) =>
    setForm((f) => ({
      ...f,
      modes: f.modes.map((m) =>
        m.key === key
          ? syncPolicyWithChannels({ ...m, channels })
          : m,
      ),
    }));

  const setDefault = (key: string) =>
    setForm((f) => ({
      ...f,
      modes: f.modes.map((m) => ({ ...m, is_default: m.key === key })),
    }));

  const addMode = () => {
    const key = newModeKey();
    setForm((f) => ({
      ...f,
      modes: [
        ...f.modes,
        {
          key,
          name: `Mode ${f.modes.length + 1}`,
          channels: ["r", "g", "b"],
          is_default: f.modes.length === 0,
          layout: null,
          color_policy: {},
          color_table: null,
        },
      ],
      activeKey: key,
    }));
  };

  const removeMode = (key: string) =>
    setForm((f) => {
      if (f.modes.length <= 1) return f;
      const next = f.modes.filter((m) => m.key !== key);
      const removed = f.modes.find((m) => m.key === key);
      if (removed?.is_default && next.length) next[0].is_default = true;
      const activeKey = f.activeKey === key ? next[0].key : f.activeKey;
      return { ...f, modes: next, activeKey };
    });

  const setActiveKey = (key: string) =>
    setForm((f) => ({ ...f, activeKey: key }));

  const moveChannel = (index: number, dir: -1 | 1) => {
    if (!activeMode) return;
    const next = [...activeMode.channels];
    const j = index + dir;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j], next[index]];
    setChannels(activeMode.key, next);
  };

  const removeChannel = (index: number) => {
    if (!activeMode) return;
    const next = [...activeMode.channels];
    next.splice(index, 1);
    setChannels(activeMode.key, next);
  };

  const addChannel = (role: string) => {
    if (!activeMode) return;
    setChannels(activeMode.key, [...activeMode.channels, role]);
  };

  const setChannel = (index: number, role: string) => {
    if (!activeMode) return;
    const next = [...activeMode.channels];
    next[index] = role;
    setChannels(activeMode.key, next);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (builtin) {
      toast.push("Built-in models can't be edited directly; clone first", "error");
      return;
    }
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
    setSaving(true);
    try {
      if (model) {
        const updated = await Api.updateModel(model.id, payload);
        setModel(updated);
        setForm(fromModel(updated));
        toast.push("Model updated", "success");
      } else {
        const created = await Api.createModel(payload);
        toast.push("Model created", "success");
        navigate(`/models/${created.id}/edit`, { replace: true });
      }
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setSaving(false);
    }
  };

  const triggerImagePick = () => {
    if (!model) return;
    imageInputRef.current?.click();
  };

  const handleImageFile = async (file: File | null) => {
    if (!file || !model) return;
    setImageBusy(true);
    try {
      const updated = await Api.uploadModelImage(model.id, file);
      setModel(updated);
      toast.push("Image uploaded", "success");
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setImageBusy(false);
      if (imageInputRef.current) imageInputRef.current.value = "";
    }
  };

  const removeImage = async () => {
    if (!model) return;
    if (!confirm("Remove image?")) return;
    setImageBusy(true);
    try {
      const updated = await Api.deleteModelImage(model.id);
      setModel(updated);
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setImageBusy(false);
    }
  };

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
      if (isCreate) {
        const modes: ModeDraft[] = parsed.modes.map((pm, i) => ({
          key: newModeKey(),
          name: pm.name || `Mode ${i + 1}`,
          channels: [...pm.channels],
          is_default: i === 0,
          layout: pm.layout ?? null,
          color_policy: {},
          color_table: pm.color_table ?? null,
        }));
        if (modes.length === 0) {
          toast.push(
            "Claude couldn't extract any modes from this manual. Try " +
              "re-uploading or use a clearer page.",
            "error",
          );
        } else {
          setForm({
            name: parsed.suggested_name || "",
            modes,
            activeKey: modes[0].key,
          });
          setFormNotes(parsed.notes || null);
          setPendingScan(null);
        }
      } else {
        setPendingScan(parsed);
      }
      setManualPickerOpen(false);
      setManualState({ phase: "idle" });
    } catch (e) {
      setManualState({ phase: "error", message: String(e) });
    }
  };

  const dismissManualPicker = () => {
    if (
      manualState.phase === "uploading" ||
      manualState.phase === "processing"
    )
      return;
    setManualPickerOpen(false);
    setManualState({ phase: "idle" });
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
              ? syncPolicyWithChannels({
                  ...m,
                  channels: [...pm.channels],
                  layout: pm.layout ?? m.layout,
                  color_table: pm.color_table ?? m.color_table,
                })
              : m,
          );
        } else if (!existing && accept.add.has(lower)) {
          nextModes.push({
            key: newModeKey(),
            name: pm.name,
            channels: [...pm.channels],
            is_default: false,
            layout: pm.layout ?? null,
            color_policy: {},
            color_table: pm.color_table ?? null,
          });
        }
      }
      if (!nextModes.some((m) => m.is_default) && nextModes.length) {
        nextModes[0].is_default = true;
      }
      return {
        ...f,
        name:
          accept.name && pendingScan.suggested_name
            ? pendingScan.suggested_name
            : f.name,
        modes: nextModes,
      };
    });
    setFormNotes(pendingScan.notes || null);
    setPendingScan(null);
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        Loading…
      </div>
    );
  }

  const saveDisabled =
    saving ||
    builtin ||
    !form.name.trim() ||
    form.modes.length === 0 ||
    form.modes.some((m) => m.channels.length === 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {model ? (
            <ModelThumbnail model={model} size={56} />
          ) : (
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md bg-bg-elev text-xs uppercase tracking-wider text-muted ring-1 ring-line">
              new
            </div>
          )}
          <div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={() => navigate("/models")}
              >
                ← Models
              </button>
              {builtin && <span className="pill text-[10px]">built-in</span>}
            </div>
            <h1 className="mt-1 text-xl font-semibold">
              {model ? `Edit ${model.name}` : "New model"}
            </h1>
            <p className="text-sm text-muted">
              Channel layout, color behavior, and optional compound-fixture
              mapping.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {aiEnabled && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setManualPickerOpen(true)}
            >
              {isCreate ? "Create from manual…" : "Re-scan manual…"}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={() => navigate("/models")}
          >
            Cancel
          </button>
          <button
            type="submit"
            form="model-form"
            className="btn-primary"
            disabled={saveDisabled}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {builtin && (
        <div className="rounded-md bg-amber-900/20 p-2 text-xs text-amber-200 ring-1 ring-amber-700/40">
          Built-in models are read-only. Clone this model from the list to make
          changes.
        </div>
      )}

      <form
        id="model-form"
        onSubmit={submit}
        className="space-y-4"
      >
        {model && (
          <div className="flex flex-wrap items-start gap-3 rounded-lg bg-bg-elev p-3 ring-1 ring-line">
            <div className="h-20 w-20 shrink-0 overflow-hidden rounded-md bg-bg-card ring-1 ring-line">
              {model.image_url ? (
                <img
                  src={model.image_url}
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
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={triggerImagePick}
                  disabled={imageBusy}
                >
                  {imageBusy
                    ? "Uploading…"
                    : model.image_url
                      ? "Replace"
                      : "Upload"}
                </button>
                {model.image_url && (
                  <button
                    type="button"
                    className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
                    onClick={removeImage}
                    disabled={imageBusy}
                  >
                    Remove
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

        <label className="block max-w-lg">
          <span className="label mb-1 block">Name</span>
          <input
            className="input"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            disabled={builtin}
            required
          />
        </label>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[240px_1fr]">
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="label">Modes ({form.modes.length})</span>
              <button
                type="button"
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={addMode}
                disabled={builtin}
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
                    {mode.is_default && (
                      <span className="text-accent">★ </span>
                    )}
                    <span className="font-medium">
                      {mode.name || "(unnamed)"}
                    </span>
                    <span className="ml-1 text-xs text-muted">
                      {mode.channels.length}ch
                    </span>
                  </button>
                  <button
                    type="button"
                    className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
                    onClick={() => setDefault(mode.key)}
                    disabled={mode.is_default || builtin}
                    title="Set as default"
                  >
                    ★
                  </button>
                  <button
                    type="button"
                    className="btn-ghost !px-1.5 !py-0.5 text-[10px] text-rose-300"
                    onClick={() => removeMode(mode.key)}
                    disabled={form.modes.length <= 1 || builtin}
                    title="Delete mode"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-4">
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
                      disabled={builtin}
                      required
                    />
                  </label>
                  <label className="flex items-end gap-2 pb-1.5 text-sm text-slate-200">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-line bg-bg-elev text-accent"
                      checked={activeMode.is_default}
                      onChange={() => setDefault(activeMode.key)}
                      disabled={builtin}
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
                          disabled={builtin}
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
                          disabled={i === 0 || builtin}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="btn-ghost !px-2 !py-1"
                          onClick={() => moveChannel(i, 1)}
                          disabled={
                            i === activeMode.channels.length - 1 || builtin
                          }
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          className="btn-ghost !px-2 !py-1 text-rose-300"
                          onClick={() => removeChannel(i)}
                          disabled={builtin}
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
                        className="rounded-full bg-bg-elev px-2.5 py-1 text-xs ring-1 ring-line hover:bg-bg-card disabled:opacity-40"
                        onClick={() => addChannel(r)}
                        disabled={builtin}
                      >
                        + {r}
                      </button>
                    ))}
                  </div>
                </div>

                <ColorPolicySection
                  draft={activeMode}
                  onChange={(next) =>
                    setModeField(activeMode.key, "color_policy", next)
                  }
                />

                <ColorTablePanel
                  channels={activeMode.channels}
                  table={activeMode.color_table}
                  onChange={(next) =>
                    setModeField(activeMode.key, "color_table", next)
                  }
                />

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

      <ManualPicker
        open={manualPickerOpen}
        state={manualState}
        title={isCreate ? "Create from manual" : "Re-scan manual"}
        onClose={dismissManualPicker}
        onFile={handleManualFile}
        onReset={() => setManualState({ phase: "idle" })}
      />
    </div>
  );
}
