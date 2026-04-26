import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Api,
  Controller,
  EffectLayer,
  Light,
  Scene,
  SceneSavedLayer,
} from "../../api";
import { useViewport } from "../../hooks/useViewport";
import { useLayerStore } from "../../state/layers";
import RigPreview from "../../components/RigPreview";
import { useToast } from "../../toast";

/** Desktop Scene Composer.
 *
 * A scene = base snapshot + ordered layer stack. The composer lets the
 * operator capture the rig's current state, then snapshot the running
 * layers alongside it so applying the scene later restores both at
 * once. */
export default function SceneComposer() {
  const { isMobile } = useViewport();
  const { layers, refresh: refreshLayers } = useLayerStore();
  const { push: notify } = useToast();
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    const [s, c, l] = await Promise.all([
      Api.listScenes().catch(() => []),
      Api.listControllers().catch(() => []),
      Api.listLights().catch(() => []),
    ]);
    setScenes(s);
    setControllers(c);
    setLights(l);
  };

  useEffect(() => {
    void refresh();
  }, []);

  const selected = scenes.find((s) => s.id === selectedId) ?? null;

  const onCreateScene = async (cid: number) => {
    const name = window.prompt("Scene name?");
    if (!name) return;
    setBusy(true);
    try {
      const created = await Api.createScene({
        name,
        controller_id: cid,
        cross_controller: false,
        from_rendered: true,
      });
      // Tack on running layers as saved layers, if present.
      if (layers.length > 0 && created.id != null) {
        const saved: SceneSavedLayer[] = layers
          .filter((l) => l.layer_id != null && l.effect_id != null)
          .map(layerToSaved);
        await Api.updateScene(created.id, { layers: saved });
      }
      await refresh();
      notify(`Saved scene ${name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const onApply = async (s: Scene) => {
    if (s.id == null) return;
    setBusy(true);
    try {
      await Api.applyScene(s.id);
      await refreshLayers();
      notify(`Applied ${s.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (s: Scene) => {
    if (s.id == null) return;
    if (!window.confirm(`Delete scene "${s.name}"?`)) return;
    try {
      await Api.deleteScene(s.id);
      setSelectedId(null);
      await refresh();
    } catch (e) {
      notify(String(e), "error");
    }
  };

  if (isMobile) {
    return (
      <div className="card p-6 text-center">
        <div className="text-base font-semibold">Scene Composer</div>
        <p className="mt-2 text-sm text-muted">
          Composing layered scenes is a desktop activity. On mobile, see{" "}
          <Link to="/scenes" className="text-accent hover:underline">
            Scenes
          </Link>{" "}
          to apply existing scenes.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[16rem_minmax(0,1fr)_18rem] gap-4">
      <aside className="card flex flex-col overflow-hidden">
        <div className="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-widest text-muted">
          Scenes
        </div>
        <ul className="flex-1 overflow-y-auto p-1.5">
          {controllers.map((c) => {
            const list = scenes.filter(
              (s) => !s.builtin && s.controller_id === c.id,
            );
            return (
              <li key={c.id} className="mb-2">
                <div className="px-2 text-[10px] uppercase tracking-wider text-muted">
                  {c.name}
                </div>
                {list.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setSelectedId(s.id ?? null)}
                    className={
                      "block w-full truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-bg-elev " +
                      (selectedId === s.id ? "bg-bg-elev ring-1 ring-line" : "")
                    }
                  >
                    {s.name}
                    {s.layers && s.layers.length > 0 && (
                      <span className="ml-1 text-[10px] text-accent">
                        +{s.layers.length}
                      </span>
                    )}
                  </button>
                ))}
                <button
                  onClick={() => onCreateScene(c.id)}
                  disabled={busy}
                  className="mt-1 block w-full rounded-md bg-bg-elev px-2 py-1.5 text-left text-xs text-muted hover:bg-bg-card"
                >
                  + Capture from {c.name}
                </button>
              </li>
            );
          })}
        </ul>
      </aside>

      <section className="flex flex-col gap-3">
        <div className="card p-3">
          <div className="text-xs uppercase tracking-widest text-muted">
            Live preview
          </div>
          <div className="mt-2">
            <RigPreview lights={lights} compact size="md" />
          </div>
        </div>

        {selected ? (
          <div className="card p-3">
            <header className="flex items-center justify-between">
              <div>
                <div className="text-base font-semibold">{selected.name}</div>
                <div className="text-xs text-muted">
                  {selected.lights.length} fixtures captured
                  {selected.layers && selected.layers.length > 0 && (
                    <> · {selected.layers.length} saved layers</>
                  )}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => onApply(selected)}
                  className="btn-primary text-xs"
                  disabled={busy}
                >
                  Apply
                </button>
                <button
                  onClick={() => onDelete(selected)}
                  className="btn-ghost text-xs text-rose-300 hover:text-rose-100"
                >
                  Delete
                </button>
              </div>
            </header>
            <SavedLayersList saved={selected.layers ?? []} />
          </div>
        ) : (
          <div className="card p-6 text-center text-sm text-muted">
            Pick a scene from the left to inspect, or capture the current
            state into a new scene.
          </div>
        )}
      </section>

      <aside className="card p-3">
        <div className="text-xs font-semibold uppercase tracking-widest text-muted">
          Running stack
        </div>
        <p className="mt-1 text-[11px] text-muted">
          When you capture a new scene, every running layer is saved alongside
          the base snapshot so applying the scene later restores both.
        </p>
        <ul className="mt-2 flex flex-col gap-1">
          {layers
            .slice()
            .reverse()
            .map((l) => (
              <li
                key={l.handle}
                className="flex items-center justify-between rounded-md bg-bg-elev px-2 py-1 text-xs"
              >
                <span className="truncate">{l.name}</span>
                <span className="font-mono text-[10px] text-muted">
                  {l.blend_mode} · {(l.opacity * 100).toFixed(0)}%
                </span>
              </li>
            ))}
          {layers.length === 0 && (
            <li className="rounded-md border border-dashed border-line p-3 text-center text-[11px] text-muted">
              No layers running.
            </li>
          )}
        </ul>
      </aside>
    </div>
  );
}

function SavedLayersList({ saved }: { saved: SceneSavedLayer[] }) {
  if (saved.length === 0) {
    return (
      <div className="mt-3 rounded-md border border-dashed border-line p-4 text-center text-xs text-muted">
        Base snapshot only. Capture a new scene with effects running to
        save them as layers.
      </div>
    );
  }
  return (
    <ul className="mt-3 flex flex-col gap-1.5">
      {saved.map((l, idx) => (
        <li
          key={idx}
          className="flex items-center justify-between rounded-md bg-bg-elev px-2 py-1.5 text-xs ring-1 ring-line"
        >
          <span className="font-mono text-[10px] text-muted">
            {l.z_index ?? idx * 100 + 100}
          </span>
          <span className="ml-2 flex-1 truncate font-medium">
            {l.name ?? `Effect #${l.effect_id}`}
          </span>
          <span className="font-mono text-[10px] text-muted">
            {l.blend_mode ?? "normal"} · {((l.opacity ?? 1) * 100).toFixed(0)}%
          </span>
        </li>
      ))}
    </ul>
  );
}

function layerToSaved(layer: EffectLayer): SceneSavedLayer {
  return {
    effect_id: layer.effect_id!,
    name: layer.name,
    z_index: layer.z_index,
    blend_mode: layer.blend_mode,
    opacity: layer.opacity,
    intensity: layer.intensity,
    target_channels: layer.target_channels,
    mask_light_ids: layer.mask_light_ids,
  };
}
