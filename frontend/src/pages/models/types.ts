import type {
  ChannelPolicy,
  ColorPolicy,
  FixtureLayout,
  LightModel,
  LightModelMode,
  LightModelModeInput,
  PolicyRole,
} from "../../api";

export const ROLES = [
  "r",
  "g",
  "b",
  "w",
  "w2",
  "w3",
  "a",
  "a2",
  "uv",
  "uv2",
  "dimmer",
  "strobe",
  "macro",
  "speed",
  "pan",
  "pan_fine",
  "tilt",
  "tilt_fine",
  "zoom",
  "focus",
  "other",
] as const;

export const ROLE_COLORS: Record<string, string> = {
  r: "#ff4d4d",
  g: "#4dff6a",
  b: "#4d6aff",
  w: "#f5f5f5",
  w2: "#f5f5f5",
  w3: "#f5f5f5",
  a: "#ffb23d",
  a2: "#ffb23d",
  uv: "#b44dff",
  uv2: "#b44dff",
  dimmer: "#cfcfcf",
  strobe: "#fff566",
  macro: "#8791a7",
  speed: "#8791a7",
  pan: "#6ba2ff",
  pan_fine: "#4e7bc9",
  tilt: "#6ba2ff",
  tilt_fine: "#4e7bc9",
  zoom: "#b28df4",
  focus: "#b28df4",
  other: "#8791a7",
};

/** Roles whose mix/direct policy the editor exposes as a toggle. Primary
 * W/A/UV get the full Mix or Direct choice; secondary instances (w2/w3,
 * a2, uv2) are always direct — listed here only so their presence in the
 * mode's channel list surfaces in the Color Behavior section. */
export const POLICY_ROLES: PolicyRole[] = [
  "w",
  "w2",
  "w3",
  "a",
  "a2",
  "uv",
  "uv2",
];

/** Extra (always-direct) aux roles. */
export const EXTRA_POLICY_ROLES: PolicyRole[] = ["w2", "w3", "a2", "uv2"];

/** Human label + hint shown under each W/A/UV toggle. */
export const POLICY_ROLE_LABEL: Record<PolicyRole, string> = {
  w: "White (W)",
  w2: "White 2 (W2)",
  w3: "White 3 (W3)",
  a: "Amber (A)",
  a2: "Amber 2 (A2)",
  uv: "Ultraviolet (UV)",
  uv2: "Ultraviolet 2 (UV2)",
};

export const POLICY_ROLE_HELP: Record<PolicyRole, string> = {
  w: "Many fixtures fire their white LED as a near-strobe accent rather than as part of the color mix. Switch to Direct to expose it as its own fader.",
  w2: "Second white LED on the fixture (often warm-vs-cool pair). Always an independent fader on the Dashboard.",
  w3: "Third white channel. Always an independent fader on the Dashboard.",
  a: "Amber can be mixed from R+G to warm up whites, or left as a direct channel for dedicated amber pops.",
  a2: "Secondary amber channel. Always an independent fader on the Dashboard.",
  uv: "UV rarely benefits from RGB mixing; Direct keeps it under manual / designer control.",
  uv2: "Secondary UV channel. Always an independent fader on the Dashboard.",
};

export type ModeDraft = {
  /** Stable key for React list rendering (id if persisted, else synthetic). */
  key: string;
  id?: number;
  name: string;
  channels: string[];
  is_default: boolean;
  layout: FixtureLayout | null;
  color_policy: ColorPolicy;
};

export type Form = {
  name: string;
  modes: ModeDraft[];
  activeKey: string;
};

let _modeKeyCounter = 0;
export const newModeKey = () => `m-${Date.now()}-${++_modeKeyCounter}`;

export const toDraft = (m: LightModelMode): ModeDraft => ({
  key: `m-${m.id}`,
  id: m.id,
  name: m.name,
  channels: [...m.channels],
  is_default: m.is_default,
  layout: m.layout ?? null,
  color_policy: { ...(m.color_policy ?? {}) },
});

export const blankForm = (): Form => {
  const key = newModeKey();
  return {
    name: "",
    modes: [
      {
        key,
        name: "3ch",
        channels: ["r", "g", "b"],
        is_default: true,
        layout: null,
        color_policy: {},
      },
    ],
    activeKey: key,
  };
};

export const fromModel = (m: LightModel): Form => {
  const modes = m.modes.length
    ? m.modes.map(toDraft)
    : [
        {
          key: newModeKey(),
          name: `${m.channel_count || m.channels.length}ch`,
          channels: [...m.channels],
          is_default: true,
          layout: null,
          color_policy: {} as ColorPolicy,
        },
      ];
  return {
    name: m.name,
    modes,
    activeKey: modes[0].key,
  };
};

export const draftsToPayload = (
  drafts: ModeDraft[],
): LightModelModeInput[] =>
  drafts.map((d) => ({
    id: d.id,
    name: d.name.trim(),
    channels: [...d.channels],
    is_default: d.is_default,
    layout: d.layout ?? null,
    color_policy: { ...d.color_policy },
  }));

/** Return the current policy for one role on a draft, defaulting to "mix". */
export const policyFor = (
  draft: ModeDraft,
  role: PolicyRole,
): ChannelPolicy => {
  const v = draft.color_policy?.[role];
  if (v === "direct") return "direct";
  return "mix";
};

export const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
};

export type ManualState =
  | { phase: "idle" }
  | {
      phase: "uploading";
      file: { name: string; size: number };
      percent: number;
      startedAt: number;
    }
  | {
      phase: "processing";
      file: { name: string; size: number };
      percent: 1;
      startedAt: number;
      processingStartedAt: number;
    }
  | { phase: "error"; message: string };
