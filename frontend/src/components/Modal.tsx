import { createPortal } from "react-dom";
import React, { useEffect, useId, useRef } from "react";
type Props = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg";
};
export default function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = "md",
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const selector =
      'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
    const first =
      ref.current?.querySelector<HTMLElement>(
        "input:not(:disabled), select:not(:disabled), textarea:not(:disabled)",
      ) || ref.current?.querySelector<HTMLElement>(selector);
    first?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
      if (e.key !== "Tab") return;
      const list = [
        ...(ref.current?.querySelectorAll<HTMLElement>(selector) || []),
      ].filter((x) => x.getClientRects().length);
      if (!list.length) return;
      if (e.shiftKey && document.activeElement === list[0]) {
        e.preventDefault();
        list[list.length - 1].focus();
      } else if (
        !e.shiftKey &&
        document.activeElement === list[list.length - 1]
      ) {
        e.preventDefault();
        list[0].focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [open]);
  if (!open) return null;
  const max =
    size === "lg" ? "max-w-2xl" : size === "sm" ? "max-w-sm" : "max-w-md";
  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        className={
          "card flex w-full max-h-[92dvh] flex-col rounded-t-2xl sm:rounded-xl " +
          max
        }
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2">
          <h2 id={id} className="min-w-0 break-words font-semibold">
            {title}
          </h2>
          <button onClick={onClose} className="btn-ghost">
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4">
          {children}
        </div>
        {footer && (
          <div
            className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-line px-4 py-3"
            style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
