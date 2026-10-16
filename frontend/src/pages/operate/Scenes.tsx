import React, { useEffect, useState } from "react";
import { Api, Scene, State, Controller } from "../../api";
import { useAuth } from "../../auth";
import { useToast } from "../../toast";
import { useLayerStore } from "../../state/layers";
import SaveLook from "../../components/SaveLook";
import Modal from "../../components/Modal";
import RestoreLook from "../../components/RestoreLook";
type Look = { kind: "scene" | "state"; data: Scene | State };
export default function Scenes() {
  const [looks, setLooks] = useState<Look[]>([]),
    [controllers, setControllers] = useState<Controller[]>([]);
  const [search, setSearch] = useState(""),
    [favorites, setFavorites] = useState(false),
    [fade, setFade] = useState(1),
    [detail, setDetail] = useState<Look | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const { canOperate } = useAuth();
  const { push } = useToast();
  const { refresh: refreshLayers } = useLayerStore();
  const refresh = async () => {
    try {
      const [s, t, c] = await Promise.all([
        Api.listScenes(),
        Api.listStates(),
        Api.listControllers(),
      ]);
      setLooks([
        ...s
          .filter((x) => !x.builtin)
          .map((data) => ({ kind: "scene" as const, data })),
        ...t
          .filter((x) => !x.builtin)
          .map((data) => ({ kind: "state" as const, data })),
      ]);
      setControllers(c);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    void refresh();
  }, []);
  const filtered = looks
    .filter(
      (l) =>
        (!favorites || l.data.favorite) &&
        l.data.name.toLowerCase().includes(search.toLowerCase()),
    )
    .sort(
      (a, b) =>
        Number(!!b.data.favorite) - Number(!!a.data.favorite) ||
        a.data.name.localeCompare(b.data.name),
    );
  const scope = (l: Look) =>
    l.kind === "state" || (l.data as Scene).cross_controller
      ? "Across the rig"
      : controllers.find((c) => c.id === (l.data as Scene).controller_id)
          ?.name || "Controller";
  const apply = async (l: Look) => {
    if (l.data.id == null) return;
    setBusy(true);
    try {
      if (l.kind === "scene") await Api.applyScene(l.data.id, fade);
      else await Api.applyState(l.data.id, fade);
      await refreshLayers();
      push(`${l.data.name} applied`, "success");
      setDetail(null);
    } catch (e) {
      push(String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const favorite = async (l: Look) => {
    if (l.data.id == null) return;
    try {
      if (l.kind === "scene")
        await Api.updateScene(l.data.id, { favorite: !l.data.favorite });
      else await Api.updateState(l.data.id, { favorite: !l.data.favorite });
      await refresh();
    } catch (e) {
      push(String(e), "error");
    }
  };
  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Saved looks</h1>
        <p className="mt-1 text-sm text-muted">
          Scenes and rig states, ready to recall.
        </p>
      </header>
      <div className="flex flex-wrap gap-2">
        <SaveLook onSaved={refresh} />
        <RestoreLook />
      </div>
      <div className="card space-y-3 p-3">
        <input
          className="input"
          type="search"
          aria-label="Search saved looks"
          placeholder="Search saved looks…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button
            className={favorites ? "btn-primary" : "btn-secondary"}
            aria-pressed={favorites}
            onClick={() => setFavorites(!favorites)}
          >
            ★ Favorites
          </button>
          <label className="flex items-center gap-2 text-sm">
            Fade
            <select
              className="input !w-auto"
              aria-label="Scene fade duration"
              value={fade}
              onChange={(e) => setFade(Number(e.target.value))}
            >
              {[0, 0.5, 1, 2, 3, 5].map((s) => (
                <option key={s} value={s}>
                  {s === 0 ? "Instant" : `${s}s`}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-rose-200">
          {error}
        </p>
      )}
      {!filtered.length && (
        <div className="card p-6 text-center">
          <h2 className="font-semibold">
            {favorites ? "No favorites yet" : "Save your first look"}
          </h2>
          <p className="mt-2 text-sm text-muted">
            Set your lights, then tap Save current look. You can capture the
            whole rig, a controller, or selected fixtures.
          </p>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {filtered.map((l) => (
          <article key={`${l.kind}-${l.data.id}`} className="card p-3">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <h2 className="break-words font-semibold">{l.data.name}</h2>
                <p className="mt-1 text-xs text-muted">
                  {scope(l)} · {l.data.lights.length} fixtures
                  {l.kind === "scene" && !!(l.data as Scene).layers?.length
                    ? ` · ${(l.data as Scene).layers?.length} effects`
                    : ""}
                </p>
              </div>
              <button
                disabled={!canOperate}
                onClick={() => favorite(l)}
                className="btn-ghost"
                aria-label={`${l.data.favorite ? "Unfavorite" : "Favorite"} ${l.data.name}`}
              >
                {l.data.favorite ? "★" : "☆"}
              </button>
            </div>
            <button
              className="my-3 flex w-full overflow-hidden rounded-lg"
              aria-label={`Preview ${l.data.name}`}
              onClick={() => setDetail(l)}
            >
              {l.data.lights.slice(0, 16).map((c, i) => (
                <span
                  key={i}
                  className="h-12 flex-1"
                  style={{
                    backgroundColor: c.on
                      ? `rgb(${c.r},${c.g},${c.b})`
                      : "#10121b",
                  }}
                />
              ))}
            </button>
            <div className="flex gap-2">
              <button
                disabled={busy || !canOperate}
                className="btn-primary flex-1"
                onClick={() => apply(l)}
              >
                Apply · {fade === 0 ? "instant" : `${fade}s`}
              </button>
              <button className="btn-secondary" onClick={() => setDetail(l)}>
                Details
              </button>
            </div>
          </article>
        ))}
      </div>
      <Modal
        open={!!detail}
        title={detail?.data.name || "Look details"}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <div className="space-y-4">
            <p className="text-sm">
              {scope(detail)} · {detail.data.lights.length} fixtures. Applying
              replaces the looks and effects on these fixtures.
            </p>
            <button
              className="btn-primary w-full"
              disabled={busy || !canOperate}
              onClick={() => apply(detail)}
            >
              Apply this look
            </button>
            {canOperate && (
              <>
                <button
                  className="btn-secondary w-full"
                  onClick={async () => {
                    const name = window.prompt(
                      "Rename this look",
                      detail.data.name,
                    );
                    if (!name?.trim() || detail.data.id == null) return;
                    try {
                      if (detail.kind === "scene")
                        await Api.updateScene(detail.data.id, { name });
                      else await Api.updateState(detail.data.id, { name });
                      await refresh();
                      setDetail(null);
                    } catch (e) {
                      push(String(e), "error");
                    }
                  }}
                >
                  Rename
                </button>
                <button
                  className="btn-ghost w-full text-rose-200"
                  onClick={async () => {
                    if (
                      detail.data.id == null ||
                      !window.confirm(
                        `Delete saved look “${detail.data.name}”? Live output will stay as it is.`,
                      )
                    )
                      return;
                    try {
                      if (detail.kind === "scene")
                        await Api.deleteScene(detail.data.id);
                      else await Api.deleteState(detail.data.id);
                      setDetail(null);
                      await refresh();
                    } catch (e) {
                      push(String(e), "error");
                    }
                  }}
                >
                  Delete saved look
                </button>
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
