import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Api,
  ApiError,
  Controller,
  DEFAULT_EFFECT_CONTROLS,
  Effect,
  EffectChatMessage,
  EffectControls,
  EffectConversationSummary,
  EffectLintError,
  EffectParamSchemaEntry,
  EffectParams,
  EffectProposal,
  EffectTargetChannel,
  Light,
  Palette,
  PaletteSpread,
  PreviewFrame,
  PreviewPatch,
  PreviewStrip,
} from "../api";
import EffectParamsForm from "../components/EffectParamsForm";
import LuaEditor from "../components/LuaEditor";
import PaletteSwatch from "../components/PaletteSwatch";
import { useToast } from "../toast";

const SPREADS: { key: PaletteSpread; label: string; hint: string }[] = [
  { key: "across_lights", label: "Lights", hint: "One step per fixture." },
  {
    key: "across_fixture",
    label: "Fixture",
    hint: "Each fixture runs the effect over its own zones.",
  },
  {
    key: "across_zones",
    label: "Zones",
    hint: "Flatten every zone into one long strip.",
  },
];

const SPREAD_LABELS: Record<PaletteSpread, string> = {
  across_lights: "across lights",
  across_fixture: "across fixture",
  across_zones: "across zones",
};

const TARGET_CHANNELS: {
  key: EffectTargetChannel;
  label: string;
  hint: string;
  swatch: string;
}[] = [
  {
    key: "rgb",
    label: "RGB",
    hint: "Full-color overlay (default).",
    swatch: "conic-gradient(red, yellow, lime, cyan, blue, magenta, red)",
  },
  { key: "w", label: "W", hint: "White LED.", swatch: "#FFFFFF" },
  { key: "a", label: "A", hint: "Amber LED.", swatch: "#FF9F3A" },
  { key: "uv", label: "UV", hint: "UV / V LED.", swatch: "#7C4DFF" },
  {
    key: "dimmer",
    label: "Dim",
    hint: "Master dimmer fader.",
    swatch: "#FFE27A",
  },
  {
    key: "strobe",
    label: "Strb",
    hint: "Fixture strobe channel.",
    swatch: "#F1F5F9",
  },
];

const STARTER_SCRIPT = `NAME = "New effect"
DESCRIPTION = "Custom Lua effect."

PARAMS = {
  { id = "speed_hz", label = "Speed", type = "number", min = 0, max = 25, default = 0.5, suffix = "Hz" },
}

function render(ctx)
  local phase = (ctx.t * (ctx.params.speed_hz or 0) + ctx.i / math.max(1, ctx.n)) % 1
  local r, g, b = ctx.palette:smooth(phase)
  return { r = r, g = g, b = b, brightness = 1.0 }
end
`;

export default function Effects() {
  const toast = useToast();

  const [palettes, setPalettes] = useState<Palette[]>([]);
  const [presets, setPresets] = useState<Effect[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [selectedLightIds, setSelectedLightIds] = useState<Set<number>>(
    new Set(),
  );
  const [loadedFrom, setLoadedFrom] = useState<{
    id: number;
    builtin: boolean;
  } | null>(null);

  const [name, setName] = useState<string>("Untitled");
  const [source, setSource] = useState<string>(STARTER_SCRIPT);
  const [paletteId, setPaletteId] = useState<number | null>(null);
  const [spread, setSpread] = useState<PaletteSpread>("across_lights");
  const [params, setParams] = useState<EffectParams>({});
  const [controls, setControls] = useState<EffectControls>({
    ...DEFAULT_EFFECT_CONTROLS,
  });
  const [targetChannels, setTargetChannels] = useState<EffectTargetChannel[]>(
    ["rgb"],
  );

  const [scriptMeta, setScriptMeta] = useState<{
    name: string;
    description: string;
    schema: EffectParamSchemaEntry[];
    has_render: boolean;
    has_tick: boolean;
  }>({
    name: "",
    description: "",
    schema: [],
    has_render: true,
    has_tick: false,
  });
  const [lintError, setLintError] = useState<EffectLintError | null>(null);
  const [linting, setLinting] = useState(false);

  const [liveHandle, setLiveHandle] = useState<string | null>(null);
  // Code editor is hidden by default - the user said "Claude is the
  // primary interface; Lua is for inspection". Toggle reveals it.
  const [showCode, setShowCode] = useState<boolean>(false);
  // Routing knobs (spread / channels / palette) live under an advanced
  // disclosure since these should mostly be told to Claude in chat.
  const [showRouting, setShowRouting] = useState<boolean>(false);

  // Chat ----
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
  const [toolBuffer, setToolBuffer] = useState<string>("");
  const [chatRetry, setChatRetry] = useState<{
    attempt: number;
    max: number;
    reason: string;
  } | null>(null);
  const [chatScriptError, setChatScriptError] = useState<{
    message: string;
    line?: number | null;
    attempts: number;
  } | null>(null);
  const chatAbortRef = useRef<(() => void) | null>(null);

  const refreshAll = useCallback(async () => {
    try {
      const [pal, eff, li, ctrls] = await Promise.all([
        Api.listPalettes(),
        Api.listEffects(),
        Api.listLights(),
        Api.listControllers(),
      ]);
      setPalettes(pal);
      setPresets(eff);
      setLights(li);
      setControllers(ctrls);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }, [toast]);

  useEffect(() => {
    void refreshAll();
    Api.effectChat
      .status()
      .then((s) => {
        setAiEnabled(!!s.enabled);
      })
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

  // Debounced lint --------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    const handle = window.setTimeout(async () => {
      setLinting(true);
      try {
        const res = await Api.lintEffect(source);
        if (cancelled) return;
        if (res.ok) {
          setLintError(null);
          setScriptMeta({
            name: res.name || "",
            description: res.description || "",
            schema: res.param_schema || [],
            has_render: res.has_render,
            has_tick: res.has_tick,
          });
        } else {
          setLintError(res.error ?? { message: "compile failed" });
        }
      } catch (e) {
        if (!cancelled) {
          setLintError({ message: String(e) });
        }
      } finally {
        if (!cancelled) setLinting(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [source]);

  // Preview WS ------------------------------------------------------------
  const [previewStrips, setPreviewStrips] = useState<PreviewStrip[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const cellCount = 32;

  const sendPatch = useCallback((patch: PreviewPatch) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ patch }));
  }, []);

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/effects/preview/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          source,
          params,
          palette: paletteFor(paletteId, palettes)?.colors ?? ["#FFFFFF"],
          cells: cellCount,
          spread,
          target_channels: targetChannels,
          intensity: controls.intensity,
        }),
      );
    };
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as PreviewFrame;
        if (data.error) {
          setLintError(data.error);
          return;
        }
        if (Array.isArray(data.strips)) {
          setPreviewStrips(data.strips);
        }
      } catch {
        // ignore
      }
    };
    return () => {
      try {
        ws.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    sendPatch({ source });
  }, [source, sendPatch]);
  useEffect(() => {
    sendPatch({ params });
  }, [params, sendPatch]);
  useEffect(() => {
    const colors = paletteFor(paletteId, palettes)?.colors ?? ["#FFFFFF"];
    sendPatch({ palette: colors });
  }, [paletteId, palettes, sendPatch]);
  useEffect(() => {
    sendPatch({ target_channels: targetChannels, spread });
  }, [targetChannels, spread, sendPatch]);
  useEffect(() => {
    sendPatch({ intensity: controls.intensity });
  }, [controls.intensity, sendPatch]);

  // Selection -------------------------------------------------------------
  function toggleLight(id: number) {
    setSelectedLightIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  const selectAll = () => setSelectedLightIds(new Set(lights.map((l) => l.id)));
  const selectNone = () => setSelectedLightIds(new Set());
  function selectController(cid: number) {
    setSelectedLightIds((prev) => {
      const next = new Set(prev);
      const controllerLights = lights
        .filter((l) => l.controller_id === cid)
        .map((l) => l.id);
      const allSelected = controllerLights.every((id) => next.has(id));
      if (allSelected) {
        for (const id of controllerLights) next.delete(id);
      } else {
        for (const id of controllerLights) next.add(id);
      }
      return next;
    });
  }

  // Live restart on edits -------------------------------------------------
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!liveHandle) return;
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void restartLive();
    }, 200);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    source,
    paletteId,
    spread,
    params,
    controls,
    targetChannels,
    selectedLightIds,
  ]);

  async function startLive() {
    if (selectedLightIds.size === 0) {
      toast.push("Select one or more lights to push live", "error");
      return;
    }
    if (lintError) {
      toast.push("Fix the script before going live", "error");
      return;
    }
    try {
      const res = await Api.playLive({
        source,
        palette_id: paletteId,
        light_ids: Array.from(selectedLightIds),
        targets: [],
        spread,
        params,
        controls,
        target_channels: targetChannels,
      });
      setLiveHandle(res.handle);
    } catch (e) {
      toast.push(formatError(e), "error");
    }
  }

  async function restartLive() {
    if (!liveHandle) return;
    const prev = liveHandle;
    try {
      const res = await Api.playLive({
        source,
        palette_id: paletteId,
        light_ids: Array.from(selectedLightIds),
        targets: [],
        spread,
        params,
        controls: { ...controls, fade_in_s: 0 },
        target_channels: targetChannels,
      });
      setLiveHandle(res.handle);
      void Api.stopLive(prev);
    } catch (e) {
      toast.push(formatError(e), "error");
    }
  }

  async function stopLive() {
    if (!liveHandle) return;
    const h = liveHandle;
    setLiveHandle(null);
    try {
      await Api.stopLive(h);
    } catch (e) {
      toast.push(formatError(e), "error");
    }
  }

  async function savePreset() {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.push("Enter a name", "error");
      return;
    }
    if (loadedFrom?.builtin) {
      toast.push(
        "Builtin effects are read-only; clone first to make an editable copy",
        "error",
      );
      return;
    }
    try {
      if (loadedFrom?.id != null) {
        await Api.updateEffect(loadedFrom.id, {
          name: trimmed,
          source,
          palette_id: paletteId,
          light_ids: Array.from(selectedLightIds),
          targets: [],
          spread,
          params,
          controls,
          target_channels: targetChannels,
        });
        toast.push(`Saved "${trimmed}"`, "success");
      } else {
        const created = await Api.createEffect({
          name: trimmed,
          source,
          palette_id: paletteId,
          light_ids: Array.from(selectedLightIds),
          targets: [],
          spread,
          params,
          controls,
          target_channels: targetChannels,
        });
        setLoadedFrom({ id: created.id, builtin: created.builtin });
        toast.push(`Saved "${trimmed}"`, "success");
      }
      await refreshAll();
    } catch (e) {
      toast.push(formatError(e), "error");
    }
  }

  function loadPreset(p: Effect) {
    setLoadedFrom({ id: p.id, builtin: p.builtin });
    setName(p.name);
    setSource(p.source || STARTER_SCRIPT);
    setPaletteId(p.palette_id);
    setSpread(p.spread);
    setParams({ ...p.params });
    setControls({ ...p.controls });
    setTargetChannels(
      p.target_channels && p.target_channels.length > 0
        ? [...p.target_channels]
        : ["rgb"],
    );
  }

  async function clonePreset(p: Effect) {
    try {
      const cloned = await Api.cloneEffect(p.id);
      await refreshAll();
      loadPreset(cloned);
      toast.push(`Cloned "${p.name}" (now editable)`, "success");
    } catch (e) {
      toast.push(formatError(e), "error");
    }
  }

  function newScratch() {
    setLoadedFrom(null);
    setName("Untitled");
    setSource(STARTER_SCRIPT);
    setParams({});
  }

  // Chat ------------------------------------------------------------------
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
    setToolBuffer("");
    setChatRetry(null);
    setChatScriptError(null);
    const optimistic: EffectChatMessage = {
      role: "user",
      text,
      proposal: null,
    };
    setChatMessages((m) => [...m, optimistic]);

    const handle = Api.effectChat.streamMessage(cid, text, {
      onText: (d) => setChatStream((s) => s + d),
      onToolDelta: (partial) => setToolBuffer((s) => s + partial),
      onRetry: (info) => {
        setChatRetry({
          attempt: info.attempt,
          max: info.max_attempts,
          reason: info.reason,
        });
        // Clear the partial buffer because the next attempt re-streams.
        setToolBuffer("");
      },
      onScriptError: (info) => {
        setChatScriptError(info);
      },
      onProposal: (p) => {
        setChatProposal(p);
        if (p) loadProposal(p);
      },
      onDone: (convo) => {
        setChatMessages(convo.messages);
        setChatProposal(convo.last_proposal);
        if (convo.last_proposal) loadProposal(convo.last_proposal);
        setChatStream("");
        setToolBuffer("");
        setChatRetry(null);
        setChatStreaming(false);
      },
      onError: (msg) => {
        toast.push(msg, "error");
        setChatStreaming(false);
        setToolBuffer("");
        setChatRetry(null);
      },
    });
    chatAbortRef.current = handle.cancel;
    await handle.done;
    setChatStreaming(false);
    chatAbortRef.current = null;
  }

  function loadProposal(p: EffectProposal) {
    setLoadedFrom(null);
    setName(p.name);
    setSource(p.source || STARTER_SCRIPT);
    setPaletteId(p.palette_id);
    setSpread(p.spread);
    setParams({ ...p.params });
    setControls({ ...(p.controls ?? DEFAULT_EFFECT_CONTROLS) });
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
      toast.push(formatError(e), "error");
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
      toast.push(formatError(e), "error");
    }
  }

  // Render ----------------------------------------------------------------
  const playing = liveHandle !== null;
  const palette = paletteFor(paletteId, palettes);
  const paletteUsed = useMemo(() => /palette/i.test(source), [source]);
  const lightsByController = useMemo(() => {
    const map = new Map<number, Light[]>();
    for (const l of lights) {
      const arr = map.get(l.controller_id) ?? [];
      arr.push(l);
      map.set(l.controller_id, arr);
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => a.start_address - b.start_address || a.id - b.id);
    }
    return map;
  }, [lights]);

  return (
    <div className="space-y-3">
      {/* 1) Top bar: name + presets + Save / Live status ---------------- */}
      <div className="card flex flex-wrap items-center gap-2 p-3">
        <input
          className="input min-w-0 flex-1"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Effect name"
          readOnly={loadedFrom?.builtin}
          title={loadedFrom?.builtin ? "Builtin — clone to edit" : undefined}
        />
        <PresetDropdown
          presets={presets}
          loadedId={loadedFrom?.id ?? null}
          onLoad={loadPreset}
          onClone={clonePreset}
          onNew={newScratch}
          onDelete={async (p) => {
            if (!confirm(`Delete "${p.name}"?`)) return;
            try {
              await Api.deleteEffect(p.id);
              if (loadedFrom?.id === p.id) newScratch();
              await refreshAll();
            } catch (e) {
              toast.push(formatError(e), "error");
            }
          }}
        />
        <div className="ml-auto flex items-center gap-2">
          {linting && (
            <span className="pill bg-bg-elev text-muted ring-line">
              Linting
            </span>
          )}
          {playing && (
            <span className="pill bg-emerald-950 text-emerald-300 ring-emerald-900">
              Live
            </span>
          )}
          <button
            className="btn-secondary"
            onClick={() => void savePreset()}
            disabled={!!lintError || loadedFrom?.builtin}
          >
            {loadedFrom?.id != null && !loadedFrom.builtin ? "Save" : "Save as"}
          </button>
        </div>
      </div>

      {/* 2) Preview ----------------------------------------------------- */}
      <div className="card p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="label !text-[11px] normal-case tracking-normal">
            Preview
          </span>
          <span className="text-[10px] text-muted">
            30 Hz · {previewStrips[0]?.cells.length ?? cellCount} cells
          </span>
          <div className="ml-auto flex items-center gap-2 text-[10px] text-muted">
            <span title="Where the effect's index axis is laid out">
              {SPREAD_LABELS[spread]}
            </span>
            <span aria-hidden>·</span>
            <span>{targetChannels.join("+")}</span>
            {paletteUsed && palette && (
              <>
                <span aria-hidden>·</span>
                <span>{palette.name}</span>
              </>
            )}
            <button
              className="btn-ghost !px-1.5 !py-0 text-[10px]"
              onClick={() => setShowRouting((s) => !s)}
              title="Override how the effect routes through fixtures"
            >
              {showRouting ? "hide routing" : "routing"}
            </button>
          </div>
        </div>
        <PreviewStrips strips={previewStrips} />
        {showRouting && (
          <RoutingDisclosure
            spread={spread}
            setSpread={setSpread}
            targetChannels={targetChannels}
            setTargetChannels={setTargetChannels}
            paletteId={paletteId}
            setPaletteId={setPaletteId}
            paletteUsed={paletteUsed}
            palette={palette}
            palettes={palettes}
          />
        )}
      </div>

      {/* 3) Claude (left) + Effect controls (right) -------------------- */}
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <ChatPanelCard
          aiEnabled={aiEnabled}
          conversations={conversations}
          activeConvoId={activeConvoId}
          chatMessages={chatMessages}
          chatProposal={chatProposal}
          chatStreaming={chatStreaming}
          chatStream={chatStream}
          toolBuffer={toolBuffer}
          chatRetry={chatRetry}
          chatScriptError={chatScriptError}
          chatInput={chatInput}
          setChatInput={setChatInput}
          onNewConvo={() => {
            setActiveConvoId(null);
            setChatMessages([]);
            setChatProposal(null);
            setChatScriptError(null);
          }}
          onLoadConversation={loadConversation}
          onSend={sendChat}
          onLoadProposal={loadProposal}
          onApplyProposal={applyProposal}
          onSaveProposal={saveProposal}
        />

        <div className="space-y-3">
          {scriptMeta.schema.length > 0 ? (
            <div className="card space-y-2 p-3">
              <div className="flex items-center justify-between">
                <div className="label !text-[11px] normal-case tracking-normal">
                  {scriptMeta.name || "Script"} controls
                </div>
                {scriptMeta.description && (
                  <span className="truncate text-[10px] text-muted">
                    {scriptMeta.description}
                  </span>
                )}
              </div>
              <EffectParamsForm
                schema={scriptMeta.schema}
                values={params}
                onChange={setParams}
              />
            </div>
          ) : (
            <div className="card p-3 text-[11px] text-muted">
              This script declares no parameters. Ask Claude to add knobs
              for things like speed, size, or warmth.
            </div>
          )}

          <div className="card space-y-3 p-3">
            <div className="label !text-[11px] normal-case tracking-normal">
              Output envelope
            </div>
            <Slider
              label="Intensity"
              value={controls.intensity}
              min={0}
              max={1}
              step={0.01}
              suffix={`${Math.round(controls.intensity * 100)}%`}
              onChange={(v) =>
                setControls((c) => ({ ...c, intensity: v }))
              }
            />
            <div className="grid grid-cols-2 gap-3">
              <Slider
                label="Fade in"
                value={controls.fade_in_s}
                min={0}
                max={10}
                step={0.1}
                suffix={`${controls.fade_in_s.toFixed(1)}s`}
                onChange={(v) =>
                  setControls((c) => ({ ...c, fade_in_s: v }))
                }
              />
              <Slider
                label="Fade out"
                value={controls.fade_out_s}
                min={0}
                max={10}
                step={0.1}
                suffix={`${controls.fade_out_s.toFixed(1)}s`}
                onChange={(v) =>
                  setControls((c) => ({ ...c, fade_out_s: v }))
                }
              />
            </div>
          </div>
        </div>
      </div>

      {/* 4) Lua code (collapsed by default) ---------------------------- */}
      <div className="card p-3">
        <button
          className="flex w-full items-center justify-between"
          onClick={() => setShowCode((s) => !s)}
        >
          <span className="flex items-center gap-2 text-sm font-semibold">
            Lua source
            {loadedFrom?.builtin && (
              <span className="pill bg-bg-elev text-[10px] text-muted ring-line">
                read-only
              </span>
            )}
            <span className="text-[10px] text-muted">
              {scriptMeta.has_render ? "render(ctx)" : ""}
              {scriptMeta.has_render && scriptMeta.has_tick ? " + " : ""}
              {scriptMeta.has_tick ? "tick(ctx)" : ""}
            </span>
          </span>
          <span className="text-[11px] text-muted">
            {showCode ? "hide" : "show"}
          </span>
        </button>
        {showCode && (
          <div className="mt-2 h-[28rem] overflow-hidden">
            <LuaEditor
              value={source}
              onChange={setSource}
              errorLine={lintError?.line ?? null}
              readOnly={loadedFrom?.builtin}
            />
          </div>
        )}
        {lintError && (
          <div className="mt-2 rounded-md bg-rose-950/60 p-2 text-[11px] text-rose-200 ring-1 ring-rose-900">
            {lintError.line != null
              ? `line ${lintError.line}: ${lintError.message}`
              : lintError.message}
          </div>
        )}
      </div>

      {/* 5) Light selection + Push live -------------------------------- */}
      <LightSelector
        lights={lights}
        controllers={controllers}
        lightsByController={lightsByController}
        selected={selectedLightIds}
        onToggle={toggleLight}
        onSelectAll={selectAll}
        onSelectNone={selectNone}
        onSelectController={selectController}
        rightControl={
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-muted">
              {selectedLightIds.size === 0
                ? "Select targets to push live"
                : `${selectedLightIds.size} target${selectedLightIds.size === 1 ? "" : "s"}`}
            </span>
            {!playing ? (
              <button
                className="btn-primary"
                onClick={() => void startLive()}
                disabled={selectedLightIds.size === 0 || !!lintError}
                title={
                  lintError?.message ??
                  (selectedLightIds.size === 0 ? "Select lights first" : undefined)
                }
              >
                Push live
              </button>
            ) : (
              <button className="btn-danger" onClick={() => void stopLive()}>
                Stop
              </button>
            )}
          </div>
        }
      />
    </div>
  );
}

// =========================================================================
// Subcomponents
// =========================================================================

function RoutingDisclosure({
  spread,
  setSpread,
  targetChannels,
  setTargetChannels,
  paletteId,
  setPaletteId,
  paletteUsed,
  palette,
  palettes,
}: {
  spread: PaletteSpread;
  setSpread: (s: PaletteSpread) => void;
  targetChannels: EffectTargetChannel[];
  setTargetChannels: React.Dispatch<
    React.SetStateAction<EffectTargetChannel[]>
  >;
  paletteId: number | null;
  setPaletteId: (id: number | null) => void;
  paletteUsed: boolean;
  palette: Palette | null;
  palettes: Palette[];
}) {
  return (
    <div className="mt-3 space-y-2 rounded-md bg-bg-elev p-2 ring-1 ring-line">
      <div className="text-[10px] uppercase tracking-wide text-muted">
        Routing override
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="label !text-[10px] normal-case tracking-normal">
            Spread
          </span>
          {SPREADS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSpread(s.key)}
              title={s.hint}
              className={
                "rounded-md px-2 py-1 text-[11px] ring-1 transition " +
                (spread === s.key
                  ? "bg-accent text-white ring-accent"
                  : "bg-bg-card text-slate-300 ring-line hover:bg-bg-elev")
              }
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="label !text-[10px] normal-case tracking-normal">
            Channels
          </span>
          {TARGET_CHANNELS.map((c) => (
            <button
              key={c.key}
              onClick={() =>
                setTargetChannels((prev) => toggleChannel(prev, c.key))
              }
              title={c.hint}
              className={
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ring-1 transition " +
                (targetChannels.includes(c.key)
                  ? "bg-accent text-white ring-accent"
                  : "bg-bg-card text-slate-300 ring-line hover:bg-bg-elev")
              }
            >
              <span
                className="h-2.5 w-2.5 rounded-full ring-1 ring-white/30"
                style={{ background: c.swatch }}
              />
              {c.label}
            </button>
          ))}
        </div>
        <div className="flex min-w-[14rem] flex-1 items-center gap-2">
          <span className="label !text-[10px] normal-case tracking-normal">
            Palette
          </span>
          <select
            className="input !h-8 flex-1 !py-0 text-xs"
            value={paletteId ?? ""}
            onChange={(e) =>
              setPaletteId(
                e.target.value === "" ? null : parseInt(e.target.value),
              )
            }
            disabled={!paletteUsed}
            title={
              paletteUsed
                ? undefined
                : "This script does not call ctx.palette - palette is unused."
            }
          >
            <option value="">(no palette)</option>
            {palettes.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          {palette && paletteUsed && (
            <div className="w-32 flex-shrink-0">
              <PaletteSwatch colors={palette.colors} />
            </div>
          )}
        </div>
      </div>
      <div className="text-[10px] text-muted">
        These usually don't need adjusting - tell Claude what you want
        ("chase the white channel", "spread across each fixture's zones")
        and it will pick.
      </div>
    </div>
  );
}

/** Wraps the chat panel in a card. Chat is the primary interface on the
 *  page (left column), so we always render it inline rather than behind a
 *  toggle. */
function ChatPanelCard(props: React.ComponentProps<typeof ChatPanel>) {
  return (
    <div className="card flex h-[34rem] flex-col p-3">
      <div className="mb-1 flex items-center justify-between">
        <div className="text-sm font-semibold">Claude</div>
        <div className="text-[10px] text-muted">
          {props.aiEnabled
            ? "Describe what you want; refine in plain English."
            : "Claude is not configured on this server."}
        </div>
      </div>
      <ChatPanel {...props} />
    </div>
  );
}

function PresetDropdown({
  presets,
  loadedId,
  onLoad,
  onClone,
  onNew,
  onDelete,
}: {
  presets: Effect[];
  loadedId: number | null;
  onLoad: (p: Effect) => void;
  onClone: (p: Effect) => void;
  onNew: () => void;
  onDelete: (p: Effect) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const current = presets.find((p) => p.id === loadedId);
  const builtin = presets.filter((p) => p.builtin);
  const user = presets.filter((p) => !p.builtin);

  return (
    <div className="relative" ref={ref}>
      <button
        className="btn-secondary !py-1 text-xs"
        onClick={() => setOpen((s) => !s)}
      >
        {current ? `Preset: ${current.name}` : `Presets (${presets.length})`}
        <span className="ml-1 text-[10px]">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-72 rounded-md bg-bg-card p-1 shadow-lg ring-1 ring-line">
          <button
            className="block w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-bg-elev"
            onClick={() => {
              onNew();
              setOpen(false);
            }}
          >
            ＋ New script
          </button>
          {builtin.length > 0 && (
            <PresetSection
              label="Built-in"
              presets={builtin}
              loadedId={loadedId}
              onLoad={(p) => {
                onLoad(p);
                setOpen(false);
              }}
              onClone={(p) => {
                onClone(p);
                setOpen(false);
              }}
            />
          )}
          {user.length > 0 && (
            <PresetSection
              label="Saved"
              presets={user}
              loadedId={loadedId}
              onLoad={(p) => {
                onLoad(p);
                setOpen(false);
              }}
              onClone={(p) => {
                onClone(p);
                setOpen(false);
              }}
              onDelete={onDelete}
            />
          )}
          {presets.length === 0 && (
            <div className="px-2 py-3 text-center text-[11px] text-muted">
              No presets yet.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PresetSection({
  label,
  presets,
  loadedId,
  onLoad,
  onClone,
  onDelete,
}: {
  label: string;
  presets: Effect[];
  loadedId: number | null;
  onLoad: (p: Effect) => void;
  onClone: (p: Effect) => void;
  onDelete?: (p: Effect) => void;
}) {
  return (
    <div className="mt-1 border-t border-line pt-1">
      <div className="px-2 pb-1 text-[10px] uppercase tracking-wide text-muted">
        {label}
      </div>
      <div className="max-h-64 overflow-y-auto">
        {presets.map((p) => (
          <div
            key={p.id}
            className={
              "group flex items-center justify-between gap-1 rounded-md px-1 py-0.5 hover:bg-bg-elev " +
              (loadedId === p.id ? "bg-bg-elev/60" : "")
            }
          >
            <button
              className="flex-1 truncate px-1 py-1 text-left text-xs"
              onClick={() => onLoad(p)}
              title={p.description || p.name}
            >
              {p.name}
              {p.is_active && (
                <span className="ml-1 text-[10px] text-emerald-300">·live</span>
              )}
            </button>
            <button
              className="btn-ghost !px-1 !py-0 text-[10px] opacity-0 group-hover:opacity-100"
              title="Clone"
              onClick={() => onClone(p)}
            >
              ⎘
            </button>
            {onDelete && (
              <button
                className="btn-ghost !px-1 !py-0 text-[10px] text-rose-300 opacity-0 group-hover:opacity-100"
                title="Delete"
                onClick={() => onDelete(p)}
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function LightSelector({
  lights,
  controllers,
  lightsByController,
  selected,
  onToggle,
  onSelectAll,
  onSelectNone,
  onSelectController,
  rightControl,
}: {
  lights: Light[];
  controllers: Controller[];
  lightsByController: Map<number, Light[]>;
  selected: Set<number>;
  onToggle: (id: number) => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
  onSelectController: (cid: number) => void;
  rightControl?: React.ReactNode;
}) {
  return (
    <div className="card p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="label !text-[11px] normal-case tracking-normal">
          Targets
        </span>
        <span className="text-xs text-muted">
          {selected.size}/{lights.length} selected
        </span>
        <div className="flex gap-1">
          <button
            className="btn-ghost !px-2 !py-1 text-[11px]"
            onClick={onSelectAll}
          >
            All
          </button>
          <button
            className="btn-ghost !px-2 !py-1 text-[11px]"
            onClick={onSelectNone}
          >
            None
          </button>
        </div>
        {rightControl && <div className="ml-auto">{rightControl}</div>}
      </div>
      {lights.length === 0 ? (
        <div className="rounded-md bg-bg-elev p-3 text-center text-[11px] text-muted">
          No lights configured yet. Add some on the Lights page.
        </div>
      ) : (
        <div className="space-y-2">
          {controllers
            .filter((c) => (lightsByController.get(c.id) ?? []).length > 0)
            .map((c) => {
              const cl = lightsByController.get(c.id) ?? [];
              const selectedCount = cl.filter((l) => selected.has(l.id))
                .length;
              const allSel = selectedCount === cl.length && cl.length > 0;
              return (
                <div key={c.id} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <button
                      className={
                        "rounded-md px-2 py-0.5 text-[11px] ring-1 transition " +
                        (allSel
                          ? "bg-accent text-white ring-accent"
                          : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                      }
                      onClick={() => onSelectController(c.id)}
                      title={`Toggle every light on ${c.name}`}
                    >
                      {c.name} {selectedCount}/{cl.length}
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {cl.map((l) => {
                      const sel = selected.has(l.id);
                      return (
                        <button
                          key={l.id}
                          onClick={() => onToggle(l.id)}
                          className={
                            "rounded-md px-2 py-1 text-[11px] ring-1 transition " +
                            (sel
                              ? "bg-accent text-white ring-accent"
                              : "bg-bg-elev text-slate-300 ring-line hover:bg-bg-card")
                          }
                          title={`ch ${l.start_address}`}
                        >
                          {l.name}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}

function PreviewStrips({ strips }: { strips: PreviewStrip[] }) {
  if (strips.length === 0) {
    return <PreviewStripRow target="rgb" cells={[]} />;
  }
  return (
    <div className="space-y-1.5">
      {strips.map((s) => (
        <PreviewStripRow key={s.target} target={s.target} cells={s.cells} />
      ))}
    </div>
  );
}

function PreviewStripRow({
  target,
  cells,
}: {
  target: EffectTargetChannel;
  cells: PreviewStrip["cells"];
}) {
  const filled: PreviewStrip["cells"] =
    cells.length > 0
      ? cells
      : Array.from({ length: 32 }, () => ({ active: false }));
  const aux = target !== "rgb";
  const swatch = auxSwatch(target);
  return (
    <div className="flex items-stretch gap-2">
      <div className="flex w-10 flex-shrink-0 flex-col items-center justify-center rounded-md bg-bg-elev px-1 text-[10px] uppercase tracking-wide ring-1 ring-line">
        <span
          className="mb-0.5 h-2.5 w-2.5 rounded-full ring-1 ring-white/30"
          style={{ background: swatch }}
        />
        {target}
      </div>
      <div className="grid h-10 flex-1 grid-flow-col auto-cols-fr gap-px overflow-hidden rounded-md bg-black/40 ring-1 ring-line">
        {filled.map((c, i) => {
          if (!c.active) {
            return <div key={i} className="h-full bg-black/60" />;
          }
          if (aux) {
            return (
              <div
                key={i}
                className="relative h-full overflow-hidden bg-black/60"
              >
                <div
                  className="absolute inset-0"
                  style={{
                    background: swatch,
                    opacity: c.brightness ?? 1,
                  }}
                />
              </div>
            );
          }
          const r = c.r ?? 0;
          const g = c.g ?? 0;
          const b = c.b ?? 0;
          const bri = c.brightness ?? 1;
          return (
            <div
              key={i}
              className="h-full"
              style={{
                background: `rgb(${(r * bri) | 0}, ${(g * bri) | 0}, ${(b * bri) | 0})`,
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

function auxSwatch(target: EffectTargetChannel): string {
  switch (target) {
    case "rgb":
      return "conic-gradient(red, yellow, lime, cyan, blue, magenta, red)";
    case "w":
    case "dimmer":
      return "#FFFFFF";
    case "a":
      return "#FF9F3A";
    case "uv":
      return "#7C4DFF";
    case "strobe":
      return "#F1F5F9";
    default:
      return "#FFFFFF";
  }
}

function paletteFor(
  id: number | null,
  palettes: Palette[],
): Palette | null {
  if (id == null) return null;
  return palettes.find((p) => p.id === id) ?? null;
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

/** Try to read a friendly hint (name or one-line summary) out of the
 *  tool-input JSON that's still being streamed. We can't fully parse a
 *  partial object, but the simple ``"name": "Theater Chase"`` field tends
 *  to land early in the buffer so a regex peek is enough for a status. */
function extractDraftPreview(buffer: string): string {
  if (!buffer) return "";
  const summary = buffer.match(/"summary"\s*:\s*"((?:\\.|[^"\\])*)"/);
  if (summary) return decodeJsonString(summary[1]).slice(0, 120);
  const name = buffer.match(/"name"\s*:\s*"((?:\\.|[^"\\])*)"/);
  if (name) return decodeJsonString(name[1]).slice(0, 80);
  return "";
}

function decodeJsonString(s: string): string {
  try {
    return JSON.parse('"' + s + '"');
  } catch {
    return s;
  }
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: unknown } | null;
    if (body && typeof body === "object" && body.detail) {
      const detail = body.detail as { error?: { message?: string; line?: number } };
      const inner = detail?.error;
      if (inner?.message) {
        return inner.line != null
          ? `line ${inner.line}: ${inner.message}`
          : inner.message;
      }
    }
    return err.message;
  }
  return String(err);
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

// Chat ----------------------------------------------------------------

function ChatPanel({
  aiEnabled,
  conversations,
  activeConvoId,
  chatMessages,
  chatProposal,
  chatStreaming,
  chatStream,
  toolBuffer,
  chatRetry,
  chatScriptError,
  chatInput,
  setChatInput,
  onNewConvo,
  onLoadConversation,
  onSend,
  onLoadProposal,
  onApplyProposal,
  onSaveProposal,
}: {
  aiEnabled: boolean;
  conversations: EffectConversationSummary[];
  activeConvoId: number | null;
  chatMessages: EffectChatMessage[];
  chatProposal: EffectProposal | null;
  chatStreaming: boolean;
  chatStream: string;
  toolBuffer: string;
  chatRetry: { attempt: number; max: number; reason: string } | null;
  chatScriptError: {
    message: string;
    line?: number | null;
    attempts: number;
  } | null;
  chatInput: string;
  setChatInput: (s: string) => void;
  onNewConvo: () => void;
  onLoadConversation: (cid: number) => void;
  onSend: () => void;
  onLoadProposal: (p: EffectProposal) => void;
  onApplyProposal: (p: EffectProposal) => void;
  onSaveProposal: (p: EffectProposal) => void;
}) {
  const draftPreview = useMemo(() => extractDraftPreview(toolBuffer), [toolBuffer]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {activeConvoId !== null && (
        <button
          className="btn-ghost mb-1 self-end !px-2 !py-0 text-[11px]"
          onClick={onNewConvo}
        >
          New chat
        </button>
      )}
      {conversations.length > 0 && activeConvoId === null && (
        <div className="mb-2 max-h-20 space-y-1 overflow-y-auto pr-1">
          {conversations.map((c) => (
            <button
              key={c.id}
              className="block w-full truncate rounded-md bg-bg-elev px-2 py-1 text-left text-[11px] ring-1 ring-line hover:bg-bg-card"
              onClick={() => void onLoadConversation(c.id)}
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
            onLoad={onLoadProposal}
            onApply={onApplyProposal}
            onSave={onSaveProposal}
          />
        ))}
        {chatStreaming && (chatStream || toolBuffer || chatRetry) && (
          <div className="rounded-md bg-bg-elev p-2 text-xs ring-1 ring-line">
            <div className="text-[10px] uppercase tracking-wide text-muted">
              Claude
            </div>
            {chatStream && (
              <div className="mt-1 whitespace-pre-wrap">{chatStream}</div>
            )}
            {chatRetry && (
              <div className="mt-1 rounded bg-amber-950/40 p-1.5 text-[11px] text-amber-200 ring-1 ring-amber-900/60">
                Retry {chatRetry.attempt}/{chatRetry.max}: {chatRetry.reason}
              </div>
            )}
            {toolBuffer && (
              <div className="mt-1 flex items-center gap-2 text-[11px] text-muted">
                <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                <span>
                  Drafting effect ({toolBuffer.length} bytes)
                  {draftPreview ? ` · "${draftPreview}"` : ""}
                </span>
              </div>
            )}
          </div>
        )}
        {chatScriptError && (
          <div className="rounded-md bg-rose-950/60 p-2 text-[11px] text-rose-200 ring-1 ring-rose-900">
            <div className="font-semibold">
              Claude couldn't fix the script after {chatScriptError.attempts}{" "}
              attempts
            </div>
            <div className="mt-0.5">
              {chatScriptError.line != null
                ? `line ${chatScriptError.line}: ${chatScriptError.message}`
                : chatScriptError.message}
            </div>
            <div className="mt-1 text-rose-300/80">
              The script is loaded in the editor; try asking for a fix or
              edit it manually.
            </div>
          </div>
        )}
        {!chatStreaming && chatProposal && (
          <ProposalCard
            proposal={chatProposal}
            onLoad={onLoadProposal}
            onApply={onApplyProposal}
            onSave={onSaveProposal}
          />
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
              onSend();
            }
          }}
        />
        <button
          className="btn-primary !py-1 text-xs"
          onClick={onSend}
          disabled={!aiEnabled || chatStreaming || !chatInput.trim()}
        >
          Send
        </button>
      </div>
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
  const speed = proposal.params?.speed_hz;
  return (
    <div className="space-y-1 rounded-md bg-bg-card p-2 ring-1 ring-line">
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-xs font-semibold">{proposal.name}</div>
        <span className="pill text-[10px]">
          {(proposal.target_channels ?? ["rgb"]).join("+")}
        </span>
      </div>
      <div className="text-[10px] text-muted">
        {typeof speed === "number" && (
          <>speed {speed.toFixed(2)} Hz · </>
        )}
        {(proposal.spread ?? "across_lights").replace(/_/g, " ")}
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
