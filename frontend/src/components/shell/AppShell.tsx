import React, { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useLocation } from "react-router-dom";
import { Api } from "../../api";
import { useAuth } from "../../auth";
import { useViewport } from "../../hooks/useViewport";
import { useLayerStore } from "../../state/layers";
import Modal from "../Modal";
import { useToast } from "../../toast";
import RestoreLook from "../RestoreLook";
import LiveLayersPanel from "../LiveLayersPanel";

type LinkDef = {
  to: string;
  label: string;
  end?: boolean;
  /** Tailwind class fragment used as a tiny inline icon glyph. */
  icon: string;
};

const OPERATE_LINKS: LinkDef[] = [
  { to: "/", label: "Now Playing", end: true, icon: "\u25CB" },
  { to: "/lights", label: "Lights", icon: "\u25EF" },
  { to: "/quick-fx", label: "FX", icon: "\u2734" },
  { to: "/scenes", label: "Scenes", icon: "\u25A2" },
];

const AUTHOR_LINKS: LinkDef[] = [
  { to: "/author/effects", label: "Effects Composer", icon: "\u25C9" },
  { to: "/author/palettes", label: "Palettes", icon: "\u25CF" },
  { to: "/author/scenes", label: "Scene Composer", icon: "\u25A3" },
];

const CONFIG_LINKS: LinkDef[] = [
  { to: "/config/controllers", label: "Controllers", icon: "\u25A1" },
  { to: "/config/models", label: "Models", icon: "\u25A6" },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { isMobile } = useViewport();
  if (isMobile) return <MobileShell>{children}</MobileShell>;
  return <DesktopShell>{children}</DesktopShell>;
}

// ---------------------------------------------------------------------------
// Desktop chrome
// ---------------------------------------------------------------------------
function DesktopShell({ children }: { children: React.ReactNode }) {
  const { logout, isAdmin, user, canOperate } = useAuth();
  const navigate = useNavigate();
  const aiEnabled = useDesignerEnabled();
  const location = useLocation();
  const composer =
    location.pathname === "/author/effects" ||
    location.pathname === "/author/scenes";
  return (
    <div
      className={
        "grid min-h-screen bg-bg " +
        (composer
          ? "grid-cols-[13rem_minmax(0,1fr)]"
          : "grid-cols-[13rem_minmax(0,1fr)] xl:grid-cols-[13rem_minmax(0,1fr)_19rem]")
      }
    >
      <aside className="flex flex-col border-r border-line bg-bg-elev/40">
        <div className="flex items-center gap-3 px-4 py-4">
          <Logo />
          <div>
            <div className="text-sm font-semibold leading-tight">
              DMX Control
            </div>
            <div className="text-[10px] uppercase tracking-wider text-muted">
              Stage Console
            </div>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pb-4">
          <NavGroup title="Operate" links={OPERATE_LINKS} />
          {canOperate && <NavGroup title="Author" links={AUTHOR_LINKS} />}
          {aiEnabled && canOperate && (
            <NavGroup
              title="AI"
              links={[
                { to: "/author/designer", label: "Designer", icon: "\u26EF" },
              ]}
            />
          )}
          {isAdmin && <NavGroup title="Configure" links={CONFIG_LINKS} />}
          <NavGroup
            title="Account"
            links={[{ to: "/me", label: user?.name || "Account", icon: "○" }]}
          />
          <NavGroup
            title="Reference"
            links={[{ to: "/api-docs", label: "API", icon: "\u2630" }]}
          />
        </nav>
        <div className="border-t border-line px-3 py-3">
          <button
            className="btn-ghost w-full justify-start text-xs"
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
          >
            Log out
          </button>
        </div>
      </aside>
      <main className="min-w-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1600px] px-4 py-5">
          {children}
        </div>
      </main>
      {!composer && (
        <aside className="hidden border-l border-line bg-bg-elev/40 xl:block">
          <div className="flex h-full flex-col p-3">
            <PanicBar />
            <div className="mt-3 flex-1 min-h-0">
              <LiveLayersPanel variant="full" />
            </div>
          </div>
        </aside>
      )}
    </div>
  );
}

function NavGroup({ title, links }: { title: string; links: LinkDef[] }) {
  return (
    <div className="mb-4">
      <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-widest text-muted">
        {title}
      </div>
      <ul className="flex flex-col gap-0.5">
        {links.map((l) => (
          <li key={l.to}>
            <NavLink
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm " +
                (isActive
                  ? "bg-bg-card text-white ring-1 ring-line"
                  : "text-slate-300 hover:bg-bg-elev hover:text-white")
              }
            >
              <span aria-hidden className="text-xs text-muted">
                {l.icon}
              </span>
              {l.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mobile chrome
// ---------------------------------------------------------------------------
const MOBILE_TABS: LinkDef[] = [
  { to: "/", label: "Now", end: true, icon: "\u25CB" },
  { to: "/lights", label: "Lights", icon: "\u25EF" },
  { to: "/quick-fx", label: "FX", icon: "\u2734" },
  { to: "/scenes", label: "Scenes", icon: "\u25A2" },
  { to: "/me", label: "Me", icon: "\u25CD" },
];

function MobileShell({ children }: { children: React.ReactNode }) {
  const { connected } = useLayerStore();
  return (
    <div className="flex min-h-[100dvh] flex-col bg-bg">
      <header className="sticky top-0 z-30 border-b border-line bg-bg/95 backdrop-blur">
        <div className="flex items-center justify-between px-4 py-2">
          <div className="flex items-center gap-2">
            <Logo small />
            <div className="hidden min-[375px]:block text-sm font-semibold">
              DMX
            </div>
          </div>
          <PanicBar compact />
        </div>
      </header>
      {!connected && (
        <div
          role="status"
          className="bg-amber-950/60 px-3 py-2 text-xs text-amber-100"
        >
          Reconnecting… Displaying the last known look.
        </div>
      )}
      <main className="phone-main min-w-0 flex-1 px-3 pt-3">{children}</main>
      <nav className="phone-nav fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/95 backdrop-blur">
        <div className="grid grid-cols-5">
          {MOBILE_TABS.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.end}
              className={({ isActive }) =>
                "flex flex-col items-center justify-center gap-0.5 py-2 text-[10px] " +
                (isActive ? "text-white" : "text-muted")
              }
            >
              <span aria-hidden className="text-base">
                {t.icon}
              </span>
              {t.label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panic / blackout bar (used in both shells)
// ---------------------------------------------------------------------------
function PanicBar({ compact = false }: { compact?: boolean }) {
  const { layers, clearAll, refresh } = useLayerStore();
  const { canOperate } = useAuth();
  const { push } = useToast();
  const [action, setAction] = useState<"clear" | "blackout" | null>(null);
  const [busy, setBusy] = useState(false);
  if (!canOperate) return <span className="pill">View only</span>;
  return (
    <>
      <div
        className={
          compact ? "flex items-center gap-1.5" : "flex flex-wrap gap-2"
        }
      >
        <RestoreLook />
        <button
          className={
            compact ? "btn-secondary px-2 text-xs" : "btn-secondary flex-1"
          }
          disabled={busy || !layers.length}
          onClick={() => setAction("clear")}
        >
          Stop FX
        </button>
        <button
          className={compact ? "btn-danger px-2 text-xs" : "btn-danger flex-1"}
          disabled={busy}
          onClick={() => setAction("blackout")}
        >
          Blackout
        </button>
      </div>
      <Modal
        open={!!action}
        title={
          action === "blackout"
            ? "Black out the whole rig?"
            : "Stop all running effects?"
        }
        onClose={() => {
          if (!busy) setAction(null);
        }}
        footer={
          <>
            <button
              className="btn-secondary"
              disabled={busy}
              onClick={() => setAction(null)}
            >
              Cancel
            </button>
            <button
              className="btn-danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  if (action === "blackout") await Api.applyBlackoutState();
                  else await clearAll();
                  await refresh();
                  setAction(null);
                  push(
                    action === "blackout"
                      ? "Rig blacked out. Restore brings back the previous look."
                      : "Effects stopped",
                    "success",
                  );
                } catch (e) {
                  push(String(e), "error");
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy
                ? "Applying…"
                : action === "blackout"
                  ? "Blackout whole rig"
                  : "Stop all effects"}
            </button>
          </>
        }
      >
        <p className="text-sm text-slate-200">
          {action === "blackout"
            ? "This turns off every configured fixture and stops its effects. Use Restore to bring back the previous look."
            : `This stops ${layers.length} running effect layers and leaves base colors in place. Use Restore to undo.`}
        </p>
      </Modal>
    </>
  );
}

// ---------------------------------------------------------------------------
// Logo + Designer probe
// ---------------------------------------------------------------------------
function Logo({ small = false }: { small?: boolean }) {
  const cls = small ? "h-7 w-7" : "h-9 w-9";
  return (
    <div
      className={
        "grid " +
        cls +
        " grid-cols-2 grid-rows-2 gap-0.5 overflow-hidden rounded-lg"
      }
    >
      <span className="bg-[#7c4dff]" />
      <span className="bg-[#00e5ff]" />
      <span className="bg-[#2ef9b6]" />
      <span className="bg-[#ffb36b]" />
    </div>
  );
}

function useDesignerEnabled(): boolean {
  const [enabled, setEnabled] = useState(false);
  const { authenticated } = useAuth();
  useEffect(() => {
    if (!authenticated) return;
    Api.designer
      .status()
      .then((s) => setEnabled(!!s.enabled))
      .catch(() => setEnabled(false));
  }, [authenticated]);
  return enabled;
}
