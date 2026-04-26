import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth";
import { useLayerStore } from "../../state/layers";

/** Mobile "Me" tab: server status, links to authoring on desktop, log
 *  out. Keeps mobile shell clean by parking everything that's not
 *  hot-path control here. */
export default function Me() {
  const navigate = useNavigate();
  const { logout } = useAuth();
  const { health, connected } = useLayerStore();

  return (
    <div className="flex flex-col gap-3">
      <section className="card p-4">
        <div className="text-xs uppercase tracking-widest text-muted">
          Engine
        </div>
        <div className="mt-1 flex items-center gap-2 text-sm font-semibold">
          <span
            className={
              "inline-block h-2 w-2 rounded-full " +
              (connected ? "bg-emerald-400" : "bg-amber-400")
            }
          />
          {connected ? "Connected" : "Reconnecting..."}
        </div>
        {health && (
          <dl className="mt-3 grid grid-cols-2 gap-y-1 text-[11px]">
            <dt className="text-muted">Tick rate</dt>
            <dd className="text-right font-mono">
              {health.tick_hz.toFixed(0)} Hz
            </dd>
            <dt className="text-muted">Last tick</dt>
            <dd className="text-right font-mono">
              {health.last_tick_ms.toFixed(2)} ms
            </dd>
            <dt className="text-muted">Active layers</dt>
            <dd className="text-right font-mono">{health.active_layers}</dd>
            <dt className="text-muted">Dropped frames</dt>
            <dd className="text-right font-mono">{health.dropped_frames}</dd>
            <dt className="text-muted">Total ticks</dt>
            <dd className="text-right font-mono">
              {health.tick_count.toLocaleString()}
            </dd>
          </dl>
        )}
      </section>

      <section className="card p-4">
        <div className="text-xs uppercase tracking-widest text-muted">
          Authoring
        </div>
        <p className="mt-1 text-xs text-muted">
          Lua editing, palette authoring, scene composing, and AI design
          live on the desktop. Open the same URL from a desktop browser
          to jump in.
        </p>
        <div className="mt-3 flex flex-col gap-1.5 text-sm">
          <Link to="/author/effects" className="btn-secondary justify-start">
            Effects Composer
          </Link>
          <Link to="/author/palettes" className="btn-secondary justify-start">
            Palettes
          </Link>
          <Link to="/author/scenes" className="btn-secondary justify-start">
            Scene Composer
          </Link>
          <Link to="/api-docs" className="btn-ghost justify-start">
            API reference
          </Link>
        </div>
      </section>

      <section className="card p-4">
        <div className="text-xs uppercase tracking-widest text-muted">
          Configure
        </div>
        <div className="mt-2 flex flex-col gap-1.5 text-sm">
          <Link
            to="/config/controllers"
            className="btn-secondary justify-start"
          >
            Controllers
          </Link>
          <Link to="/config/models" className="btn-secondary justify-start">
            Light models
          </Link>
        </div>
      </section>

      <button
        className="btn-ghost"
        onClick={async () => {
          await logout();
          navigate("/login");
        }}
      >
        Log out
      </button>
    </div>
  );
}
