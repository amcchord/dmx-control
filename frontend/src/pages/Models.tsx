import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Api, LightModel } from "../api";
import { useToast } from "../toast";
import ModelThumbnail from "./models/ModelThumbnail";
import { ROLE_COLORS } from "./models/types";

/** Light Models list page.
 *
 * Each model card links to the dedicated ``/models/:id/edit`` page; the
 * list intentionally stays small and card-based so the full-screen
 * editor has room to breathe. */
export default function Models() {
  const navigate = useNavigate();
  const toast = useToast();
  const [models, setModels] = useState<LightModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [aiEnabled, setAiEnabled] = useState(false);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clone = async (m: LightModel) => {
    try {
      const created = await Api.cloneModel(m.id);
      toast.push("Cloned", "success");
      navigate(`/models/${created.id}/edit`);
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Light Models</h1>
          <p className="text-sm text-muted">
            Channel layouts for your fixtures.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {aiEnabled && (
            <button
              className="btn-secondary"
              onClick={() => navigate("/models/new?manual=1")}
            >
              Create from manual…
            </button>
          )}
          <button
            className="btn-primary"
            onClick={() => navigate("/models/new")}
          >
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
                      <button
                        className="btn-ghost"
                        onClick={() => navigate(`/models/${m.id}/edit`)}
                      >
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
    </div>
  );
}
