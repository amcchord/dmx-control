import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Api,
  Controller,
  DesignerConversation,
  DesignerConversationSummary,
  DesignerMessage,
  DesignerProposal,
  DesignerProposalLight,
  DesignerStreamHandle,
  Light,
} from "../api";
import { useToast } from "../toast";
import { rgbToHex } from "../util";

type TurnState =
  | { kind: "idle" }
  | {
      kind: "streaming";
      text: string;
      hasTool: boolean;
      handle: DesignerStreamHandle;
      userMessage: string;
    }
  | { kind: "error"; message: string; lastUserMessage: string };

export default function Designer() {
  const toast = useToast();
  const [aiEnabled, setAiEnabled] = useState<boolean | null>(null);
  const [conversations, setConversations] = useState<
    DesignerConversationSummary[]
  >([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeConvo, setActiveConvo] = useState<DesignerConversation | null>(
    null,
  );
  const [turn, setTurn] = useState<TurnState>({ kind: "idle" });
  const [input, setInput] = useState("");
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [savePrompt, setSavePrompt] = useState<{
    cid: number;
    proposal: DesignerProposal;
    name: string;
  } | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const autoScroll = useRef(true);

  // Reactive mirror of the streaming text so React renders every frame.
  // We batch updates through a ref to avoid re-rendering per token.
  const pendingText = useRef<string>("");
  const rafPending = useRef<number | null>(null);
  const bumpStreamingText = useCallback(() => {
    if (rafPending.current !== null) return;
    rafPending.current = window.requestAnimationFrame(() => {
      rafPending.current = null;
      setTurn((t) => {
        if (t.kind !== "streaming") return t;
        if (t.text === pendingText.current) return t;
        return { ...t, text: pendingText.current };
      });
    });
  }, []);

  useEffect(() => {
    return () => {
      if (rafPending.current !== null) {
        window.cancelAnimationFrame(rafPending.current);
      }
    };
  }, []);

  // Bootstrap: AI status, conversation list, rig context for proposal swatches.
  useEffect(() => {
    Api.designer
      .status()
      .then((s) => setAiEnabled(s.enabled))
      .catch(() => setAiEnabled(false));
    refreshConversations();
    Promise.all([Api.listControllers(), Api.listLights()])
      .then(([c, l]) => {
        setControllers(c);
        setLights(l);
      })
      .catch(() => {});
  }, []);

  const refreshConversations = async () => {
    try {
      const list = await Api.designer.listConversations();
      setConversations(list);
      if (activeId === null && list.length > 0) {
        setActiveId(list[0].id);
      }
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  // Load active conversation.
  useEffect(() => {
    if (activeId === null) {
      setActiveConvo(null);
      return;
    }
    let cancelled = false;
    Api.designer
      .getConversation(activeId)
      .then((c) => {
        if (!cancelled) setActiveConvo(c);
      })
      .catch((e) => toast.push(String(e), "error"));
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // Auto-scroll to bottom during streaming unless the user scrolled away.
  useEffect(() => {
    const el = transcriptRef.current;
    if (!el) return;
    if (!autoScroll.current) return;
    el.scrollTop = el.scrollHeight;
  }, [activeConvo?.messages.length, turn]);

  const handleScroll = () => {
    const el = transcriptRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    autoScroll.current = nearBottom;
  };

  const controllerById = useMemo(() => {
    const map = new Map<number, Controller>();
    for (const c of controllers) map.set(c.id, c);
    return map;
  }, [controllers]);

  const lightById = useMemo(() => {
    const map = new Map<number, Light>();
    for (const l of lights) map.set(l.id, l);
    return map;
  }, [lights]);

  // ---- conversation actions ---------------------------------------------
  const newConversation = async () => {
    try {
      const c = await Api.designer.createConversation();
      setConversations((prev) => [
        {
          id: c.id,
          name: c.name,
          message_count: 0,
          updated_at: c.updated_at,
        },
        ...prev,
      ]);
      setActiveId(c.id);
      setActiveConvo(c);
      setTurn({ kind: "idle" });
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const renameConversation = async (cid: number) => {
    const current =
      conversations.find((c) => c.id === cid)?.name ?? "Chat";
    const name = window.prompt("Rename chat", current);
    if (!name) return;
    try {
      const updated = await Api.designer.renameConversation(cid, name);
      setConversations((prev) =>
        prev.map((c) =>
          c.id === cid
            ? { ...c, name: updated.name, updated_at: updated.updated_at }
            : c,
        ),
      );
      if (activeId === cid) setActiveConvo(updated);
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const deleteConversation = async (cid: number) => {
    if (!confirm("Delete this chat and its history?")) return;
    try {
      await Api.designer.deleteConversation(cid);
      setConversations((prev) => prev.filter((c) => c.id !== cid));
      if (activeId === cid) {
        setActiveId(null);
        setActiveConvo(null);
      }
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  // ---- sending a turn ----------------------------------------------------
  const sendMessage = async (text: string) => {
    let cid = activeId;
    let convo = activeConvo;
    if (cid === null) {
      try {
        const c = await Api.designer.createConversation();
        cid = c.id;
        convo = c;
        setActiveId(c.id);
        setActiveConvo(c);
        setConversations((prev) => [
          {
            id: c.id,
            name: c.name,
            message_count: 0,
            updated_at: c.updated_at,
          },
          ...prev,
        ]);
      } catch (e) {
        toast.push(String(e), "error");
        return;
      }
    }
    if (cid === null) return;

    // Optimistically show the user turn immediately.
    if (convo) {
      setActiveConvo({
        ...convo,
        messages: [
          ...convo.messages,
          { role: "user", text, proposals: [] },
        ],
      });
    }

    pendingText.current = "";
    const handle = Api.designer.streamMessage(cid, text, {
      onText: (delta) => {
        pendingText.current += delta;
        bumpStreamingText();
      },
      onToolStart: () => {
        setTurn((t) => {
          if (t.kind !== "streaming") return t;
          return { ...t, hasTool: true };
        });
      },
      onProposal: () => {
        // Proposals arrive inside the `done` payload too; no-op here. Keep
        // hook wired so future UI (e.g. partial preview) can subscribe.
      },
      onDone: (full) => {
        setActiveConvo(full);
        setConversations((prev) => {
          const exists = prev.find((c) => c.id === full.id);
          const summary: DesignerConversationSummary = {
            id: full.id,
            name: full.name,
            message_count: full.messages.length,
            updated_at: full.updated_at,
          };
          if (!exists) return [summary, ...prev];
          return prev.map((c) => (c.id === full.id ? summary : c));
        });
        setTurn({ kind: "idle" });
        pendingText.current = "";
      },
      onError: (msg) => {
        setTurn({ kind: "error", message: msg, lastUserMessage: text });
        pendingText.current = "";
      },
    });
    setTurn({
      kind: "streaming",
      text: "",
      hasTool: false,
      handle,
      userMessage: text,
    });
    autoScroll.current = true;
  };

  const submitForm = (e: React.FormEvent) => {
    e.preventDefault();
    if (!aiEnabled) return;
    const trimmed = input.trim();
    if (!trimmed) return;
    if (turn.kind === "streaming") return;
    setInput("");
    void sendMessage(trimmed);
  };

  const stopStreaming = () => {
    if (turn.kind !== "streaming") return;
    turn.handle.cancel();
    setTurn({ kind: "idle" });
  };

  const retryLast = () => {
    if (turn.kind !== "error") return;
    const text = turn.lastUserMessage;
    setTurn({ kind: "idle" });
    void sendMessage(text);
  };

  // ---- proposals ---------------------------------------------------------
  const applyProposal = async (proposal: DesignerProposal) => {
    if (activeId === null) return;
    try {
      const res = await Api.designer.applyProposal(
        activeId,
        proposal.proposal_id,
      );
      if (proposal.kind === "palette") {
        toast.push(`Saved palette "${proposal.name}"`, "success");
      } else if (proposal.kind === "effect") {
        toast.push(`Playing "${proposal.name}"`, "success");
      } else {
        const n = res.applied ?? 0;
        toast.push(
          `Applied "${proposal.name}" (${n} light${n === 1 ? "" : "s"})`,
          "success",
        );
        // Refresh light snapshot so swatches in the rig reflect the new state.
        Api.listLights()
          .then(setLights)
          .catch(() => {});
      }
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  const openSave = (proposal: DesignerProposal) => {
    if (activeId === null) return;
    setSavePrompt({
      cid: activeId,
      proposal,
      name: proposal.name,
    });
  };

  const submitSave = async () => {
    if (savePrompt === null) return;
    const name = savePrompt.name.trim();
    if (!name) return;
    try {
      const res = (await Api.designer.saveProposal(
        savePrompt.cid,
        savePrompt.proposal.proposal_id,
        name,
      )) as {
        ok: boolean;
        kind: "state" | "scene" | "palette" | "effect";
        id: number;
        name: string;
      };
      const label = res.kind;
      toast.push(`Saved ${label} "${res.name}"`, "success");
      setSavePrompt(null);
    } catch (e) {
      toast.push(String(e), "error");
    }
  };

  // ---- rendering ---------------------------------------------------------
  if (aiEnabled === false) {
    return (
      <div className="card p-8 text-center">
        <h1 className="text-lg font-semibold">Designer unavailable</h1>
        <p className="mt-2 text-sm text-muted">
          Claude Opus isn't configured on this server. Set the
          <code className="mx-1 rounded bg-bg-elev px-1 py-0.5 text-xs">
            ANTHROPIC_API_KEY
          </code>
          environment variable (or drop it into
          <code className="mx-1 rounded bg-bg-elev px-1 py-0.5 text-xs">
            claudeKey.env
          </code>
          ) and restart.
        </p>
      </div>
    );
  }
  if (aiEnabled === null) {
    return <div className="text-muted">Loading...</div>;
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col gap-3 sm:flex-row">
      {/* Sidebar */}
      <aside className="flex w-full shrink-0 flex-col gap-2 sm:w-56">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Chats</h2>
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={newConversation}
          >
            + new
          </button>
        </div>
        <div className="flex max-h-60 flex-col gap-1 overflow-y-auto rounded-lg bg-bg-elev p-1 ring-1 ring-line sm:max-h-none sm:flex-1">
          {conversations.length === 0 && (
            <div className="px-2 py-3 text-xs text-muted">
              No chats yet. Start one with a prompt.
            </div>
          )}
          {conversations.map((c) => (
            <button
              key={c.id}
              onClick={() => setActiveId(c.id)}
              className={
                "flex flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left text-sm transition " +
                (c.id === activeId
                  ? "bg-bg-card ring-1 ring-accent"
                  : "hover:bg-bg-card")
              }
            >
              <span className="w-full truncate font-medium">
                {c.name || "Untitled chat"}
              </span>
              <span className="text-[10px] text-muted">
                {c.message_count} msg{c.message_count === 1 ? "" : "s"}
              </span>
            </button>
          ))}
        </div>
        {activeId !== null && (
          <div className="flex gap-1">
            <button
              className="btn-ghost flex-1 text-xs"
              onClick={() => renameConversation(activeId)}
            >
              Rename
            </button>
            <button
              className="btn-ghost flex-1 text-xs text-rose-300 hover:bg-rose-950"
              onClick={() => deleteConversation(activeId)}
            >
              Delete
            </button>
          </div>
        )}
      </aside>

      {/* Chat panel */}
      <section className="card flex min-h-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">
              {activeConvo?.name || "New chat"}
            </h1>
            <p className="text-xs text-muted">
              Ask Claude Opus to design rig states and scenes. Honors
              per-controller and per-light notes.
            </p>
          </div>
        </header>

        <div
          ref={transcriptRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-4 py-4 space-y-4"
        >
          {(!activeConvo || activeConvo.messages.length === 0) &&
            turn.kind !== "streaming" && (
              <EmptyState onPick={(p) => setInput(p)} />
            )}

          {activeConvo?.messages.map((m, i) => (
            <MessageBubble
              key={`${activeConvo.id}-${i}`}
              message={m}
              controllerById={controllerById}
              lightById={lightById}
              onApply={applyProposal}
              onSave={openSave}
            />
          ))}

          {turn.kind === "streaming" && (
            <StreamingBubble
              text={turn.text}
              hasTool={turn.hasTool}
              onStop={stopStreaming}
            />
          )}
          {turn.kind === "error" && (
            <div className="rounded-lg bg-rose-950/60 p-3 text-sm text-rose-200 ring-1 ring-rose-900">
              <div className="font-semibold">Something went wrong</div>
              <div className="mt-1 text-xs text-rose-200/80">
                {turn.message}
              </div>
              <div className="mt-2">
                <button className="btn-secondary text-xs" onClick={retryLast}>
                  Retry
                </button>
              </div>
            </div>
          )}
        </div>

        <form
          onSubmit={submitForm}
          className="flex gap-2 border-t border-line px-4 py-3"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.metaKey &&
                !e.ctrlKey
              ) {
                e.preventDefault();
                submitForm(e);
              }
            }}
            rows={2}
            placeholder='Try "warm amber wash for a ballad" or "build a 4-part show for a DJ set".'
            className="input flex-1 resize-none"
            disabled={turn.kind === "streaming"}
          />
          {turn.kind === "streaming" ? (
            <button
              type="button"
              className="btn-danger self-end"
              onClick={stopStreaming}
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              className="btn-primary self-end"
              disabled={!input.trim()}
            >
              Send
            </button>
          )}
        </form>
      </section>

      {savePrompt && (
        <SaveModal
          value={savePrompt.name}
          kind={savePrompt.proposal.kind}
          onChange={(name) =>
            setSavePrompt((s) => (s ? { ...s, name } : s))
          }
          onCancel={() => setSavePrompt(null)}
          onSubmit={submitSave}
        />
      )}
    </div>
  );
}

function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  const samples = [
    "Design a moody teal and magenta wash for a slow ballad.",
    "Give me four looks for a DJ set: build, drop, breakdown, outro.",
    "Warm candle-light state for an acoustic intro, keep movers aimed low.",
    "Turn every uplighter deep red and blackout the back line.",
  ];
  return (
    <div className="rounded-lg border border-dashed border-line p-6 text-sm text-muted">
      <div className="mb-3 font-medium text-slate-200">
        Start by describing a look or a show.
      </div>
      <div className="space-y-1">
        {samples.map((s) => (
          <button
            key={s}
            className="block w-full rounded-md bg-bg-elev px-3 py-2 text-left text-xs text-slate-200 ring-1 ring-line hover:bg-bg-card"
            onClick={() => onPick(s)}
          >
            {s}
          </button>
        ))}
      </div>
      <div className="mt-4 text-[11px] leading-relaxed">
        Tip: add notes on your controllers and lights (Controllers tab) so
        Claude knows what each fixture is for. Claude always responds with
        proposals you can Apply or Save.
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  controllerById,
  lightById,
  onApply,
  onSave,
}: {
  message: DesignerMessage;
  controllerById: Map<number, Controller>;
  lightById: Map<number, Light>;
  onApply: (p: DesignerProposal) => void;
  onSave: (p: DesignerProposal) => void;
}) {
  const isUser = message.role === "user";
  return (
    <div className={"flex " + (isUser ? "justify-end" : "justify-start")}>
      <div
        className={
          "max-w-[85%] space-y-2 rounded-2xl px-4 py-2.5 text-sm shadow-sm " +
          (isUser
            ? "bg-accent/90 text-white"
            : "bg-bg-elev text-slate-100 ring-1 ring-line")
        }
      >
        {message.text && (
          <div className="whitespace-pre-wrap">{message.text}</div>
        )}
        {!isUser &&
          message.proposals.map((p) => (
            <ProposalCard
              key={p.proposal_id}
              proposal={p}
              controllerById={controllerById}
              lightById={lightById}
              onApply={() => onApply(p)}
              onSave={() => onSave(p)}
            />
          ))}
      </div>
    </div>
  );
}

function StreamingBubble({
  text,
  hasTool,
  onStop,
}: {
  text: string;
  hasTool: boolean;
  onStop: () => void;
}) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2 rounded-2xl bg-bg-elev px-4 py-2.5 text-sm text-slate-100 ring-1 ring-line">
        {text ? (
          <div className="whitespace-pre-wrap">
            {text}
            <span className="inline-block w-1.5 animate-pulse bg-slate-200 align-middle">
              &nbsp;
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-bg-card border-t-accent" />
            Claude is thinking…
          </div>
        )}
        {hasTool && (
          <div className="rounded-md bg-bg-card/60 p-2 ring-1 ring-accent/30">
            <div className="flex items-center gap-2 text-[11px] text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              Building proposal…
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-bg-card">
              <div className="indeterminate-bar h-full rounded-full bg-accent/80" />
            </div>
          </div>
        )}
        <div className="pt-1">
          <button className="btn-ghost !py-1 text-xs" onClick={onStop}>
            Stop
          </button>
        </div>
      </div>
    </div>
  );
}

function ProposalCard({
  proposal,
  controllerById,
  lightById,
  onApply,
  onSave,
}: {
  proposal: DesignerProposal;
  controllerById: Map<number, Controller>;
  lightById: Map<number, Light>;
  onApply: () => void;
  onSave: () => void;
}) {
  let scopeLabel = "rig state";
  if (proposal.kind === "scene") {
    const ctrl = proposal.controller_id
      ? controllerById.get(proposal.controller_id)
      : null;
    scopeLabel = ctrl ? `scene · ${ctrl.name}` : "scene";
  } else if (proposal.kind === "palette") {
    scopeLabel = "palette";
  } else if (proposal.kind === "effect") {
    scopeLabel = "effect";
  }

  let applyLabel = "Apply";
  if (proposal.kind === "palette") applyLabel = "Save as palette";
  else if (proposal.kind === "effect") applyLabel = "Play";

  return (
    <div className="rounded-lg bg-bg-card p-3 text-slate-100 ring-1 ring-line">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold">{proposal.name}</span>
            <span className="pill text-[10px]">{scopeLabel}</span>
            {(proposal.kind === "state" || proposal.kind === "scene") && (
              <span className="pill text-[10px]">
                {proposal.lights.length} light
                {proposal.lights.length === 1 ? "" : "s"}
              </span>
            )}
            {proposal.kind === "effect" && proposal.effect && (
              <span className="pill text-[10px]">
                {proposal.effect.effect_type}
              </span>
            )}
          </div>
          {proposal.notes && (
            <div className="mt-0.5 text-[11px] text-muted">
              {proposal.notes}
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            className="btn-secondary !px-2 !py-1 text-xs"
            onClick={onSave}
          >
            Save
          </button>
          <button
            className="btn-primary !px-2 !py-1 text-xs"
            onClick={onApply}
          >
            {applyLabel}
          </button>
        </div>
      </div>
      {(proposal.kind === "state" || proposal.kind === "scene") && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {proposal.lights.slice(0, 64).map((pl) => (
            <ProposalLightSwatch
              key={pl.light_id}
              pl={pl}
              lightById={lightById}
            />
          ))}
          {proposal.lights.length > 64 && (
            <span className="text-[10px] text-muted">
              +{proposal.lights.length - 64} more
            </span>
          )}
        </div>
      )}
      {proposal.kind === "palette" &&
        proposal.palette_entries &&
        proposal.palette_entries.length > 0 && (
          <div className="mt-2 flex h-6 overflow-hidden rounded-md ring-1 ring-line">
            {proposal.palette_entries.map((e, i) => (
              <div
                key={i}
                className="h-full flex-1"
                style={{ background: `rgb(${e.r}, ${e.g}, ${e.b})` }}
                title={`${e.r}, ${e.g}, ${e.b}`}
              />
            ))}
          </div>
        )}
      {proposal.kind === "effect" && proposal.effect && (
        <div className="mt-2 text-[11px] text-muted">
          spread {proposal.effect.spread} · speed{" "}
          {proposal.effect.params.speed_hz.toFixed(2)} Hz · channels{" "}
          {proposal.effect.target_channels.join("+")}
        </div>
      )}
    </div>
  );
}

function ProposalLightSwatch({
  pl,
  lightById,
}: {
  pl: DesignerProposalLight;
  lightById: Map<number, Light>;
}) {
  const hex = rgbToHex(pl.r, pl.g, pl.b);
  let opacity = 1;
  if (!pl.on) opacity = 0.2;
  else if (pl.dimmer < 40) opacity = 0.2;
  else opacity = pl.dimmer / 255;
  const light = lightById.get(pl.light_id);
  const name = light?.name ?? `#${pl.light_id}`;
  let title = `${name} · ${hex}`;
  if (!pl.on) title = `${name} · off`;
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md bg-bg-elev px-1.5 py-0.5 text-[10px] ring-1 ring-line"
      title={title}
    >
      <span
        className="h-3 w-3 rounded ring-1 ring-line"
        style={{ backgroundColor: hex, opacity }}
      />
      <span className="max-w-[80px] truncate">{name}</span>
    </span>
  );
}

function SaveModal({
  value,
  kind,
  onChange,
  onCancel,
  onSubmit,
}: {
  value: string;
  kind: DesignerProposal["kind"];
  onChange: (v: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  let blurb = "Saved as a rig-wide state on the Scenes page.";
  if (kind === "scene") {
    blurb = "Saved as a per-controller scene on the Scenes page.";
  } else if (kind === "palette") {
    blurb = "Saved to the Palettes page.";
  } else if (kind === "effect") {
    blurb = "Saved to the Effects page, ready to play.";
  }
  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="card flex w-full max-w-sm flex-col rounded-t-2xl sm:rounded-xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="font-semibold">Save proposal</div>
          <button className="btn-ghost -mr-2" onClick={onCancel}>
            Close
          </button>
        </div>
        <div className="space-y-3 px-5 py-4 text-sm">
          <p className="text-xs text-muted">{blurb}</p>
          <label className="block">
            <span className="label mb-1 block">Name</span>
            <input
              autoFocus
              className="input"
              value={value}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && value.trim()) {
                  e.preventDefault();
                  onSubmit();
                }
              }}
            />
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
          <button className="btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn-primary"
            disabled={!value.trim()}
            onClick={onSubmit}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
