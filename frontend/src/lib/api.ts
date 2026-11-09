import { ApiError } from "./api-error";
import type {
  AuditEvent,
  Client,
  ClientCreateRequest,
  ClientLinkResponse,
  ClientStats,
  ClientUpdateRequest,
  DeleteServerResponse,
  Exit,
  ExitCreateRequest,
  ExitUpdateRequest,
  HealthResponse,
  Incident,
  Job,
  JobCreatedResponse,
  JobEventData,
  LoginResponse,
  MetricsOverview,
  PreflightRequest,
  PreflightResult,
  RouteCheckResult,
  RoutingApplyResponse,
  RoutingProfile,
  RoutingProfileUpdate,
  RoutingRevision,
  Server,
  ServerAction,
  ServerCreateRequest,
  ServerCreateResponse,
  SitePreset,
  SiteRule,
  SiteRuleCreateRequest,
  SiteRuleUpdateRequest,
  TrafficResponse,
  UserMe,
} from "./api-types";

const API_PREFIX = "/api/v1";
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "1";

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function isMockMode(): boolean {
  return USE_MOCKS;
}

type QueryValue = string | number | boolean | undefined | null;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  /** Не вызывать глобальный обработчик 401 (например, для /auth/me). */
  skipUnauthorizedHandler?: boolean;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(API_PREFIX + path, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function mockRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const module = await import("./mock-api");
  return module.mockRequest<T>(path, options);
}

async function parseErrorResponse(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = `Ошибка запроса (${response.status})`;
  try {
    const payload = (await response.json()) as { error?: { code?: unknown; message?: unknown } };
    if (payload && typeof payload === "object" && payload.error) {
      if (typeof payload.error.code === "string" && payload.error.code) code = payload.error.code;
      if (typeof payload.error.message === "string" && payload.error.message) {
        message = payload.error.message;
      }
    }
  } catch {
    // тело не JSON — оставляем общий текст
  }
  if (response.status === 401) message = "Сессия истекла. Войдите заново.";
  if (response.status === 403) message = message === "Ошибка запроса (403)" ? "Доступ запрещён" : message;
  if (response.status === 429) message = message === "Ошибка запроса (429)" ? "Слишком много попыток. Попробуйте позже." : message;
  return new ApiError(message, response.status, code);
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (USE_MOCKS) return mockRequest<T>(path, options);

  const method = options.method ?? "GET";
  const url = buildUrl(path, options.query);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && !path.endsWith("/auth/login") && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      credentials: "include",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Сервер недоступен. Проверьте подключение к панели.", 0, "network_error");
  }

  if (response.status === 401) {
    if (!options.skipUnauthorizedHandler) unauthorizedHandler?.();
    throw await parseErrorResponse(response);
  }
  if (!response.ok) throw await parseErrorResponse(response);
  if (response.status === 204) return undefined as T;

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as T;
}

/** Запрос «как есть» — для скачивания SVG/JSON клиентских профилей. */
export async function apiRaw(path: string, options: RequestOptions = {}): Promise<Response> {
  if (USE_MOCKS) {
    const module = await import("./mock-api");
    return module.mockRaw(path, options);
  }
  const url = buildUrl(path, options.query);
  const response = await fetch(url, { credentials: "include", signal: options.signal });
  if (!response.ok) throw await parseErrorResponse(response);
  return response;
}

export interface JobStreamEvent {
  event: "progress" | "done" | "error";
  data: JobEventData;
  id?: number;
}

/** Подписка на SSE-прогресс job'а. Возвращает функцию отписки. */
export function subscribeJob(jobId: number | string, onEvent: (event: JobStreamEvent) => void): () => void {
  if (USE_MOCKS) {
    let unsubscribe: (() => void) | null = null;
    let closed = false;
    void import("./mock-api").then((module) => {
      if (closed) return;
      unsubscribe = module.subscribeMockJob(jobId, onEvent);
    });
    return () => {
      closed = true;
      unsubscribe?.();
    };
  }

  const source = new EventSource(`${API_PREFIX}/jobs/${encodeURIComponent(String(jobId))}/stream`);
  const close = () => source.close();
  const handleNamed = (name: "progress" | "done" | "error") => (raw: Event) => {
    const messageEvent = raw as MessageEvent<string>;
    let data: JobEventData = {};
    if (typeof messageEvent.data === "string" && messageEvent.data.length > 0) {
      try {
        const parsed: unknown = JSON.parse(messageEvent.data);
        if (parsed && typeof parsed === "object") data = parsed as JobEventData;
      } catch {
        data = { message: messageEvent.data };
      }
    }
    const parsedId = Number(messageEvent.lastEventId);
    onEvent({ event: name, data, id: Number.isFinite(parsedId) ? parsedId : undefined });
    if (name === "done" || name === "error") close();
  };
  source.addEventListener("progress", handleNamed("progress"));
  source.addEventListener("done", handleNamed("done"));
  source.addEventListener("error", (raw) => {
    const messageEvent = raw as MessageEvent<string>;
    if (typeof messageEvent.data === "string" && messageEvent.data.length > 0) {
      handleNamed("error")(raw);
      return;
    }
    onEvent({ event: "error", data: { error: "connection_lost" } });
  });
  return close;
}

function normalizeRevision(raw: RoutingRevision): RoutingRevision {
  return {
    ...raw,
    profile_json: raw.profile_json ?? raw.profile ?? null,
    config_hashes_json: raw.config_hashes_json ?? raw.hashes ?? null,
  };
}

export interface ExitCheckResponse {
  job_id?: number;
  health?: "up" | "down" | "unknown";
  last_delay_ms?: number | null;
  actual_exit_ip?: string | null;
  expected_exit_ip?: string | null;
}

export const api = {
  auth: {
    login: (email: string, password: string) =>
      apiRequest<LoginResponse>("/auth/login", { method: "POST", body: { email, password } }),
    logout: () => apiRequest<void>("/auth/logout", { method: "POST" }),
    me: () =>
      apiRequest<UserMe>("/auth/me", { skipUnauthorizedHandler: true }),
    changePassword: (currentPassword: string, newPassword: string) =>
      apiRequest<UserMe>("/auth/password", {
        method: "POST",
        body: { current_password: currentPassword, new_password: newPassword },
      }),
  },
  servers: {
    list: () => apiRequest<Server[]>("/servers"),
    get: (id: number) => apiRequest<Server>(`/servers/${id}`),
    preflight: (payload: PreflightRequest) =>
      apiRequest<PreflightResult>("/servers/preflight", { method: "POST", body: payload }),
    create: (payload: ServerCreateRequest) =>
      apiRequest<ServerCreateResponse>("/servers", { method: "POST", body: payload }),
    action: (id: number, action: ServerAction) =>
      apiRequest<JobCreatedResponse>(`/servers/${id}/actions`, { method: "POST", body: { action } }),
    /** force=true удаляет запись без SSH-очистки (backend: {confirm_name, force}). */
    remove: (id: number, confirmName: string, force = false) =>
      apiRequest<DeleteServerResponse>(`/servers/${id}`, {
        method: "DELETE",
        body: { confirm_name: confirmName, force },
      }),
  },
  exits: {
    list: () => apiRequest<Exit[]>("/exits"),
    create: (payload: ExitCreateRequest) => apiRequest<Exit>("/exits", { method: "POST", body: payload }),
    update: (id: number, payload: ExitUpdateRequest) =>
      apiRequest<Exit>(`/exits/${id}`, { method: "PATCH", body: payload }),
    remove: (id: number) => apiRequest<void>(`/exits/${id}`, { method: "DELETE" }),
    check: (id: number) => apiRequest<ExitCheckResponse>(`/exits/${id}/check`, { method: "POST" }),
  },
  sites: {
    list: () => apiRequest<SiteRule[]>("/sites"),
    create: (payload: SiteRuleCreateRequest) => apiRequest<SiteRule>("/sites", { method: "POST", body: payload }),
    update: (id: number, payload: SiteRuleUpdateRequest) =>
      apiRequest<SiteRule>(`/sites/${id}`, { method: "PATCH", body: payload }),
    remove: (id: number) => apiRequest<void>(`/sites/${id}`, { method: "DELETE" }),
    presets: async (): Promise<SitePreset[]> => {
      const presets = await apiRequest<SitePreset[]>("/sites/presets");
      return presets.map((preset) => ({
        ...preset,
        domains: preset.domains ?? preset.values ?? [],
      }));
    },
  },
  routing: {
    profile: () => apiRequest<RoutingProfile>("/routing/profile"),
    saveProfile: (payload: RoutingProfileUpdate) =>
      apiRequest<RoutingProfile>("/routing/profile", { method: "PUT", body: payload }),
    apply: () => apiRequest<RoutingApplyResponse>("/routing/apply", { method: "POST" }),
    check: (domain: string) =>
      apiRequest<RouteCheckResult>("/routing/check", { method: "POST", body: { domain } }),
    revisions: async () =>
      (await apiRequest<RoutingRevision[]>("/routing/revisions")).map(normalizeRevision),
    revision: async (id: number) =>
      normalizeRevision(await apiRequest<RoutingRevision>(`/routing/revisions/${id}`)),
  },
  clients: {
    list: () => apiRequest<Client[]>("/clients"),
    create: (payload: ClientCreateRequest) => apiRequest<Client>("/clients", { method: "POST", body: payload }),
    update: (id: number, payload: ClientUpdateRequest) =>
      apiRequest<Client>(`/clients/${id}`, { method: "PATCH", body: payload }),
    remove: (id: number) => apiRequest<void>(`/clients/${id}`, { method: "DELETE" }),
    link: (id: number, serverId: number) =>
      apiRequest<ClientLinkResponse>(`/clients/${id}/link`, { query: { server_id: serverId } }),
    profile: (id: number, mode: "smart" | "vless") =>
      apiRequest<unknown>(`/clients/${id}/profile`, { query: { mode } }),
    /** URL готового SVG-QR от backend (smart → URL подписки, vless → vless://). */
    qrUrl: (id: number, mode: "smart" | "vless", serverId?: number) => {
      const url = new URL(`${API_PREFIX}/clients/${id}/qr.svg`, window.location.origin);
      url.searchParams.set("mode", mode);
      if (serverId) url.searchParams.set("server_id", String(serverId));
      return url.toString();
    },
    qrSvgText: async (id: number, mode: "smart" | "vless", serverId?: number) => {
      const response = await apiRaw(`/clients/${id}/qr.svg`, {
        query: { mode, server_id: serverId },
      });
      return response.text();
    },
    stats: async (id: number): Promise<ClientStats> => {
      const raw = await apiRequest<ClientStats>(`/clients/${id}/stats`);
      const traffic = raw.traffic ?? null;
      return {
        ...raw,
        traffic_24h: raw.traffic_24h ?? traffic?.["24h"] ?? null,
        traffic_7d: raw.traffic_7d ?? traffic?.["7d"] ?? null,
        traffic_30d: raw.traffic_30d ?? traffic?.["30d"] ?? null,
      };
    },
  },
  metrics: {
    overview: () => apiRequest<MetricsOverview>("/metrics/overview"),
    traffic: (range: string, groupBy: "server" | "client") =>
      apiRequest<TrafficResponse>("/metrics/traffic", { query: { range, group_by: groupBy } }),
  },
  jobs: {
    get: (id: number | string) => apiRequest<Job>(`/jobs/${id}`),
    list: (serverId?: number) => apiRequest<Job[]>("/jobs", { query: { server_id: serverId } }),
  },
  incidents: {
    list: () => apiRequest<Incident[]>("/incidents"),
    ack: (id: number) => apiRequest<Incident>(`/incidents/${id}/ack`, { method: "POST" }),
    resolve: (id: number) => apiRequest<Incident>(`/incidents/${id}/resolve`, { method: "POST" }),
  },
  events: {
    /** Журнал аудита: GET /events -> [{id, ts, kind, actor, ip, user_agent, payload}]. */
    async list(limit = 100): Promise<AuditEvent[]> {
      const normalize = (event: AuditEvent): AuditEvent => ({
        ...event,
        payload_json: event.payload_json ?? event.payload ?? null,
      });
      try {
        const result = await apiRequest<AuditEvent[]>("/events", { query: { limit } });
        return result.map(normalize);
      } catch (error) {
        if (error instanceof ApiError && (error.status === 404 || error.status === 501)) {
          const result = await apiRequest<AuditEvent[]>("/audit", { query: { limit } });
          return result.map(normalize);
        }
        throw error;
      }
    },
  },
  health: async (): Promise<HealthResponse> => {
    if (USE_MOCKS) {
      const module = await import("./mock-api");
      return module.mockHealth();
    }
    const response = await fetch("/healthz", { credentials: "include" });
    if (!response.ok) throw new ApiError("Панель недоступна", response.status, "health_failed");
    return (await response.json()) as HealthResponse;
  },
};
