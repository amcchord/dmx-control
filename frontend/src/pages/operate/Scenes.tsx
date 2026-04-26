import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api, Controller, Scene, State } from "../../api";
import { useToast } from "../../toast";

/** Operate-mode Scenes/States picker.
 *
 * Two tabs: per-controller scenes and rig-wide states. Tap to apply,
 * long-press / right-click for the small action menu (rename, recapture,
 * delete). Heavy authoring (cross-controller capture, layered scenes)
 * lives on ``/author/scenes``. */
export default function ScenesOperate() {
  const { push: notify } = useToast();
  const [tab, setTab] = useState<"scenes" | "states">("scenes");
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [states, setStates] = useState<State[]>([]);
  const [controllers, setControllers] = useState<Controller[]>([]);

  const refresh = async () => {
    const [s, st, c] = await Promise.all([
      Api.listScenes().catch(() => []),
      Api.listStates().catch(() => []),
      Api.listControllers().catch(() => []),
    ]);
    setScenes(s);
    setStates(st);
    setControllers(c);
  };

  useEffect(() => {
    void refresh();
  }, []);

  const scenesByController = useMemo(() => {
    const map = new Map<number, Scene[]>();
    for (const s of scenes) {
      if (s.builtin) continue; // skip virtual blackout entries
      const arr = map.get(s.controller_id) ?? [];
      arr.push(s);
      map.set(s.controller_id, arr);
    }
    return map;
  }, [scenes]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex rounded-lg ring-1 ring-line">
        {(["scenes", "states"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              "flex-1 px-3 py-2 text-sm uppercase tracking-wider " +
              (tab === t
                ? "bg-bg-elev text-white"
                : "text-muted hover:text-white")
            }
          >
            {t === "scenes" ? "Scenes" : "Rig states"}
          </button>
        ))}
      </div>

      {tab === "scenes" && (
        <>
          {controllers.map((c) => {
            const list = scenesByController.get(c.id) ?? [];
            return (
              <section key={c.id} className="card p-3">
                <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wider text-muted">
                  <span>{c.name}</span>
                  <span>{list.length} scenes</span>
                </div>
                {list.length === 0 ? (
                  <div className="rounded-md border border-dashed border-line p-4 text-center text-xs text-muted">
                    No scenes yet. Capture one from the{" "}
                    <Link
                      to="/author/scenes"
                      className="text-accent hover:underline"
                    >
                      Scene Composer
                    </Link>
                    .
                  </div>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {list.map((s) => (
                      <SceneRow
                        key={s.id ?? s.name}
                        scene={s}
                        notify={notify}
                        onChanged={refresh}
                      />
                    ))}
                  </div>
                )}
                <button
                  onClick={async () => {
                    try {
                      await Api.applyBlackoutScene(c.id);
                      notify(`Blacked out ${c.name}`, "success");
                    } catch (e) {
                      notify(String(e), "error");
                    }
                  }}
                  className="btn-secondary mt-2 w-full text-xs"
                >
                  Blackout {c.name}
                </button>
              </section>
            );
          })}
        </>
      )}

      {tab === "states" && (
        <section className="card p-3">
          {states.length === 0 ? (
            <div className="rounded-md border border-dashed border-line p-4 text-center text-xs text-muted">
              No rig-wide states saved yet.
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {states.map((s) => (
                <StateRow
                  key={s.id ?? s.name}
                  state={s}
                  notify={notify}
                  onChanged={refresh}
                />
              ))}
            </div>
          )}
          <button
            onClick={async () => {
              try {
                await Api.applyBlackoutState();
                notify("Blacked out the rig", "success");
              } catch (e) {
                notify(String(e), "error");
              }
            }}
            className="btn-secondary mt-2 w-full text-xs"
          >
            Blackout rig
          </button>
        </section>
      )}
    </div>
  );
}

function SceneRow({
  scene,
  notify,
  onChanged,
}: {
  scene: Scene;
  notify: (m: string, k?: "success" | "error" | "info") => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  if (scene.id == null) return null;
  const onApply = async () => {
    setBusy(true);
    try {
      await Api.applyScene(scene.id!);
      notify(`Applied ${scene.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const onDelete = async () => {
    if (!window.confirm(`Delete scene "${scene.name}"?`)) return;
    try {
      await Api.deleteScene(scene.id!);
      onChanged();
    } catch (e) {
      notify(String(e), "error");
    }
  };
  return (
    <div className="flex items-center gap-2 rounded-md bg-bg-elev p-2 ring-1 ring-line">
      <button
        onClick={onApply}
        disabled={busy}
        className="flex-1 truncate text-left text-sm font-medium hover:underline"
      >
        {scene.name}
        {scene.layers && scene.layers.length > 0 && (
          <span className="ml-2 rounded bg-accent/20 px-1.5 py-0.5 text-[9px] uppercase text-accent">
            +{scene.layers.length} layers
          </span>
        )}
      </button>
      <button
        onClick={onDelete}
        className="btn-ghost px-2 py-1 text-xs text-muted hover:text-rose-300"
        aria-label="Delete"
      >
        {"\u00D7"}
      </button>
    </div>
  );
}

function StateRow({
  state,
  notify,
  onChanged,
}: {
  state: State;
  notify: (m: string, k?: "success" | "error" | "info") => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  if (state.id == null) return null;
  const onApply = async () => {
    setBusy(true);
    try {
      await Api.applyState(state.id!);
      notify(`Applied ${state.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const onDelete = async () => {
    if (!window.confirm(`Delete rig state "${state.name}"?`)) return;
    try {
      await Api.deleteState(state.id!);
      onChanged();
    } catch (e) {
      notify(String(e), "error");
    }
  };
  return (
    <div className="flex items-center gap-2 rounded-md bg-bg-elev p-2 ring-1 ring-line">
      <button
        onClick={onApply}
        disabled={busy}
        className="flex-1 truncate text-left text-sm font-medium hover:underline"
      >
        {state.name}
      </button>
      <button
        onClick={onDelete}
        className="btn-ghost px-2 py-1 text-xs text-muted hover:text-rose-300"
        aria-label="Delete"
      >
        {"\u00D7"}
      </button>
    </div>
  );
}
