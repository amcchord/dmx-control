import React, { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

const links = [
  { to: "/", label: "Lights", end: true },
  { to: "/controllers", label: "Controllers" },
  { to: "/scenes", label: "Scenes" },
  { to: "/models", label: "Models" },
  { to: "/palettes", label: "Palettes" },
];

export default function Nav() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const onLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 grid-cols-2 grid-rows-2 gap-0.5 overflow-hidden rounded-lg">
            <span className="bg-[#7c4dff]" />
            <span className="bg-[#00e5ff]" />
            <span className="bg-[#2ef9b6]" />
            <span className="bg-[#ffb36b]" />
          </div>
          <div className="text-sm font-semibold tracking-wide">DMX Control</div>
        </div>
        <nav className="hidden gap-1 sm:flex">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                "rounded-lg px-3 py-1.5 text-sm " +
                (isActive
                  ? "bg-bg-elev text-white ring-1 ring-line"
                  : "text-slate-300 hover:bg-bg-elev hover:text-white")
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost hidden sm:inline-flex"
            onClick={onLogout}
            title="Log out"
          >
            Log out
          </button>
          <button
            className="btn-secondary sm:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label="Toggle menu"
          >
            <span className="block h-0.5 w-5 bg-current" />
          </button>
        </div>
      </div>
      {open && (
        <div className="border-t border-line bg-bg-elev sm:hidden">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-3">
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.end}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  "rounded-lg px-3 py-2 text-sm " +
                  (isActive
                    ? "bg-bg-card text-white ring-1 ring-line"
                    : "text-slate-300 hover:bg-bg-card hover:text-white")
                }
              >
                {l.label}
              </NavLink>
            ))}
            <button className="btn-ghost mt-1 justify-start" onClick={onLogout}>
              Log out
            </button>
          </div>
        </div>
      )}
    </header>
  );
}
