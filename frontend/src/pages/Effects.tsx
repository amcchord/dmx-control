import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Api,
  DEFAULT_EFFECT_PARAMS,
  EFFECT_LIMITS,
  Effect,
  EffectChatMessage,
  EffectConversationSummary,
  EffectDirection,
  EffectParams,
  EffectProposal,
  EffectTargetChannel,
  EffectType,
  Light,
  Palette,
  PaletteSpread,
} from "../api";
import PaletteSwatch from "../components/PaletteSwatch";
import { computePreview, PreviewCell } from "../lib/effectSim";
import { useToast } from "../toast";

type Meta = {
  key: EffectType;
  label: string;
  icon: string;
  needsPalette: boolean;
  needsSize: boolean;
  needsSoftness: boolean;
  description: string;
};

const EFFECTS: Meta[] = [
  { key: "fade", label: "Fade", icon: "\u25C9", needsPalette: true, needsSize: false, needsSoftness: false, description: "Smooth palette crossfade per target." },
  { key: "cycle", label: "Cycle", icon: "\u21BB", needsPalette: true, needsSize: false, needsSoftness: false, description: "Step through palette colors." },
  { key: "chase", label: "Chase", icon: "\u27A4", needsPalette: true, needsSize: true, needsSoftness: true, description: "Moving window of color across targets." },
  { key: "pulse", label: "Pulse", icon: "\u2665", needsPalette: true, needsSize: false, needsSoftness: false, description: "Brightness breathing on palette color." },
  { key: "rainbow", label: "Rainbow", icon: "\u2728", needsPalette: false, needsSize: false, needsSoftness: false, description: "Full-hue sweep; ignores palette." },
  { key: "strobe", label: "Strobe", icon: "\u26A1", needsPalette: true, needsSize: true, needsSoftness: false, description: "Fast on/off flashes." },
  { key: "sparkle", label: "Sparkle", icon: "\u2734", needsPalette: true, needsSize: false, needsSoftness: false, description: "Random per-target flashes." },
  { key: "wave", label: "Wave", icon: "\u223F", needsPalette: true, needsSize: false, needsSoftness: false, description: "Smooth sinusoidal brightness wave." },
  { key: "static", label: "Static", icon: "\u25A0", needsPalette: true, needsSize: false, needsSoftness: false, description: "Distribute palette once; no motion." },
];

const SPREADS: { key: PaletteSpread; label: string; hint: string }[] = [
  {
    key: "across_lights",
    label: "Across lights",
    hint: "One step per fixture.",
  },
  {
    key: "across_fixture",
    label: "Across fixture",
    hint: "Each fixture runs the effect over its own zones.",
  },
  {
    key: "across_zones",
    label: "Across zones",
    hint: "Flatten every zone into one long strip.",
  },
];

const TARGET_CHANNELS: {
  key: EffectTargetChannel;
  label: string;
  hint: string;
  swatch: string;
}[] = [
  { key: "rgb", label: "RGB", hint: "Full-color overlay (default).", swatch: "conic-gradient(red, yellow, lime, cyan, blue, magenta, red)" },
  { key: "w", label: "White", hint: "Chase the white LED only; preserves base color.", swatch: "#FFFFFF" },
  { key: "a", label: "Amber", hint: "Chase the amber LED only.", swatch: "#FF9F3A" },
  { key: "uv", label: "UV / V", hint: "Chase the UV (violet) channel only.", swatch: "#7C4DFF" },
  { key: "dimmer", label: "Dimmer", hint: "Animate the master dimmer fader.", swatch: "#FFE27A" },
  { key: "strobe", label: "Strobe", hint: "Animate the fixture strobe channel.", swatch: "#F1F5F9" },
];

function findEffect(type: EffectType): Meta {
  return EFFECTS.find((e) => e.key === type) ?? EFFECTS[0];
}

function speedLabel(hz: number): string {
  if (hz <= 0) return "Stopped";
  return `${(hz * 60).toFixed(hz < 1 ? 1 : 0)} BPM`;
}

export default function Effects() {
  const toast = useToast();

  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [presets, setPresets] = useState<Effect[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [selectedLightIds, setSelectedLightIds] = useState<Set<number>>(
    new Set(),
  );

  const [effectType, setEffectType] = useState<EffectType>("fade");
  const [paletteId, setPaletteId] = useState<number | null>(null);
  const [spread, setSpread] = useState<PaletteSpread>("across_lights");
  const [params, setParams] = useState<EffectParams>({
    ...DEFAULT_EFFECT_PARAMS,
  });
  const [targetChannels, setTargetChannels] = useState<
    EffectTargetChannel[]
  >(["rgb"]);

  const [liveHandle, setLiveHandle] = useState<string | null>(null);

  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");

  // Chat state
  const [aiEnabled, setAiEnabled] = useState(false);
  const [conversations, setConversations] = useState<
    EffectConversationSummary[]
  >([]);
  const [activeConvoId, setActiveConvoId] = useState<number | null>(null);
  const [chatMessages, setChatMessages] = useState<EffectChatMessage[]>([]);
  const [chatProposal, setChatProposal] = useState<EffectProposal | null>(
    null,
  );
  const [chatInput, setChatInput] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);
  const [chatStream, setChatStream] = useState<string>("");
  const chatAbortRef = useRef<(() => void) | null>(null);

  const refreshAll = useCallback(async () => {
    try {
      const [pal, eff, li] = await Promise.all([
        Api.listPalettes(),
        Api.listEffects(),
        Api.listLights(),
      ]);
      setPalettes(pal);
      setPresets(eff);
      setLights(li);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }, [toast]);

  useEffect(() => {
    void refreshAll();
    Api.effectChat
      .status()
      .then((s) => setAiEnabled(!!s.enabled))
      .catch(() => setAiEnabled(false));
  }, [refreshAll]);

  useEffect(() => {
    if (!aiEnabled) return;
    Api.effectChat
      .listConversations()
      .then(setConversations)
      .catch(() => void 0);
  }, [aiEnabled]);

  useEffect(() => {
    if (paletteId !== null) return;
    if (palettes.length > 0) setPaletteId(palettes[0].id);
  }, [palettes, paletteId]);

  const meta = findEffect(effectType);
  const paletteEntries = useMemo(() => {
    if (paletteId === null) return [];
    const p = palettes.find((pp) => pp.id === paletteId);
    return p?.entries ?? [];
  }, [paletteId, palettes]);

  // Animated preview.
  const [previewCells, setPreviewCells] = useState<PreviewCell[]>([]);
  const previewStart = useRef<number>(performance.now());
  const previewRaf = useRef<number | null>(null);
  const cellCount = 16;

  useEffect(() => {
    let running = true;
    const tick = () => {
      if (!running) return;
      const t = (performance.now() - previewStart.current) / 1000;
      const entries =
        paletteEntries.length > 0
          ? paletteEntries
          : [{ r: 255, g: 255, b: 255 }];
      setPreviewCells(
        computePreview(
          effectType,
          entries,
          params,
          targetChannels,
          cellCount,
          t,
        ),
      );
      previewRaf.current = requestAnimationFrame(tick);
    };
    previewRaf.current = requestAnimationFrame(tick);
    return () => {
      running = false;
      if (previewRaf.current !== null) cancelAnimationFrame(previewRaf.current);
    };
  }, [effectType, paletteEntries, params, targetChannels]);

  // Selection
  const toggleLight = (id: number) => {
    setSelectedLightIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const selectAll = () => setSelectedLightIds(new Set(lights.map((l) => l.id)));
  const selectNone = () => setSelectedLightIds(new Set());

  // Live push: restart on param change (debounced).
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!liveHandle) return;
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void restartLive();
    }, 160);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    effectType,
    paletteId,
    spread,
    params,
    targetChannels,
    selectedLightIds,
  ]);

  async function startLive() {
    if (selectedLightIds.size === 0) {
      toast.push("Select one or more lights to push live", "error");
      return;
    }
    try {
      const res = await Api.playLive({
        effect_type: effectType,
        palette_id: meta.needsPalette ? paletteId : null,
        light_ids: Array.from(selectedLightIds),
        targets: [],
        spread,
        params,
        target_channels: targetChannels,
      });
      setLiveHandle(res.handle);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  async function restartLive() {
    if (!liveHandle) return;
    const prev = liveHandle;
    try {
      const res = await Api.playLive({
        effect_type: effectType,
        palette_id: meta.needsPalette ? paletteId : null,
        light_ids: Array.from(selectedLightIds),
        targets: [],
        spread,
        params: { ...params, fade_in_s: 0 },
        target_channels: targetChannels,
      });
      setLiveHandle(res.handle);
      void Api.stopLive(prev);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  async function stopLive() {
    if (!liveHandle) return;
    const h = liveHandle;
    setLiveHandle(null);
    try {
      await Api.stopLive(h);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  async function savePreset() {
    if (!liveHandle) {
      toast.push("Start the live preview first, then save.", "error");
      return;
    }
    const name = saveName.trim();
    if (!name) return;
    try {
      await Api.saveLive(liveHandle, name);
      toast.push(`Saved "${name}"`, "success");
      setSaveOpen(false);
      setSaveName("");
      await refreshAll();
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  function loadPreset(p: Effect) {
    setEffectType(p.effect_type);
    setPaletteId(p.palette_id);
    setSpread(p.spread);
    setParams({ ...p.params });
    setTargetChannels(
      p.target_channels && p.target_channels.length > 0
        ? [...p.target_channels]
        : ["rgb"],
    );
  }

  // --- Chat handlers --------------------------------------------------------
  async function ensureConversation(): Promise<number | null> {
    if (activeConvoId !== null) return activeConvoId;
    try {
      const convo = await Api.effectChat.createConversation();
      setActiveConvoId(convo.id);
      setChatMessages([]);
      setChatProposal(null);
      setConversations((cs) => [
        {
          id: convo.id,
          name: convo.name,
          message_count: 0,
          updated_at: convo.updated_at,
        },
        ...cs,
      ]);
      return convo.id;
    } catch (e) {
      toast.push(String(e), "error");
      return null;
    }
  }

  async function loadConversation(cid: number) {
    try {
      const convo = await Api.effectChat.getConversation(cid);
      setActiveConvoId(cid);
      setChatMessages(convo.messages);
      setChatProposal(convo.last_proposal);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  async function sendChat() {
    const text = chatInput.trim();
    if (!text || chatStreaming) return;
    const cid = await ensureConversation();
    if (cid === null) return;
    setChatInput("");
    setChatStreaming(true);
    setChatStream("");
    const optimistic: EffectChatMessage = {
      role: "user",
      text,
      proposal: null,
    };
    setChatMessages((m) => [...m, optimistic]);

    const handle = Api.effectChat.streamMessage(cid, text, {
      onText: (d) => setChatStream((s) => s + d),
      onProposal: (p) => setChatProposal(p),
      onDone: (convo) => {
        setChatMessages(convo.messages);
        setChatProposal(convo.last_proposal);
        setChatStream("");
        setChatStreaming(false);
      },
      onError: (msg) => {
        toast.push(msg, "error");
        setChatStreaming(false);
      },
    });
    chatAbortRef.current = handle.cancel;
    await handle.done;
    setChatStreaming(false);
    chatAbortRef.current = null;
  }

  function loadProposal(p: EffectProposal) {
    setEffectType(p.effect_type);
    setPaletteId(p.palette_id);
    setSpread(p.spread);
    setParams({ ...p.params });
    setTargetChannels(
      p.target_channels && p.target_channels.length > 0
        ? [...p.target_channels]
        : ["rgb"],
    );
  }

  async function applyProposal(p: EffectProposal) {
    if (activeConvoId === null) return;
    if (selectedLightIds.size === 0) {
      toast.push("Select lights first", "error");
      return;
    }
    try {
      const res = await Api.effectChat.applyProposal(
        activeConvoId,
        p.proposal_id,
        Array.from(selectedLightIds),
      );
      setLiveHandle(res.handle);
      toast.push(`Playing "${res.name}"`, "success");
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  async function saveProposal(p: EffectProposal) {
    if (activeConvoId === null) return;
    try {
      const res = await Api.effectChat.saveProposal(
        activeConvoId,
        p.proposal_id,
      );
      toast.push(`Saved "${res.name}"`, "success");
      await refreshAll();
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  // --- Render ---------------------------------------------------------------
  const playing = liveHandle !== null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Effects</h1>
          <p className="text-sm text-muted">
            Preview and design animated effects. Channel targeting lets you
            chase the white, amber, UV, or strobe channel without touching
            the base color.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {playing && (
            <span className="pill bg-emerald-950 text-emerald-300 ring-emerald-900">
              Live
            </span>
          )}
          {!playing ? (
            <button
              className="btn-primary"
              onClick={() => void startLive()}
              disabled={selectedLightIds.size === 0}
            >
              Push live
            </button>
          ) : (
            <>
              <button
                className="btn-secondary"
                onClick={() => {
                  setSaveName(
                    `${findEffect(effectType).label} ${Date.now() % 10000}`,
                  );
                  setSaveOpen(true);
                }}
              >
                Save preset
              </button>
              <button className="btn-danger" onClick={() => void stopLive()}>
                Stop
              </button>
            </>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)_22rem]">
        {/* Left: type + presets + lights ---------------------------------- */}
        <div className="space-y-4">
          <div className="card p-4">
            <div className="label mb-2 !text-[11px] normal-case tracking-normal">
              Effect type
            </div>
            <div className="flex flex-wrap gap-1.5">
              {EFFECTS.map((e) => (
                <button
                  key={e.key}
                  onClick={() => setEffectType(e.key)}
                  title={e.description}
                  className={
                    "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs ring-1 transition " +
                    (effectType === e.key
                      ? "bg-accent text-white ring-accent"
                      : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                  }
                >
                  <span aria-hidden>{e.icon}</span>
                  <span>{e.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card p-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="label !text-[11px] normal-case tracking-normal">
                Saved presets
              </div>
              <div className="text-[11px] text-muted">
                {presets.length}
              </div>
            </div>
            <div className="max-h-80 space-y-1 overflow-y-auto pr-1">
              {presets.map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between gap-2 rounded-md bg-bg-elev p-2 ring-1 ring-line"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">{p.name}</div>
                    <div className="text-[10px] text-muted">
                      {p.effect_type} · {p.target_channels?.join("+") ?? "rgb"}
                    </div>
                  </div>
                  <button
                    className="btn-ghost !px-2 !py-1 text-[11px]"
                    onClick={() => loadPreset(p)}
                  >
                    Load
                  </button>
                  {!p.builtin && (
                    <button
                      className="btn-ghost !px-2 !py-1 text-[11px] text-rose-300"
                      onClick={async () => {
                        if (!confirm(`Delete "${p.name}"?`)) return;
                        try {
                          await Api.deleteEffect(p.id);
                          await refreshAll();
                        } catch (e) {
                          toast.push(String(e), "error");
                        }
                      }}
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
              {presets.length === 0 && (
                <div className="rounded-md bg-bg-elev p-3 text-center text-[11px] text-muted">
                  No saved effects yet.
                </div>
              )}
            </div>
          </div>

          <div className="card p-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="label !text-[11px] normal-case tracking-normal">
                Lights ({selectedLightIds.size}/{lights.length})
              </div>
              <div className="flex gap-1">
                <button className="btn-ghost !px-2 !py-0.5 text-[11px]" onClick={selectAll}>
                  All
                </button>
                <button className="btn-ghost !px-2 !py-0.5 text-[11px]" onClick={selectNone}>
                  None
                </button>
              </div>
            </div>
            <div className="max-h-60 space-y-1 overflow-y-auto pr-1">
              {lights.map((l) => (
                <label
                  key={l.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md bg-bg-elev p-1.5 text-xs ring-1 ring-line hover:bg-bg-card"
                >
                  <input
                    type="checkbox"
                    checked={selectedLightIds.has(l.id)}
                    onChange={() => toggleLight(l.id)}
                  />
                  <span className="truncate">{l.name}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        {/* Center: preview + params --------------------------------------- */}
        <div className="space-y-4">
          <div className="card p-4">
            <div className="mb-2 flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">{meta.label}</div>
                <div className="text-xs text-muted">{meta.description}</div>
              </div>
            </div>
            <PreviewGrid cells={previewCells} />
          </div>

          <div className="card space-y-4 p-4">
            {meta.needsPalette && (
              <div>
                <div className="label mb-1.5 !text-[11px] normal-case tracking-normal">
                  Palette
                </div>
                <div className="grid max-h-40 grid-cols-1 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
                  {palettes.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => setPaletteId(p.id)}
                      className={
                        "flex items-center gap-2 rounded-md p-1.5 text-left text-xs ring-1 transition " +
                        (paletteId === p.id
                          ? "bg-bg-elev ring-accent"
                          : "bg-bg-elev/50 ring-line hover:bg-bg-elev")
                      }
                    >
                      <div className="w-20 flex-shrink-0 truncate">{p.name}</div>
                      <div className="flex-1">
                        <PaletteSwatch colors={p.colors} />
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="label mb-1.5 !text-[11px] normal-case tracking-normal">
                Channels to animate
              </div>
              <div className="flex flex-wrap gap-1.5">
                {TARGET_CHANNELS.map((c) => (
                  <button
                    key={c.key}
                    onClick={() =>
                      setTargetChannels((prev) =>
                        toggleChannel(prev, c.key),
                      )
                    }
                    title={c.hint}
                    className={
                      "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs ring-1 transition " +
                      (targetChannels.includes(c.key)
                        ? "bg-accent text-white ring-accent"
                        : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                    }
                  >
                    <span
                      className="h-3 w-3 rounded-full ring-1 ring-white/30"
                      style={{ background: c.swatch }}
                    />
                    {c.label}
                  </button>
                ))}
              </div>
              <div className="mt-1 text-[10px] text-muted">
                Tip: pick W / A / UV / strobe without RGB to animate that
                channel while keeping the base color steady.
              </div>
            </div>

            <div>
              <div className="label mb-1.5 !text-[11px] normal-case tracking-normal">
                Spread
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                {SPREADS.map((s) => (
                  <button
                    key={s.key}
                    onClick={() => setSpread(s.key)}
                    title={s.hint}
                    className={
                      "rounded-md px-2 py-1.5 text-[11px] ring-1 transition " +
                      (spread === s.key
                        ? "bg-accent text-white ring-accent"
                        : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                    }
                  >
                    <div className="font-medium">{s.label}</div>
                  </button>
                ))}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Slider
                label="Speed"
                value={params.speed_hz}
                min={0}
                max={EFFECT_LIMITS.speed_hz_max}
                step={0.05}
                suffix={speedLabel(params.speed_hz)}
                onChange={(v) => setParams((p) => ({ ...p, speed_hz: v }))}
              />
              <Slider
                label="Offset"
                value={params.offset}
                min={0}
                max={1}
                step={0.01}
                suffix={`${Math.round(params.offset * 100)}%`}
                onChange={(v) => setParams((p) => ({ ...p, offset: v }))}
              />
              <Slider
                label="Intensity"
                value={params.intensity}
                min={0}
                max={1}
                step={0.01}
                suffix={`${Math.round(params.intensity * 100)}%`}
                onChange={(v) => setParams((p) => ({ ...p, intensity: v }))}
              />
              {meta.needsSize && (
                <Slider
                  label={effectType === "strobe" ? "Duty" : "Window"}
                  value={params.size}
                  min={0.05}
                  max={
                    effectType === "strobe" ? 0.95 : EFFECT_LIMITS.size_max
                  }
                  step={0.05}
                  suffix={
                    effectType === "strobe"
                      ? `${Math.round(params.size * 100)}%`
                      : params.size.toFixed(2)
                  }
                  onChange={(v) => setParams((p) => ({ ...p, size: v }))}
                />
              )}
              {meta.needsSoftness && (
                <Slider
                  label="Softness"
                  value={params.softness}
                  min={0}
                  max={1}
                  step={0.01}
                  suffix={`${Math.round(params.softness * 100)}%`}
                  onChange={(v) => setParams((p) => ({ ...p, softness: v }))}
                />
              )}
              <Slider
                label="Fade in"
                value={params.fade_in_s}
                min={0}
                max={EFFECT_LIMITS.fade_max_s}
                step={0.1}
                suffix={`${params.fade_in_s.toFixed(1)}s`}
                onChange={(v) => setParams((p) => ({ ...p, fade_in_s: v }))}
              />
              <Slider
                label="Fade out"
                value={params.fade_out_s}
                min={0}
                max={EFFECT_LIMITS.fade_max_s}
                step={0.1}
                suffix={`${params.fade_out_s.toFixed(1)}s`}
                onChange={(v) =>
                  setParams((p) => ({ ...p, fade_out_s: v }))
                }
              />
              <div>
                <div className="label mb-1 !text-[11px] normal-case tracking-normal">
                  Direction
                </div>
                <div className="flex gap-1">
                  {(["forward", "reverse", "pingpong"] as EffectDirection[]).map(
                    (d) => (
                      <button
                        key={d}
                        onClick={() =>
                          setParams((p) => ({ ...p, direction: d }))
                        }
                        className={
                          "rounded-md px-2 py-1 text-[11px] ring-1 " +
                          (params.direction === d
                            ? "bg-accent text-white ring-accent"
                            : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                        }
                      >
                        {d}
                      </button>
                    ),
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Claude chat -------------------------------------------- */}
        <div className="space-y-2">
          <div className="card flex h-[34rem] flex-col p-3">
            <div className="mb-2 flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">Claude</div>
                <div className="text-[11px] text-muted">
                  {aiEnabled
                    ? "Chat to iterate on the effect."
                    : "Claude not configured."}
                </div>
              </div>
              {activeConvoId !== null && (
                <button
                  className="btn-ghost !px-2 !py-1 text-[11px]"
                  onClick={() => {
                    setActiveConvoId(null);
                    setChatMessages([]);
                    setChatProposal(null);
                  }}
                >
                  New
                </button>
              )}
            </div>

            {conversations.length > 0 && activeConvoId === null && (
              <div className="mb-2 max-h-24 overflow-y-auto space-y-1 pr-1">
                {conversations.map((c) => (
                  <button
                    key={c.id}
                    className="block w-full truncate rounded-md bg-bg-elev px-2 py-1 text-left text-[11px] ring-1 ring-line hover:bg-bg-card"
                    onClick={() => void loadConversation(c.id)}
                  >
                    {c.name || `Chat ${c.id}`}
                  </button>
                ))}
              </div>
            )}

            <div className="flex-1 space-y-2 overflow-y-auto pr-1">
              {chatMessages.map((m, i) => (
                <ChatBubble
                  key={i}
                  role={m.role}
                  text={m.text}
                  proposal={m.proposal}
                  onLoad={loadProposal}
                  onApply={applyProposal}
                  onSave={saveProposal}
                />
              ))}
              {chatStreaming && chatStream && (
                <ChatBubble role="assistant" text={chatStream} />
              )}
              {!chatStreaming && chatProposal && (
                <div className="rounded-md bg-bg-elev p-2 ring-1 ring-accent/50">
                  <div className="text-[11px] font-semibold">
                    Latest proposal
                  </div>
                  <ProposalCard
                    proposal={chatProposal}
                    onLoad={loadProposal}
                    onApply={applyProposal}
                    onSave={saveProposal}
                  />
                </div>
              )}
            </div>

            <div className="mt-2 flex gap-1">
              <input
                className="input text-xs"
                placeholder={
                  aiEnabled
                    ? "e.g. warmer, faster, chase the white channel"
                    : "Claude is not enabled"
                }
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                disabled={!aiEnabled || chatStreaming}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void sendChat();
                  }
                }}
              />
              <button
                className="btn-primary !py-1 text-xs"
                onClick={() => void sendChat()}
                disabled={!aiEnabled || chatStreaming || !chatInput.trim()}
              >
                Send
              </button>
            </div>
          </div>
        </div>
      </div>

      {saveOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
          <div className="card w-full max-w-sm space-y-3 p-4">
            <div className="font-semibold">Save preset</div>
            <input
              className="input"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                className="btn-ghost"
                onClick={() => setSaveOpen(false)}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={() => void savePreset()}
                disabled={!saveName.trim()}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function toggleChannel(
  prev: EffectTargetChannel[],
  key: EffectTargetChannel,
): EffectTargetChannel[] {
  if (prev.includes(key)) {
    const next = prev.filter((k) => k !== key);
    return next.length === 0 ? ["rgb"] : next;
  }
  return [...prev, key];
}

function PreviewGrid({ cells }: { cells: PreviewCell[] }) {
  return (
    <div className="grid h-24 grid-flow-col auto-cols-fr gap-1 overflow-hidden rounded-md bg-black/40 p-1 ring-1 ring-line">
      {cells.map((c, i) => {
        if (c.auxTint) {
          return (
            <div
              key={i}
              className="relative h-full w-full rounded-sm bg-black/60"
            >
              <div
                className="absolute inset-0 rounded-sm"
                style={{
                  background: c.auxTint,
                  opacity: c.brightness,
                }}
              />
            </div>
          );
        }
        return (
          <div
            key={i}
            className="h-full w-full rounded-sm"
            style={{ background: c.rgb }}
          />
        );
      })}
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between">
        <span className="label !text-[11px] normal-case tracking-normal">
          {label}
        </span>
        <span className="text-[11px] text-muted">{suffix}</span>
      </div>
      <input
        type="range"
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-bg-elev accent-accent"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
    </div>
  );
}

function ChatBubble({
  role,
  text,
  proposal,
  onLoad,
  onApply,
  onSave,
}: {
  role: "user" | "assistant";
  text: string;
  proposal?: EffectProposal | null;
  onLoad?: (p: EffectProposal) => void;
  onApply?: (p: EffectProposal) => void;
  onSave?: (p: EffectProposal) => void;
}) {
  return (
    <div
      className={
        "rounded-md p-2 text-xs ring-1 " +
        (role === "user"
          ? "bg-accent/20 text-slate-100 ring-accent/30"
          : "bg-bg-elev ring-line")
      }
    >
      <div className="text-[10px] uppercase tracking-wide text-muted">
        {role === "user" ? "You" : "Claude"}
      </div>
      {text && <div className="mt-1 whitespace-pre-wrap">{text}</div>}
      {proposal && onLoad && onApply && onSave && (
        <div className="mt-2">
          <ProposalCard
            proposal={proposal}
            onLoad={onLoad}
            onApply={onApply}
            onSave={onSave}
          />
        </div>
      )}
    </div>
  );
}

function ProposalCard({
  proposal,
  onLoad,
  onApply,
  onSave,
}: {
  proposal: EffectProposal;
  onLoad: (p: EffectProposal) => void;
  onApply: (p: EffectProposal) => void;
  onSave: (p: EffectProposal) => void;
}) {
  return (
    <div className="space-y-1 rounded-md bg-bg-card p-2 ring-1 ring-line">
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-xs font-semibold">{proposal.name}</div>
        <span className="pill text-[10px]">{proposal.effect_type}</span>
      </div>
      <div className="text-[10px] text-muted">
        {proposal.target_channels.join("+")} · speed{" "}
        {proposal.params.speed_hz.toFixed(2)} Hz
      </div>
      <div className="flex flex-wrap gap-1 pt-1">
        <button
          className="btn-ghost !px-2 !py-0.5 text-[10px]"
          onClick={() => onLoad(proposal)}
        >
          Load
        </button>
        <button
          className="btn-ghost !px-2 !py-0.5 text-[10px]"
          onClick={() => onApply(proposal)}
        >
          Play
        </button>
        <button
          className="btn-ghost !px-2 !py-0.5 text-[10px]"
          onClick={() => onSave(proposal)}
        >
          Save
        </button>
      </div>
    </div>
  );
}
