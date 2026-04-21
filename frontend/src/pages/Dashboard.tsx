import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api, Controller, Light, LightModel, Palette } from "../api";
import { useToast } from "../toast";
import ColorPicker from "../components/ColorPicker";
import Modal from "../components/Modal";
import PaletteSwatch from "../components/PaletteSwatch";
import { hexToRgb, rgbToHex } from "../util";

type Mode = "cycle" | "gradient" | "random";

export default function Dashboard() {
  const toast = useToast();
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [models, setModels] = useState<LightModel[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [pickerFor, setPickerFor] = useState<number | "bulk" | null>(null);
  const [pickerColor, setPickerColor] = useState("#FFFFFF");
  const [showPalettes, setShowPalettes] = useState(false);
  const [paletteMode, setPaletteMode] = useState<Mode>("cycle");

  const refresh = async () => {
    try {
      const [c, m, l, p] = await Promise.all([
        Api.listControllers(),
        Api.listModels(),
        Api.listLights(),
        Api.listPalettes(),
      ]);
      setControllers(c);
      setModels(m);
      setLights(l);
      setPalettes(p);
    } catch (e) {
      toast.push(String(e), "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const toggleSelect = (id: number, shift: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (shift) {
        if (next.has(id)) next.delete(id);
        else next.add(id);
      } else {
        if (next.size === 1 && next.has(id)) {
          next.clear();
        } else {
          next.clear();
          next.add(id);
        }
      }
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(lights.map((l) => l.id)));
  const clearSelection = () => setSelected(new Set());

  const openPickerFor = (light: Light) => {
    setPickerColor(rgbToHex(light.r, light.g, light.b));
    setPickerFor(light.id);
  };

  const openBulkPicker = () => {
    setPickerColor("#FFFFFF");
    setPickerFor("bulk");
  };

  const commitColor = async (hex: string) => {
    const { r, g, b } = hexToRgb(hex);
    try {
      if (pickerFor === "bulk") {
        const ids = Array.from(selected);
        if (ids.length === 0) return;
        await Api.bulkColor(ids, { r, g, b, on: true });
        setLights((prev) =>
          prev.map((l) =>
            ids.includes(l.id) ? { ...l, r, g, b, on: true } : l,
          ),
        );
      } else if (typeof pickerFor === "number") {
        const updated = await Api.setColor(pickerFor, { r, g, b, on: true });
        setLights((prev) => prev.map((l) => (l.id === pickerFor ? updated : l)));
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

  const blackoutController = async (c: Controller) => {
    if (!confirm(`Blackout ${c.name}? All lights on this controller will be turned off.`)) return;
    try {
      await Api.blackoutController(c.id);
      toast.push(`${c.name} blacked out`, "success");
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const applyPalette = async (p: Palette) => {
    const ids = Array.from(selected);
    if (ids.length === 0) {
      toast.push("Select one or more lights first", "error");
      return;
    }
    try {
      await Api.applyPalette(p.id, ids, paletteMode);
      toast.push(`Applied ${p.name} to ${ids.length} light${ids.length === 1 ? "" : "s"}`, "success");
      setShowPalettes(false);
      await refresh();
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

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
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">Lights</h1>
          <p className="text-sm text-muted">
            {lights.length} lights across {controllers.length} controller
            {controllers.length === 1 ? "" : "s"}.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={selectAll}>
            Select all
          </button>
          <button
            className="btn-secondary"
            onClick={clearSelection}
            disabled={selected.size === 0}
          >
            Clear selection
          </button>
          <button
            className="btn-primary"
            onClick={openBulkPicker}
            disabled={selected.size === 0}
          >
            Set color ({selected.size})
          </button>
          <button
            className="btn-secondary"
            onClick={() => setShowPalettes(true)}
            disabled={selected.size === 0}
          >
            Apply palette
          </button>
        </div>
      </div>

      {groupedLights.map(({ controller, lights: clights }) => (
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
            <button
              className="btn-ghost text-rose-300 hover:bg-rose-950 hover:text-rose-200"
              onClick={() => blackoutController(controller)}
            >
              Blackout
            </button>
          </header>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {clights.map((light) => (
              <LightCard
                key={light.id}
                light={light}
                model={modelById.get(light.model_id)}
                selected={selected.has(light.id)}
                onToggleSelect={(shift) => toggleSelect(light.id, shift)}
                onOpen={() => openPickerFor(light)}
                onToggleOn={() => toggleOn(light)}
              />
            ))}
          </div>
        </section>
      ))}

      <Modal
        open={pickerFor !== null}
        onClose={() => setPickerFor(null)}
        title={pickerFor === "bulk" ? `Set color on ${selected.size} lights` : "Set light color"}
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
      </Modal>

      <Modal
        open={showPalettes}
        onClose={() => setShowPalettes(false)}
        title={`Apply palette to ${selected.size} light${selected.size === 1 ? "" : "s"}`}
        size="lg"
      >
        <div className="mb-4 flex items-center gap-2">
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
    </div>
  );
}

function LightCard({
  light,
  model,
  selected,
  onToggleSelect,
  onOpen,
  onToggleOn,
}: {
  light: Light;
  model?: LightModel;
  selected: boolean;
  onToggleSelect: (shift: boolean) => void;
  onOpen: () => void;
  onToggleOn: () => void;
}) {
  const swatch = rgbToHex(light.r, light.g, light.b);
  const off = !light.on || (light.r === 0 && light.g === 0 && light.b === 0);
  return (
    <div
      className={
        "card flex flex-col overflow-hidden transition " +
        (selected ? "ring-2 ring-accent" : "hover:ring-1 hover:ring-line")
      }
    >
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
          aria-checked={selected}
        >
          {selected ? "✓" : ""}
        </span>
      </button>
      <div className="flex items-center justify-between gap-2 p-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{light.name}</div>
          <div className="truncate text-xs text-muted">
            {model ? `${model.name}` : "unknown model"}
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
