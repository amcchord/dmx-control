import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ActiveEffect,
  Api,
  BulkTarget,
  Controller,
  Effect,
  FixtureLayout,
  Light,
  LightModel,
  LightModelMode,
  Palette,
  PaletteSpread,
  PolicyRole,
  RenderedLight,
  Scene,
  State,
} from "../api";
import { useToast } from "../toast";
import ActiveEffectsBar from "../components/ActiveEffectsBar";
import ColorPicker from "../components/ColorPicker";
import EffectPanel from "../components/EffectPanel";
import Modal from "../components/Modal";
import PaletteSwatch from "../components/PaletteSwatch";
import EffectsSidebar from "../components/EffectsSidebar";
import { hexToRgb, rgbToHex } from "../util";
import {
  MOTION_AXES,
  hasMotion,
  isCompoundLayout,
  orderedZones,
  zoneHex,
} from "../fixtureLayout";

type Mode = "cycle" | "gradient" | "random";

type ZoneSet = Set<string>;
// Per-light selection: "all" = whole fixture; ZoneSet = specific zone(s).
type Selection = Map<number, "all" | ZoneSet>;

function selHasLight(sel: Selection, lightId: number): boolean {
  return sel.has(lightId);
}

function selZoneSelected(
  sel: Selection,
  lightId: number,
  zoneId: string,
): boolean {
  const entry = sel.get(lightId);
  if (entry === undefined) return false;
  if (entry === "all") return true;
  return entry.has(zoneId);
}

function selLightIds(sel: Selection): number[] {
  return Array.from(sel.keys());
}

function selCountZones(sel: Selection, layoutOf: (id: number) => FixtureLayout | null): number {
  let n = 0;
  for (const [lid, entry] of sel) {
    if (entry === "all") {
      const layout = layoutOf(lid);
      if (layout && layout.zones.length > 0) n += layout.zones.length;
      else n += 1;
    } else {
      n += entry.size;
    }
  }
  return n;
}

function isPartialSelection(sel: Selection): boolean {
  for (const entry of sel.values()) {
    if (entry !== "all") return true;
  }
  return false;
}

function selToBulkTargets(sel: Selection): {
  light_ids: number[];
  targets: BulkTarget[];
} {
  const light_ids: number[] = [];
  const targets: BulkTarget[] = [];
  for (const [lid, entry] of sel) {
    if (entry === "all") {
      light_ids.push(lid);
    } else {
      for (const zid of entry) targets.push({ light_id: lid, zone_id: zid });
    }
  }
  return { light_ids, targets };
}

export default function Dashboard() {
  const toast = useToast();
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [models, setModels] = useState<LightModel[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [effects, setEffects] = useState<Effect[]>([]);
  const [activeEffects, setActiveEffects] = useState<ActiveEffect[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [states, setStates] = useState<State[]>([]);
  const [saveScenePrompt, setSaveScenePrompt] = useState<Controller | null>(
    null,
  );
  const [saveSceneName, setSaveSceneName] = useState("");
  // Per-controller dropdown selection for the "Restore scene" picker.
  // Values are scene ids as strings, or "__blackout__" for the virtual
  // Blackout entry. Empty string = nothing selected yet.
  const [sceneSelection, setSceneSelection] = useState<Record<number, string>>(
    {},
  );
  // Global dropdown selection for the rig-wide "State" picker. Values are
  // state ids as strings, or "__blackout__" for rig-wide blackout.
  const [stateSelection, setStateSelection] = useState<string>("");
  const [saveStateOpen, setSaveStateOpen] = useState(false);
  const [saveStateName, setSaveStateName] = useState("");
  const [rendered, setRendered] = useState<Record<number, RenderedLight>>({});
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Selection>(new Map());
  const [pickerFor, setPickerFor] = useState<
    | { kind: "light"; id: number }
    | { kind: "zone"; lightId: number; zoneId: string }
    | { kind: "bulk" }
    | null
  >(null);
  const [pickerColor, setPickerColor] = useState("#FFFFFF");
  const [showPalettes, setShowPalettes] = useState(false);
  const [showEffects, setShowEffects] = useState(false);
  const [paletteMode, setPaletteMode] = useState<Mode>("cycle");
  const [paletteSpread, setPaletteSpread] =
    useState<PaletteSpread>("across_lights");

  const refresh = async () => {
    try {
      const [c, m, l, p, e, sc, st] = await Promise.all([
        Api.listControllers(),
        Api.listModels(),
        Api.listLights(),
        Api.listPalettes(),
        Api.listEffects(),
        Api.listScenes(),
        Api.listStates(),
      ]);
      setControllers(c);
      setModels(m);
      setLights(l);
      setPalettes(p);
      setEffects(e);
      setScenes(sc);
      setStates(st);
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setLoading(false);
    }
  };

  const refreshScenes = async () => {
    try {
      const [sc, st] = await Promise.all([Api.listScenes(), Api.listStates()]);
      setScenes(sc);
      setStates(st);
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const refreshActive = async () => {
    try {
      const a = await Api.activeEffects();
      setActiveEffects(a);
    } catch {
      // Ignore — non-fatal background poll.
    }
  };

  const refreshEffects = async () => {
    try {
      const e = await Api.listEffects();
      setEffects(e);
    } catch (err) {
      toast.push(String(err), "error");
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    refreshActive();
    const h = window.setInterval(refreshActive, 1000);
    return () => window.clearInterval(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When any effect is running, poll the rendered DMX snapshot at 10 Hz so
  // the on-screen cards visibly animate. Clear the snapshot when nothing
  // is running so the cards fall back to the DB base state cleanly.
  useEffect(() => {
    if (activeEffects.length === 0) {
      if (Object.keys(rendered).length > 0) setRendered({});
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const snap = await Api.listRenderedLights();
        if (cancelled) return;
        const map: Record<number, RenderedLight> = {};
        for (const [k, v] of Object.entries(snap)) {
          map[Number(k)] = v;
        }
        setRendered(map);
      } catch {
        // Ignore — non-fatal background poll.
      }
    };
    tick();
    const h = window.setInterval(tick, 100);
    return () => {
      cancelled = true;
      window.clearInterval(h);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeEffects.length]);

  const modelById = useMemo(() => {
    const m = new Map<number, LightModel>();
    models.forEach((x) => m.set(x.id, x));
    return m;
  }, [models]);

  const controllerById = useMemo(() => {
    const m = new Map<number, Controller>();
    controllers.forEach((x) => m.set(x.id, x));
    return m;
  }, [controllers]);

  const modeByLightId = useMemo(() => {
    const map = new Map<number, LightModelMode | undefined>();
    for (const l of lights) {
      const m = modelById.get(l.model_id);
      let mode: LightModelMode | undefined;
      if (m) {
        mode = m.modes.find((x) => x.id === l.mode_id);
        if (!mode) mode = m.modes.find((x) => x.is_default);
        if (!mode) mode = m.modes[0];
      }
      map.set(l.id, mode);
    }
    return map;
  }, [lights, modelById]);

  const layoutByLightId = useMemo(() => {
    const map = new Map<number, FixtureLayout | null>();
    for (const l of lights) {
      map.set(l.id, modeByLightId.get(l.id)?.layout ?? null);
    }
    return map;
  }, [lights, modeByLightId]);

  const layoutOf = (lightId: number) => layoutByLightId.get(lightId) ?? null;

  /** Roles marked "direct" on this light's mode *and* actually present in
   * the channel list. For "direct" roles we expose a Dashboard slider so
   * the user can drive W / A / UV independently of the RGB mix. */
  const directRolesFor = (lightId: number): PolicyRole[] => {
    const mode = modeByLightId.get(lightId);
    if (!mode) return [];
    const policy = mode.color_policy ?? {};
    const channels = new Set(mode.channels);
    const out: PolicyRole[] = [];
    for (const role of ["w", "a", "uv"] as PolicyRole[]) {
      if (channels.has(role) && policy[role] === "direct") out.push(role);
    }
    return out;
  };

  const groupedLights = useMemo(() => {
    const byCtrl = new Map<number, Light[]>();
    for (const l of lights) {
      const list = byCtrl.get(l.controller_id) ?? [];
      list.push(l);
      byCtrl.set(l.controller_id, list);
    }
    return controllers.map((c) => ({
      controller: c,
      lights: (byCtrl.get(c.id) ?? []).slice().sort(
        (a, b) => a.position - b.position || a.start_address - b.start_address,
      ),
    }));
  }, [lights, controllers]);

  const scenesByController = useMemo(() => {
    const byCtrl = new Map<number, Scene[]>();
    for (const s of scenes) {
      if (s.id === null) continue; // virtual entries rendered per-controller
      const list = byCtrl.get(s.controller_id) ?? [];
      list.push(s);
      byCtrl.set(s.controller_id, list);
    }
    for (const list of byCtrl.values()) {
      list.sort((a, b) => a.name.localeCompare(b.name));
    }
    return byCtrl;
  }, [scenes]);

  const toggleLight = (id: number, additive: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (additive) {
        if (next.has(id)) next.delete(id);
        else next.set(id, "all");
      } else {
        const only = next.size === 1 && next.has(id) && next.get(id) === "all";
        next.clear();
        if (!only) next.set(id, "all");
      }
      return next;
    });
  };

  const toggleZone = (lightId: number, zoneId: string, additive: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev);
      const layout = layoutOf(lightId);
      const totalZones = layout?.zones.length ?? 0;
      const current = next.get(lightId);
      if (!additive) {
        // Non-additive click on a zone: select only that zone.
        next.clear();
        const s: ZoneSet = new Set([zoneId]);
        next.set(lightId, s);
        return next;
      }
      if (current === undefined) {
        next.set(lightId, new Set([zoneId]));
      } else if (current === "all") {
        // Demote to explicit set minus the clicked zone.
        if (totalZones === 0) {
          // No layout zones known; just clear.
          next.delete(lightId);
        } else {
          const s: ZoneSet = new Set(
            layout!.zones.map((z) => z.id).filter((id) => id !== zoneId),
          );
          if (s.size === 0) next.delete(lightId);
          else next.set(lightId, s);
        }
      } else {
        const s = new Set(current);
        if (s.has(zoneId)) s.delete(zoneId);
        else s.add(zoneId);
        if (s.size === 0) next.delete(lightId);
        else if (totalZones > 0 && s.size === totalZones) next.set(lightId, "all");
        else next.set(lightId, s);
      }
      return next;
    });
  };

  const selectAll = () => {
    const next: Selection = new Map();
    for (const l of lights) next.set(l.id, "all");
    setSelected(next);
  };
  const selectAllOnController = (controllerId: number) => {
    const next: Selection = new Map();
    for (const l of lights) {
      if (l.controller_id === controllerId) next.set(l.id, "all");
    }
    setSelected(next);
  };
  const clearSelection = () => setSelected(new Map());

  const openPickerFor = (light: Light) => {
    setPickerColor(rgbToHex(light.r, light.g, light.b));
    setPickerFor({ kind: "light", id: light.id });
  };

  const openZonePicker = (lightId: number, zoneId: string) => {
    const light = lights.find((l) => l.id === lightId);
    if (light) {
      const { hex } = zoneHex(light, zoneId);
      setPickerColor(hex);
    } else {
      setPickerColor("#FFFFFF");
    }
    setPickerFor({ kind: "zone", lightId, zoneId });
  };

  const openBulkPicker = () => {
    setPickerColor("#FFFFFF");
    setPickerFor({ kind: "bulk" });
  };

  const commitColor = async (hex: string) => {
    const { r, g, b } = hexToRgb(hex);
    if (pickerFor == null) return;
    try {
      if (pickerFor.kind === "bulk") {
        if (selected.size === 0) return;
        const { light_ids, targets } = selToBulkTargets(selected);
        await Api.bulkColor(
          light_ids,
          { r, g, b, on: true },
          targets.length ? targets : undefined,
        );
        // Optimistic state update: the next refresh will reconcile.
        await refresh();
      } else if (pickerFor.kind === "light") {
        const id = pickerFor.id;
        const updated = await Api.setColor(id, { r, g, b, on: true });
        setLights((prev) => prev.map((l) => (l.id === id ? updated : l)));
      } else {
        const { lightId, zoneId } = pickerFor;
        const updated = await Api.setColor(lightId, {
          r,
          g,
          b,
          on: true,
          zone_id: zoneId,
        });
        setLights((prev) => prev.map((l) => (l.id === lightId ? updated : l)));
      }
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const toggleOn = async (light: Light) => {
    try {
      const updated = await Api.setColor(light.id, {
        r: light.r,
        g: light.g,
        b: light.b,
        on: !light.on,
      });
      setLights((prev) => prev.map((l) => (l.id === light.id ? updated : l)));
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const openSaveScene = (c: Controller) => {
    setSaveSceneName("");
    setSaveScenePrompt(c);
  };

  const saveScene = async () => {
    if (saveScenePrompt === null) return;
    const name = saveSceneName.trim();
    if (!name) return;
    try {
      await Api.createScene({
        name,
        controller_id: saveScenePrompt.id,
      });
      toast.push(`Saved "${name}"`, "success");
      setSaveScenePrompt(null);
      setSaveSceneName("");
      await refreshScenes();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const applySelectedScene = async (c: Controller) => {
    const value = sceneSelection[c.id] ?? "";
    if (!value) return;
    try {
      if (value === "__blackout__") {
        await Api.applyBlackoutScene(c.id);
        toast.push(`${c.name} blacked out`, "success");
      } else {
        const id = Number(value);
        if (!Number.isFinite(id)) return;
        const scene = scenes.find((s) => s.id === id);
        const label = scene?.name ?? `scene ${id}`;
        await Api.applyScene(id);
        toast.push(`Applied "${label}"`, "success");
      }
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const applySelectedState = async () => {
    if (!stateSelection) return;
    try {
      if (stateSelection === "__blackout__") {
        await Api.applyBlackoutState();
        toast.push("All controllers blacked out", "success");
      } else {
        const id = Number(stateSelection);
        if (!Number.isFinite(id)) return;
        const state = states.find((s) => s.id === id);
        const label = state?.name ?? `state ${id}`;
        await Api.applyState(id);
        toast.push(`Applied "${label}"`, "success");
      }
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const openSaveState = () => {
    setSaveStateName("");
    setSaveStateOpen(true);
  };

  const saveState = async () => {
    const name = saveStateName.trim();
    if (!name) return;
    try {
      await Api.createState({ name });
      toast.push(`Saved state "${name}"`, "success");
      setSaveStateOpen(false);
      setSaveStateName("");
      await refreshScenes();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const applyPalette = async (p: Palette) => {
    const ids = selLightIds(selected);
    if (ids.length === 0) {
      toast.push("Select one or more lights first", "error");
      return;
    }
    try {
      await Api.applyPalette(p.id, ids, paletteMode, paletteSpread);
      toast.push(
        `Applied ${p.name} to ${ids.length} light${ids.length === 1 ? "" : "s"}`,
        "success",
      );
      setShowPalettes(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  // NOTE: All hooks must run before any early returns to satisfy the Rules
  // of Hooks. We compute selection-derived memos up here, then gate render
  // paths on the data below.
  const selectedLights = selected.size;
  const selectedZones = useMemo(
    () => selCountZones(selected, layoutOf),
    // layoutOf closes over layoutByLightId, which is itself memoized.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected, layoutByLightId],
  );
  const selectionPartial = isPartialSelection(selected);
  const bulkLabel = selectionPartial
    ? `Set color (${selectedLights}L • ${selectedZones}z)`
    : `Set color (${selectedLights})`;
  let selectionSummary = "none selected";
  if (selected.size > 0) {
    const lightWord = `${selectedLights} light${selectedLights === 1 ? "" : "s"}`;
    if (selectionPartial) {
      const zoneWord = `${selectedZones} zone${selectedZones === 1 ? "" : "s"}`;
      selectionSummary = `${lightWord} · ${zoneWord}`;
    } else {
      selectionSummary = lightWord;
    }
  }

  if (loading) {
    return <div className="text-muted">Loading...</div>;
  }

  if (controllers.length === 0) {
    return (
      <EmptyState
        title="No controllers yet"
        body="Add an Art-Net controller to get started."
        cta={<Link to="/controllers" className="btn-primary">Add controller</Link>}
      />
    );
  }

  if (lights.length === 0) {
    return (
      <EmptyState
        title="No lights yet"
        body="Add your first DMX light to a controller."
        cta={<Link to="/controllers" className="btn-primary">Manage lights</Link>}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Lights</h1>
        <p className="text-sm text-muted">
          {lights.length} lights across {controllers.length} controller
          {controllers.length === 1 ? "" : "s"}.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 rounded-lg bg-bg-elev px-2 py-1.5 ring-1 ring-line">
          <span className="pl-1 text-[10px] uppercase tracking-wider text-muted">
            Selection
          </span>
          <button className="btn-secondary text-xs" onClick={selectAll}>
            Select all
          </button>
          <button
            className="btn-secondary text-xs"
            onClick={clearSelection}
            disabled={selected.size === 0}
          >
            Clear
          </button>
          <span className="px-1 text-xs text-muted">{selectionSummary}</span>
          <button
            className="btn-primary text-xs"
            onClick={openBulkPicker}
            disabled={selected.size === 0}
            title="Set a single color on the current selection"
          >
            {bulkLabel}
          </button>
          <button
            className="btn-secondary text-xs"
            onClick={() => setShowPalettes(true)}
            disabled={selected.size === 0}
            title="Spread a palette across the current selection"
          >
            Apply palette
          </button>
        </div>

        <div className="flex items-center gap-2 rounded-lg bg-bg-elev px-2 py-1.5 ring-1 ring-line sm:ml-auto">
          <span className="pl-1 text-[10px] uppercase tracking-wider text-muted">
            Rig state
          </span>
          <select
            className="input !h-7 w-40 !py-0.5 !text-xs"
            value={stateSelection}
            onChange={(e) => setStateSelection(e.target.value)}
            title="Pick a rig-wide state to apply to every light on every controller"
          >
            <option value="" disabled>
              Select…
            </option>
            {states.map((s) => {
              const value = s.id === null ? "__blackout__" : String(s.id);
              return (
                <option
                  key={s.id === null ? "blackout" : `state-${s.id}`}
                  value={value}
                >
                  {s.name}
                </option>
              );
            })}
          </select>
          <button
            className="btn-primary text-xs"
            onClick={applySelectedState}
            disabled={!stateSelection}
            title="Apply the selected rig state"
          >
            Apply
          </button>
          <button
            className="btn-ghost text-xs"
            onClick={openSaveState}
            title="Capture the current state of every light as a rig-wide state"
          >
            Save
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-stretch gap-3">
        <div className="flex items-center gap-2 rounded-lg bg-bg-elev px-2 py-1.5 ring-1 ring-line">
          <span className="pl-1 text-[10px] uppercase tracking-wider text-muted">
            Effect controls
          </span>
          <button
            className="btn-primary text-xs"
            onClick={() => setShowEffects(true)}
            disabled={selected.size === 0}
            title="Run a dynamic effect on the current selection"
          >
            Effects{activeEffects.length > 0 ? ` (${activeEffects.length} live)` : "..."}
          </button>
        </div>
        <ActiveEffectsBar
          activeEffects={activeEffects}
          onChanged={async () => {
            await refreshActive();
            await refreshEffects();
          }}
          notify={(msg, kind) => toast.push(msg, kind)}
        />
      </div>

      {groupedLights.map(({ controller, lights: clights }) => {
        const savedScenes = scenesByController.get(controller.id) ?? [];
        return (
        <section key={controller.id} className="space-y-3">
          <header className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">{controller.name}</h2>
              <span className="pill">{controller.ip}:{controller.port}</span>
              <span className="pill">U {controller.net}:{controller.subnet}:{controller.universe}</span>
              {!controller.enabled && (
                <span className="pill bg-rose-950 text-rose-300 ring-rose-900">
                  disabled
                </span>
              )}
            </div>
            <div className="flex flex-nowrap items-center gap-2">
              <button
                className="btn-ghost text-xs"
                onClick={() => selectAllOnController(controller.id)}
                title={`Select every light on ${controller.name}`}
              >
                Select all
              </button>
              <select
                className="input !h-8 w-36 !py-1 !text-xs"
                value={sceneSelection[controller.id] ?? ""}
                onChange={(e) =>
                  setSceneSelection((prev) => ({
                    ...prev,
                    [controller.id]: e.target.value,
                  }))
                }
                title="Pick a saved scene to restore to this controller"
              >
                <option value="" disabled>
                  Restore scene…
                </option>
                <option value="__blackout__">Blackout</option>
                {savedScenes.length > 0 && (
                  <optgroup label="Saved">
                    {savedScenes.map((s) => (
                      <option key={s.id ?? `x-${s.name}`} value={String(s.id)}>
                        {s.name}
                        {s.cross_controller ? " (multi)" : ""}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
              <button
                className="btn-primary text-xs"
                onClick={() => applySelectedScene(controller)}
                disabled={!sceneSelection[controller.id]}
                title="Apply the selected scene to this controller"
              >
                Apply
              </button>
              <button
                className="btn-ghost text-xs"
                onClick={() => openSaveScene(controller)}
                title="Capture the current state of this controller as a scene"
              >
                Save scene
              </button>
            </div>
          </header>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {clights.map((light) => (
              <LightCard
                key={light.id}
                light={light}
                rendered={rendered[light.id] ?? null}
                model={modelById.get(light.model_id)}
                layout={layoutOf(light.id)}
                selection={selected.get(light.id) ?? null}
                onToggleSelect={(shift) => toggleLight(light.id, shift)}
                onToggleZone={(zoneId, additive) =>
                  toggleZone(light.id, zoneId, additive)
                }
                onOpen={() => openPickerFor(light)}
                onOpenZone={(zoneId) => openZonePicker(light.id, zoneId)}
                onToggleOn={() => toggleOn(light)}
              />
            ))}
          </div>
        </section>
        );
      })}

      <Modal
        open={showEffects}
        onClose={() => setShowEffects(false)}
        title={`Effects — ${selected.size} light${selected.size === 1 ? "" : "s"} selected`}
        size="lg"
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <EffectPanel
            selection={selToBulkTargets(selected)}
            palettes={palettes}
            onActiveChanged={refreshActive}
            onSaved={async () => {
              await refreshEffects();
              await refreshActive();
            }}
            notify={(msg, kind) => toast.push(msg, kind)}
          />
          <EffectsSidebar
            effects={effects}
            activeEffects={activeEffects}
            palettes={palettes}
            onEffectSelected={(e) => {
              // Load this effect's targets into the Dashboard selection so
              // the user can tweak it without manually reselecting lights.
              const next: Selection = new Map();
              for (const lid of e.light_ids) next.set(lid, "all");
              for (const t of e.targets) {
                const existing = next.get(t.light_id);
                if (existing === "all") continue;
                const set = existing ?? new Set<string>();
                if (t.zone_id) set.add(t.zone_id);
                next.set(t.light_id, set);
              }
              setSelected(next);
            }}
            onChanged={async () => {
              await refreshEffects();
              await refreshActive();
            }}
            notify={(msg, kind) => toast.push(msg, kind)}
          />
        </div>
      </Modal>

      <Modal
        open={pickerFor !== null}
        onClose={() => setPickerFor(null)}
        title={pickerTitle(pickerFor, selectedLights, selectedZones)}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setPickerFor(null)}>
              Done
            </button>
          </>
        }
      >
        <ColorPicker
          value={pickerColor}
          onChange={(hex) => {
            setPickerColor(hex);
            commitColor(hex);
          }}
        />
        <DirectChannelSliders
          pickerFor={pickerFor}
          lights={lights}
          directRolesFor={directRolesFor}
          onCommit={async (lightId, patch, zoneId) => {
            try {
              const updated = await Api.setColor(lightId, {
                ...patch,
                zone_id: zoneId ?? null,
              });
              setLights((prev) =>
                prev.map((l) => (l.id === lightId ? updated : l)),
              );
            } catch (e) {
              toast.push(String(e), "error");
            }
          }}
        />
      </Modal>

      <Modal
        open={showPalettes}
        onClose={() => setShowPalettes(false)}
        title={`Apply palette to ${selected.size} light${selected.size === 1 ? "" : "s"}`}
        size="lg"
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="label mr-2 !text-xs normal-case tracking-normal">Mode</span>
          {(["cycle", "gradient", "random"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setPaletteMode(m)}
              className={
                "rounded-full px-3 py-1 text-xs ring-1 " +
                (paletteMode === m
                  ? "bg-accent text-white ring-accent"
                  : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
              }
            >
              {m}
            </button>
          ))}
        </div>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="label mr-2 !text-xs normal-case tracking-normal">Spread</span>
          {(
            [
              {
                key: "across_lights" as PaletteSpread,
                label: "Across lights",
                hint: "one color per fixture",
              },
              {
                key: "across_fixture" as PaletteSpread,
                label: "Across fixture",
                hint: "roll palette across each fixture's own zones",
              },
              {
                key: "across_zones" as PaletteSpread,
                label: "Across all zones",
                hint: "flatten every zone and spread end-to-end",
              },
            ]
          ).map((opt) => (
            <button
              key={opt.key}
              onClick={() => setPaletteSpread(opt.key)}
              title={opt.hint}
              className={
                "rounded-full px-3 py-1 text-xs ring-1 " +
                (paletteSpread === opt.key
                  ? "bg-accent text-white ring-accent"
                  : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="mb-3 text-[11px] text-muted">
          {paletteSpread === "across_fixture" &&
            "Each fixture receives the palette rolled across its own pixels/zones. Simple pars get the first color."}
          {paletteSpread === "across_zones" &&
            "Every pixel/zone in the selection is treated as one long strip; the palette spreads end-to-end."}
          {paletteSpread === "across_lights" &&
            "Each fixture gets one color from the palette (the classic behavior)."}
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {palettes.map((p) => (
            <button
              key={p.id}
              onClick={() => applyPalette(p)}
              className="card flex flex-col gap-2 p-3 text-left hover:bg-bg-elev"
            >
              <div className="flex items-center justify-between">
                <div className="font-medium">{p.name}</div>
                {p.builtin && <span className="pill text-[10px]">built-in</span>}
              </div>
              <PaletteSwatch colors={p.colors} />
            </button>
          ))}
        </div>
      </Modal>

      <Modal
        open={saveScenePrompt !== null}
        onClose={() => setSaveScenePrompt(null)}
        title={
          saveScenePrompt
            ? `Save scene for ${saveScenePrompt.name}`
            : "Save scene"
        }
        footer={
          <>
            <button
              className="btn-ghost"
              onClick={() => setSaveScenePrompt(null)}
            >
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={saveScene}
              disabled={!saveSceneName.trim()}
            >
              Save
            </button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          Captures the current color, dimmer, and on/off state of every light
          on{" "}
          <span className="font-medium text-slate-100">
            {saveScenePrompt?.name}
          </span>
          .
        </p>
        <label className="label mb-1 block !text-xs normal-case tracking-normal">
          Name
        </label>
        <input
          className="input w-full"
          value={saveSceneName}
          autoFocus
          placeholder="Evening wash"
          onChange={(e) => setSaveSceneName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && saveSceneName.trim()) {
              void saveScene();
            }
          }}
        />
      </Modal>

      <Modal
        open={saveStateOpen}
        onClose={() => setSaveStateOpen(false)}
        title="Save rig state"
        footer={
          <>
            <button
              className="btn-ghost"
              onClick={() => setSaveStateOpen(false)}
            >
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={saveState}
              disabled={!saveStateName.trim()}
            >
              Save
            </button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          Captures the current color, dimmer, and on/off state of{" "}
          <span className="font-medium text-slate-100">every light</span> on
          every controller.
        </p>
        <label className="label mb-1 block !text-xs normal-case tracking-normal">
          Name
        </label>
        <input
          className="input w-full"
          value={saveStateName}
          autoFocus
          placeholder="Showtime"
          onChange={(e) => setSaveStateName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && saveStateName.trim()) {
              void saveState();
            }
          }}
        />
      </Modal>
    </div>
  );
}

function pickerTitle(
  picker:
    | { kind: "light"; id: number }
    | { kind: "zone"; lightId: number; zoneId: string }
    | { kind: "bulk" }
    | null,
  lightCount: number,
  zoneCount: number,
): string {
  if (!picker) return "";
  if (picker.kind === "bulk") {
    if (zoneCount && lightCount)
      return `Set color on ${lightCount} light${lightCount === 1 ? "" : "s"} • ${zoneCount} zone${zoneCount === 1 ? "" : "s"}`;
    return `Set color on ${lightCount} light${lightCount === 1 ? "" : "s"}`;
  }
  if (picker.kind === "zone") return `Set zone color — ${picker.zoneId}`;
  return "Set light color";
}

const DIRECT_ROLE_META: Record<
  PolicyRole,
  { label: string; swatch: string; help: string }
> = {
  w: {
    label: "White",
    swatch: "#f5f5f5",
    help: "White LED fader (not mixed from RGB).",
  },
  a: {
    label: "Amber",
    swatch: "#ffb23d",
    help: "Amber LED fader (not mixed from RGB).",
  },
  uv: {
    label: "UV",
    swatch: "#b44dff",
    help: "Ultraviolet LED fader.",
  },
};

/** Extra sliders rendered inside the color picker modal for any W/A/UV
 * role that the light's mode marks as "direct". For zone pickers we
 * edit the current zone_state entry; for single-light pickers we edit
 * the flat fields on the light. Bulk selections are skipped since the
 * lights may not share a compatible policy.
 *
 * Props are all pulled up from the parent rather than hooking into it
 * directly so the picker modal stays a dumb, stateless consumer. */
function DirectChannelSliders({
  pickerFor,
  lights,
  directRolesFor,
  onCommit,
}: {
  pickerFor:
    | { kind: "light"; id: number }
    | { kind: "zone"; lightId: number; zoneId: string }
    | { kind: "bulk" }
    | null;
  lights: Light[];
  directRolesFor: (lightId: number) => PolicyRole[];
  onCommit: (
    lightId: number,
    patch: { r: number; g: number; b: number; w?: number; a?: number; uv?: number },
    zoneId?: string,
  ) => void | Promise<void>;
}) {
  if (!pickerFor || pickerFor.kind === "bulk") return null;

  const lightId =
    pickerFor.kind === "light" ? pickerFor.id : pickerFor.lightId;
  const zoneId =
    pickerFor.kind === "zone" ? pickerFor.zoneId : undefined;

  const light = lights.find((l) => l.id === lightId);
  if (!light) return null;

  const roles = directRolesFor(lightId);
  if (roles.length === 0) return null;

  // Resolve the current value for a role from the right source: the zone
  // state for zone pickers, the flat fields for light pickers.
  const readValue = (role: PolicyRole): number => {
    if (zoneId) {
      const zs = (light.zone_state ?? {})[zoneId] ?? {};
      const v = zs[role];
      if (typeof v === "number") return v;
      return light[role];
    }
    return light[role];
  };

  // Build the RGB basis to send with the partial update. Palette / zone
  // RGB falls back to the flat RGB exactly the way the backend does.
  const readRgb = (): { r: number; g: number; b: number } => {
    if (zoneId) {
      const zs = (light.zone_state ?? {})[zoneId] ?? {};
      return {
        r: typeof zs.r === "number" ? zs.r : light.r,
        g: typeof zs.g === "number" ? zs.g : light.g,
        b: typeof zs.b === "number" ? zs.b : light.b,
      };
    }
    return { r: light.r, g: light.g, b: light.b };
  };

  return (
    <div className="mt-3 space-y-2 rounded-md bg-bg-elev p-2 ring-1 ring-line">
      <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-muted">
        <span>Direct channels</span>
        <span>
          {zoneId ? "zone" : "light"} ·{" "}
          {roles.map((r) => r.toUpperCase()).join(" / ")}
        </span>
      </div>
      {roles.map((role) => {
        const meta = DIRECT_ROLE_META[role];
        const value = readValue(role);
        return (
          <label key={role} className="flex items-center gap-2 text-sm">
            <span
              className="h-3 w-3 rounded-full ring-1 ring-line"
              style={{ background: meta.swatch }}
            />
            <span className="w-16 text-xs text-slate-300" title={meta.help}>
              {meta.label}
            </span>
            <input
              type="range"
              min={0}
              max={255}
              value={value}
              onChange={(e) => {
                const next = Number(e.target.value);
                const rgb = readRgb();
                onCommit(lightId, { ...rgb, [role]: next }, zoneId);
              }}
              className="flex-1 accent-accent"
            />
            <span className="w-10 text-right font-mono text-xs tabular-nums text-muted">
              {value}
            </span>
          </label>
        );
      })}
    </div>
  );
}

function LightCard({
  light,
  rendered,
  model,
  layout,
  selection,
  onToggleSelect,
  onToggleZone,
  onOpen,
  onOpenZone,
  onToggleOn,
}: {
  light: Light;
  rendered: RenderedLight | null;
  model?: LightModel;
  layout: FixtureLayout | null;
  selection: "all" | ZoneSet | null;
  onToggleSelect: (additive: boolean) => void;
  onToggleZone: (zoneId: string, additive: boolean) => void;
  onOpen: () => void;
  onOpenZone: (zoneId: string) => void;
  onToggleOn: () => void;
}) {
  // Prefer the live-rendered RGB when an effect is running so the card
  // animates in real time; fall back to the DB base state otherwise.
  const liveR = rendered?.r ?? light.r;
  const liveG = rendered?.g ?? light.g;
  const liveB = rendered?.b ?? light.b;
  const swatch = rgbToHex(liveR, liveG, liveB);
  const off =
    !light.on ||
    (rendered !== null
      ? liveR === 0 && liveG === 0 && liveB === 0 && !rendered.on
      : liveR === 0 && liveG === 0 && liveB === 0);
  const selectedAll = selection === "all";
  const hasAnySelection = selection !== null;
  const compound = isCompoundLayout(layout);
  const motion = hasMotion(layout);

  const ringCls = selectedAll
    ? "ring-2 ring-accent"
    : hasAnySelection
      ? "ring-2 ring-accent/60"
      : "hover:ring-1 hover:ring-line";

  return (
    <div className={"card flex flex-col overflow-hidden transition " + ringCls}>
      {compound && layout ? (
        <ZoneStrip
          light={light}
          rendered={rendered}
          layout={layout}
          selection={selection}
          onZoneClick={(zoneId, e) => {
            if (e.shiftKey || e.metaKey || e.ctrlKey) {
              onToggleZone(zoneId, true);
            } else {
              onOpenZone(zoneId);
            }
          }}
          onZoneContextMenu={(zoneId, e) => {
            e.preventDefault();
            onToggleZone(zoneId, true);
          }}
          onBgClick={(e) => {
            if (e.shiftKey || e.metaKey || e.ctrlKey) onToggleSelect(true);
            else onOpen();
          }}
          startAddress={light.start_address}
          motion={motion}
          selectedAll={selectedAll}
          hasAnySelection={hasAnySelection}
          onToggleSelect={() => onToggleSelect(true)}
        />
      ) : (
        <button
          className="relative h-24 w-full"
          style={{
            background: off
              ? "repeating-linear-gradient(45deg,#1a1f2b,#1a1f2b 6px,#141821 6px,#141821 12px)"
              : swatch,
            boxShadow: off ? "none" : `inset 0 0 40px ${swatch}66`,
          }}
          onClick={(e) => {
            if (e.shiftKey || e.metaKey || e.ctrlKey) {
              onToggleSelect(true);
            } else {
              onOpen();
            }
          }}
          onContextMenu={(e) => {
            e.preventDefault();
            onToggleSelect(true);
          }}
          aria-label={`Set color for ${light.name}`}
        >
          <span className="absolute left-2 top-2 rounded bg-black/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-white/90">
            ch {light.start_address}
          </span>
          <span
            className="absolute right-2 top-2 inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded border border-white/30 bg-black/30 text-[11px] text-white"
            onClick={(e) => {
              e.stopPropagation();
              onToggleSelect(true);
            }}
            role="checkbox"
            aria-checked={selectedAll}
          >
            {selectedAll ? "✓" : ""}
          </span>
        </button>
      )}
      <div className="flex items-center justify-between gap-2 p-3">
        <div className="flex min-w-0 items-center gap-2">
          {model?.image_url && (
            <img
              src={model.image_url}
              alt=""
              className="h-8 w-8 shrink-0 rounded object-cover ring-1 ring-line"
            />
          )}
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{light.name}</div>
            <div className="truncate text-xs text-muted">
              {model ? model.name : "unknown model"}
              {(() => {
                const mode =
                  model?.modes.find((x) => x.id === light.mode_id) ??
                  model?.modes.find((x) => x.is_default);
                return mode ? ` · ${mode.name}` : "";
              })()}
              {compound && layout && ` · ${layout.zones.length}z`}
            </div>
          </div>
        </div>
        <button
          className={
            "h-7 w-10 shrink-0 rounded-full p-0.5 transition " +
            (light.on ? "bg-accent" : "bg-bg-elev ring-1 ring-line")
          }
          onClick={onToggleOn}
          aria-label={light.on ? "Turn off" : "Turn on"}
        >
          <span
            className={
              "block h-6 w-6 rounded-full bg-white transition " +
              (light.on ? "translate-x-3" : "translate-x-0")
            }
          />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ZoneStrip: compound-fixture hero region with clickable zone cells
// ---------------------------------------------------------------------------

function ZoneStrip({
  light,
  rendered,
  layout,
  selection,
  onZoneClick,
  onZoneContextMenu,
  onBgClick,
  startAddress,
  motion,
  selectedAll,
  hasAnySelection,
  onToggleSelect,
}: {
  light: Light;
  rendered: RenderedLight | null;
  layout: FixtureLayout;
  selection: "all" | ZoneSet | null;
  onZoneClick: (zoneId: string, e: React.MouseEvent) => void;
  onZoneContextMenu: (zoneId: string, e: React.MouseEvent) => void;
  onBgClick: (e: React.MouseEvent) => void;
  startAddress: number;
  motion: boolean;
  selectedAll: boolean;
  hasAnySelection: boolean;
  onToggleSelect: () => void;
}) {
  const zones = orderedZones(layout);
  const isSel = (id: string) => {
    if (selection === "all") return true;
    if (selection === null) return false;
    return selection.has(id);
  };

  return (
    <div
      className="relative h-24 w-full cursor-pointer bg-[#0e1220]"
      onClick={(e) => {
        // Only trigger bg click when the user clicks the background itself.
        if (e.target === e.currentTarget) onBgClick(e);
      }}
    >
      {/* Address + selection pill */}
      <span className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-black/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-white/90">
        ch {startAddress}
      </span>
      <span
        className="absolute right-2 top-2 z-10 inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded border border-white/40 bg-black/40 text-[11px] text-white"
        onClick={(e) => {
          e.stopPropagation();
          onToggleSelect();
        }}
        role="checkbox"
        aria-checked={selectedAll}
        title={
          selectedAll
            ? "Whole fixture selected"
            : hasAnySelection
              ? "Partial selection — click to select all"
              : "Select whole fixture"
        }
      >
        {selectedAll ? "✓" : hasAnySelection ? "·" : ""}
      </span>

      {/* Motion badges */}
      {motion && (
        <div className="pointer-events-none absolute left-2 bottom-2 z-10 flex gap-1">
          {MOTION_AXES.filter((a) => {
            const m = layout.motion;
            if (!m) return false;
            return (
              typeof m[a] === "number" ||
              typeof (m as Record<string, unknown>)[`${a}_fine`] === "number"
            );
          }).map((a) => (
            <span
              key={a}
              className="rounded bg-sky-900/70 px-1 py-0.5 text-[9px] font-mono uppercase tracking-wider text-sky-100"
              title={`${a} axis present`}
            >
              {a}
            </span>
          ))}
        </div>
      )}

      {/* Zones */}
      <ZoneGrid
        zones={zones}
        layout={layout}
        light={light}
        rendered={rendered}
        isSelected={isSel}
        onZoneClick={onZoneClick}
        onZoneContextMenu={onZoneContextMenu}
      />
    </div>
  );
}

function ZoneGrid({
  zones,
  layout,
  light,
  rendered,
  isSelected,
  onZoneClick,
  onZoneContextMenu,
}: {
  zones: FixtureLayout["zones"];
  layout: FixtureLayout;
  light: Light;
  rendered: RenderedLight | null;
  isSelected: (zoneId: string) => boolean;
  onZoneClick: (zoneId: string, e: React.MouseEvent) => void;
  onZoneContextMenu: (zoneId: string, e: React.MouseEvent) => void;
}) {
  const cellBg = (zoneId: string) => {
    const live = rendered?.zone_state?.[zoneId];
    let hex: string;
    let on: boolean;
    if (live) {
      hex = rgbToHex(live.r, live.g, live.b);
      on = live.on;
    } else {
      const z = zoneHex(light, zoneId);
      hex = z.hex;
      on = z.on;
    }
    if (!on || hex === "#000000")
      return "repeating-linear-gradient(45deg,#1a1f2b,#1a1f2b 4px,#141821 4px,#141821 8px)";
    return hex;
  };
  const clsBase =
    "rounded-sm ring-1 ring-black/30 transition hover:scale-[1.04] hover:ring-accent cursor-pointer";

  if (layout.shape === "ring") {
    const count = zones.length;
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="relative h-20 w-20">
          {zones.map((z, i) => {
            const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
            const radius = 34;
            const x = 40 + Math.cos(angle) * radius - 7;
            const y = 40 + Math.sin(angle) * radius - 7;
            return (
              <div
                key={z.id}
                className={
                  clsBase +
                  " absolute h-3.5 w-3.5 rounded-full " +
                  (isSelected(z.id) ? "ring-2 ring-accent" : "")
                }
                style={{ left: x, top: y, background: cellBg(z.id) }}
                title={z.label}
                onClick={(e) => onZoneClick(z.id, e)}
                onContextMenu={(e) => onZoneContextMenu(z.id, e)}
              />
            );
          })}
        </div>
      </div>
    );
  }

  if (layout.shape === "grid") {
    const cols = layout.cols ?? Math.ceil(Math.sqrt(zones.length || 1));
    return (
      <div className="absolute inset-0 flex items-center justify-center p-2">
        <div
          className="grid w-full gap-0.5"
          style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
        >
          {zones.map((z) => (
            <div
              key={z.id}
              className={
                clsBase +
                " aspect-square " +
                (isSelected(z.id) ? "ring-2 !ring-accent" : "")
              }
              style={{ background: cellBg(z.id) }}
              title={z.label}
              onClick={(e) => onZoneClick(z.id, e)}
              onContextMenu={(e) => onZoneContextMenu(z.id, e)}
            />
          ))}
        </div>
      </div>
    );
  }

  if (layout.shape === "cluster") {
    return (
      <div className="absolute inset-0 flex flex-wrap items-center justify-center gap-1 p-2">
        {zones.map((z) => (
          <div
            key={z.id}
            className={
              clsBase +
              " min-w-[42px] flex-1 px-2 py-1 text-[10px] text-center font-medium text-white/90 " +
              (isSelected(z.id) ? "ring-2 !ring-accent" : "")
            }
            style={{ background: cellBg(z.id) }}
            title={z.label}
            onClick={(e) => onZoneClick(z.id, e)}
            onContextMenu={(e) => onZoneContextMenu(z.id, e)}
          >
            <span
              className="drop-shadow"
              style={{ textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}
            >
              {z.label}
            </span>
          </div>
        ))}
      </div>
    );
  }

  // linear (default) — single row of cells
  return (
    <div className="absolute inset-0 flex items-center gap-0.5 px-2 py-3">
      {zones.map((z) => (
        <div
          key={z.id}
          className={
            clsBase +
            " h-full min-w-0 flex-1 " +
            (isSelected(z.id) ? "ring-2 !ring-accent" : "")
          }
          style={{ background: cellBg(z.id) }}
          title={z.label}
          onClick={(e) => onZoneClick(z.id, e)}
          onContextMenu={(e) => onZoneContextMenu(z.id, e)}
        />
      ))}
    </div>
  );
}

function EmptyState({
  title,
  body,
  cta,
}: {
  title: string;
  body: string;
  cta: React.ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center gap-4 p-10 text-center">
      <div className="text-lg font-semibold">{title}</div>
      <div className="text-sm text-muted">{body}</div>
      {cta}
    </div>
  );
}
