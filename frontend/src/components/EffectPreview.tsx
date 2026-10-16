import React, { useEffect, useRef, useState } from "react";
import { Effect, EffectParams, PreviewFrame } from "../api";
export default function EffectPreview({
  effect,
  params,
  palette,
  intensity = 1,
}: {
  effect: Effect;
  params: EffectParams;
  palette: string[];
  intensity?: number;
}) {
  const socket = useRef<WebSocket | null>(null);
  const [frame, setFrame] = useState<PreviewFrame | null>(null);
  const [error, setError] = useState("");
  const data = {
    source: effect.source,
    params,
    palette,
    cells: 12,
    spread: effect.spread,
    target_channels: effect.target_channels,
    intensity,
  };
  const latest = useRef(data);
  latest.current = data;
  useEffect(() => {
    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/effects/preview/ws`,
    );
    socket.current = ws;
    setError("");
    setFrame(null);
    ws.onopen = () => ws.send(JSON.stringify(latest.current));
    ws.onmessage = (e) => {
      try {
        const f = JSON.parse(e.data);
        if (f.error) setError(f.error.message || "Preview failed");
        else {
          setFrame(f);
          setError("");
        }
      } catch {}
    };
    ws.onerror = () =>
      setError("Preview unavailable. Reopen this panel to retry.");
    return () => {
      socket.current = null;
      ws.close();
    };
  }, [effect.id]);
  useEffect(() => {
    if (socket.current?.readyState === WebSocket.OPEN)
      socket.current.send(JSON.stringify({ patch: data }));
  }, [
    effect.source,
    JSON.stringify(params),
    JSON.stringify(palette),
    intensity,
  ]);
  return (
    <section className="rounded-xl bg-bg-elev p-3 ring-1 ring-line">
      <div className="mb-2 text-xs font-semibold text-violet-200">
        PREVIEW ONLY · no hardware output
      </div>
      {error ? (
        <p role="alert" className="text-sm text-rose-200">
          {error}
        </p>
      ) : !frame ? (
        <p className="text-sm text-muted">Starting preview…</p>
      ) : (
        frame.strips?.map((s) => (
          <div key={s.target} className="mt-2">
            <div className="mb-1 text-xs uppercase text-muted">{s.target}</div>
            <div className="grid grid-cols-12 gap-1">
              {s.cells.map((c, i) => (
                <div
                  key={i}
                  className="aspect-square rounded"
                  style={{
                    backgroundColor: c.active
                      ? `rgb(${c.r ?? 255},${c.g ?? 255},${c.b ?? 255})`
                      : "#151822",
                    opacity: c.active ? Math.max(0.08, c.brightness ?? 1) : 1,
                  }}
                />
              ))}
            </div>
          </div>
        ))
      )}
    </section>
  );
}
