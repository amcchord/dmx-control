import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/** Tiny typed undo/redo ring buffer for layer ops + scene edits.
 *
 * Each entry knows how to undo itself (a function returning a Promise);
 * the store does not snapshot full state, so callers are responsible
 * for capturing the pre/post state inside their action closures. We
 * also persist the most recent ``HORIZON`` entries to localStorage so
 * a stray reload doesn't lose the trail. */
type UndoAction = {
  label: string;
  undo: () => Promise<void> | void;
  redo: () => Promise<void> | void;
};

const HORIZON = 32;
const STORAGE_KEY = "dmx-control:undo-labels";

type UndoCtxValue = {
  labels: { back: string[]; forward: string[] };
  push: (action: UndoAction) => void;
  undo: () => Promise<boolean>;
  redo: () => Promise<boolean>;
};

const Ctx = createContext<UndoCtxValue | null>(null);

export function UndoProvider({ children }: { children: React.ReactNode }) {
  const back = useRef<UndoAction[]>([]);
  const forward = useRef<UndoAction[]>([]);
  const [labels, setLabels] = useState<{
    back: string[];
    forward: string[];
  }>(() => loadStored());

  const sync = useCallback(() => {
    const b = back.current.map((a) => a.label);
    const f = forward.current.map((a) => a.label);
    setLabels({ back: b, forward: f });
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ back: b, forward: f }),
      );
    } catch {
      // Quota errors and private-mode browsers silently drop the trail.
    }
  }, []);

  const push = useCallback(
    (action: UndoAction) => {
      back.current.push(action);
      while (back.current.length > HORIZON) back.current.shift();
      forward.current = [];
      sync();
    },
    [sync],
  );

  const undo = useCallback(async () => {
    const a = back.current.pop();
    if (!a) {
      sync();
      return false;
    }
    try {
      await a.undo();
      forward.current.push(a);
      while (forward.current.length > HORIZON) forward.current.shift();
    } catch {
      // Best-effort: drop the action so a stuck state doesn't trap the
      // user in an infinite undo loop.
    }
    sync();
    return true;
  }, [sync]);

  const redo = useCallback(async () => {
    const a = forward.current.pop();
    if (!a) {
      sync();
      return false;
    }
    try {
      await a.redo();
      back.current.push(a);
    } catch {
      // ignored
    }
    sync();
    return true;
  }, [sync]);

  // Global keyboard shortcuts so any focused page can undo/redo.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      const isMeta = e.metaKey || e.ctrlKey;
      if (!isMeta) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        void undo();
      } else if (key === "z" && e.shiftKey) {
        e.preventDefault();
        void redo();
      } else if (key === "y") {
        e.preventDefault();
        void redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const value = useMemo<UndoCtxValue>(
    () => ({ labels, push, undo, redo }),
    [labels, push, undo, redo],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useUndo(): UndoCtxValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useUndo: missing UndoProvider");
  return v;
}

function loadStored(): { back: string[]; forward: string[] } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { back: [], forward: [] };
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      Array.isArray(parsed.back) &&
      Array.isArray(parsed.forward)
    ) {
      return {
        back: parsed.back.map(String),
        forward: parsed.forward.map(String),
      };
    }
  } catch {
    // ignored
  }
  return { back: [], forward: [] };
}
