import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Api,
  BaseStateChange,
  EffectLayer,
  EngineHealth,
  LayerPatchInput,
} from "../api";
import { useAuth } from "../auth";

/** Single source of truth for "what's on stage right now."
 *
 * The store opens one WebSocket to ``/api/layers/ws`` and pushes every
 * layer mutation through optimistic-then-reconciled patches. Every
 * surface that shows live layer state — mobile Now Playing pill,
 * desktop Live rail, the Effects Composer layer list — subscribes to
 * the same store so they never disagree.
 *
 * The same WS also carries ``base_state`` frames (recent manual color /
 * scene / state / palette / blackout applies). Surfacing those next to
 * the running layer stack is what lets operators answer "why is this
 * light red?" when nothing is running. */
export type LayerStoreValue = {
  layers: EffectLayer[];
  health: EngineHealth | null;
  connected: boolean;
  /** Recent base-state changes (manual color, scene/state/palette
   *  apply, blackout). Newest first; capped server-side. */
  baseStateLog: BaseStateChange[];
  refresh: () => Promise<void>;
  patchLayer: (layerId: number, patch: LayerPatchInput) => Promise<void>;
  removeLayer: (layerId: number) => Promise<void>;
  reorder: (
    order: { layer_id: number; z_index: number }[],
  ) => Promise<void>;
  clearAll: () => Promise<void>;
};

const Ctx = createContext<LayerStoreValue | null>(null);

const RECONNECT_BASE_MS = 750;
const RECONNECT_MAX_MS = 8000;

export function LayerStoreProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { authenticated } = useAuth();
  const [layers, setLayers] = useState<EffectLayer[]>([]);
  const [health, setHealth] = useState<EngineHealth | null>(null);
  const [connected, setConnected] = useState(false);
  const [baseStateLog, setBaseStateLog] = useState<BaseStateChange[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const stopRef = useRef(false);
  const attemptRef = useRef(0);

  const ingest = useCallback((msg: unknown) => {
    if (!msg || typeof msg !== "object") return;
    const typed = msg as {
      type?: string;
      layers?: EffectLayer[];
      health?: EngineHealth;
      log?: BaseStateChange[];
    };
    if (typed.type === "layers" && Array.isArray(typed.layers)) {
      setLayers([...typed.layers].sort(byZ));
    }
    if (typed.type === "base_state" && Array.isArray(typed.log)) {
      setBaseStateLog([...typed.log]);
    }
    if (typed.health) {
      setHealth(typed.health);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [layerList, h, log] = await Promise.all([
        Api.listLayers(),
        Api.health().catch(() => null),
        Api.getBaseStateLog().catch(() => null),
      ]);
      setLayers([...layerList].sort(byZ));
      if (h) setHealth(h);
      if (log) setBaseStateLog(log);
    } catch {
      // ignored — websocket will retry independently.
    }
  }, []);

  useEffect(() => {
    if (!authenticated) {
      setLayers([]);
      setHealth(null);
      setBaseStateLog([]);
      setConnected(false);
      return;
    }
    stopRef.current = false;
    attemptRef.current = 0;
    let timer: number | null = null;

    const connect = () => {
      if (stopRef.current) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${window.location.host}/api/layers/ws`;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;
      ws.onopen = () => {
        attemptRef.current = 0;
        setConnected(true);
        // Always re-pull on connect so we resync after a network blip.
        void refresh();
      };
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          ingest(data);
        } catch {
          // Ignore non-JSON frames.
        }
      };
      ws.onerror = () => {
        // ``onclose`` will fire next; reconnect is centralized there.
      };
      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (stopRef.current) return;
      attemptRef.current += 1;
      const delay = Math.min(
        RECONNECT_MAX_MS,
        RECONNECT_BASE_MS * 2 ** Math.max(0, attemptRef.current - 1),
      );
      timer = window.setTimeout(connect, delay);
    };

    connect();
    void refresh();
    return () => {
      stopRef.current = true;
      if (timer !== null) window.clearTimeout(timer);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        try {
          ws.close();
        } catch {
          // ignored
        }
      }
    };
  }, [authenticated, ingest, refresh]);

  const patchLayer = useCallback(
    async (layerId: number, patch: LayerPatchInput) => {
      // Optimistic local update so sliders feel instant.
      setLayers((prev) =>
        prev
          .map((l) =>
            l.layer_id === layerId
              ? ({ ...l, ...patch } as EffectLayer)
              : l,
          )
          .sort(byZ),
      );
      try {
        await Api.patchLayer(layerId, patch);
      } catch (err) {
        // Reconcile by re-fetching authoritative state.
        await refresh();
        throw err;
      }
    },
    [refresh],
  );

  const removeLayer = useCallback(
    async (layerId: number) => {
      setLayers((prev) =>
        prev.map((l) =>
          l.layer_id === layerId ? { ...l, stopping: true } : l,
        ),
      );
      try {
        await Api.deleteLayer(layerId);
      } finally {
        await refresh();
      }
    },
    [refresh],
  );

  const reorder = useCallback(
    async (order: { layer_id: number; z_index: number }[]) => {
      const byId = new Map(order.map((o) => [o.layer_id, o.z_index]));
      setLayers((prev) =>
        prev
          .map((l) => {
            if (l.layer_id == null) return l;
            const z = byId.get(l.layer_id);
            return z != null ? { ...l, z_index: z } : l;
          })
          .sort(byZ),
      );
      try {
        await Api.reorderLayers(order);
      } catch {
        await refresh();
      }
    },
    [refresh],
  );

  const clearAll = useCallback(async () => {
    setLayers((prev) => prev.map((l) => ({ ...l, stopping: true })));
    try {
      await Api.clearLayers();
    } finally {
      await refresh();
    }
  }, [refresh]);

  const value = useMemo<LayerStoreValue>(
    () => ({
      layers,
      health,
      connected,
      baseStateLog,
      refresh,
      patchLayer,
      removeLayer,
      reorder,
      clearAll,
    }),
    [
      layers,
      health,
      connected,
      baseStateLog,
      refresh,
      patchLayer,
      removeLayer,
      reorder,
      clearAll,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLayerStore(): LayerStoreValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLayerStore: missing LayerStoreProvider");
  return v;
}

function byZ(a: EffectLayer, b: EffectLayer): number {
  if (a.z_index !== b.z_index) return a.z_index - b.z_index;
  const ai = a.layer_id ?? 0;
  const bi = b.layer_id ?? 0;
  return ai - bi;
}
