import type { ChannelPolicy, ColorPolicy, PolicyRole } from "../../api";
import {
  EXTRA_POLICY_ROLES,
  POLICY_ROLES,
  POLICY_ROLE_HELP,
  POLICY_ROLE_LABEL,
  ROLE_COLORS,
  policyFor,
  type ModeDraft,
} from "./types";

const EXTRA_SET = new Set<PolicyRole>(EXTRA_POLICY_ROLES);

/** Per-mode "color behavior" editor.
 *
 * Lists every W/A/UV role that actually appears in the active mode's
 * channel list with a Mix / Direct toggle. Mix (default) keeps the
 * historical behavior where the renderer derives W/A/UV from RGB if
 * unspecified; Direct exposes the channel as an independent fader that
 * the Dashboard, palette paints and effect blends leave alone. */
export default function ColorPolicySection({
  draft,
  onChange,
}: {
  draft: ModeDraft;
  onChange: (next: ColorPolicy) => void;
}) {
  const presentRoles = POLICY_ROLES.filter((r) => draft.channels.includes(r));

  const setRole = (role: PolicyRole, value: ChannelPolicy) => {
    const next: ColorPolicy = { ...draft.color_policy };
    if (value === "mix") {
      delete next[role];
    } else {
      next[role] = value;
    }
    onChange(next);
  };

  return (
    <div className="rounded-lg bg-bg-elev p-3 ring-1 ring-line">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Color behavior</div>
          <div className="text-xs text-muted">
            How auxiliary channels (W / A / UV and any secondary W2 / W3 / A2
            / UV2) relate to the RGB color mix.
          </div>
        </div>
      </div>
      {presentRoles.length === 0 ? (
        <div className="rounded-md bg-bg-card px-3 py-2 text-xs text-muted ring-1 ring-line">
          This mode has no auxiliary color channels — nothing to configure.
          Add a W / A / UV role (or a secondary W2 / W3 / A2 / UV2) to the
          channel list above to unlock these options.
        </div>
      ) : (
        <div className="space-y-2">
          {presentRoles.map((role) => {
            const current = policyFor(draft, role);
            const isExtra = EXTRA_SET.has(role);
            return (
              <div
                key={role}
                className="rounded-md bg-bg-card p-2 ring-1 ring-line"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className="h-3 w-3 rounded-full"
                    style={{ background: ROLE_COLORS[role] ?? "#8791a7" }}
                  />
                  <span className="text-sm font-medium">
                    {POLICY_ROLE_LABEL[role]}
                  </span>
                  {isExtra ? (
                    <span
                      className="ml-auto rounded-full bg-accent/20 px-2.5 py-0.5 text-xs text-accent ring-1 ring-accent/40"
                      title="Secondary aux channels are always driven directly — no mixing from RGB."
                    >
                      Direct (always)
                    </span>
                  ) : (
                    <span className="ml-auto inline-flex rounded-full bg-bg-elev p-0.5 text-xs ring-1 ring-line">
                      <PolicyPill
                        active={current === "mix"}
                        onClick={() => setRole(role, "mix")}
                      >
                        Mix from RGB
                      </PolicyPill>
                      <PolicyPill
                        active={current === "direct"}
                        onClick={() => setRole(role, "direct")}
                      >
                        Direct channel
                      </PolicyPill>
                    </span>
                  )}
                </div>
                <div className="mt-1 text-[11px] text-muted">
                  {POLICY_ROLE_HELP[role]}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PolicyPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={
        "rounded-full px-2.5 py-0.5 transition " +
        (active
          ? "bg-accent text-white"
          : "text-slate-300 hover:bg-bg-card")
      }
      onClick={onClick}
    >
      {children}
    </button>
  );
}
