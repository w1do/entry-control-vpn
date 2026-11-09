/*
 * Мок-режим панели (VITE_USE_MOCKS=1): полностью in-memory реализация API
 * для разработки UI без backend. Модуль подгружается динамически и не попадает
 * в начальный бандл production-сборки.
 */
import { ApiError } from "./api-error";
import type {
  AuditEvent,
  Client,
  ClientStats,
  Exit,
  Incident,
  Job,
  JobEventData,
  JobStatus,
  MetricsOverview,
  RoutingProfile,
  RoutingRevision,
  Server,
  ServerRole,
  SitePreset,
  SiteRule,
  TrafficPoint,
  TrafficResponse,
  UserMe,
} from "./api-types";
import type { JobStreamEvent, RequestOptions } from "./api";

const MOCK_EMAIL = "admin@vpn.local";
const MOCK_PASSWORD = "password12345";

interface MockJobState {
  job: Job;
  log: JobEventData[];
  timers: Array<ReturnType<typeof setTimeout>>;
}

interface MockState {
  session: boolean;
  email: string;
  password: string;
  weakPassword: boolean;
  servers: Server[];
  exits: Exit[];
  sites: SiteRule[];
  profile: RoutingProfile;
  revisions: RoutingRevision[];
  clients: Client[];
  incidents: Incident[];
  events: AuditEvent[];
  jobs: Map<string, MockJobState>;
  nextServerId: number;
  nextExitId: number;
  nextSiteId: number;
  nextClientId: number;
  nextRevisionId: number;
  nextJobId: number;
  nextIncidentId: number;
  nextEventId: number;
}

const presets: SitePreset[] = [
  {
    key: "anthropic",
    label: "Anthropic",
    description: "Claude, API и консоль",
    domains: ["anthropic.com", "claude.ai", "claude.com", "console.anthropic.com", "api.anthropic.com"],
    match_type: "domain",
  },
  {
    key: "openai",
    label: "OpenAI",
    description: "ChatGPT и API",
    domains: ["openai.com", "chatgpt.com", "api.openai.com", "platform.openai.com"],
    match_type: "domain",
  },
  {
    key: "google",
    label: "Google",
    description: "Gemini, Google AI",
    domains: ["gemini.google.com", "aistudio.google.com", "generativelanguage.googleapis.com"],
    match_type: "domain",
  },
  {
    key: "telegram",
    label: "Telegram",
    description: "Мессенджер и API",
    domains: ["telegram.org", "t.me", "api.telegram.org"],
    match_type: "domain",
  },
  {
    key: "github",
    label: "GitHub",
    description: "GitHub и API",
    domains: ["github.com", "api.github.com", "raw.githubusercontent.com", "objects.githubusercontent.com"],
    match_type: "domain",
  },
];

const nowIso = () => new Date().toISOString();
const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();
const daysAgo = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();

function delay(ms = 120 + Math.round(Math.random() * 180)): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function seedServers(): Server[] {
  return [
    {
      id: 1,
      name: "entry-msk-1",
      host: "203.0.113.5",
      ssh_port: 22,
      ssh_user: "root",
      auth_type: "key",
      host_fingerprint: "SHA256:mockEntryFingerprint0000000000000000000000000000",
      roles: "entry",
      region: "RU",
      status: "online",
      agent_version: "0.1.0",
      xray_version: "26.11.9",
      last_seen_at: minutesAgo(0.3),
      revision_id: 3,
      uptime_24h_pct: 99.8,
      traffic_24h: { up: 4_294_967_296, down: 18_253_611_000 },
      base_dir: "/root/config/vpn",
      created_at: daysAgo(21),
    },
    {
      id: 2,
      name: "exit-sg-1",
      host: "203.0.113.10",
      ssh_port: 22,
      ssh_user: "root",
      auth_type: "key",
      host_fingerprint: "SHA256:mockExitSgFingerprint00000000000000000000000000000",
      roles: "exit",
      region: "SG",
      status: "online",
      agent_version: "0.1.0",
      xray_version: "26.11.9",
      last_seen_at: minutesAgo(0.2),
      revision_id: 3,
      uptime_24h_pct: 100,
      traffic_24h: { up: 3_221_225_472, down: 12_884_901_888 },
      base_dir: "/root/config/vpn",
      created_at: daysAgo(18),
    },
    {
      id: 3,
      name: "exit-us-1",
      host: "203.0.113.20",
      ssh_port: 22,
      ssh_user: "root",
      auth_type: "password",
      host_fingerprint: "SHA256:mockExitUsFingerprint00000000000000000000000000000",
      roles: "exit",
      region: "US",
      status: "offline",
      agent_version: "0.1.0",
      xray_version: "26.11.9",
      last_seen_at: hoursAgo(2.4),
      revision_id: 2,
      uptime_24h_pct: 91.2,
      traffic_24h: { up: 536_870_912, down: 2_147_483_648 },
      base_dir: "/root/config/vpn",
      created_at: daysAgo(14),
      degraded_reason: "Нет heartbeat более 90 секунд",
    },
  ];
}

function seedExits(): Exit[] {
  const now = nowIso();
  return [
    {
      id: 1,
      kind: "node",
      server_id: 2,
      label: "sg-1",
      region: "SG",
      endpoint_host: "203.0.113.10",
      endpoint_port: 18443,
      protocol: "vless_reality",
      expected_exit_ip: "203.0.113.10",
      bandwidth_budget_bytes: null,
      used_bytes: 14_295_032_320,
      enabled: true,
      health: "up",
      last_check_at: now,
      last_delay_ms: 42,
      server_name: "www.apple.com",
    },
    {
      id: 2,
      kind: "node",
      server_id: 3,
      label: "us-1",
      region: "US",
      endpoint_host: "203.0.113.20",
      endpoint_port: 18443,
      protocol: "vless_reality",
      expected_exit_ip: "203.0.113.20",
      bandwidth_budget_bytes: null,
      used_bytes: 2_684_354_560,
      enabled: true,
      health: "down",
      last_check_at: minutesAgo(12),
      last_delay_ms: null,
      server_name: "www.apple.com",
    },
    {
      id: 3,
      kind: "external",
      server_id: null,
      label: "res-us",
      region: "US",
      endpoint_host: "proxy.example.net",
      endpoint_port: 8080,
      protocol: "http",
      expected_exit_ip: "198.51.100.7",
      bandwidth_budget_bytes: 214_748_364_800,
      used_bytes: 132_070_064_128,
      enabled: true,
      health: "up",
      last_check_at: minutesAgo(1),
      last_delay_ms: 118,
      sticky_session: "session-us-7",
    },
  ];
}

function seedSites(): SiteRule[] {
  return [
    {
      id: 1,
      name: "Anthropic",
      match_type: "domain",
      values: ["anthropic.com", "claude.ai", "claude.com", "console.anthropic.com", "api.anthropic.com"],
      allowed_exit_ids: [1, 2],
      preset_key: "anthropic",
      enabled: true,
      position: 10,
      created_at: daysAgo(12),
    },
    {
      id: 2,
      name: "OpenAI",
      match_type: "domain",
      values: ["openai.com", "chatgpt.com", "api.openai.com", "platform.openai.com"],
      allowed_exit_ids: [2, 3],
      preset_key: "openai",
      enabled: true,
      position: 20,
      created_at: daysAgo(11),
    },
    {
      id: 3,
      name: "Telegram",
      match_type: "domain",
      values: ["telegram.org", "t.me", "api.telegram.org"],
      allowed_exit_ids: [1],
      preset_key: "telegram",
      enabled: true,
      position: 30,
      created_at: daysAgo(10),
    },
    {
      id: 4,
      name: "GitHub",
      match_type: "domain",
      values: ["github.com", "api.github.com", "raw.githubusercontent.com"],
      allowed_exit_ids: [2, 3],
      preset_key: "github",
      enabled: false,
      position: 40,
      created_at: daysAgo(9),
    },
  ];
}

function seedRevisions(): RoutingRevision[] {
  const profile = seedProfile();
  return [
    {
      id: 1,
      created_at: daysAgo(3),
      profile_json: { ...profile, default_exit_ids: [1] },
      config_hashes_json: { "1": "a1b2c3d4e5f6", "2": "0f9e8d7c6b5a" },
      status: "applied",
      applied_at: daysAgo(3),
    },
    {
      id: 2,
      created_at: daysAgo(1),
      profile_json: {
        ...profile,
        default_exit_ids: [1, 2],
        updated_at: daysAgo(1),
      },
      config_hashes_json: { "1": "b1c2d3e4f5a6", "2": "1a2b3c4d5e6f" },
      status: "applied",
      applied_at: daysAgo(1),
    },
    {
      id: 3,
      created_at: hoursAgo(26),
      profile_json: {
        ...profile,
        default_exit_ids: [1, 3],
        updated_at: hoursAgo(26),
      },
      config_hashes_json: null,
      status: "failed",
      applied_at: null,
      error: "Узел exit-us-1 недоступен: xray -test не выполнен на entry-msk-1",
    },
  ];
}

function seedProfile(): RoutingProfile {
  return {
    default_exit_ids: [1, 2],
    enabled: true,
    updated_at: daysAgo(1),
    updated_by: MOCK_EMAIL,
  };
}

function seedClients(): Client[] {
  return [
    {
      id: 1,
      name: "iPhone",
      uuid: "8f2c1d5e-4a3b-4c6d-9e8f-0a1b2c3d4e5f",
      email_tag: "client-1@vpn",
      enabled: true,
      traffic_limit_bytes: 107_374_182_400,
      sub_token: "sub_9f8e7d6c5b4a3210",
      created_at: daysAgo(20),
      last_seen_at: minutesAgo(4),
      traffic_24h: { up: 734_003_200, down: 3_221_225_472 },
      status: "active",
      nodes: [
        { server_id: 1, name: "entry-msk-1", enabled: true },
        { server_id: 3, name: "exit-us-1", enabled: true },
      ],
    },
    {
      id: 2,
      name: "MacBook",
      uuid: "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
      email_tag: "client-2@vpn",
      enabled: true,
      traffic_limit_bytes: null,
      sub_token: "sub_1a2b3c4d5e6f7a8b",
      created_at: daysAgo(17),
      last_seen_at: minutesAgo(1),
      traffic_24h: { up: 1_073_741_824, down: 5_368_709_120 },
      status: "active",
      nodes: [{ server_id: 1, name: "entry-msk-1", enabled: true }],
    },
    {
      id: 3,
      name: "Роутер на даче",
      uuid: "9e8d7c6b-5a4f-4e3d-2c1b-0a9f8e7d6c5b",
      email_tag: "client-3@vpn",
      enabled: false,
      traffic_limit_bytes: 10_737_418_240,
      sub_token: "sub_0a9f8e7d6c5b4321",
      created_at: daysAgo(8),
      last_seen_at: daysAgo(6),
      traffic_24h: 0,
      status: "disabled",
      nodes: [{ server_id: 1, name: "entry-msk-1", enabled: false }],
    },
  ];
}

function seedIncidents(): Incident[] {
  return [
    {
      id: 1,
      severity: "critical",
      kind: "node_offline",
      subject: "exit-us-1",
      status: "open",
      details_json: { server_id: 3, last_seen_at: hoursAgo(2.4) },
      created_at: hoursAgo(2.3),
    },
    {
      id: 2,
      severity: "warning",
      kind: "exit_down",
      subject: "us-1",
      status: "open",
      details_json: { exit_id: 2, affected_sites: ["OpenAI", "GitHub"] },
      created_at: hoursAgo(2.2),
    },
    {
      id: 3,
      severity: "info",
      kind: "revision_applied",
      subject: "revision 2",
      status: "resolved",
      details_json: { revision_id: 2 },
      created_at: hoursAgo(26),
      resolved_at: hoursAgo(26),
    },
  ];
}

function seedEvents(): AuditEvent[] {
  return [
    {
      id: 1,
      ts: hoursAgo(1.2),
      kind: "routing.apply",
      actor: MOCK_EMAIL,
      ip: "192.0.2.10",
      user_agent: "Mozilla/5.0 (X11; Linux x86_64)",
      payload_json: { revision_id: 2, result: "applied" },
    },
    {
      id: 2,
      ts: daysAgo(1.1),
      kind: "auth.login",
      actor: MOCK_EMAIL,
      ip: "192.0.2.10",
      user_agent: "Mozilla/5.0 (X11; Linux x86_64)",
      payload_json: {},
    },
    {
      id: 3,
      ts: daysAgo(2),
      kind: "server.delete",
      actor: MOCK_EMAIL,
      ip: "192.0.2.10",
      user_agent: "Mozilla/5.0 (X11; Linux x86_64)",
      payload_json: { server: "exit-eu-1" },
    },
    {
      id: 4,
      ts: daysAgo(3),
      kind: "password.change",
      actor: MOCK_EMAIL,
      ip: "192.0.2.10",
      user_agent: "Mozilla/5.0 (X11; Linux x86_64)",
      payload_json: {},
    },
  ];
}

function readMockSession(): boolean {
  try {
    return window.sessionStorage.getItem("vpn-panel-mock-session") === "1";
  } catch {
    return false;
  }
}

function writeMockSession(active: boolean): void {
  try {
    if (active) window.sessionStorage.setItem("vpn-panel-mock-session", "1");
    else window.sessionStorage.removeItem("vpn-panel-mock-session");
  } catch {
    // sessionStorage может быть недоступен
  }
}

function createState(): MockState {
  return {
    session: readMockSession(),
    email: MOCK_EMAIL,
    password: MOCK_PASSWORD,
    weakPassword: true,
    servers: seedServers(),
    exits: seedExits(),
    sites: seedSites(),
    profile: seedProfile(),
    revisions: seedRevisions(),
    clients: seedClients(),
    incidents: seedIncidents(),
    events: seedEvents(),
    jobs: new Map(),
    nextServerId: 4,
    nextExitId: 4,
    nextSiteId: 5,
    nextClientId: 4,
    nextRevisionId: 4,
    nextJobId: 1,
    nextIncidentId: 4,
    nextEventId: 5,
  };
}

let state: MockState | null = null;

function getState(): MockState {
  if (!state) state = createState();
  return state;
}

/* ---------- SSE-эмуляция job'ов ---------- */

const jobSubscribers = new Map<string, Set<(event: JobStreamEvent) => void>>();

function emitJobEvent(jobId: string, event: JobStreamEvent) {
  const stateNow = getState();
  const entry = stateNow.jobs.get(jobId);
  if (entry && event.event === "progress") entry.log.push(event.data);
  const subscribers = jobSubscribers.get(jobId);
  if (!subscribers) return;
  for (const subscriber of subscribers) {
    try {
      subscriber(event);
    } catch {
      // подписчик отвалился — не мешаем остальным
    }
  }
}

export function subscribeMockJob(
  jobId: number | string,
  onEvent: (event: JobStreamEvent) => void,
): () => void {
  const key = String(jobId);
  const stateNow = getState();
  const entry = stateNow.jobs.get(key);
  if (!entry) {
    onEvent({ event: "error", data: { error: "job_not_found" } });
    return () => undefined;
  }
  for (const data of entry.log) onEvent({ event: "progress", data });
  if (entry.job.status === "succeeded") {
    onEvent({
      event: "done",
      data: { status: "succeeded", progress: 100, result: entry.job.result_json ?? undefined },
    });
    return () => undefined;
  }
  if (entry.job.status === "failed" || entry.job.status === "cancelled") {
    onEvent({
      event: "error",
      data: { status: entry.job.status, error: entry.job.error ?? "Ошибка выполнения job'а" },
    });
    return () => undefined;
  }
  const subscribers = jobSubscribers.get(key) ?? new Set();
  subscribers.add(onEvent);
  jobSubscribers.set(key, subscribers);
  return () => {
    subscribers.delete(onEvent);
  };
}

interface JobOptions {
  type: string;
  serverId?: number | null;
  steps: Array<{ step: string; message: string }>;
  failAt?: number;
  error?: string;
  result?: unknown;
  onSuccess?: () => void;
  onFail?: () => void;
}

function startJob(options: JobOptions): Job {
  const stateNow = getState();
  const id = stateNow.nextJobId;
  stateNow.nextJobId += 1;
  const key = String(id);
  const job: Job = {
    id,
    type: options.type,
    server_id: options.serverId ?? null,
    status: "queued",
    progress: 0,
    payload_json: null,
    result_json: null,
    error: null,
    created_at: nowIso(),
    started_at: null,
    finished_at: null,
  };
  const entry: MockJobState = { job, log: [], timers: [] };
  stateNow.jobs.set(key, entry);

  const totalSteps = options.steps.length;
  const perStep = 550;
  options.steps.forEach((step, index) => {
    const timer = setTimeout(() => {
      const current = entry.job;
      if (current.status === "cancelled") return;
      if (current.status === "queued") {
        current.status = "running";
        current.started_at = nowIso();
      }
      if (options.failAt === index) {
        current.status = "failed";
        current.error = options.error ?? "Шаг завершился ошибкой";
        current.progress = Math.round((index / totalSteps) * 100);
        current.finished_at = nowIso();
        emitJobEvent(key, {
          event: "progress",
          data: { step: step.step, message: step.message, level: "error", progress: current.progress },
        });
        emitJobEvent(key, {
          event: "error",
          data: { status: "failed", error: current.error, step: step.step },
        });
        options.onFail?.();
        return;
      }
      current.progress = Math.round(((index + 1) / totalSteps) * 100);
      emitJobEvent(key, {
        event: "progress",
        data: {
          step: step.step,
          message: step.message,
          level: "info",
          progress: current.progress,
          seq: index + 1,
        },
      });
      if (index === totalSteps - 1) {
        current.status = "succeeded";
        current.finished_at = nowIso();
        current.result_json = options.result ?? null;
        emitJobEvent(key, {
          event: "done",
          data: {
            status: "succeeded",
            progress: 100,
            result: options.result ?? undefined,
          },
        });
        options.onSuccess?.();
      }
    }, perStep * (index + 1));
    entry.timers.push(timer);
  });

  return job;
}

/* ---------- метрики ---------- */

function pseudoRandom(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

function buildTraffic(range: string, groupBy: "server" | "client"): TrafficResponse {
  const stateNow = getState();
  const rangeConfig: Record<string, { points: number; stepMs: number }> = {
    "24h": { points: 24, stepMs: 3_600_000 },
    "7d": { points: 56, stepMs: 3 * 3_600_000 },
    "30d": { points: 60, stepMs: 12 * 3_600_000 },
  };
  const config = rangeConfig[range] ?? rangeConfig["24h"];
  const sources =
    groupBy === "client"
      ? stateNow.clients.map((client) => ({ id: client.id, name: client.name }))
      : stateNow.servers.map((server) => ({ id: server.id, name: server.name }));
  const series = sources.map((source, sourceIndex) => {
    const base = 40_000_000 + pseudoRandom(source.id * 7 + sourceIndex) * 260_000_000;
    const points: TrafficPoint[] = [];
    for (let index = 0; index < config.points; index += 1) {
      const wave = 0.35 + 0.65 * Math.abs(Math.sin((index / config.points) * Math.PI * 2.2 + sourceIndex));
      const noise = 0.55 + pseudoRandom(source.id * 131 + index * 17 + sourceIndex) * 0.9;
      const up = Math.round(base * wave * noise * 0.32);
      const down = Math.round(base * wave * noise * 0.68);
      points.push({
        ts: new Date(Date.now() - (config.points - 1 - index) * config.stepMs).toISOString(),
        up,
        down,
      });
    }
    return { key: String(source.id), name: source.name, points };
  });
  return { series, range, group_by: groupBy };
}

function buildOverview(): MetricsOverview {
  const stateNow = getState();
  const online = stateNow.servers.filter((server) => server.status === "online").length;
  const traffic = buildTraffic("24h", "server");
  const totalTraffic = (traffic.series ?? []).reduce((sum, series) => {
    return (
      sum +
      (series.points ?? []).reduce(
        (pointSum, point) => pointSum + (point.up ?? 0) + (point.down ?? 0),
        0,
      )
    );
  }, 0);
  const enabledExits = stateNow.exits.filter((exit) => exit.enabled);
  const healthyDefaults = stateNow.profile.default_exit_ids.filter((id) => {
    const exit = enabledExits.find((candidate) => candidate.id === id);
    return exit?.health === "up";
  });
  return {
    nodes: {
      total: stateNow.servers.length,
      online,
      degraded: stateNow.servers.filter((server) => server.status === "degraded").length,
    },
    nodes_online: online,
    nodes_total: stateNow.servers.length,
    traffic_today_bytes: totalTraffic,
    active_connections: 137,
    errors_last_hour: 18,
    errors_1h: 18,
    open_incidents: stateNow.incidents.filter((incident) => incident.status === "open").length,
    routing_enabled: stateNow.profile.enabled && healthyDefaults.length > 0,
    uptime_24h_pct: 99.4,
    routing: {
      enabled: stateNow.profile.enabled,
      configured: stateNow.profile.default_exit_ids.length > 0,
      healthy: healthyDefaults.length > 0,
      default_exits_healthy: healthyDefaults.length,
    },
    generated_at: nowIso(),
  };
}

/* ---------- helpers ---------- */

function notFound(subject: string): never {
  throw new ApiError(`${subject} не найден`, 404, "not_found");
}

function getServer(id: number): Server {
  const server = getState().servers.find((candidate) => candidate.id === id);
  if (!server) notFound("Сервер");
  return server;
}

function getExit(id: number): Exit {
  const exit = getState().exits.find((candidate) => candidate.id === id);
  if (!exit) notFound("Выход");
  return exit;
}

function getSite(id: number): SiteRule {
  const site = getState().sites.find((candidate) => candidate.id === id);
  if (!site) notFound("Правило");
  return site;
}

function getClient(id: number): Client {
  const client = getState().clients.find((candidate) => candidate.id === id);
  if (!client) notFound("Клиент");
  return client;
}

function validateSitePayload(payload: {
  values?: string[];
  allowed_exit_ids?: number[];
}): void {
  if (payload.values && payload.values.some((value) => /(^|\.)ru$/i.test(value) || /^ru$/i.test(value))) {
    throw new ApiError(
      "Российские домены не проксируются: правило для RU-доменов запрещено (ru_direct_locked)",
      422,
      "ru_direct_locked",
    );
  }
  if (payload.allowed_exit_ids && payload.allowed_exit_ids.length === 0) {
    throw new ApiError(
      "Fail-closed: укажите хотя бы один разрешённый выход, иначе правило будет блокировать трафик",
      422,
      "empty_allowlist",
    );
  }
}

function requireSession(): void {
  if (!getState().session) {
    throw new ApiError("Требуется вход в панель", 401, "unauthorized");
  }
}

function roleMatches(server: Server, role: ServerRole): boolean {
  return server.roles === role || server.roles === "both";
}

function buildVlessLink(client: Client, serverId: number): string {
  const server = getServer(serverId);
  const params = new URLSearchParams({
    encryption: "none",
    security: "reality",
    pbk: "MOCK_REALITY_PUBLIC_KEY_0000000000000000000000000000",
    sid: "a1b2c3d4",
    fp: "chrome",
    sni: "www.microsoft.com",
    spx: "/",
    flow: "xtls-rprx-vision",
    type: "tcp",
  });
  const host = server.host.includes(":") ? `[${server.host}]` : server.host;
  return `vless://${client.uuid}@${host}:443?${params.toString()}#${encodeURIComponent(server.name)}`;
}

/* ---------- маршрутизация mock-запросов ---------- */

export async function mockRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  await delay();
  const stateNow = getState();
  const method = options.method ?? "GET";
  const body = (options.body ?? {}) as Record<string, unknown>;
  const query = options.query ?? {};

  if (path === "/auth/login" && method === "POST") {
    const email = String(body.email ?? "").trim();
    const password = String(body.password ?? "");
    if (!email || !password) {
      throw new ApiError("Введите email и пароль", 400, "invalid_credentials");
    }
    if (email !== stateNow.email || password !== stateNow.password) {
      throw new ApiError("Неверный email или пароль", 401, "invalid_credentials");
    }
    stateNow.session = true;
    writeMockSession(true);
    stateNow.events.unshift({
      id: stateNow.nextEventId++,
      ts: nowIso(),
      kind: "auth.login",
      actor: email,
      ip: "127.0.0.1",
      user_agent: navigator.userAgent,
      payload_json: {},
    });
    return { user: { email }, csrf_token: "mock-csrf-token" } as T;
  }

  if (path === "/auth/logout" && method === "POST") {
    stateNow.session = false;
    writeMockSession(false);
    return undefined as T;
  }

  if (path === "/auth/me" && method === "GET") {
    requireSession();
    const me: UserMe = {
      email: stateNow.email,
      csrf_token: "mock-csrf-token",
      weak_password: stateNow.weakPassword,
    };
    return me as T;
  }

  if (path === "/auth/password" && method === "POST") {
    requireSession();
    const current = String(body.current_password ?? "");
    const next = String(body.new_password ?? "");
    if (current !== stateNow.password) {
      throw new ApiError("Текущий пароль неверен", 422, "invalid_password");
    }
    if (next.length < 12) {
      throw new ApiError("Новый пароль должен быть не короче 12 символов", 422, "weak_password");
    }
    stateNow.password = next;
    stateNow.weakPassword = false;
    return undefined as T;
  }

  requireSession();

  if (path === "/servers" && method === "GET") return stateNow.servers as T;
  if (path === "/servers/preflight" && method === "POST") {
    const host = String(body.host ?? "").trim();
    if (!host) throw new ApiError("Укажите host", 400, "invalid_host");
    if (host.includes("fail") || host.includes("unreachable")) {
      throw new ApiError(
        "Хост недоступен по SSH: проверьте адрес, порт и доступность сервера",
        422,
        "host_unreachable",
      );
    }
    if (String(body.password ?? "").includes("wrong")) {
      throw new ApiError("Ошибка аутентификации SSH: неверный пароль или ключ", 422, "auth_failed");
    }
    return {
      ok: true,
      os: "Ubuntu 24.04.1 LTS",
      arch: "x86_64",
      sudo: true,
      port_443_free: !host.includes("busy"),
      existing_install: false,
      host_fingerprint: "SHA256:mockHostFingerprint000000000000000000000000000000",
      warnings: host.includes("busy")
        ? ["Порт 443 уже занят другим сервисом — провижининг может не запуститься"]
        : [],
    } as T;
  }
  if (path === "/servers" && method === "POST") {
    const host = String(body.host ?? "").trim();
    if (!host) throw new ApiError("Укажите host", 400, "invalid_host");
    const name = String(body.name ?? host).trim();
    if (stateNow.servers.some((server) => server.name === name)) {
      throw new ApiError("Сервер с таким именем уже существует", 422, "name_taken");
    }
    const roles = (body.roles as ServerRole) ?? "entry";
    const id = stateNow.nextServerId++;
    const server: Server = {
      id,
      name,
      host,
      ssh_port: Number(body.port ?? 22),
      ssh_user: String(body.user ?? "root"),
      auth_type: (body.auth_type as "password" | "key") ?? "key",
      host_fingerprint: "SHA256:mockHostFingerprint000000000000000000000000000000",
      roles,
      region: String(body.region ?? "RU"),
      status: "provisioning",
      agent_version: null,
      xray_version: null,
      last_seen_at: null,
      revision_id: null,
      uptime_24h_pct: null,
      traffic_24h: 0,
      base_dir: "/root/config/vpn",
      created_at: nowIso(),
    };
    stateNow.servers.push(server);
    const job = startJob({
      type: "provision_server",
      serverId: id,
      steps: [
        { step: "ssh", message: "Проверка SSH-доступа" },
        { step: "dirs", message: "Создание каталогов ~/config/vpn" },
        { step: "xray", message: "Загрузка Xray 26.11.9 и проверка sha256" },
        { step: "agent", message: "Установка vpn-agent и юнитов systemd" },
        { step: "firewall", message: "Настройка firewall (nft/ufw)" },
        { step: "start", message: "Запуск сервисов и проверка is-active" },
        { step: "register", message: "Регистрация узла в панели" },
      ],
      result: { server_id: id, status: "online" },
      onSuccess: () => {
        server.status = "online";
        server.agent_version = "0.1.0";
        server.xray_version = "26.11.9";
        server.last_seen_at = nowIso();
        server.revision_id = 1;
        server.uptime_24h_pct = 100;
      },
    });
    return { server_id: id, job_id: job.id } as T;
  }

  let match: RegExpExecArray | null = null;
  if ((match = /^\/servers\/(\d+)$/.exec(path))) {
    const serverId = Number(match[1]);
    if (method === "GET") return getServer(serverId) as T;
    if (method === "DELETE") {
      const server = getServer(serverId);
      const force = body.force === true || query.force === true || query.force === "true";
      if (force) {
        stateNow.servers = stateNow.servers.filter((candidate) => candidate.id !== serverId);
        stateNow.exits = stateNow.exits.filter((exit) => exit.server_id !== serverId);
        stateNow.sites = stateNow.sites.map((site) => ({
          ...site,
          allowed_exit_ids: site.allowed_exit_ids.filter((id) =>
            stateNow.exits.some((exit) => exit.id === id),
          ),
        }));
        return { deleted: true, force: true } as T;
      }
      if (body.confirm_name !== server.name) {
        throw new ApiError(
          "Имя сервера не совпадает. Введите имя точно, чтобы подтвердить удаление",
          422,
          "confirm_name_mismatch",
        );
      }
      server.status = "delete_pending";
      const job = startJob({
        type: "uninstall_server",
        serverId,
        steps: [
          { step: "stop", message: "Остановка и отключение юнитов vpn-agent и xray" },
          { step: "remove", message: "Удаление каталога ~/config/vpn" },
          { step: "firewall", message: "Снятие правил firewall" },
          { step: "verify", message: "Проверка: каталог удалён, юниты не загружены" },
          { step: "record", message: "Удаление записи сервера и отзыв токена агента" },
        ],
        result: { server_id: serverId, removed: true },
        onSuccess: () => {
          stateNow.servers = stateNow.servers.filter((candidate) => candidate.id !== serverId);
          stateNow.exits = stateNow.exits.filter((exit) => exit.server_id !== serverId);
          stateNow.profile = {
            ...stateNow.profile,
            default_exit_ids: stateNow.profile.default_exit_ids.filter((id) =>
              stateNow.exits.some((exit) => exit.id === id),
            ),
          };
        },
      });
      return { job_id: job.id } as T;
    }
  }

  if ((match = /^\/servers\/(\d+)\/actions$/.exec(path)) && method === "POST") {
    const serverId = Number(match[1]);
    const server = getServer(serverId);
    const action = String(body.action ?? "");
    const actionConfig: Record<string, { steps: Array<{ step: string; message: string }>; result?: unknown }> = {
      reapply: {
        steps: [
          { step: "compile", message: "Компиляция конфигурации Xray" },
          { step: "validate", message: "Валидация xray -test на узле" },
          { step: "swap", message: "Атомарная замена конфига" },
          { step: "restart", message: "Перезапуск xray.service" },
          { step: "health", message: "Проверка health и ревизии" },
        ],
        result: { reapply: true },
      },
      restart: {
        steps: [
          { step: "stop", message: "Остановка сервисов" },
          { step: "start", message: "Запуск сервисов" },
          { step: "health", message: "Проверка is-active" },
        ],
        result: { restarted: true },
      },
      reinstall: {
        steps: [
          { step: "uninstall", message: "Удаление прежней установки" },
          { step: "dirs", message: "Создание каталогов" },
          { step: "xray", message: "Загрузка Xray" },
          { step: "agent", message: "Установка агента" },
          { step: "start", message: "Запуск сервисов" },
        ],
        result: { reinstalled: true },
      },
      diagnose: {
        steps: [
          { step: "ssh", message: "Сбор состояния SSH" },
          { step: "units", message: "Проверка systemd-юнитов" },
          { step: "xray", message: "Проверка xray -test и логов" },
          { step: "report", message: "Формирование диагностического отчёта" },
        ],
        result: { report: "ok", issues: [] },
      },
    };
    const config = actionConfig[action];
    if (!config) throw new ApiError("Неизвестное действие", 422, "unknown_action");
    const job = startJob({
      type: `server_${action}`,
      serverId,
      steps: config.steps,
      result: config.result,
      onSuccess: () => {
        if (action === "reapply") {
          server.revision_id = (server.revision_id ?? 1) + 1;
          server.status = "online";
        }
      },
    });
    return { job_id: job.id } as T;
  }

  if (path === "/exits" && method === "GET") {
    return stateNow.exits.map((exit) => ({
      ...exit,
      server_name: exit.server_id
        ? stateNow.servers.find((server) => server.id === exit.server_id)?.name ?? null
        : null,
      used_by_sites: stateNow.sites
        .filter((site) => site.allowed_exit_ids.includes(exit.id))
        .map((site) => site.name),
    })) as T;
  }
  if (path === "/exits" && method === "POST") {
    const label = String(body.label ?? "").trim();
    if (!label) throw new ApiError("Укажите метку выхода", 400, "invalid_label");
    if (stateNow.exits.some((exit) => exit.label === label)) {
      throw new ApiError("Выход с такой меткой уже существует", 422, "label_taken");
    }
    const exit: Exit = {
      id: stateNow.nextExitId++,
      kind: (body.kind as Exit["kind"]) ?? "external",
      server_id: (body.server_id as number | null) ?? null,
      label,
      region: String(body.region ?? "US").toUpperCase(),
      endpoint_host: String(body.endpoint_host ?? "proxy.example.net"),
      endpoint_port: Number(body.endpoint_port ?? 1080),
      protocol: (body.protocol as Exit["protocol"]) ?? "http",
      expected_exit_ip: (body.expected_exit_ip as string | undefined) ?? null,
      bandwidth_budget_bytes: (body.bandwidth_budget_bytes as number | null | undefined) ?? null,
      used_bytes: 0,
      enabled: Boolean(body.enabled ?? true),
      health: "unknown",
      last_check_at: null,
      last_delay_ms: null,
      sticky_session: (body.sticky_session as string | null | undefined) ?? null,
    };
    stateNow.exits.push(exit);
    return exit as T;
  }
  if ((match = /^\/exits\/(\d+)$/.exec(path))) {
    const exitId = Number(match[1]);
    if (method === "PATCH") {
      const exit = getExit(exitId);
      if (typeof body.label === "string") exit.label = body.label;
      if (typeof body.region === "string") exit.region = body.region.toUpperCase();
      if (typeof body.endpoint_host === "string") exit.endpoint_host = body.endpoint_host;
      if (body.endpoint_port !== undefined) exit.endpoint_port = Number(body.endpoint_port);
      if (typeof body.protocol === "string") exit.protocol = body.protocol as Exit["protocol"];
      if (body.enabled !== undefined) exit.enabled = Boolean(body.enabled);
      if (body.expected_exit_ip !== undefined) exit.expected_exit_ip = String(body.expected_exit_ip);
      if (body.bandwidth_budget_bytes !== undefined) {
        exit.bandwidth_budget_bytes = body.bandwidth_budget_bytes as number | null;
      }
      if (body.sticky_session !== undefined) exit.sticky_session = body.sticky_session as string | null;
      return exit as T;
    }
    if (method === "DELETE") {
      getExit(exitId);
      stateNow.exits = stateNow.exits.filter((exit) => exit.id !== exitId);
      stateNow.sites = stateNow.sites.map((site) => ({
        ...site,
        allowed_exit_ids: site.allowed_exit_ids.filter((id) => id !== exitId),
      }));
      return undefined as T;
    }
  }
  if ((match = /^\/exits\/(\d+)\/check$/.exec(path)) && method === "POST") {
    const exit = getExit(Number(match[1]));
    exit.health = exit.health === "up" ? "up" : "down";
    exit.last_check_at = nowIso();
    exit.last_delay_ms = exit.health === "up" ? 40 + Math.round(Math.random() * 120) : null;
    return {
      health: exit.health,
      last_delay_ms: exit.last_delay_ms,
      actual_exit_ip: exit.expected_exit_ip,
      expected_exit_ip: exit.expected_exit_ip,
    } as T;
  }

  if (path === "/sites/presets" && method === "GET") return presets as T;
  if (path === "/sites" && method === "GET") return stateNow.sites as T;
  if (path === "/sites" && method === "POST") {
    const values = (body.values as string[]) ?? [];
    const allowed = (body.allowed_exit_ids as number[]) ?? [];
    validateSitePayload({ values, allowed_exit_ids: allowed });
    const site: SiteRule = {
      id: stateNow.nextSiteId++,
      name: String(body.name ?? "Правило"),
      match_type: (body.match_type as SiteRule["match_type"]) ?? "domain",
      values,
      allowed_exit_ids: allowed,
      preset_key: (body.preset_key as string | null | undefined) ?? null,
      enabled: Boolean(body.enabled ?? true),
      position: (stateNow.sites.at(-1)?.position ?? 0) + 10,
      created_at: nowIso(),
    };
    stateNow.sites.push(site);
    return site as T;
  }
  if ((match = /^\/sites\/(\d+)$/.exec(path))) {
    const siteId = Number(match[1]);
    if (method === "PATCH") {
      const site = getSite(siteId);
      if (body.values !== undefined || body.allowed_exit_ids !== undefined) {
        validateSitePayload({
          values: (body.values as string[] | undefined) ?? site.values,
          allowed_exit_ids: (body.allowed_exit_ids as number[] | undefined) ?? site.allowed_exit_ids,
        });
      }
      if (typeof body.name === "string") site.name = body.name;
      if (typeof body.match_type === "string") site.match_type = body.match_type as SiteRule["match_type"];
      if (Array.isArray(body.values)) site.values = body.values as string[];
      if (Array.isArray(body.allowed_exit_ids)) site.allowed_exit_ids = body.allowed_exit_ids as number[];
      if (body.enabled !== undefined) site.enabled = Boolean(body.enabled);
      return site as T;
    }
    if (method === "DELETE") {
      getSite(siteId);
      stateNow.sites = stateNow.sites.filter((site) => site.id !== siteId);
      return undefined as T;
    }
  }

  if (path === "/routing/profile" && method === "GET") return buildMockProfile() as T;
  if (path === "/routing/profile" && method === "PUT") {
    const defaultExitIds = (body.default_exit_ids as number[]) ?? [];
    const enabled = Boolean(body.enabled ?? true);
    if (enabled && defaultExitIds.length === 0) {
      throw new ApiError(
        "Нельзя включить маршрутизацию без выхода по умолчанию — fail-closed",
        422,
        "default_exit_required",
      );
    }
    stateNow.profile = {
      default_exit_ids: defaultExitIds,
      enabled,
      updated_at: nowIso(),
      updated_by: stateNow.email,
    };
    return buildMockProfile() as T;
  }
  if (path === "/routing/apply" && method === "POST") {
    if (stateNow.profile.enabled && stateNow.profile.default_exit_ids.length === 0) {
      throw new ApiError("Профиль невалиден: не выбран выход по умолчанию", 422, "invalid_profile");
    }
    const revision: RoutingRevision = {
      id: stateNow.nextRevisionId++,
      created_at: nowIso(),
      profile_json: { ...stateNow.profile, site_rules: stateNow.sites },
      config_hashes_json: {
        "1": `${stateNow.nextRevisionId}a1b2c3d4`,
        "2": `${stateNow.nextRevisionId}e5f6a7b8`,
      },
      status: "pending",
      applied_at: null,
    };
    stateNow.revisions.unshift(revision);
    const job = startJob({
      type: "routing_apply",
      steps: [
        { step: "compile", message: "Компиляция профиля маршрутизации" },
        { step: "validate", message: "Валидация конфигурации xray -test на entry-узлах" },
        { step: "apply", message: "Атомарное применение ревизии" },
        { step: "health", message: "Проверка health и балансировщиков" },
      ],
      result: { revision_id: revision.id },
      onSuccess: () => {
        revision.status = "applied";
        revision.applied_at = nowIso();
        stateNow.servers = stateNow.servers.map((server) =>
          roleMatches(server, "entry") ? { ...server, revision_id: revision.id } : server,
        );
      },
      onFail: () => {
        revision.status = "failed";
        revision.error = "Не удалось применить ревизию";
      },
    });
    return { revision_id: revision.id, job_id: job.id } as T;
  }
  if (path === "/routing/check" && method === "POST") {
    const domain = String(body.domain ?? "").trim().toLowerCase();
    if (!domain) throw new ApiError("Введите домен", 400, "invalid_domain");
    if (/(^|\.)ru$/i.test(domain) || domain === "ru") {
      return {
        kind: "ru_direct",
        rule_id: null,
        exit_ids: [],
        compiled_rule: { type: "field", domain: ["geosite:category-ru"], outboundTag: "direct" },
        explanation: "Российский домен: прямой выход (direct), не проксируется — правило заблокировано в конфиге",
      } as T;
    }
    const matchingRule = stateNow.sites.find((site) => {
      if (!site.enabled) return false;
      return site.values.some((value) => {
        const normalized = value.toLowerCase();
        if (site.match_type === "wildcard") return domain.endsWith(normalized.replace(/^\*\./, ""));
        if (site.match_type === "geosite") return false;
        return domain === normalized || domain.endsWith(`.${normalized}`);
      });
    });
    if (matchingRule) {
      const exitIds = matchingRule.allowed_exit_ids;
      return {
        kind: "site_rule",
        rule_id: matchingRule.id,
        exit_ids: exitIds,
        compiled_rule: {
          type: "field",
          domain: matchingRule.values,
          balancerTag: `bal-rule-${matchingRule.id}`,
        },
        explanation: `Правило «${matchingRule.name}»: только выходы ${exitIds
          .map((id) => stateNow.exits.find((exit) => exit.id === id)?.label ?? id)
          .join(", ")}; при недоступности — block (fail-closed)`,
      } as T;
    }
    if (stateNow.profile.enabled && stateNow.profile.default_exit_ids.length > 0) {
      return {
        kind: "default",
        rule_id: null,
        exit_ids: stateNow.profile.default_exit_ids,
        compiled_rule: { type: "field", network: "tcp,udp", balancerTag: "bal-default" },
        explanation: "Нет совпадений: трафик пойдёт через выход по умолчанию",
      } as T;
    }
    return {
      kind: "block",
      rule_id: null,
      exit_ids: [],
      compiled_rule: { type: "field", network: "tcp,udp", outboundTag: "block" },
      explanation: "Маршрутизация не настроена: трафик блокируется",
    } as T;
  }
  if (path === "/routing/revisions" && method === "GET") return stateNow.revisions as T;
  if ((match = /^\/routing\/revisions\/(\d+)$/.exec(path)) && method === "GET") {
    const revision = stateNow.revisions.find((candidate) => candidate.id === Number(match?.[1]));
    if (!revision) notFound("Ревизия");
    return revision as T;
  }

  if (path === "/clients" && method === "GET") {
    return stateNow.clients.map((client) => ({
      ...client,
      traffic_bytes: normalizeMockTraffic(client.traffic_24h),
    })) as T;
  }
  if (path === "/clients" && method === "POST") {
    const name = String(body.name ?? "").trim();
    if (!name) throw new ApiError("Укажите имя клиента", 400, "invalid_name");
    if (stateNow.clients.some((client) => client.name === name)) {
      throw new ApiError("Клиент с таким именем уже существует", 422, "name_taken");
    }
    const id = stateNow.nextClientId++;
    const client: Client = {
      id,
      name,
      uuid: `${id.toString(16).padStart(8, "0")}-1111-4222-8333-${String(Date.now()).slice(-12)}`,
      email_tag: `client-${id}@vpn`,
      enabled: Boolean(body.enabled ?? true),
      traffic_limit_bytes: (body.traffic_limit_bytes as number | null | undefined) ?? null,
      sub_token: `sub_${Math.random().toString(16).slice(2, 18)}`,
      created_at: nowIso(),
      last_seen_at: null,
      traffic_24h: 0,
      status: body.enabled === false ? "disabled" : "active",
      nodes: stateNow.servers
        .filter((server) => roleMatches(server, "entry"))
        .map((server) => ({ server_id: server.id, name: server.name, enabled: true })),
    };
    stateNow.clients.push(client);
    return client as T;
  }
  if ((match = /^\/clients\/(\d+)$/.exec(path))) {
    const clientId = Number(match[1]);
    if (method === "GET") return getClient(clientId) as T;
    if (method === "PATCH") {
      const client = getClient(clientId);
      if (typeof body.name === "string" && body.name.trim()) client.name = body.name.trim();
      if (body.enabled !== undefined) {
        client.enabled = Boolean(body.enabled);
        client.status = client.enabled ? "active" : "disabled";
        client.nodes = client.nodes?.map((node) => ({ ...node, enabled: client.enabled }));
      }
      if (body.traffic_limit_bytes !== undefined) {
        client.traffic_limit_bytes = body.traffic_limit_bytes as number | null;
      }
      return client as T;
    }
    if (method === "DELETE") {
      getClient(clientId);
      stateNow.clients = stateNow.clients.filter((client) => client.id !== clientId);
      return undefined as T;
    }
  }
  if ((match = /^\/clients\/(\d+)\/link$/.exec(path)) && method === "GET") {
    const client = getClient(Number(match[1]));
    const serverId = Number(query.server_id);
    const server = getServer(serverId);
    if (!roleMatches(server, "entry")) {
      throw new ApiError("Узел не имеет роли entry — выдайте клиенту entry-узел", 422, "not_entry_server");
    }
    return { url: buildVlessLink(client, serverId) } as T;
  }
  if ((match = /^\/clients\/(\d+)\/profile$/.exec(path)) && method === "GET") {
    const client = getClient(Number(match[1]));
    const mode = String(query.mode ?? "smart");
    if (mode === "vless") {
      const entry = stateNow.servers.find((server) => roleMatches(server, "entry"));
      if (!entry) throw new ApiError("Нет entry-узлов", 422, "no_entry_server");
      return { mode: "vless", url: buildVlessLink(client, entry.id) } as T;
    }
    return {
      mode: "smart",
      outbounds: [
        {
          type: "vless",
          tag: "proxy",
          server: stateNow.servers.find((server) => roleMatches(server, "entry"))?.host ?? "203.0.113.5",
          server_port: 443,
          uuid: client.uuid,
          flow: "xtls-rprx-vision",
          tls: {
            enabled: true,
            server_name: "www.microsoft.com",
            utls: { enabled: true, fingerprint: "chrome" },
            reality: { enabled: true, public_key: "MOCK_REALITY_PUBLIC_KEY", short_id: "a1b2c3d4" },
          },
        },
        { type: "direct", tag: "direct" },
      ],
      route: {
        rules: [{ rule_set: ["geosite-category-ru", "geoip-ru"], outbound: "direct" }],
        final: "proxy",
      },
    } as T;
  }
  if ((match = /^\/clients\/(\d+)\/stats$/.exec(path)) && method === "GET") {
    const client = getClient(Number(match[1]));
    const traffic24 = typeof client.traffic_24h === "object" ? client.traffic_24h : null;
    const up = traffic24 && "up" in traffic24 ? Number(traffic24.up ?? 0) : 0;
    const down = traffic24 && "down" in traffic24 ? Number(traffic24.down ?? 0) : 0;
    const total = up + down;
    const stats: ClientStats = {
      client_id: client.id,
      traffic: { "24h": total, "7d": total * 5, "30d": total * 18 },
      traffic_24h: { up_bytes: up, down_bytes: down, total_bytes: total },
      traffic_7d: { up_bytes: up * 5, down_bytes: down * 5, total_bytes: total * 5 },
      traffic_30d: { up_bytes: up * 18, down_bytes: down * 18, total_bytes: total * 18 },
      total_bytes: total * 18,
      traffic_limit_bytes: client.traffic_limit_bytes,
      last_seen_at: client.last_seen_at ?? null,
      status: client.enabled ? "active" : "disabled",
    };
    return stats as T;
  }

  if (path === "/metrics/overview" && method === "GET") return buildOverview() as T;
  if (path === "/metrics/traffic" && method === "GET") {
    const range = String(query.range ?? "24h");
    const groupBy = (String(query.group_by ?? "server") as "server" | "client") ?? "server";
    return buildTraffic(range, groupBy) as T;
  }

  if (path === "/jobs" && method === "GET") {
    const list = Array.from(stateNow.jobs.values())
      .map((entry) => entry.job)
      .filter(
        (job) =>
          query.server_id === undefined || job.server_id === Number(query.server_id),
      )
      .sort((a, b) => b.id - a.id);
    return list as T;
  }
  if ((match = /^\/jobs\/(\d+)$/.exec(path)) && method === "GET") {
    const entry = stateNow.jobs.get(String(Number(match[1])));
    if (!entry) notFound("Job");
    return entry.job as T;
  }

  if (path === "/incidents" && method === "GET") return stateNow.incidents as T;
  if ((match = /^\/incidents\/(\d+)\/(ack|resolve)$/.exec(path)) && method === "POST") {
    const incident = stateNow.incidents.find((candidate) => candidate.id === Number(match?.[1]));
    if (!incident) notFound("Инцидент");
    if (match[2] === "ack") {
      incident.status = "ack";
      incident.acked_at = nowIso();
    } else {
      incident.status = "resolved";
      incident.resolved_at = nowIso();
    }
    return incident as T;
  }

  if (path === "/events" && method === "GET") return stateNow.events as T;

  throw new ApiError(`Мок-API: маршрут ${method} ${path} не реализован`, 404, "mock_not_found");
}

export function mockRaw(path: string, options: RequestOptions = {}): Response {
  const match = /^\/clients\/(\d+)\/qr\.svg$/.exec(path);
  if (!match) {
    throw new ApiError("Мок-API: ресурс не найден", 404, "mock_not_found");
  }
  const stateNow = getState();
  const client = getClient(Number(match[1]));
  const mode = String(options.query?.mode ?? "vless");
  const entry = stateNow.servers.find((server) => roleMatches(server, "entry"));
  if (!entry) throw new ApiError("Нет entry-узла", 409, "entry_unavailable");
  const value =
    mode === "smart"
      ? `${window.location.origin}/sub/${client.sub_token ?? "mock-sub-token"}`
      : buildVlessLink(client, entry.id);
  const escaped = value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240"><rect width="240" height="240" fill="#ffffff"/><rect x="12" y="12" width="216" height="216" fill="none" stroke="#000000" stroke-width="4"/><text x="120" y="110" font-size="14" text-anchor="middle" fill="#000000">MOCK QR</text><text x="120" y="132" font-size="12" text-anchor="middle" fill="#000000">${mode.toUpperCase()}</text><text x="120" y="154" font-size="9" text-anchor="middle" fill="#555555">${escaped.slice(0, 48)}</text></svg>`;
  return new Response(svg, { status: 200, headers: { "content-type": "image/svg+xml" } });
}

function normalizeMockTraffic(value: unknown): number {
  if (typeof value === "number") return value;
  if (value && typeof value === "object") {
    const item = value as { up?: number; down?: number; up_bytes?: number; down_bytes?: number };
    return Number(item.up ?? item.up_bytes ?? 0) + Number(item.down ?? item.down_bytes ?? 0);
  }
  return 0;
}

function buildMockProfile() {
  const stateNow = getState();
  const enabledExits = stateNow.exits.filter((exit) => exit.enabled);
  const defaultExits = stateNow.profile.default_exit_ids
    .map((id) => enabledExits.find((exit) => exit.id === id))
    .filter((exit): exit is Exit => Boolean(exit));
  const issues: Array<{ code: string; message: string; subject: string | null }> = [];
  if (stateNow.profile.enabled && stateNow.profile.default_exit_ids.length === 0) {
    issues.push({ code: "default_exit_missing", message: "Не выбран выход по умолчанию", subject: null });
  }
  if (stateNow.profile.enabled && defaultExits.filter((exit) => exit.health === "up").length === 0) {
    issues.push({ code: "no_healthy_exits", message: "Нет ни одного доступного выхода", subject: null });
  }
  return {
    enabled: stateNow.profile.enabled,
    default_exit_ids: stateNow.profile.default_exit_ids,
    issues,
    ready: stateNow.profile.enabled && issues.length === 0,
    exits: stateNow.exits.map((exit) => ({
      id: exit.id,
      label: exit.label,
      region: exit.region,
      kind: exit.kind,
      enabled: exit.enabled,
      health: exit.health,
    })),
    sites: stateNow.sites.map((site) => ({
      id: site.id,
      name: site.name,
      allowed_exit_ids: site.allowed_exit_ids,
      enabled: site.enabled,
    })),
    updated_at: stateNow.profile.updated_at ?? null,
  };
}

export function mockHealth(): { status: string; db: string; version: string } {
  return { status: "ok", db: "ok", version: "mock-0.1.0" };
}

export type { JobStatus, MetricsOverview, RoutingProfile, Server, SiteRule };
