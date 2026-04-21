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

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, body?: unknown) => request<T>("POST", p, body),
  patch: <T>(p: string, body?: unknown) => request<T>("PATCH", p, body),
  del: <T>(p: string) => request<T>("DELETE", p),
};

// ---- Types mirroring backend schemas ----

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

export type LightModel = {
  id: number;
  name: string;
  channels: string[];
  channel_count: number;
  builtin: boolean;
};

export type Light = {
  id: number;
  name: string;
  controller_id: number;
  model_id: number;
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
};

export type Palette = {
  id: number;
  name: string;
  colors: string[];
  builtin: boolean;
};

export type AuthStatus = { authenticated: boolean };

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
  createModel: (body: { name: string; channels: string[] }) =>
    api.post<LightModel>("/api/models", body),
  updateModel: (id: number, body: { name: string; channels: string[] }) =>
    api.patch<LightModel>(`/api/models/${id}`, body),
  deleteModel: (id: number) => api.del<void>(`/api/models/${id}`),
  cloneModel: (id: number) => api.post<LightModel>(`/api/models/${id}/clone`),

  listLights: () => api.get<Light[]>("/api/lights"),
  createLight: (body: {
    name: string;
    controller_id: number;
    model_id: number;
    start_address: number;
    position?: number;
  }) => api.post<Light>("/api/lights", body),
  updateLight: (id: number, body: {
    name: string;
    controller_id: number;
    model_id: number;
    start_address: number;
    position?: number;
  }) => api.patch<Light>(`/api/lights/${id}`, body),
  deleteLight: (id: number) => api.del<void>(`/api/lights/${id}`),
  setColor: (id: number, rgb: { r: number; g: number; b: number; on?: boolean; dimmer?: number }) =>
    api.post<Light>(`/api/lights/${id}/color`, rgb),
  bulkColor: (ids: number[], rgb: { r: number; g: number; b: number; on?: boolean; dimmer?: number }) =>
    api.post<{ updated: number }>(`/api/lights/bulk-color`, { light_ids: ids, ...rgb }),

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
  ) =>
    api.post<{ updated: number }>(`/api/palettes/${id}/apply`, {
      light_ids: lightIds,
      mode,
    }),
};
