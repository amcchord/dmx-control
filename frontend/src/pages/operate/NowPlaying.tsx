import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Api,
  Controller,
  Effect,
  Light,
  LightModel,
  Scene,
  State,
} from "../../api";
import { useViewport } from "../../hooks/useViewport";
import { useLayerStore } from "../../state/layers";
import RigPreview from "../../components/RigPreview";
import LiveLayersPanel from "../../components/LiveLayersPanel";
import LightColorPicker from "../../components/LightColorPicker";
import Modal from "../../components/Modal";
import { useToast } from "../../toast";

/** Mobile-first home screen.
 *
 * Shows the rig as a live preview hero, a master intensity slider, the
 * stack of running layers, and quick-launch chips for recent scenes/
 * states. Desktop users see the same content but get the side rail
 * with the full layer panel automatically (rendered by AppShell), so
 * this page focuses on rig preview + recent actions. */
export default function NowPlaying() {
  const { isMobile } = useViewport();
  const { layers, patchLayer } = useLayerStore();
  const { push: notify } = useToast();
  const [lights, setLights] = useState<Light[]>([]);
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [models, setModels] = useState<LightModel[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [states, setStates] = useState<State[]>([]);
  const [effects, setEffects] = useState<Effect[]>([]);
  const [pickingLightId, setPickingLightId] = useState<number | null>(null);

  useEffect(() => {
    void Promise.all([
      Api.listLights().then(setLights).catch(() => null),
      Api.listControllers().then(setControllers).catch(() => null),
      Api.listModels().then(setModels).catch(() => null),
      Api.listScenes().then(setScenes).catch(() => null),
      Api.listStates().then(setStates).catch(() => null),
      Api.listEffects().then(setEffects).catch(() => null),
    ]);
  }, []);

  const pickingLight =
    pickingLightId == null
      ? null
      : lights.find((l) => l.id === pickingLightId) ?? null;

  // Compute an aggregate "master" opacity = mean of running layer
  // opacities * intensities. Adjusting it drags every layer with it
  // proportionally — handy as a single fader during shows.
  const running = layers.filter((l) => l.layer_id != null && !l.mute);
  const master =
    running.length === 0
      ? 1.0
      : running.reduce((a, l) => a + l.opacity, 0) / running.length;

  const onMasterChange = (next: number) => {
    if (running.length === 0) return;
    const ratio = master <= 0.001 ? 1 : next / master;
    for (const l of running) {
      const target = Math.max(0, Math.min(1, l.opacity * ratio));
      if (l.layer_id != null) {
        void patchLayer(l.layer_id, { opacity: target }).catch(() => null);
      }
    }
  };

  const onApplyState = async (s: State) => {
    if (s.id == null) return;
    try {
      await Api.applyState(s.id);
      notify(`Applied ${s.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    }
  };

  const onApplyScene = async (s: Scene) => {
    if (s.id == null) return;
    try {
      await Api.applyScene(s.id);
      notify(`Applied ${s.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <section className="card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-xs uppercase tracking-widest text-muted">
              Now Playing
            </div>
            <div className="text-lg font-semibold">
              {running.length === 0
                ? "Lights are static"
                : `${running.length} layer${running.length === 1 ? "" : "s"} running`}
            </div>
            <div className="mt-0.5 text-[10px] text-muted">
              Click a fixture to edit its color, dimmer, and aux channels.
            </div>
          </div>
          <Link
            to="/quick-fx"
            className="btn-secondary px-2 py-1 text-xs"
          >
            + Add FX
          </Link>
        </div>
        <div className="px-3 pb-3">
          <RigPreview
            lights={lights}
            controllers={controllers}
            onSelect={(id) => setPickingLightId(id)}
            size={isMobile ? "md" : "lg"}
            showLabels
            compact
          />
        </div>
      </section>

      <section className="card px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-muted">
            Master
          </div>
          <span className="font-mono text-xs text-muted">
            {(master * 100).toFixed(0)}%
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={master}
          onChange={(e) => onMasterChange(parseFloat(e.currentTarget.value))}
          disabled={running.length === 0}
          className="mt-2 w-full accent-accent"
          aria-label="Master intensity"
        />
      </section>

      {isMobile && (
        <section>
          <SectionHeader title="Layers" trailing={`${layers.length}`} />
          <LiveLayersPanel variant="full" showBlend={false} />
        </section>
      )}

      {scenes.length > 0 && (
        <section>
          <SectionHeader title="Scenes" />
          <div className="flex flex-wrap gap-2">
            {scenes
              .filter((s) => !s.builtin && s.id != null)
              .slice(0, 8)
              .map((s) => (
                <button
                  key={s.id}
                  onClick={() => onApplyScene(s)}
                  className="btn-secondary px-3 py-1.5 text-xs"
                >
                  {s.name}
                </button>
              ))}
          </div>
        </section>
      )}

      {states.length > 0 && (
        <section>
          <SectionHeader title="Rig states" />
          <div className="flex flex-wrap gap-2">
            {states
              .filter((s) => s.id != null)
              .slice(0, 8)
              .map((s) => (
                <button
                  key={s.id}
                  onClick={() => onApplyState(s)}
                  className="btn-secondary px-3 py-1.5 text-xs"
                >
                  {s.name}
                </button>
              ))}
          </div>
        </section>
      )}

      {effects.length > 0 && (
        <section>
          <SectionHeader
            title="Quick FX"
            trailing={
              <Link to="/quick-fx" className="text-xs text-accent hover:underline">
                See all
              </Link>
            }
          />
          <div className="flex flex-wrap gap-2">
            {effects.slice(0, 8).map((e) => (
              <QuickFxChip key={e.id} effect={e} notify={notify} />
            ))}
          </div>
        </section>
      )}

      <Modal
        open={pickingLight !== null}
        onClose={() => setPickingLightId(null)}
        title={
          pickingLight
            ? `Color: ${pickingLight.name}`
            : "Color"
        }
        size="md"
      >
        {pickingLight && (
          <LightColorPicker
            lights={[pickingLight]}
            models={models}
            notify={notify}
            onApplied={(updated) => {
              const byId = new Map(updated.map((l) => [l.id, l]));
              setLights((prev) =>
                prev.map((l) => byId.get(l.id) ?? l),
              );
            }}
          />
        )}
      </Modal>
    </div>
  );
}

function SectionHeader({
  title,
  trailing,
}: {
  title: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <div className="text-xs font-semibold uppercase tracking-widest text-muted">
        {title}
      </div>
      {trailing && (
        <div className="text-xs text-muted">{trailing}</div>
      )}
    </div>
  );
}

function QuickFxChip({
  effect,
  notify,
}: {
  effect: Effect;
  notify: (m: string, k?: "success" | "error" | "info") => void;
}) {
  const [busy, setBusy] = useState(false);
  const onPlay = async () => {
    setBusy(true);
    try {
      await Api.playEffect(effect.id);
      notify(`Started ${effect.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      onClick={onPlay}
      disabled={busy}
      className="btn-secondary px-3 py-1.5 text-xs"
    >
      {busy ? "..." : "+ "}
      {effect.name}
    </button>
  );
}
