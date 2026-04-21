import React, { createContext, useCallback, useContext, useState } from "react";

type ToastKind = "info" | "success" | "error";
type Toast = { id: number; kind: ToastKind; message: string };
type ToastCtx = { push: (msg: string, kind?: ToastKind) => void };

const Ctx = createContext<ToastCtx | null>(null);

export function useToast() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside provider");
  return v;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, kind: ToastKind = "info") => {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts, { id, kind, message }]);
    setTimeout(() => {
      setToasts((ts) => ts.filter((t) => t.id !== id));
    }, 3200);
  }, []);

  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 sm:inset-auto sm:bottom-6 sm:right-6 sm:items-end">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={
              "pointer-events-auto w-full max-w-sm rounded-lg px-4 py-3 text-sm shadow-lg ring-1 backdrop-blur " +
              (t.kind === "error"
                ? "bg-rose-600/90 text-white ring-rose-400"
                : t.kind === "success"
                  ? "bg-emerald-600/90 text-white ring-emerald-400"
                  : "bg-bg-card/90 text-slate-100 ring-line")
            }
          >
            {t.message}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
