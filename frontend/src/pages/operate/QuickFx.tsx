import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Api,
  Effect,
  Palette,
  Light,
  Controller,
  LayerBlendMode,
  LAYER_BLEND_MODES,
  EffectParams,
} from "../../api";
import { useSelection } from "../../state/selection";
import { useLayerStore } from "../../state/layers";
import { useAuth } from "../../auth";
import { useToast } from "../../toast";
import Modal from "../../components/Modal";
import EffectPreview from "../../components/EffectPreview";
import EffectParamsForm from "../../components/EffectParamsForm";
import PaletteSwatch from "../../components/PaletteSwatch";
export default function QuickFx() {
  const [effects, setEffects] = useState<Effect[]>([]),
    [palettes, setPalettes] = useState<Palette[]>([]),
    [lights, setLights] = useState<Light[]>([]);
  const [controllers, setControllers] = useState<Controller[]>([]);
  useEffect(() => {
    void Api.listControllers().then(setControllers);
  }, []);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Effect | null>(null);
  const [error, setError] = useState("");
  const [query] = useSearchParams();
  const { selection } = useSelection();
  const { layers } = useLayerStore();
  useEffect(() => {
    Promise.all([Api.listEffects(), Api.listPalettes(), Api.listLights()])
      .then(([e, p, l]) => {
        setEffects(e);
        setPalettes(p);
        setLights(l);
        const id = Number(query.get("effect"));
        if (id) setSelected(e.find((x) => x.id === id) || null);
      })
      .catch((e) => setError(String(e)));
  }, []);
  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Effects</h1>
        <p className="mt-1 text-sm text-muted">
          Preview a look, choose its fixtures, then launch it live.
        </p>
      </header>
      <div className="rounded-xl bg-violet-950/40 p-3 text-sm ring-1 ring-violet-800">
        {selection.lightIds.size
          ? `${selection.lightIds.size} fixtures selected`
          : "No selection · choose whole rig or a controller before launch"}
        <Link to="/lights" className="btn-ghost ml-1">
          Choose fixtures
        </Link>
      </div>
      <input
        className="input"
        type="search"
        aria-label="Search effects"
        placeholder="Search effects…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {error && (
        <p role="alert" className="text-rose-200">
          {error}
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 min-[380px]:grid-cols-2 xl:grid-cols-3">
        {effects
          .filter((e) =>
            `${e.name} ${e.description}`
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((e) => (
            <article className="card flex flex-col gap-3 p-3" key={e.id}>
              <div className="flex gap-2">
                <h2 className="min-w-0 flex-1 break-words font-semibold">
                  {e.name}
                </h2>
                {layers.some((l) => l.effect_id === e.id) && (
                  <span className="pill self-start text-emerald-200">Live</span>
                )}
              </div>
              <p className="text-xs text-muted">{e.description}</p>
              <PaletteSwatch
                colors={
                  palettes
                    .find((p) => p.id === e.palette_id)
                    ?.colors.slice(0, 6) || ["#9b75ff"]
                }
              />
              <button
                className="btn-secondary mt-auto"
                onClick={() => setSelected(e)}
              >
                Preview & configure
              </button>
            </article>
          ))}
      </div>
      {selected && (
        <Configure
          key={selected.id}
          effect={selected}
          lights={lights}
          controllers={controllers}
          palettes={palettes}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
function Configure({
  effect,
  lights,
  controllers: controllerList,
  palettes,
  onClose,
}: {
  effect: Effect;
  lights: Light[];
  controllers: Controller[];
  palettes: Palette[];
  onClose: () => void;
}) {
  const { selection } = useSelection();
  const { canOperate } = useAuth();
  const { refresh } = useLayerStore();
  const { push } = useToast();
  const [scope, setScope] = useState(
    selection.lightIds.size ? "selection" : "",
  );
  const [blend, setBlend] = useState<LayerBlendMode>("normal");
  const [opacity, setOpacity] = useState(1);
  const [params, setParams] = useState<EffectParams>(effect.params);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controllers = [...new Set(lights.map((l) => l.controller_id))];
  const ids = lights
    .filter(
      (l) =>
        scope === "rig" ||
        (scope === "selection"
          ? selection.lightIds.has(l.id)
          : l.controller_id === Number(scope)),
    )
    .map((l) => l.id);
  const launch = async () => {
    if (!ids.length) return;
    setBusy(true);
    setError("");
    try {
      await Api.createLayer({
        effect_id: effect.id,
        mask_light_ids: ids,
        light_ids: ids,
        opacity,
        blend_mode: blend,
        params_override: params,
      });
      await refresh();
      push(`${effect.name} live on ${ids.length} fixtures`, "success");
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      title={effect.name}
      onClose={onClose}
      footer={
        <button
          className="btn-primary w-full"
          disabled={busy || !ids.length || !canOperate}
          onClick={launch}
        >
          {busy ? "Launching…" : `Launch live · ${ids.length} fixtures`}
        </button>
      }
    >
      <div className="space-y-4">
        <EffectPreview
          effect={effect}
          params={params}
          palette={
            palettes.find((p) => p.id === effect.palette_id)?.colors || [
              "#ffffff",
            ]
          }
          intensity={opacity}
        />
        <label className="block">
          <span className="label">Send to</span>
          <select
            className="input mt-1"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="">Choose target…</option>
            <option value="selection" disabled={!selection.lightIds.size}>
              Selected fixtures ({selection.lightIds.size})
            </option>
            <option value="rig">Whole rig ({lights.length} fixtures)</option>
            {controllers.map((cid) => (
              <option key={cid} value={cid}>
                {controllerList.find((c) => c.id === cid)?.name ||
                  `Controller #${cid}`}{" "}
                ({lights.filter((l) => l.controller_id === cid).length}{" "}
                fixtures)
              </option>
            ))}
          </select>
        </label>
        <p className="text-xs text-muted break-words">
          {ids.length
            ? lights
                .filter((l) => ids.includes(l.id))
                .map((l) => l.name)
                .join(" · ")
            : "Nothing will launch until you choose a target."}
        </p>
        <label className="block">
          <span className="label">Opacity · {Math.round(opacity * 100)}%</span>
          <input
            className="w-full"
            aria-label="Effect opacity"
            type="range"
            min="0"
            max="1"
            step=".01"
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
          />
        </label>
        {effect.param_schema.length > 0 && (
          <EffectParamsForm
            schema={effect.param_schema}
            values={params}
            onChange={setParams}
          />
        )}
        <details>
          <summary className="btn-secondary cursor-pointer">
            Layer blending
          </summary>
          <select
            aria-label="Blend mode"
            className="input mt-2"
            value={blend}
            onChange={(e) => setBlend(e.target.value as LayerBlendMode)}
          >
            {LAYER_BLEND_MODES.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </details>
        {error && (
          <p role="alert" className="text-rose-200">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
