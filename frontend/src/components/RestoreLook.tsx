import React, { useEffect, useState } from "react";
import Modal from "./Modal";
import { Api } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../toast";
import { useLayerStore } from "../state/layers";
export default function RestoreLook() {
  const { canOperate } = useAuth();
  const { push } = useToast();
  const { refresh: refreshLayers } = useLayerStore();
  const [available, setAvailable] = useState(false),
    [confirm, setConfirm] = useState(false),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () =>
      Api.lookHistory()
        .then((x) => {
          if (active) setAvailable(x.available);
        })
        .catch(() => {});
    void refresh();
    window.addEventListener("dmx:changed", refresh);
    return () => {
      active = false;
      window.removeEventListener("dmx:changed", refresh);
    };
  }, []);
  if (!canOperate) return null;
  return (
    <>
      <button
        className="btn-secondary text-xs"
        aria-label="Restore previous look"
        disabled={!available || busy}
        onClick={() => setConfirm(true)}
      >
        Restore
      </button>
      <Modal
        open={confirm}
        title="Restore the previous look?"
        onClose={() => {
          if (!busy) setConfirm(false);
        }}
        footer={
          <>
            <button
              className="btn-secondary"
              disabled={busy}
              onClick={() => setConfirm(false)}
            >
              Cancel
            </button>
            <button
              className="btn-primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await Api.restoreLook();
                  await refreshLayers();
                  setConfirm(false);
                  push("Previous look restored", "success");
                } catch (e) {
                  push(String(e), "error");
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Restoring…" : "Restore look"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          This restores the previous base colors and saved effect layers across
          the whole rig. It changes live output.
        </p>
      </Modal>
    </>
  );
}
