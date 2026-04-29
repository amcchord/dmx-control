import React from "react";
import {
  DesignerCritique,
  DesignerCritiqueRisk,
  DesignerCritiqueVerdict,
} from "../api";

type Props = {
  /** ``null`` while the verifier request is in flight; shows a spinner. */
  critique: DesignerCritique | null;
  /** Set when the verifier call failed; replaces the body with an
   *  inline retry hint. */
  error?: string | null;
  /** Whether the auto-verifier is enabled in the operator's settings. */
  enabled?: boolean;
  /** Trigger a (re)verification. */
  onVerify?: () => void;
  /** Optional regenerate shortcut surfaced when verdict === "regenerate"
   *  or the user clicks the "Try again" button. */
  onRegenerate?: (suggestion?: string) => void;
};

const VERDICT_LABEL: Record<DesignerCritiqueVerdict, string> = {
  looks_good: "Looks good",
  minor_issues: "Minor issues",
  needs_review: "Needs review",
  regenerate: "Regenerate",
};

const VERDICT_TONE: Record<DesignerCritiqueVerdict, string> = {
  looks_good:
    "bg-emerald-950/30 ring-emerald-800 text-emerald-200",
  minor_issues:
    "bg-amber-950/30 ring-amber-800 text-amber-200",
  needs_review:
    "bg-orange-950/30 ring-orange-800 text-orange-200",
  regenerate:
    "bg-rose-950/30 ring-rose-800 text-rose-200",
};

const SEVERITY_TONE: Record<DesignerCritiqueRisk["severity"], string> = {
  low: "bg-slate-800 text-slate-200 ring-slate-700",
  med: "bg-amber-900/50 text-amber-100 ring-amber-700",
  high: "bg-rose-900/60 text-rose-100 ring-rose-700",
};

/** Inline self-critique panel rendered under each AI proposal.
 *
 * Displays a verdict pill, the verifier's restated intent, a
 * checklist-style coverage breakdown, any risks flagged, and short
 * suggestions. The token usage footer helps operators see how much
 * cost the auto-verify pass is adding. */
export default function VerificationPanel({
  critique,
  error,
  enabled = true,
  onVerify,
  onRegenerate,
}: Props) {
  if (!enabled) {
    return (
      <div className="mt-2 flex items-center justify-between rounded-md border border-dashed border-line bg-bg-card/40 px-2.5 py-1.5 text-[11px] text-muted">
        <span>Auto-verify is off.</span>
        {onVerify && (
          <button
            className="btn-ghost !px-2 !py-0.5 text-[10px]"
            onClick={onVerify}
          >
            Verify
          </button>
        )}
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-2 rounded-md border border-rose-800 bg-rose-950/40 px-2.5 py-1.5 text-[11px] text-rose-200">
        <div className="flex items-center justify-between gap-2">
          <span>Verifier failed: {error}</span>
          {onVerify && (
            <button
              className="btn-ghost !px-2 !py-0.5 text-[10px]"
              onClick={onVerify}
            >
              Retry
            </button>
          )}
        </div>
      </div>
    );
  }

  if (critique === null) {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-md border border-dashed border-line bg-bg-card/40 px-2.5 py-1.5 text-[11px] text-muted">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-bg-card border-t-accent" />
        <span>Double-checking against your request…</span>
      </div>
    );
  }

  const verdictTone = VERDICT_TONE[critique.verdict];
  const verdictLabel = VERDICT_LABEL[critique.verdict];
  const usage = critique.usage;
  const totalTok =
    (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0);

  return (
    <div className="mt-2 rounded-md border border-line bg-bg-card/60 p-2.5 text-[11px] text-slate-200">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ring-1 " +
            verdictTone
          }
        >
          {verdictLabel}
        </span>
        <span className="font-mono text-[10px] text-muted">
          confidence {(critique.confidence * 100).toFixed(0)}%
        </span>
        {onVerify && (
          <button
            className="ml-auto btn-ghost !px-2 !py-0.5 text-[10px]"
            onClick={onVerify}
            title="Re-run the verifier"
          >
            Re-verify
          </button>
        )}
      </div>

      {critique.intent_summary && (
        <div className="mt-1.5 italic text-slate-300">
          {critique.intent_summary}
        </div>
      )}

      {critique.coverage.length > 0 && (
        <ul className="mt-2 space-y-1">
          {critique.coverage.map((c, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[11px]">
              <span
                className={
                  "mt-[1px] inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ring-1 " +
                  (c.addressed
                    ? "bg-emerald-900/50 text-emerald-200 ring-emerald-700"
                    : "bg-rose-900/40 text-rose-200 ring-rose-700")
                }
                aria-label={c.addressed ? "addressed" : "not addressed"}
              >
                {c.addressed ? "\u2713" : "\u00D7"}
              </span>
              <span className="min-w-0">
                <span className="text-slate-100">{c.requirement}</span>
                {c.evidence && (
                  <span className="ml-1.5 text-muted">— {c.evidence}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {critique.risks.length > 0 && (
        <div className="mt-2 space-y-1">
          {critique.risks.map((r, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5"
            >
              <span
                className={
                  "rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wider ring-1 " +
                  SEVERITY_TONE[r.severity]
                }
              >
                {r.severity}
              </span>
              <span className="min-w-0 text-slate-200">{r.issue}</span>
            </div>
          ))}
        </div>
      )}

      {critique.suggestions.length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] uppercase tracking-wider text-muted">
            Suggestions
          </div>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-slate-200">
            {critique.suggestions.map((s, i) => (
              <li key={i} className="flex items-start justify-between gap-2">
                <span className="min-w-0">{s}</span>
                {onRegenerate && (
                  <button
                    className="shrink-0 btn-ghost !px-1.5 !py-0 text-[10px]"
                    onClick={() => onRegenerate(s)}
                    title="Ask Claude to revise with this suggestion"
                  >
                    apply
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {critique.verdict === "regenerate" && onRegenerate && (
        <div className="mt-2 flex justify-end">
          <button
            className="btn-secondary !px-2 !py-0.5 text-[10px]"
            onClick={() => onRegenerate()}
          >
            Try again
          </button>
        </div>
      )}

      {totalTok > 0 && (
        <div className="mt-2 border-t border-line pt-1 text-right font-mono text-[9px] text-muted">
          verified · {totalTok.toLocaleString()} tok
        </div>
      )}
    </div>
  );
}
