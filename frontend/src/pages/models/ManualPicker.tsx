import React, { useEffect, useRef, useState } from "react";
import Modal from "../../components/Modal";
import { ManualState, formatBytes } from "./types";

/** Modal wrapper around the manual-upload drop zone.
 *
 * Used both for "Create from manual…" on the list page and
 * "Re-scan manual…" in the editor. The parent owns the upload state
 * (``ManualState``) so the same in-flight upload can survive navigation
 * or dialog re-mounts. */
export default function ManualPicker({
  open,
  state,
  title,
  onClose,
  onFile,
  onReset,
}: {
  open: boolean;
  state: ManualState;
  title: string;
  onClose: () => void;
  onFile: (file: File | null) => void;
  onReset: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    if (state.phase !== "uploading" && state.phase !== "processing") return;
    const id = setInterval(() => setNowTick(Date.now()), 250);
    return () => clearInterval(id);
  }, [state.phase]);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (state.phase !== "idle" && state.phase !== "error") return;
    const f = e.dataTransfer.files?.[0] ?? null;
    onFile(f);
  };

  const busy = state.phase === "uploading" || state.phase === "processing";

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      size="md"
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (state.phase === "idle" || state.phase === "error") {
            setDragging(true);
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={
          "flex flex-col items-stretch gap-3 rounded-lg border-2 border-dashed p-6 text-sm transition " +
          (dragging
            ? "border-accent bg-accent/10"
            : "border-line bg-bg-elev")
        }
      >
        {state.phase === "idle" && (
          <IdleBody
            onPick={() => inputRef.current?.click()}
            inputRef={inputRef}
            onFile={onFile}
          />
        )}

        {state.phase === "error" && (
          <ErrorBody
            message={state.message}
            onRetry={onReset}
            onPick={() => inputRef.current?.click()}
            inputRef={inputRef}
            onFile={onFile}
          />
        )}

        {(state.phase === "uploading" || state.phase === "processing") && (
          <BusyBody state={state} now={nowTick} />
        )}
      </div>
    </Modal>
  );
}

function IdleBody({
  onPick,
  inputRef,
  onFile,
}: {
  onPick: () => void;
  inputRef: React.MutableRefObject<HTMLInputElement | null>;
  onFile: (f: File | null) => void;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-4 text-center">
      <div className="text-slate-200">
        Drop a PDF or screenshot of the fixture manual here.
      </div>
      <div className="text-xs text-muted">
        PDF, PNG, JPG, or WEBP — up to 10 MB. Claude extracts the name and
        every channel mode.
      </div>
      <button type="button" className="btn-primary" onClick={onPick}>
        Choose file
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function ErrorBody({
  message,
  onRetry,
  onPick,
  inputRef,
  onFile,
}: {
  message: string;
  onRetry: () => void;
  onPick: () => void;
  inputRef: React.MutableRefObject<HTMLInputElement | null>;
  onFile: (f: File | null) => void;
}) {
  return (
    <div className="flex flex-col items-stretch gap-3 text-center">
      <div className="rounded-md bg-rose-950/60 p-3 text-left text-sm text-rose-200 ring-1 ring-rose-900">
        <div className="mb-1 font-semibold">Something went wrong</div>
        <div className="text-xs text-rose-200/80">{message}</div>
      </div>
      <div className="flex items-center justify-center gap-2">
        <button type="button" className="btn-ghost" onClick={onRetry}>
          Dismiss
        </button>
        <button type="button" className="btn-primary" onClick={onPick}>
          Try another file
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function BusyBody({
  state,
  now,
}: {
  state: Extract<ManualState, { phase: "uploading" | "processing" }>;
  now: number;
}) {
  const uploadPct = Math.round(
    Math.min(1, Math.max(0, state.percent ?? 0)) * 100,
  );
  const overallElapsedS = Math.max(0, (now - state.startedAt) / 1000);
  const processingS =
    state.phase === "processing"
      ? Math.max(0, (now - state.processingStartedAt) / 1000)
      : 0;

  return (
    <div className="flex flex-col gap-4 py-2">
      <div className="flex items-center gap-3">
        <FileIcon />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-slate-100">
            {state.file.name}
          </div>
          <div className="text-xs text-muted">
            {formatBytes(state.file.size)}
          </div>
        </div>
        <div className="text-xs text-muted tabular-nums">
          {overallElapsedS.toFixed(1)}s
        </div>
      </div>

      <Step
        active={state.phase === "uploading"}
        done={state.phase === "processing"}
        title={
          state.phase === "uploading"
            ? `Uploading… ${uploadPct}%`
            : "Upload complete"
        }
      >
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-card">
          <div
            className="h-full bg-accent transition-all duration-200"
            style={{
              width: state.phase === "uploading" ? `${uploadPct}%` : "100%",
            }}
          />
        </div>
      </Step>

      <Step
        active={state.phase === "processing"}
        done={false}
        pending={state.phase === "uploading"}
        title={
          state.phase === "processing"
            ? `Claude is reading the manual… (${processingS.toFixed(1)}s)`
            : "Claude will read the manual next"
        }
      >
        <IndeterminateBar active={state.phase === "processing"} />
      </Step>

      <div className="text-center text-[11px] text-muted">
        Large PDFs can take 10–60s. Please don't close this dialog.
      </div>
    </div>
  );
}

function Step({
  active,
  done,
  pending,
  title,
  children,
}: {
  active: boolean;
  done: boolean;
  pending?: boolean;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={
        "rounded-md p-2 ring-1 transition " +
        (active
          ? "bg-bg-card ring-accent/40"
          : done
            ? "bg-bg-card/60 ring-line"
            : "bg-bg-card/30 ring-line opacity-60")
      }
    >
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <StepGlyph active={active} done={done} pending={pending} />
        <span
          className={
            active
              ? "text-slate-100"
              : done
                ? "text-slate-300"
                : "text-muted"
          }
        >
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function StepGlyph({
  active,
  done,
  pending,
}: {
  active: boolean;
  done: boolean;
  pending?: boolean;
}) {
  if (done) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-accent text-[10px] text-white">
        ✓
      </span>
    );
  }
  if (active) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-bg-card border-t-accent" />
      </span>
    );
  }
  if (pending) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full ring-1 ring-line" />
    );
  }
  return (
    <span className="inline-flex h-4 w-4 items-center justify-center rounded-full ring-1 ring-line" />
  );
}

function IndeterminateBar({ active }: { active: boolean }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-card">
      {active ? (
        <div className="indeterminate-bar h-full rounded-full bg-accent/80" />
      ) : (
        <div className="h-full w-0" />
      )}
    </div>
  );
}

function FileIcon() {
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent/10 text-accent ring-1 ring-accent/30">
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
        <path d="M14 3v6h6" />
      </svg>
    </div>
  );
}
