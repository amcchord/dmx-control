import React, { useEffect, useMemo, useRef, useState } from "react";
import { HexColorPicker } from "react-colorful";
import {
  Api,
  ColorRequestBody,
  ColorTable,
  ColorTableEntry,
  EffectLayer,
  ExtraColorRole,
  Light,
  LightModel,
  LightModelMode,
  PolicyRole,
} from "../api";
import { useLayerStore } from "../state/layers";

type Props = {
  /** Lights this picker edits. One = single-light mode (full control over
   *  flat fields + direct W/A/UV + extras). More than one = bulk mode
   *  (bulk RGB + dimmer + on/off; aux only when every fixture in the
   *  selection has the same direct-channel set). */
  lights: Light[];
  models: LightModel[];
  onApplied?: (updated: Light[]) => void;
  /** Optional notifier (toast). */
  notify?: (msg: string, kind?: "success" | "error" | "info") => void;
};

/** Rich color picker.
 *
 * Hex wheel for RGB, an on/off + dimmer pair, and per-role sliders for
 * any direct W/A/UV channel and any extra (w2/w3/a2/uv2) the fixture
 * exposes. Single-light mode reads/writes the flat color fields; bulk
 * mode pushes the same ColorRequest to every selected light, but only
 * surfaces aux sliders when every fixture in the selection has that
 * channel — otherwise we'd silently drop the value on incompatible
 * fixtures.
 *
 * Designed to be embedded in a modal/sheet; the parent owns open/close. */
export default function LightColorPicker({
  lights,
  models,
  onApplied,
  notify,
}: Props) {
  const isBulk = lights.length > 1;
  const seedLight = lights[0];
  const { layers, patchLayer } = useLayerStore();

  const [hex, setHex] = useState<string>(() => rgbToHex(seedLight));
  const [dimmer, setDimmer] = useState<number>(() => seedLight?.dimmer ?? 255);
  const [on, setOn] = useState<boolean>(() => seedLight?.on ?? true);
  const [aux, setAux] = useState<Partial<Record<PolicyRole, number>>>(() =>
    initialAuxFor(seedLight),
  );
  const [busyLayers, setBusyLayers] = useState(false);

  // Stable identity key so bulk re-opens reset state when the *set* of
  // selected lights changes (not just lights[0]). Otherwise the picker
  // can show stale hex/dimmer/aux when the parent rebuilds the array
  // but happens to keep the same first element instance.
  const lightsKey = lights.map((l) => l.id).join(",");

  useEffect(() => {
    setHex(rgbToHex(seedLight));
    setDimmer(seedLight?.dimmer ?? 255);
    setOn(seedLight?.on ?? true);
    setAux(initialAuxFor(seedLight));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lightsKey]);

  // Layers competing with this selection: not muted, and either
  // universal (no mask) or with a mask that overlaps our light ids.
  // These overwrite any manual color we push within ~33ms (one engine
  // tick), so we surface them with a banner + remediation buttons.
  const selectedIds = useMemo(() => lights.map((l) => l.id), [lights]);
  const competing = useMemo<EffectLayer[]>(() => {
    if (selectedIds.length === 0) return [];
    const sel = new Set(selectedIds);
    return layers.filter((l) => {
      if (l.mute || l.auto_muted) return false;
      if (!l.mask_light_ids || l.mask_light_ids.length === 0) return true;
      return l.mask_light_ids.some((id) => sel.has(id));
    });
  }, [layers, selectedIds]);

  // Resolve aux roles available across the selection. We surface every
  // W/A/UV/W*/A*/UV* role that appears in the mode's channel list, not
  // just ones tagged "direct" — operators expect to see *all* white
  // channels their fixture has, even on compound modes where the flat
  // value renders as the fallback for any zone that doesn't override.
  const directRoles = useMemo<AuxSliderEntry[]>(
    () => commonAuxRoles(lights, models),
    [lights, models],
  );
  // For fixtures with an indexed-color mode (Blizzard StormChaser etc.),
  // surface the discrete preset palette as quick-pick swatches under the
  // hex wheel. The wheel still works (renderer snaps RGB -> nearest
  // entry); the swatches are just a faster way to land on a preset
  // exactly. Only shown when every selected fixture shares the same
  // table.
  const sharedColorTable = useMemo<ColorTable | null>(
    () => commonColorTable(lights, models),
    [lights, models],
  );

  if (lights.length === 0) {
    return (
      <div className="rounded-md bg-bg-elev p-4 text-center text-sm text-muted">
        No lights to edit.
      </div>
    );
  }

  const apply = async (overrides: Partial<ColorRequestBody> = {}) => {
    const [r, g, b] = parseHex(hex);
    const roles = directRoles.map((r) => r.role);
    const body: ColorRequestBody = {
      r,
      g,
      b,
      dimmer,
      on,
      ...auxToBody(aux, roles),
      ...overrides,
    };
    try {
      if (isBulk) {
        await Api.bulkColor(
          lights.map((l) => l.id),
          body,
        );
        // Bulk endpoint doesn't return updated rows; re-fetch only the
        // affected ids for the parent.
        const all = await Api.listLights();
        const ids = new Set(lights.map((l) => l.id));
        onApplied?.(all.filter((l) => ids.has(l.id)));
        notify?.(
          `Color applied to ${lights.length} light${lights.length === 1 ? "" : "s"}`,
          "success",
        );
      } else {
        const updated = await Api.setColor(seedLight.id, body);
        onApplied?.([updated]);
        notify?.("Color applied", "success");
      }
    } catch (e) {
      notify?.(String(e), "error");
    }
  };

  const onPauseLayers = async () => {
    if (competing.length === 0) return;
    setBusyLayers(true);
    try {
      for (const layer of competing) {
        if (layer.layer_id != null) {
          await patchLayer(layer.layer_id, { mute: true });
        } else {
          // Transient (unsaved) layer — stop it directly.
          try {
            await Api.stopLive(layer.handle);
          } catch {
            // Best-effort; the layer may already be gone.
          }
        }
      }
      notify?.(
        `Paused ${competing.length} layer${competing.length === 1 ? "" : "s"}`,
        "success",
      );
    } catch (e) {
      notify?.(String(e), "error");
    } finally {
      setBusyLayers(false);
    }
  };

  const onDropFromLayers = async () => {
    if (competing.length === 0 || selectedIds.length === 0) return;
    setBusyLayers(true);
    const sel = new Set(selectedIds);
    try {
      // For universal layers (no mask), we need a concrete "every
      // light minus the selection" mask — fetch the rig once.
      let allLightIds: number[] | null = null;
      const ensureAllIds = async () => {
        if (allLightIds === null) {
          const all = await Api.listLights();
          allLightIds = all.map((l) => l.id);
        }
        return allLightIds;
      };
      let updated = 0;
      for (const layer of competing) {
        if (layer.layer_id == null) continue; // transient — Pause is the path.
        const existing = layer.mask_light_ids ?? [];
        let nextMask: number[];
        if (existing.length === 0) {
          const all = await ensureAllIds();
          nextMask = all.filter((id) => !sel.has(id));
        } else {
          nextMask = existing.filter((id) => !sel.has(id));
        }
        await patchLayer(layer.layer_id, { mask_light_ids: nextMask });
        updated += 1;
      }
      if (updated > 0) {
        notify?.(
          `Removed selection from ${updated} layer${updated === 1 ? "" : "s"}`,
          "success",
        );
      }
    } catch (e) {
      notify?.(String(e), "error");
    } finally {
      setBusyLayers(false);
    }
  };

  // We track the latest hex via a ref so window-scoped pointer listeners
  // can read the freshest value without re-binding on every render. The
  // wheel onChange fires on every pointermove during a drag; flooding
  // the server is avoided via a trailing debounced commit, *plus* an
  // explicit commit on pointer release that fires even if the user
  // dragged outside the wheel before letting go.
  const hexRef = useRef(hex);
  useEffect(() => {
    hexRef.current = hex;
  }, [hex]);
  const debounceRef = useRef<number | null>(null);
  const draggingRef = useRef(false);

  const commitHex = (next: string) => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    void apply({
      r: parseHex(next)[0],
      g: parseHex(next)[1],
      b: parseHex(next)[2],
    });
  };

  const onHexCommit = (next: string) => {
    setHex(next);
    hexRef.current = next;
    commitHex(next);
  };

  const onWheelChange = (next: string) => {
    setHex(next);
    hexRef.current = next;
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      commitHex(hexRef.current);
    }, 250);
  };

  const onWheelPointerDown = () => {
    draggingRef.current = true;
    const release = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("touchend", release);
      window.removeEventListener("touchcancel", release);
      commitHex(hexRef.current);
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("touchend", release);
    window.addEventListener("touchcancel", release);
  };

  // Cancel any pending debounce on unmount so we don't fire after the
  // modal closes.
  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, []);

  const onAuxChange = (role: PolicyRole, value: number) => {
    setAux((prev) => ({ ...prev, [role]: value }));
  };
  const onAuxCommit = (role: PolicyRole, value: number) => {
    void apply(
      auxToBody(
        { ...aux, [role]: value },
        directRoles.map((r) => r.role),
      ),
    );
  };

  return (
    <div className="flex flex-col gap-4">
      {isBulk && (
        <div className="rounded-md bg-emerald-950/40 px-3 py-2 text-xs text-emerald-200 ring-1 ring-emerald-800">
          Editing {lights.length} lights · changes apply to every fixture.
        </div>
      )}

      {competing.length > 0 && (
        <div className="rounded-md bg-amber-950/40 px-3 py-2 text-xs text-amber-100 ring-1 ring-amber-800">
          <div className="font-semibold">
            {competing.length} effect layer{competing.length === 1 ? "" : "s"}{" "}
            running on{" "}
            {selectedIds.length === 1
              ? "this light"
              : `these ${selectedIds.length} lights`}
            .
          </div>
          <div className="mt-0.5 text-amber-200/80">
            Manual color will be overwritten on the next engine tick. Pause
            the layers, or remove these lights from their masks.
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {competing.slice(0, 4).map((l) => (
              <span
                key={l.handle}
                className="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] ring-1 ring-amber-800"
                title={l.error ?? ""}
              >
                {l.name}
              </span>
            ))}
            {competing.length > 4 && (
              <span className="text-[10px] text-amber-200/70">
                +{competing.length - 4} more
              </span>
            )}
            <div className="ml-auto flex gap-1.5">
              <button
                type="button"
                disabled={busyLayers}
                onClick={onPauseLayers}
                className="rounded-md bg-amber-700/40 px-2 py-1 text-[11px] font-semibold text-amber-50 ring-1 ring-amber-700 hover:bg-amber-700/60 disabled:opacity-50"
              >
                Pause FX
              </button>
              <button
                type="button"
                disabled={busyLayers}
                onClick={onDropFromLayers}
                className="rounded-md bg-amber-900/40 px-2 py-1 text-[11px] font-semibold text-amber-100 ring-1 ring-amber-800 hover:bg-amber-900/60 disabled:opacity-50"
              >
                Remove from FX
              </button>
            </div>
          </div>
        </div>
      )}

      <div onPointerDown={onWheelPointerDown}>
        <HexColorPicker color={hex} onChange={onWheelChange} />
      </div>

      <div className="flex items-center gap-2">
        <div
          className="h-9 w-9 rounded-lg ring-1 ring-line"
          style={{ background: hex }}
        />
        <input
          className="input font-mono uppercase"
          value={hex}
          onChange={(e) => {
            const v = e.target.value;
            setHex(v);
            if (/^#[0-9a-fA-F]{6}$/.test(v)) {
              onHexCommit(v);
            }
          }}
          spellCheck={false}
        />
        <button
          onClick={() => {
            const next = !on;
            setOn(next);
            void apply({ on: next });
          }}
          className={
            "rounded-md px-3 py-2 text-xs font-semibold ring-1 " +
            (on
              ? "bg-emerald-700/40 text-emerald-100 ring-emerald-700"
              : "bg-bg-elev text-muted ring-line")
          }
          title="Toggle on/off"
        >
          {on ? "ON" : "OFF"}
        </button>
      </div>

      {sharedColorTable && uniquePresetEntries(sharedColorTable).length > 0 && (
        <div className="rounded-md bg-bg-elev p-2 ring-1 ring-line">
          <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-muted">
            <span>Indexed presets</span>
            <span>Click to snap</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {uniquePresetEntries(sharedColorTable).map((e, i) => {
              const presetHex = entryHex(e);
              return (
                <button
                  key={i}
                  type="button"
                  className="h-7 w-7 rounded ring-1 ring-line transition hover:ring-accent"
                  style={{ background: presetHex }}
                  title={
                    e.name
                      ? `${e.name} (${e.lo}-${e.hi})`
                      : `${e.lo}-${e.hi} → ${presetHex}`
                  }
                  onClick={() => onHexCommit(presetHex)}
                />
              );
            })}
          </div>
        </div>
      )}

      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <span className="label !text-[11px] normal-case tracking-normal">
            Dimmer
          </span>
          <span className="text-[11px] text-muted">{dimmer}</span>
        </div>
        <input
          type="range"
          min={0}
          max={255}
          step={1}
          value={dimmer}
          onChange={(e) => setDimmer(parseInt(e.currentTarget.value, 10))}
          onMouseUp={() => void apply({ dimmer })}
          onTouchEnd={() => void apply({ dimmer })}
          onKeyUp={() => void apply({ dimmer })}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-bg-elev accent-accent"
        />
      </div>

      {directRoles.length > 0 && (
        <div className="space-y-2 rounded-md bg-bg-elev p-3 ring-1 ring-line">
          <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-muted">
            <span>Aux channels</span>
            <span>
              {directRoles.map((r) => r.role.toUpperCase()).join(" / ")}
            </span>
          </div>
          {directRoles.map((entry) => {
            const meta = DIRECT_ROLE_META[entry.role];
            const value = aux[entry.role] ?? 0;
            return (
              <label
                key={entry.role}
                className="flex items-center gap-2 text-sm"
              >
                <span
                  className="h-3 w-3 rounded-full ring-1 ring-line"
                  style={{ background: meta.swatch }}
                />
                <span
                  className="flex w-20 items-center gap-1 text-xs text-slate-300"
                  title={meta.help}
                >
                  {meta.label}
                  <BadgeFor entry={entry} />
                </span>
                <input
                  type="range"
                  min={0}
                  max={255}
                  step={1}
                  value={value}
                  onChange={(e) =>
                    onAuxChange(
                      entry.role,
                      parseInt(e.currentTarget.value, 10),
                    )
                  }
                  onMouseUp={() => onAuxCommit(entry.role, value)}
                  onTouchEnd={() => onAuxCommit(entry.role, value)}
                  onKeyUp={() => onAuxCommit(entry.role, value)}
                  className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-bg-card accent-accent"
                />
                <span className="w-9 text-right font-mono text-[11px] text-muted">
                  {value}
                </span>
              </label>
            );
          })}
          {directRoles.some((e) => e.kind === "mix") && (
            <p className="text-[10px] text-muted">
              "mix" channels get re-derived from RGB on the next color
              change. Set the mode's color policy to "direct" in the
              model editor to make manual values stick.
            </p>
          )}
          {directRoles.some((e) => e.kind === "zone") && (
            <p className="text-[10px] text-muted">
              "zone" channels are per-pixel on this fixture; this slider
              writes the fallback applied to any zone not overridden in
              the zone editor.
            </p>
          )}
          {isBulk && (
            <p className="text-[10px] text-muted">
              Sliders only show roles every selected fixture exposes.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function BadgeFor({ entry }: { entry: AuxSliderEntry }) {
  if (entry.kind === "direct" || entry.kind === "extra") return null;
  const cls =
    entry.kind === "mix"
      ? "bg-amber-900/40 text-amber-300 ring-amber-800"
      : "bg-bg-card text-muted ring-line";
  return (
    <span
      className={
        "rounded px-1 py-px text-[8px] uppercase tracking-wider ring-1 " +
        cls
      }
    >
      {entry.kind}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const DIRECT_ROLE_META: Record<
  PolicyRole,
  { label: string; swatch: string; help: string }
> = {
  w: {
    label: "White",
    swatch: "#f5f5f5",
    help: "White LED fader (independent of RGB).",
  },
  w2: {
    label: "White 2",
    swatch: "#f5f5f5",
    help: "Second white LED (e.g. cool/warm pair).",
  },
  w3: { label: "White 3", swatch: "#f5f5f5", help: "Third white LED." },
  a: {
    label: "Amber",
    swatch: "#ffb23d",
    help: "Amber LED fader (independent of RGB).",
  },
  a2: { label: "Amber 2", swatch: "#ffb23d", help: "Secondary amber LED." },
  uv: { label: "UV", swatch: "#b44dff", help: "Ultraviolet LED fader." },
  uv2: { label: "UV 2", swatch: "#b44dff", help: "Secondary UV LED." },
};

const EXTRA_ROLES = new Set<PolicyRole>(["w2", "w3", "a2", "uv2"]);
const ROLE_ORDER: PolicyRole[] = ["w", "w2", "w3", "a", "a2", "uv", "uv2"];

function rgbToHex(light: Light | undefined): string {
  if (!light) return "#FFFFFF";
  return (
    "#" +
    [light.r, light.g, light.b]
      .map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()
  );
}

function parseHex(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return [255, 255, 255];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

function initialAuxFor(
  light: Light | undefined,
): Partial<Record<PolicyRole, number>> {
  if (!light) return {};
  const extras = light.extra_colors ?? {};
  return {
    w: light.w,
    a: light.a,
    uv: light.uv,
    w2: extras.w2 ?? 0,
    w3: extras.w3 ?? 0,
    a2: extras.a2 ?? 0,
    uv2: extras.uv2 ?? 0,
  };
}

/** What kind of aux fader we're rendering for one role.
 *
 * - "direct": mode's color_policy explicitly tags this role as a direct
 *   independent fader. Manual values always stick.
 * - "extra":  w2 / w3 / a2 / uv2 — always direct, never mixed from RGB.
 * - "mix":    w / a / uv on a flat (non-compound) mode whose policy is
 *   the default mix-from-RGB. Manual values get re-derived next time
 *   RGB changes; we still surface the slider so the operator sees the
 *   channel exists.
 * - "zone":   role appears multiple times in a compound mode, e.g.
 *   per-pixel W on a 112ch bar. This slider writes the flat fallback
 *   that any zone without explicit zone_state will inherit. */
type AuxSliderKind = "direct" | "extra" | "mix" | "zone";

type AuxSliderEntry = {
  role: PolicyRole;
  kind: AuxSliderKind;
};

function commonAuxRoles(
  lights: Light[],
  models: LightModel[],
): AuxSliderEntry[] {
  if (lights.length === 0) return [];
  const modelById = new Map<number, LightModel>(
    models.map((m) => [m.id, m]),
  );
  const perLight: Map<PolicyRole, AuxSliderKind>[] = [];
  for (const l of lights) {
    const model = modelById.get(l.model_id);
    if (!model) {
      perLight.push(new Map());
      continue;
    }
    const mode =
      l.mode_id != null
        ? model.modes.find((m) => m.id === l.mode_id)
        : model.modes.find((m) => m.is_default) ?? model.modes[0];
    if (!mode) {
      perLight.push(new Map());
      continue;
    }
    perLight.push(auxRolesFor(mode));
  }
  // Intersect across the selection. We keep the *least permissive* kind
  // when fixtures disagree (e.g. one's policy is direct, another's is
  // mix → the picker still works but warns the user).
  const intersection = new Map<PolicyRole, AuxSliderKind>(
    perLight[0] ?? [],
  );
  for (const s of perLight.slice(1)) {
    for (const role of [...intersection.keys()]) {
      const otherKind = s.get(role);
      if (otherKind == null) {
        intersection.delete(role);
        continue;
      }
      const a = intersection.get(role)!;
      intersection.set(role, weakest(a, otherKind));
    }
  }
  return ROLE_ORDER.filter((r) => intersection.has(r)).map((r) => ({
    role: r,
    kind: intersection.get(r)!,
  }));
}

function auxRolesFor(
  mode: LightModelMode,
): Map<PolicyRole, AuxSliderKind> {
  const policy = mode.color_policy ?? {};
  const counts = new Map<string, number>();
  for (const ch of mode.channels) counts.set(ch, (counts.get(ch) ?? 0) + 1);
  const out = new Map<PolicyRole, AuxSliderKind>();
  for (const role of ROLE_ORDER) {
    if (!counts.has(role)) continue;
    if (role === "w" || role === "a" || role === "uv") {
      const repeated = (counts.get(role) ?? 0) > 1;
      if (policy[role] === "direct") out.set(role, "direct");
      else if (repeated) out.set(role, "zone");
      else out.set(role, "mix");
    } else {
      out.set(role, "extra");
    }
  }
  return out;
}

const KIND_RANK: Record<AuxSliderKind, number> = {
  direct: 3,
  extra: 3,
  mix: 2,
  zone: 1,
};

function weakest(a: AuxSliderKind, b: AuxSliderKind): AuxSliderKind {
  return KIND_RANK[a] <= KIND_RANK[b] ? a : b;
}

function auxToBody(
  aux: Partial<Record<PolicyRole, number>>,
  roles: PolicyRole[],
): Partial<ColorRequestBody> {
  const out: Partial<ColorRequestBody> = {};
  for (const role of roles) {
    const value = aux[role];
    if (value == null) continue;
    if (EXTRA_ROLES.has(role)) {
      (out as Record<ExtraColorRole, number>)[role as ExtraColorRole] = value;
    } else {
      (out as Record<"w" | "a" | "uv", number>)[role as "w" | "a" | "uv"] =
        value;
    }
  }
  return out;
}

/** Return the color table shared by every selected light, or null if
 * any fixture in the selection is missing the table or holds a
 * different one (we can't show a single coherent preset row when the
 * palettes disagree). */
function commonColorTable(
  lights: Light[],
  models: LightModel[],
): ColorTable | null {
  if (lights.length === 0) return null;
  const modelById = new Map<number, LightModel>(
    models.map((m) => [m.id, m]),
  );
  let shared: ColorTable | null = null;
  let sharedKey: string | null = null;
  for (const l of lights) {
    const model = modelById.get(l.model_id);
    if (!model) return null;
    const mode =
      l.mode_id != null
        ? model.modes.find((m) => m.id === l.mode_id)
        : (model.modes.find((m) => m.is_default) ?? model.modes[0]);
    if (!mode) return null;
    const t = mode.color_table;
    if (!t || !t.entries?.length) return null;
    const key = JSON.stringify(t.entries.map((e) => [e.lo, e.hi, e.r, e.g, e.b]));
    if (sharedKey == null) {
      shared = t;
      sharedKey = key;
    } else if (key !== sharedKey) {
      return null;
    }
  }
  return shared;
}

const entryHex = (e: ColorTableEntry): string =>
  "#" +
  [e.r, e.g, e.b]
    .map((c) =>
      Math.max(0, Math.min(255, Math.round(c)))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")
    .toUpperCase();

/** Some manuals split a color into multiple ranges (e.g. light blue
 * appears twice for chase animations). Dedupe by representative RGB so
 * the swatch row stays compact. */
function uniquePresetEntries(table: ColorTable): ColorTableEntry[] {
  const seen = new Set<string>();
  const out: ColorTableEntry[] = [];
  for (const e of table.entries) {
    const key = `${e.r}-${e.g}-${e.b}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(e);
  }
  return out;
}
