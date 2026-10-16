import React from "react";
import { Link } from "react-router-dom";
import { Controller } from "../api";
import { useControllers } from "../hooks/useControllers";
import { useAuth } from "../auth";
export function ControllerStatus({ controller }: { controller: Controller }) {
  const h = controller.health;
  const problem = h?.status === "error";
  return (
    <div
      className={"mt-2 text-xs " + (problem ? "text-rose-200" : "text-muted")}
    >
      <span
        className={
          "inline-block mr-1.5 h-2 w-2 rounded-full " +
          (problem
            ? "bg-rose-400"
            : h?.status === "sending"
              ? "bg-emerald-400"
              : "bg-amber-400")
        }
      />
      {problem
        ? "Output failed"
        : h?.status === "sending"
          ? "Sending packets"
          : h?.status === "disabled"
            ? "Output disabled"
            : h?.status === "simulation"
              ? "Preview server · no hardware output"
              : "Waiting for output"}
      {h?.last_sent_at && (
        <span>
          {" "}
          · Last sent{" "}
          {new Date(h.last_sent_at * 1000).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      )}
      {problem && <p className="mt-1 break-words">{h?.last_error}</p>}
    </div>
  );
}
export default function OutputStatus() {
  const { controllers, error } = useControllers();
  const { isAdmin } = useAuth();
  const problems = controllers.filter((c) => c.health?.status === "error");
  if (!problems.length && !error)
    return (
      <p className="text-xs text-muted">
        Packet status is available in Controllers. Fixture receipt is not
        confirmed by Art-Net.
      </p>
    );
  return (
    <section
      role="status"
      className="rounded-xl border border-amber-700 bg-amber-950/30 p-3 text-sm"
    >
      <div className="font-semibold text-amber-100">
        {error ||
          `${problems.length} controller${problems.length === 1 ? " needs" : "s need"} attention`}
      </div>
      {problems.map((c) => (
        <div key={c.id} className="mt-2">
          <strong>{c.name}</strong>
          <ControllerStatus controller={c} />
        </div>
      ))}
      {isAdmin && (
        <Link to="/config/controllers" className="btn-secondary mt-3">
          Review controllers
        </Link>
      )}
    </section>
  );
}
