export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const init: RequestInit = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      (data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : null) || res.statusText;
    throw new ApiError(String(detail), res.status, data);
  }
  return data as T;
}

async function requestMultipart<T>(
  method: string,
  path: string,
  form: FormData,
): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    body: form,
  });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      (data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : null) || res.statusText;
    throw new ApiError(String(detail), res.status, data);
  }
  return data as T;
}

export type UploadPhase = "uploading" | "processing";

export type UploadProgress = {
  phase: UploadPhase;
  loaded?: number;
  total?: number;
  // 0..1 during "uploading"; undefined/1 when upload has completed.
  percent?: number;
};

function xhrUpload<T>(
  path: string,
  form: FormData,
  onProgress?: (p: UploadProgress) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.responseType = "text";
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.onprogress = (e) => {
      if (!onProgress) return;
      onProgress({
        phase: "uploading",
        loaded: e.loaded,
        total: e.lengthComputable ? e.total : undefined,
        percent: e.lengthComputable && e.total ? e.loaded / e.total : undefined,
      });
    };
    xhr.upload.onload = () => {
      onProgress?.({ phase: "processing", percent: 1 });
    };

    xhr.onerror = () =>
      reject(new ApiError("network error", 0, null));
    xhr.onabort = () => reject(new ApiError("upload aborted", 0, null));
    xhr.onload = () => {
      const text = xhr.responseText || "";
      let data: unknown = null;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch {
          data = text;
        }
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as T);
      } else {
        const detail =
          (data && typeof data === "object" && "detail" in data
            ? (data as { detail: unknown }).detail
            : null) || xhr.statusText;
        reject(new ApiError(String(detail), xhr.status, data));
      }
    };

    xhr.send(form);
  });
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, body?: unknown) => request<T>("POST", p, body),
  patch: <T>(p: string, body?: unknown) => request<T>("PATCH", p, body),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export type Controller = {
  id: number;
  name: string;
  ip: string;
  port: number;
  net: number;
  subnet: number;
  universe: number;
  enabled: boolean;
};

export type ColorRole = "r" | "g" | "b" | "w" | "a" | "uv";

export type ZoneKind =
  | "pixel"
  | "segment"
  | "ring"
  | "panel"
  | "eye"
  | "head"
  | "beam"
  | "global"
  | "other";

export type LayoutShape =
  | "single"
  | "linear"
  | "grid"
  | "ring"
  | "cluster";

export type FixtureZone = {
  id: string;
  label: string;
  kind?: ZoneKind;
  row?: number;
  col?: number;
  colors: Partial<Record<ColorRole, number>>;
  dimmer?: number | null;
  strobe?: number | null;
};

export type FixtureMotion = {
  pan?: number | null;
  pan_fine?: number | null;
  tilt?: number | null;
  tilt_fine?: number | null;
  zoom?: number | null;
  focus?: number | null;
  pan_degrees?: number | null;
  tilt_degrees?: number | null;
};

export type FixtureGlobals = {
  dimmer?: number | null;
  strobe?: number | null;
  macro?: number | null;
  speed?: number | null;
};

export type FixtureLayout = {
  shape: LayoutShape;
  cols?: number | null;
  rows?: number | null;
  zones: FixtureZone[];
  motion?: FixtureMotion | null;
  globals?: FixtureGlobals | null;
};

export type LightModelMode = {
  id: number;
  name: string;
  channels: string[];
  channel_count: number;
  is_default: boolean;
  layout?: FixtureLayout | null;
};

export type LightModelModeInput = {
  id?: number;
  name: string;
  channels: string[];
  is_default: boolean;
  layout?: FixtureLayout | null;
};

export type LightModel = {
  id: number;
  name: string;
  channels: string[];
  channel_count: number;
  builtin: boolean;
  image_url?: string | null;
  modes: LightModelMode[];
};

export type ZoneColorState = {
  r?: number;
  g?: number;
  b?: number;
  w?: number;
  a?: number;
  uv?: number;
  dimmer?: number;
  on?: boolean;
};

export type MotionState = {
  pan?: number;
  tilt?: number;
  zoom?: number;
  focus?: number;
};

export type Light = {
  id: number;
  name: string;
  controller_id: number;
  model_id: number;
  mode_id: number | null;
  start_address: number;
  position: number;
  r: number;
  g: number;
  b: number;
  w: number;
  a: number;
  uv: number;
  dimmer: number;
  on: boolean;
  zone_state: Record<string, ZoneColorState>;
  motion_state: MotionState;
};

/** Live DMX snapshot for one light, decoded from the Art-Net buffer.
 *
 * Returned by `GET /api/lights/rendered`. The Dashboard polls this while
 * any effect is active so the on-screen light cards animate alongside the
 * physical fixtures. Unlike `Light`, this is ephemeral — the server
 * reconstructs it from the current universe buffer on every request. */
export type RenderedLightZone = {
  r: number;
  g: number;
  b: number;
  on: boolean;
};

export type RenderedLight = {
  r: number;
  g: number;
  b: number;
  on: boolean;
  zone_state: Record<string, RenderedLightZone>;
};

export type Palette = {
  id: number;
  name: string;
  colors: string[];
  builtin: boolean;
};

export type AuthStatus = { authenticated: boolean };

export type AiStatus = { enabled: boolean; model: string };

export type ParsedMode = {
  name: string;
  channels: string[];
  notes?: string | null;
  layout?: FixtureLayout | null;
};

export type ParsedManual = {
  suggested_name: string;
  modes: ParsedMode[];
  notes?: string | null;
};

export type LightModelPayload = {
  name: string;
  modes: LightModelModeInput[];
};

export type LightPayload = {
  name: string;
  controller_id: number;
  model_id: number;
  mode_id?: number | null;
  start_address: number;
  position?: number;
};

export type ColorRequestBody = {
  r: number;
  g: number;
  b: number;
  w?: number | null;
  a?: number | null;
  uv?: number | null;
  dimmer?: number | null;
  on?: boolean | null;
  zone_id?: string | null;
  motion?: MotionState | null;
};

export type BulkTarget = { light_id: number; zone_id?: string | null };

export type PaletteSpread =
  | "across_lights"
  | "across_fixture"
  | "across_zones";

export type EffectType =
  | "static"
  | "fade"
  | "cycle"
  | "chase"
  | "pulse"
  | "rainbow"
  | "strobe"
  | "sparkle"
  | "wave";

export type EffectDirection = "forward" | "reverse" | "pingpong";

export type EffectParams = {
  speed_hz: number;
  direction: EffectDirection;
  offset: number;
  intensity: number;
  size: number;
  softness: number;
  fade_in_s: number;
  fade_out_s: number;
};

export const DEFAULT_EFFECT_PARAMS: EffectParams = {
  speed_hz: 0.5,
  direction: "forward",
  offset: 0,
  intensity: 1,
  size: 1,
  softness: 0.5,
  fade_in_s: 0.25,
  fade_out_s: 0.25,
};

export type Effect = {
  id: number;
  name: string;
  effect_type: EffectType;
  palette_id: number | null;
  light_ids: number[];
  targets: BulkTarget[];
  spread: PaletteSpread;
  params: EffectParams;
  is_active: boolean;
  builtin: boolean;
};

export type EffectInput = {
  name: string;
  effect_type: EffectType;
  palette_id: number | null;
  light_ids: number[];
  targets: BulkTarget[];
  spread: PaletteSpread;
  params: EffectParams;
};

export type LiveEffectInput = Omit<EffectInput, "name"> & { name?: string };

export type ActiveEffect = {
  id: number | null;
  handle: string;
  name: string;
  effect_type: EffectType;
  runtime_s: number;
};

/** Per-light snapshot inside a saved Scene. Mirrors the writable fields
 * on `Light` plus the `light_id` key used to restore it. */
export type SceneLightState = {
  light_id: number;
  r: number;
  g: number;
  b: number;
  w: number;
  a: number;
  uv: number;
  dimmer: number;
  on: boolean;
  zone_state: Record<string, ZoneColorState>;
  motion_state: MotionState;
};

/** A saved (or virtual) state snapshot for a controller.
 *
 * `id` is null for virtual built-ins (currently just Blackout). `builtin`
 * is true in that case. `cross_controller` means the captured state may
 * cover lights on other controllers too. */
export type Scene = {
  id: number | null;
  name: string;
  controller_id: number;
  cross_controller: boolean;
  lights: SceneLightState[];
  builtin: boolean;
};

export type SceneCreateInput = {
  name: string;
  controller_id: number;
  cross_controller?: boolean;
  /** Subset of lights to capture; omit to capture the controller's lights
   * (or every light, if `cross_controller` is true). */
  light_ids?: number[];
  /** When true, capture from the live rendered DMX buffer instead of the
   * DB state. Useful for freezing an effect's current output. */
  from_rendered?: boolean;
};

export type SceneUpdateInput = {
  name?: string;
  controller_id?: number;
  cross_controller?: boolean;
  /** When true, re-capture the snapshot from the current state. */
  recapture?: boolean;
  from_rendered?: boolean;
  light_ids?: number[];
};

export const Api = {
  login: (password: string) =>
    api.post<AuthStatus>("/api/auth/login", { password }),
  logout: () => api.post<AuthStatus>("/api/auth/logout"),
  status: () => api.get<AuthStatus>("/api/auth/status"),

  listControllers: () => api.get<Controller[]>("/api/controllers"),
  createController: (body: Omit<Controller, "id">) =>
    api.post<Controller>("/api/controllers", body),
  updateController: (id: number, body: Omit<Controller, "id">) =>
    api.patch<Controller>(`/api/controllers/${id}`, body),
  deleteController: (id: number) => api.del<void>(`/api/controllers/${id}`),
  blackoutController: (id: number) =>
    api.post<{ ok: boolean }>(`/api/controllers/${id}/blackout`),

  listModels: () => api.get<LightModel[]>("/api/models"),
  createModel: (body: LightModelPayload) =>
    api.post<LightModel>("/api/models", body),
  updateModel: (id: number, body: LightModelPayload) =>
    api.patch<LightModel>(`/api/models/${id}`, body),
  deleteModel: (id: number) => api.del<void>(`/api/models/${id}`),
  cloneModel: (id: number) => api.post<LightModel>(`/api/models/${id}/clone`),
  uploadModelImage: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestMultipart<LightModel>(
      "POST",
      `/api/models/${id}/image`,
      form,
    );
  },
  deleteModelImage: (id: number) =>
    api.del<LightModel>(`/api/models/${id}/image`),

  listLights: () => api.get<Light[]>("/api/lights"),
  listRenderedLights: () =>
    api.get<Record<string, RenderedLight>>("/api/lights/rendered"),
  createLight: (body: LightPayload) => api.post<Light>("/api/lights", body),
  updateLight: (id: number, body: LightPayload) =>
    api.patch<Light>(`/api/lights/${id}`, body),
  deleteLight: (id: number) => api.del<void>(`/api/lights/${id}`),
  reorderLights: (lightIds: number[]) =>
    api.post<{ updated: number }>(`/api/lights/reorder`, {
      light_ids: lightIds,
    }),
  setColor: (
    id: number,
    rgb: ColorRequestBody,
  ) => api.post<Light>(`/api/lights/${id}/color`, rgb),
  bulkColor: (
    ids: number[],
    rgb: ColorRequestBody,
    targets?: BulkTarget[],
  ) =>
    api.post<{ updated: number }>(`/api/lights/bulk-color`, {
      light_ids: ids,
      targets,
      ...rgb,
    }),

  listPalettes: () => api.get<Palette[]>("/api/palettes"),
  createPalette: (body: { name: string; colors: string[] }) =>
    api.post<Palette>("/api/palettes", body),
  updatePalette: (id: number, body: { name: string; colors: string[] }) =>
    api.patch<Palette>(`/api/palettes/${id}`, body),
  deletePalette: (id: number) => api.del<void>(`/api/palettes/${id}`),
  clonePalette: (id: number) => api.post<Palette>(`/api/palettes/${id}/clone`),
  applyPalette: (
    id: number,
    lightIds: number[],
    mode: "cycle" | "random" | "gradient",
    spread: "across_lights" | "across_fixture" | "across_zones" = "across_lights",
  ) =>
    api.post<{ updated: number }>(`/api/palettes/${id}/apply`, {
      light_ids: lightIds,
      mode,
      spread,
    }),

  listEffects: () => api.get<Effect[]>("/api/effects"),
  createEffect: (body: EffectInput) => api.post<Effect>("/api/effects", body),
  updateEffect: (id: number, body: EffectInput) =>
    api.patch<Effect>(`/api/effects/${id}`, body),
  deleteEffect: (id: number) => api.del<void>(`/api/effects/${id}`),
  cloneEffect: (id: number) => api.post<Effect>(`/api/effects/${id}/clone`),
  playEffect: (id: number) =>
    api.post<{ ok: boolean; handle: string }>(`/api/effects/${id}/play`),
  stopEffect: (id: number) =>
    api.post<{ ok: boolean; stopped: number }>(`/api/effects/${id}/stop`),
  stopAllEffects: () =>
    api.post<{ ok: boolean; stopped: number }>(`/api/effects/stop-all`),
  activeEffects: () => api.get<ActiveEffect[]>(`/api/effects/active`),
  playLive: (body: LiveEffectInput) =>
    api.post<{ ok: boolean; handle: string; name: string }>(
      `/api/effects/live`,
      body,
    ),
  stopLive: (handle: string) =>
    api.post<{ ok: boolean }>(`/api/effects/live/${handle}/stop`),
  saveLive: (handle: string, name: string) =>
    api.post<Effect>(`/api/effects/live/${handle}/save`, { name }),

  listScenes: (controllerId?: number) =>
    api.get<Scene[]>(
      controllerId !== undefined
        ? `/api/scenes?controller_id=${controllerId}`
        : `/api/scenes`,
    ),
  createScene: (body: SceneCreateInput) =>
    api.post<Scene>(`/api/scenes`, body),
  updateScene: (id: number, body: SceneUpdateInput) =>
    api.patch<Scene>(`/api/scenes/${id}`, body),
  deleteScene: (id: number) => api.del<void>(`/api/scenes/${id}`),
  applyScene: (id: number) =>
    api.post<{ ok: boolean; applied: number }>(`/api/scenes/${id}/apply`),
  applyBlackoutScene: (controllerId: number) =>
    api.post<{ ok: boolean; applied: number }>(
      `/api/scenes/blackout/${controllerId}/apply`,
    ),

  aiStatus: () => api.get<AiStatus>("/api/ai/status"),
  parseManual: (
    file: File,
    onProgress?: (p: UploadProgress) => void,
  ) => {
    const form = new FormData();
    form.append("file", file);
    return xhrUpload<ParsedManual>(
      "/api/models/parse-manual",
      form,
      onProgress,
    );
  },
};
