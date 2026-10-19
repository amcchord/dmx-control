import React, { useEffect, useMemo, useState } from "react";
import { Api, Controller, Light, LightModel, Palette, Scene } from "../../api";
import { useSelection } from "../../state/selection";
import RigPreview from "../../components/RigPreview";
import LightColorPicker from "../../components/LightColorPicker";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth";
import OutputStatus from "../../components/OutputStatus";
import Modal from "../../components/Modal";
import { useToast } from "../../toast";

/** Operate-mode rig view.
 *
 * Multi-select fixtures by tap; bottom action sheet exposes the most
 * common per-selection ops (color, palette, blackout, scene apply).
 * Heavy authoring (per-zone editing, motion, palette policy) lives on
 * the desktop config pages — keeping mobile fast and forgiving. */
export default function LightsPage() {
  const {
    selection,
    toggle,
    setMany,
    clear,
    hasSelection,
    size,
    setControllerFilter,
  } = useSelection();
  const { push: notify } = useToast();
  const [lights, setLights] = useState<Light[]>([]);
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [models, setModels] = useState<LightModel[]>([]);
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const { canOperate, isAdmin } = useAuth();
  const [search, setSearch] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    void Promise.all([
      Api.listLights().then(setLights),
      Api.listControllers().then(setControllers),
      Api.listModels().then(setModels),
      Api.listPalettes().then(setPalettes),
      Api.listScenes()
        .then(setScenes)
        .catch(() => null),
    ]);
  }, []);

  const selectedLights = lights.filter((l) => selection.lightIds.has(l.id));

  const filteredLights = useMemo(() => {
    return lights.filter(
      (l) =>
        (selection.controllerId == null ||
          l.controller_id === selection.controllerId) &&
        `${l.name} ${l.notes || ""} ${l.start_address}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    );
  }, [lights, selection.controllerId, search]);

  // Prune ghost selections: when the user narrows the controller filter
  // we drop any selected lights that aren't in the current view so the
  // bottom action bar can't quietly apply to fixtures the user can't
  // see.
  const onControllerFilterChange = (id: number | null) => {
    setControllerFilter(id);
    if (id == null) return;
    const allowed = new Set(
      lights.filter((l) => l.controller_id === id).map((l) => l.id),
    );
    const next: number[] = [];
    for (const lid of selection.lightIds) {
      if (allowed.has(lid)) next.push(lid);
    }
    if (next.length !== selection.lightIds.size) {
      setMany(next);
    }
  };

  const onColorController = (cid: number, arr: Light[]) => {
    setControllerFilter(cid);
    setMany(arr.map((l) => l.id));
    setPickerOpen(true);
  };

  const groups = useMemo(() => {
    const map = new Map<number, Light[]>();
    for (const l of filteredLights) {
      const arr = map.get(l.controller_id) ?? [];
      arr.push(l);
      map.set(l.controller_id, arr);
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => a.position - b.position || a.id - b.id);
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [filteredLights]);

  const selectAll = () => {
    setMany(filteredLights.map((l) => l.id));
  };

  const onBlackoutSelected = async () => {
    if (!hasSelection) return;
    try {
      await Api.bulkColor([...selection.lightIds], {
        r: 0,
        g: 0,
        b: 0,
        on: false,
      });
      notify("Blacked out selection", "success");
    } catch (e) {
      notify(String(e), "error");
    }
  };

  const onApplyPalette = async (palette: Palette) => {
    if (!hasSelection) return;
    try {
      await Api.applyPalette(
        palette.id,
        [...selection.lightIds],
        "cycle",
        "across_lights",
      );
      notify(`Applied ${palette.name}`, "success");
    } catch (e) {
      notify(String(e), "error");
    }
  };

  return (
    <div className="flex flex-col gap-4 pb-32">
      <OutputStatus />
      <input
        className="input"
        type="search"
        aria-label="Find fixtures"
        placeholder="Find by name, location, or DMX address…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <section className="card px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <ControllerFilter
            controllers={controllers}
            selected={selection.controllerId}
            onChange={onControllerFilterChange}
          />
          <button
            onClick={selectAll}
            className="btn-ghost text-xs"
            disabled={filteredLights.length === 0}
          >
            Select all
          </button>
          {hasSelection && (
            <button onClick={clear} className="btn-ghost text-xs">
              Clear
            </button>
          )}
          <span className="ml-auto text-xs text-muted">
            {hasSelection
              ? `${size} selected`
              : `${filteredLights.length} fixtures`}
          </span>
        </div>
      </section>

      {groups.map(([cid, arr]) => {
        const ctrl = controllers.find((c) => c.id === cid);
        return (
          <section key={cid} className="card overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-3 py-2 text-sm font-semibold">
              <span className="truncate">
                {ctrl?.name ?? `Controller #${cid}`}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">
                  {arr.length} fixture{arr.length === 1 ? "" : "s"}
                </span>
                <button
                  disabled={!canOperate}
                  onClick={() => onColorController(cid, arr)}
                  className="btn-secondary text-xs"
                  title="Color every fixture on this controller"
                >
                  Set color
                </button>
              </div>
            </div>
            <div className="px-3 pb-3">
              <RigPreview
                lights={arr}
                onSelect={canOperate ? toggle : undefined}
                showLabels
                selected={selection.lightIds}
                size="lg"
                compact
              />
            </div>
          </section>
        );
      })}

      {hasSelection && canOperate && (
        <div className="phone-actions fixed inset-x-0 bottom-16 z-30 border-t border-line bg-bg-elev/95 px-3 py-3 backdrop-blur md:bottom-0">
          <div className="mx-auto flex max-w-3xl flex-col gap-2">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPickerOpen(true)}
                className="btn-primary flex-1 text-sm"
              >
                Edit color · {size} {size === 1 ? "light" : "lights"}
              </button>
              <button
                onClick={onBlackoutSelected}
                className="btn-secondary text-sm"
              >
                Off
              </button>
            </div>
            <Link to="/quick-fx" className="btn-secondary text-sm">
              Preview FX for {size} selected fixtures
            </Link>
            {isAdmin && size === 1 && (
              <Link
                to={`/config/controllers/${selectedLights[0]?.controller_id}`}
                className="btn-ghost text-xs"
              >
                Edit fixture name, location & patch
              </Link>
            )}
          </div>
        </div>
      )}

      <Modal
        open={pickerOpen && hasSelection}
        onClose={() => setPickerOpen(false)}
        title={
          size === 1
            ? `Color: ${selectedLights[0]?.name ?? "Light"}`
            : `Color · ${size} lights`
        }
        size="md"
      >
        {selectedLights.length > 0 && (
          <LightColorPicker
            lights={selectedLights}
            models={models}
            notify={notify}
            onApplied={(updated) => {
              const byId = new Map(updated.map((l) => [l.id, l]));
              setLights((prev) => prev.map((l) => byId.get(l.id) ?? l));
            }}
          />
        )}
      </Modal>
    </div>
  );
}

function ControllerFilter({
  controllers,
  selected,
  onChange,
}: {
  controllers: Controller[];
  selected: number | null;
  onChange: (id: number | null) => void;
}) {
  return (
    <select
      value={selected ?? ""}
      onChange={(e) => {
        const v = e.currentTarget.value;
        onChange(v === "" ? null : parseInt(v, 10));
      }}
      className="rounded bg-bg-card px-2 py-1 text-xs ring-1 ring-line"
      aria-label="Filter by controller"
    >
      <option value="">All controllers</option>
      {controllers.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </select>
  );
}
