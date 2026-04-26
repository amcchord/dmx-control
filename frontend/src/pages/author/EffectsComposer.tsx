import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Api,
  Effect,
  EffectLayer,
  EffectTargetChannel,
  LAYER_BLEND_MODES,
  LayerBlendMode,
  Light,
  Palette,
} from "../../api";
import { useViewport } from "../../hooks/useViewport";
import { useLayerStore } from "../../state/layers";
import { useSelection } from "../../state/selection";
import { useUndo } from "../../state/undo";
import RigPreview from "../../components/RigPreview";
import LiveLayersPanel from "../../components/LiveLayersPanel";
import EffectParamsForm from "../../components/EffectParamsForm";
import LuaEditor from "../../components/LuaEditor";
import PaletteSwatch from "../../components/PaletteSwatch";
import { useToast } from "../../toast";

const TARGET_CHANNELS: EffectTargetChannel[] = [
  "rgb",
  "w",
  "a",
  "uv",
  "dimmer",
  "strobe",
];

/** Desktop Effects Composer (replaces ``/effects``).
 *
 * Layout (large screens):
 *
 *   library | composer (rig + Lua + params) | layers
 *
 * The Live rail (with running layers + engine telemetry) is the right
 * column owned by ``AppShell``, so this page focuses on authoring +
 * pushing onto the stack. Mobile users get a redirect since heavy Lua
 * editing isn't a phone activity. */
export default function EffectsComposer() {
  const { isMobile } = useViewport();
  const { layers, patchLayer } = useLayerStore();
  const { selection } = useSelection();
  const { push: notify } = useToast();
  const { push: pushUndo } = useUndo();

  const [effects, setEffects] = useState<Effect[]>([]);
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [search, setSearch] = useState("");
  const [selectedEffect, setSelectedEffect] = useState<Effect | null>(null);
  const [selectedLayerId, setSelectedLayerId] = useState<number | null>(null);
  const [editingSource, setEditingSource] = useState<string>("");
  const [editingDirty, setEditingDirty] = useState(false);
  const [showSource, setShowSource] = useState(false);

  const refresh = async () => {
    const [e, p, l] = await Promise.all([
      Api.listEffects().catch(() => []),
      Api.listPalettes().catch(() => []),
      Api.listLights().catch(() => []),
    ]);
    setEffects(e);
    setPalettes(p);
    setLights(l);
  };

  useEffect(() => {
    void refresh();
  }, []);

  const palettesById = useMemo(
    () => new Map(palettes.map((p) => [p.id, p])),
    [palettes],
  );

  // Pick a layer the inspector edits; default to top of stack.
  const selectedLayer = useMemo<EffectLayer | null>(() => {
    if (selectedLayerId == null) return null;
    return (
      layers.find((l) => l.layer_id === selectedLayerId) ?? null
    );
  }, [layers, selectedLayerId]);

  useEffect(() => {
    if (selectedLayer == null && layers.length > 0) {
      const top = layers[layers.length - 1];
      if (top.layer_id != null) setSelectedLayerId(top.layer_id);
    }
  }, [layers, selectedLayer]);

  useEffect(() => {
    if (selectedEffect) {
      setEditingSource(selectedEffect.source);
      setEditingDirty(false);
    }
  }, [selectedEffect]);

  const filteredEffects = useMemo(() => {
    if (!search.trim()) return effects;
    const q = search.toLowerCase();
    return effects.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        (e.description ?? "").toLowerCase().includes(q),
    );
  }, [effects, search]);

  const onLaunch = async (e: Effect) => {
    const mask =
      selection.lightIds.size > 0 ? [...selection.lightIds] : undefined;
    try {
      const created = await Api.createLayer({
        effect_id: e.id,
        mask_light_ids: mask,
      });
      pushUndo({
        label: `Add layer ${e.name}`,
        undo: async () => {
          if (created.layer_id != null) await Api.deleteLayer(created.layer_id);
        },
        redo: async () => {
          await Api.createLayer({
            effect_id: e.id,
            mask_light_ids: mask,
          });
        },
      });
      notify(`+ ${e.name}`, "success");
    } catch (err) {
      notify(String(err), "error");
    }
  };

  const onSaveSource = async () => {
    if (!selectedEffect) return;
    try {
      const lint = await Api.lintEffect(editingSource);
      if (!lint.ok) {
        notify(lint.error?.message ?? "Lua compile error", "error");
        return;
      }
      const next = await Api.updateEffect(selectedEffect.id, {
        name: selectedEffect.name,
        source: editingSource,
        palette_id: selectedEffect.palette_id,
        light_ids: selectedEffect.light_ids,
        targets: selectedEffect.targets,
        spread: selectedEffect.spread,
        params: selectedEffect.params,
        controls: selectedEffect.controls,
        target_channels: selectedEffect.target_channels,
      });
      setSelectedEffect(next);
      setEffects((prev) =>
        prev.map((e) => (e.id === next.id ? next : e)),
      );
      setEditingDirty(false);
      notify("Saved", "success");
    } catch (err) {
      notify(String(err), "error");
    }
  };

  if (isMobile) {
    return (
      <div className="card p-6 text-center">
        <div className="text-base font-semibold">Effects Composer</div>
        <p className="mt-2 text-sm text-muted">
          Layered effect authoring lives on desktop. On mobile, use{" "}
          <Link to="/quick-fx" className="text-accent hover:underline">
            Quick FX
          </Link>{" "}
          to launch presets and the layer pill on Now Playing to manage
          the running stack.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[14rem_minmax(0,1fr)_18rem] gap-4">
      {/* Library */}
      <aside className="flex flex-col gap-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          placeholder="Search presets..."
          className="input"
        />
        <div className="card flex max-h-[70vh] flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-3 py-2 text-xs">
            <span className="font-semibold uppercase tracking-wider text-muted">
              Library
            </span>
            <span className="text-[10px] text-muted">
              {filteredEffects.length}
            </span>
          </div>
          <ul className="flex-1 overflow-y-auto p-1.5">
            {filteredEffects.map((e) => (
              <li
                key={e.id}
                className={
                  "group flex items-center gap-2 rounded-md p-2 text-sm hover:bg-bg-elev " +
                  (selectedEffect?.id === e.id ? "bg-bg-elev ring-1 ring-line" : "")
                }
              >
                <button
                  onClick={() => setSelectedEffect(e)}
                  className="min-w-0 flex-1 truncate text-left"
                  title={e.description ?? e.name}
                >
                  <div className="truncate font-medium">{e.name}</div>
                  {e.description && (
                    <div className="truncate text-[10px] text-muted">
                      {e.description}
                    </div>
                  )}
                </button>
                <button
                  onClick={() => onLaunch(e)}
                  className="invisible rounded-md bg-emerald-700/40 px-2 py-1 text-[10px] uppercase tracking-wider text-emerald-100 hover:bg-emerald-600 group-hover:visible"
                  title="Push as new top layer"
                >
                  + Layer
                </button>
                {!e.builtin && (
                  <button
                    onClick={async (ev) => {
                      ev.stopPropagation();
                      if (!window.confirm(`Delete "${e.name}"?`)) return;
                      try {
                        await Api.deleteEffect(e.id);
                        if (selectedEffect?.id === e.id) {
                          setSelectedEffect(null);
                        }
                        await refresh();
                        notify(`Deleted ${e.name}`, "success");
                      } catch (err) {
                        notify(String(err), "error");
                      }
                    }}
                    className="invisible inline-flex h-6 w-6 items-center justify-center rounded-full text-muted hover:bg-rose-900/40 hover:text-rose-200 group-hover:visible"
                    title="Delete preset"
                    aria-label={`Delete ${e.name}`}
                  >
                    {"\u00D7"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      </aside>

      {/* Composer */}
      <section className="flex min-w-0 flex-col gap-3">
        <div className="card flex flex-col gap-2 p-3">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-widest text-muted">
              Live preview
            </div>
            <div className="flex gap-2">
              {selectedEffect && (
                <button
                  className="btn-primary text-xs"
                  onClick={() => onLaunch(selectedEffect)}
                >
                  + Push as layer
                </button>
              )}
            </div>
          </div>
          <RigPreview lights={lights} compact size="md" />
        </div>

        {selectedEffect ? (
          <div className="card flex flex-col gap-3 p-3">
            <header className="flex items-center justify-between">
              <div>
                <div className="text-base font-semibold">
                  {selectedEffect.name}
                  {selectedEffect.builtin && (
                    <span className="ml-2 rounded bg-bg-elev px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted">
                      built-in
                    </span>
                  )}
                </div>
                {selectedEffect.description && (
                  <div className="text-xs text-muted">
                    {selectedEffect.description}
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    const next = await Api.cloneEffect(selectedEffect.id);
                    await refresh();
                    setSelectedEffect(next);
                  }}
                  className="btn-ghost text-xs"
                >
                  Clone
                </button>
                {!selectedEffect.builtin && (
                  <button
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `Delete "${selectedEffect.name}"?`,
                        )
                      )
                        return;
                      await Api.deleteEffect(selectedEffect.id);
                      await refresh();
                      setSelectedEffect(null);
                    }}
                    className="btn-ghost text-xs text-rose-300 hover:text-rose-100"
                  >
                    Delete
                  </button>
                )}
              </div>
            </header>

            {selectedEffect.palette_id != null && (
              <PaletteSwatch
                colors={
                  palettesById.get(selectedEffect.palette_id)?.colors ?? []
                }
              />
            )}

            <div>
              <div className="label mb-1">Parameters</div>
              <EffectParamsForm
                schema={selectedEffect.param_schema}
                values={selectedEffect.params}
                onChange={(next) =>
                  setSelectedEffect({ ...selectedEffect, params: next })
                }
              />
            </div>

            <div className="flex flex-wrap gap-1">
              <span className="label !text-[10px]">Channels:</span>
              {TARGET_CHANNELS.map((tc) => (
                <button
                  key={tc}
                  onClick={() => {
                    if (selectedEffect.builtin) return;
                    const cur = new Set(selectedEffect.target_channels);
                    if (cur.has(tc)) cur.delete(tc);
                    else cur.add(tc);
                    setSelectedEffect({
                      ...selectedEffect,
                      target_channels: [...cur].length
                        ? ([...cur] as EffectTargetChannel[])
                        : ["rgb"],
                    });
                  }}
                  disabled={selectedEffect.builtin}
                  className={
                    "rounded-md px-2 py-0.5 text-[10px] uppercase ring-1 " +
                    (selectedEffect.target_channels.includes(tc)
                      ? "bg-accent text-white ring-accent"
                      : "bg-bg-elev text-muted ring-line")
                  }
                >
                  {tc}
                </button>
              ))}
            </div>

            <button
              onClick={() => setShowSource((v) => !v)}
              className="btn-ghost self-start text-[11px]"
            >
              {showSource ? "Hide" : "Show"} Lua source
            </button>
            {showSource && (
              <div className="rounded-md ring-1 ring-line">
                <LuaEditor
                  value={editingSource}
                  onChange={(v) => {
                    setEditingSource(v);
                    setEditingDirty(true);
                  }}
                  readOnly={selectedEffect.builtin}
                />
                {!selectedEffect.builtin && editingDirty && (
                  <div className="flex justify-end gap-2 border-t border-line p-2">
                    <button
                      onClick={() => {
                        setEditingSource(selectedEffect.source);
                        setEditingDirty(false);
                      }}
                      className="btn-ghost text-xs"
                    >
                      Revert
                    </button>
                    <button
                      onClick={onSaveSource}
                      className="btn-primary text-xs"
                    >
                      Save script
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="card p-6 text-center text-sm text-muted">
            Pick an effect from the library, or build one in the AI{" "}
            <Link
              to="/author/designer"
              className="text-accent hover:underline"
            >
              Designer
            </Link>
            .
          </div>
        )}
      </section>

      {/* Inspector for the currently-selected layer */}
      <aside className="flex flex-col gap-3">
        <LiveLayersPanel variant="full" />
        {selectedLayer && (
          <LayerInspector
            layer={selectedLayer}
            allLights={lights}
            onPatch={(patch) => {
              if (selectedLayer.layer_id != null)
                void patchLayer(selectedLayer.layer_id, patch).catch(() => null);
            }}
          />
        )}
      </aside>
    </div>
  );
}

function LayerInspector({
  layer,
  allLights,
  onPatch,
}: {
  layer: EffectLayer;
  allLights: Light[];
  onPatch: (patch: Record<string, unknown>) => void;
}) {
  const [maskOpen, setMaskOpen] = useState(false);
  const maskSet = new Set(layer.mask_light_ids);
  return (
    <div className="card flex flex-col gap-2 p-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted">
        Layer inspector
      </div>
      <div className="text-sm font-semibold">{layer.name}</div>
      <div>
        <label className="label !text-[10px]">Blend</label>
        <select
          value={layer.blend_mode}
          onChange={(e) =>
            onPatch({ blend_mode: e.currentTarget.value as LayerBlendMode })
          }
          className="input"
        >
          {LAYER_BLEND_MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="label !text-[10px]">
          Opacity {(layer.opacity * 100).toFixed(0)}%
        </label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={layer.opacity}
          onChange={(e) =>
            onPatch({ opacity: parseFloat(e.currentTarget.value) })
          }
          className="w-full accent-accent"
        />
      </div>
      <div>
        <label className="label !text-[10px]">
          Intensity {(layer.intensity * 100).toFixed(0)}%
        </label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={layer.intensity}
          onChange={(e) =>
            onPatch({ intensity: parseFloat(e.currentTarget.value) })
          }
          className="w-full accent-accent"
        />
      </div>
      <button
        className="btn-secondary text-xs"
        onClick={() => setMaskOpen((v) => !v)}
      >
        Mask: {maskSet.size === 0 ? "all lights" : `${maskSet.size} lights`}
      </button>
      {maskOpen && (
        <div className="max-h-48 overflow-y-auto rounded-md ring-1 ring-line">
          {allLights.map((l) => (
            <label
              key={l.id}
              className="flex items-center gap-2 px-2 py-1 text-xs hover:bg-bg-elev"
            >
              <input
                type="checkbox"
                checked={maskSet.has(l.id)}
                onChange={(e) => {
                  const next = new Set(maskSet);
                  if (e.currentTarget.checked) next.add(l.id);
                  else next.delete(l.id);
                  onPatch({ mask_light_ids: [...next] });
                }}
              />
              {l.name}
            </label>
          ))}
        </div>
      )}
      <div>
        <label className="label !text-[10px]">Z-index {layer.z_index}</label>
        <div className="flex gap-1">
          <button
            onClick={() => onPatch({ z_index: layer.z_index - 50 })}
            className="btn-secondary flex-1 text-xs"
          >
            Lower
          </button>
          <button
            onClick={() => onPatch({ z_index: layer.z_index + 50 })}
            className="btn-secondary flex-1 text-xs"
          >
            Raise
          </button>
        </div>
      </div>
    </div>
  );
}
