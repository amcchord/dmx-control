import React, { useEffect, useState } from "react";
import { Api, Controller, Light } from "../api";
import { useSelection } from "../state/selection";
import { useAuth } from "../auth";
import { useToast } from "../toast";
import Modal from "./Modal";
export default function SaveLook({ onSaved }: { onSaved?: () => void }) {
  const { canOperate } = useAuth();
  const { selection } = useSelection();
  const { push } = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState("rig");
  const [layers, setLayers] = useState(true);
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [lights, setLights] = useState<Light[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open)
      void Promise.all([
        Api.listControllers().then(setControllers),
        Api.listLights().then(setLights),
      ]).catch((e) => setError(String(e)));
  }, [open]);
  const ids =
    scope === "selection"
      ? lights.filter((l) => selection.lightIds.has(l.id)).map((l) => l.id)
      : scope === "rig"
        ? lights.map((l) => l.id)
        : lights
            .filter((l) => l.controller_id === Number(scope))
            .map((l) => l.id);
  if (!canOperate) return null;
  return (
    <>
      <button
        className="btn-primary"
        onClick={() => {
          setOpen(true);
          setName("");
          setError("");
        }}
      >
        Save current look
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Save current look"
        footer={
          <button
            form="save-look"
            className="btn-primary"
            disabled={busy || !name.trim() || !ids.length}
          >
            {busy ? "Saving…" : "Save look"}
          </button>
        }
      >
        <form
          id="save-look"
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              const first = lights.find((l) => ids.includes(l.id));
              if (!first) throw new Error("Choose at least one fixture");
              await Api.createScene({
                name,
                controller_id: first.controller_id,
                cross_controller: scope === "rig" || scope === "selection",
                light_ids: ids,
                include_layers: layers,
                from_rendered: !layers,
                favorite: true,
              });
              setOpen(false);
              onSaved?.();
              push("Look saved to favorites", "success");
            } catch (e) {
              setError(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="block">
            <span className="label">Look name</span>
            <input
              className="input mt-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Warm evening"
              maxLength={128}
              required
            />
          </label>
          <label className="block">
            <span className="label">Capture scope</span>
            <select
              className="input mt-1"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="rig">Whole rig</option>
              <option value="selection" disabled={!selection.lightIds.size}>
                Selected fixtures ({selection.lightIds.size})
              </option>
              {controllers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-3 rounded-lg bg-bg-elev p-3">
            <input
              type="checkbox"
              checked={layers}
              onChange={(e) => setLayers(e.target.checked)}
            />
            <span>Keep running effects with this look</span>
          </label>
          <p className="text-sm text-muted">
            Saves {ids.length} fixtures.{" "}
            {layers
              ? "Recalling restores base colors and the running layer settings."
              : "Captures a still frame of the current output."}{" "}
            Saving does not change live output.
          </p>
          {error && (
            <p role="alert" className="text-sm text-rose-200">
              {error}
            </p>
          )}
        </form>
      </Modal>
    </>
  );
}
