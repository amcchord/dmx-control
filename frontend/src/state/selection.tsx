import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

/** Cross-page light selection store.
 *
 * Selection used to live inside ``Dashboard.tsx`` and was lost whenever
 * the user navigated away. Lifting it into a small context lets the
 * mobile Lights tab, desktop Effects Composer, and Scene Composer all
 * share one set of "currently selected" fixtures. Persistence is
 * deliberately session-scoped: refreshing the tab clears it. */
export type Selection = {
  lightIds: Set<number>;
  /** Sticky controller filter (when the user drilled into a controller
   * view). ``null`` means "show all controllers". */
  controllerId: number | null;
};

export type SelectionStoreValue = {
  selection: Selection;
  toggle: (id: number) => void;
  setMany: (ids: number[]) => void;
  clear: () => void;
  hasSelection: boolean;
  size: number;
  setControllerFilter: (id: number | null) => void;
};

const Ctx = createContext<SelectionStoreValue | null>(null);

export function SelectionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [selection, setSelection] = useState<Selection>({
    lightIds: new Set(),
    controllerId: null,
  });

  const toggle = useCallback((id: number) => {
    setSelection((prev) => {
      const next = new Set(prev.lightIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...prev, lightIds: next };
    });
  }, []);

  const setMany = useCallback((ids: number[]) => {
    setSelection((prev) => ({
      ...prev,
      lightIds: new Set(ids),
    }));
  }, []);

  const clear = useCallback(() => {
    setSelection((prev) => ({ ...prev, lightIds: new Set() }));
  }, []);

  const setControllerFilter = useCallback((id: number | null) => {
    setSelection((prev) => ({ ...prev, controllerId: id }));
  }, []);

  const value = useMemo<SelectionStoreValue>(
    () => ({
      selection,
      toggle,
      setMany,
      clear,
      hasSelection: selection.lightIds.size > 0,
      size: selection.lightIds.size,
      setControllerFilter,
    }),
    [selection, toggle, setMany, clear, setControllerFilter],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSelection(): SelectionStoreValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSelection: missing SelectionProvider");
  return v;
}
