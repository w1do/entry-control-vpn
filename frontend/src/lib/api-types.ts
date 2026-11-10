// Типы ответов API панели (docs/INTERFACES.md §2.3).
// Ответы backend'а нормализуются в UI-слое; здесь — контракт, на который опирается SPA.

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
  };
}

export interface UserMe {
  email: string;
  csrf_token: string;
  weak_password: boolean;
}

export interface LoginResponse {
  user: { email: string };
  csrf_token: string;
}

export type ServerRole = "entry" | "exit" | "both";

export type ServerStatus =
  | "new"
  | "provisioning"
  | "online"
  | "degraded"
  | "offline"
  | "delete_pending"
  | "failed";

export type AuthType = "password" | "key";

/** Трафик в ответах сервера может быть числом (всего) или объектом. */
export type TrafficField =
  | number
  | string
  | null
  | {
      up?: number;
      down?: number;
      total?: number;
      up_bytes?: number;
      down_bytes?: number;
      total_bytes?: number;
      uplink?: number;
      downlink?: number;
    };

export interface Server {
  id: number;
  name: string;
  host: string;
  roles: ServerRole;
  region: string;
  status: ServerStatus;
  ssh_port?: number;
  ssh_user?: string;
  auth_type?: AuthType;
  host_fingerprint?: string | null;
  agent_version?: string | null;
  xray_version?: string | null;
  last_seen_at?: string | null;
  revision_id?: number | null;
  uptime_24h_pct?: number | null;
  traffic_24h?: TrafficField;
  base_dir?: string | null;
  created_at?: string | null;
  degraded_reason?: string | null;
}

export interface PreflightRequest {
  host: string;
  port: number;
  user: string;
  auth_type: AuthType;
  password?: string;
  private_key?: string;
}

export interface PreflightResult {
  ok: boolean;
  os?: string | null;
  arch?: string | null;
  sudo?: boolean;
  port_443_free?: boolean;
  existing_install?: boolean;
  host_fingerprint?: string | null;
  warnings?: string[];
}

export interface ServerCreateRequest {
  name: string;
  host: string;
  port: number;
  user: string;
  auth_type: AuthType;
  password?: string;
  private_key?: string;
  roles: ServerRole;
  region: string;
}

export interface ServerCreateResponse {
  server_id: number;
  job_id: number;
}

export type ServerAction = "reapply" | "restart" | "reinstall" | "diagnose";

export interface JobCreatedResponse {
  job_id: number;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: number;
  type: string;
  server_id?: number | null;
  status: JobStatus;
  progress: number;
  payload_json?: unknown;
  result_json?: unknown;
  /** GET /jobs/{id} отдаёт result (job_to_dict). */
  result?: unknown;
  error?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  events?: JobEventData[];
}

export type JobEventLevel = "debug" | "info" | "warning" | "error";

export interface JobEventData {
  seq?: number;
  code?: string;
  status?: JobStatus;
  progress?: number;
  level?: JobEventLevel;
  step?: string;
  message?: string;
  data?: unknown;
  result?: unknown;
  error?: string | null;
}

export type ExitKind = "node" | "external";
export type ExitProtocol = "vless_reality" | "http" | "socks5";
export type ExitHealth = "up" | "down" | "unknown";

export interface Exit {
  id: number;
  kind: ExitKind;
  server_id: number | null;
  label: string;
  region: string;
  endpoint_host: string;
  endpoint_port: number;
  protocol: ExitProtocol;
  expected_exit_ip?: string | null;
  bandwidth_budget_bytes?: number | null;
  used_bytes?: number | null;
  enabled: boolean;
  health: ExitHealth;
  last_check_at?: string | null;
  last_delay_ms?: number | null;
  sticky_session?: string | null;
  username?: string | null;
  /** Имя сервера для node-выхода. */
  server_name?: string | null;
  /** Названия правил, где выход разрешён (GET /exits). */
  used_by_sites?: string[];
}

export interface ExitCreateRequest {
  kind: ExitKind;
  server_id?: number | null;
  label: string;
  region: string;
  endpoint_host?: string;
  endpoint_port?: number;
  protocol?: ExitProtocol;
  username?: string;
  password?: string;
  expected_exit_ip?: string;
  bandwidth_budget_bytes?: number | null;
  enabled?: boolean;
  sticky_session?: string | null;
}

export type ExitUpdateRequest = Partial<Omit<ExitCreateRequest, "kind" | "server_id">> & {
  enabled?: boolean;
};

export type SiteMatchType = "domain" | "wildcard" | "regexp" | "geosite";

export interface SiteRule {
  id: number;
  name: string;
  match_type: SiteMatchType;
  values: string[];
  allowed_exit_ids: number[];
  preset_key?: string | null;
  enabled: boolean;
  position: number;
  created_at?: string | null;
}

export interface SiteRuleCreateRequest {
  name: string;
  match_type: SiteMatchType;
  values: string[];
  allowed_exit_ids: number[];
  preset_key?: string | null;
  enabled?: boolean;
}

export type SiteRuleUpdateRequest = Partial<SiteRuleCreateRequest> & {
  position?: number;
};

export interface SitePreset {
  key: string;
  label?: string;
  name?: string;
  description?: string;
  /** Backend отдаёт values, mock-режим — domains. */
  values?: string[];
  domains?: string[];
  match_type?: SiteMatchType;
}

export interface ProfileIssue {
  code: string;
  message: string;
  subject?: string | null;
}

export interface RoutingProfile {
  default_exit_ids: number[];
  enabled: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
  /** Ответ GET /routing/profile: готов ли профиль и что мешает. */
  ready?: boolean;
  issues?: ProfileIssue[];
  exits?: Array<{
    id: number;
    label: string;
    region: string | null;
    kind: ExitKind;
    enabled: boolean;
    health: ExitHealth;
  }>;
  sites?: Array<{ id: number; name: string; allowed_exit_ids: number[]; enabled: boolean }>;
}

export interface RoutingProfileUpdate {
  default_exit_ids: number[];
  enabled: boolean;
}

export type RouteKind = "ru_direct" | "site_rule" | "default" | "block";

export interface RouteCheckResult {
  kind: RouteKind;
  rule_id: number | null;
  exit_ids: number[];
  compiled_rule: Record<string, unknown> | null;
  explanation: string;
}

export type RevisionStatus = "pending" | "applied" | "failed";

export interface RoutingRevision {
  id: number;
  created_at: string;
  profile_json?: unknown;
  config_hashes_json?: Record<string, string> | null;
  /** Сырые поля backend (нормализуются в api.ts). */
  hashes?: Record<string, string> | null;
  profile?: unknown;
  configs?: Record<string, unknown> | null;
  status: RevisionStatus;
  applied_at?: string | null;
  error?: string | null;
}

export interface RoutingApplyResponse {
  revision_id: number;
  job_id: number;
  servers?: number[];
  hashes?: Record<string, string>;
}

export interface Incident {
  id: number;
  severity: "info" | "warning" | "critical";
  kind: string;
  subject?: string | null;
  status: "open" | "ack" | "resolved";
  /** GET /incidents отдаёт details; mock-режим — details_json. */
  details?: Record<string, unknown> | null;
  details_json?: Record<string, unknown> | null;
  created_at: string;
  acked_at?: string | null;
  resolved_at?: string | null;
}

export interface Client {
  id: number;
  name: string;
  uuid: string;
  email_tag: string;
  enabled: boolean;
  traffic_limit_bytes: number | null;
  /** Суммарный трафик за всё время (GET /clients). */
  traffic_bytes?: number | null;
  created_at?: string | null;
  /** Поле есть не во всех ответах backend (last_seen_at пока не отдаётся). */
  last_seen_at?: string | null;
  /** Токен подписки отдаётся только в mock-режиме; реальный QR строит backend. */
  sub_token?: string | null;
  traffic_24h?: TrafficField;
  status?: "active" | "disabled" | "limit" | string | null;
  nodes?: Array<{ server_id: number; name?: string; enabled?: boolean }>;
}

export interface ClientCreateRequest {
  name: string;
  traffic_limit_bytes?: number | null;
  enabled?: boolean;
}

export interface ClientUpdateRequest {
  name?: string;
  enabled?: boolean;
  traffic_limit_bytes?: number | null;
}

export interface ClientLinkResponse {
  url: string;
}

export interface ClientStatsRange {
  up_bytes?: number;
  down_bytes?: number;
  total_bytes?: number;
  up?: number;
  down?: number;
  total?: number;
}

export interface ClientStats {
  traffic_24h?: ClientStatsRange | number | null;
  traffic_7d?: ClientStatsRange | number | null;
  traffic_30d?: ClientStatsRange | number | null;
  /** Сырой ответ GET /clients/{id}/stats: traffic: {"24h": n, "7d": n, "30d": n}. */
  traffic?: Record<string, number> | null;
  total_bytes?: number | null;
  client_id?: number;
  traffic_limit_bytes?: number | null;
  last_seen_at?: string | null;
  status?: string | null;
  uptime?: string | null;
}

export interface DeleteServerResponse {
  job_id?: number;
  status?: string;
  deleted?: boolean;
  force?: boolean;
}

export interface MetricsOverview {
  nodes?: { total?: number; online?: number; degraded?: number } | null;
  nodes_online?: number;
  nodes_total?: number;
  servers_online?: number;
  servers_total?: number;
  traffic_today_bytes?: number | null;
  traffic_24h_bytes?: number | null;
  traffic_24h?: TrafficField;
  active_connections?: number | null;
  online_clients?: number | null;
  connections?: number | null;
  errors_last_hour?: number | null;
  errors_1h?: number | null;
  errors?: number | null;
  open_incidents?: number | null;
  routing_enabled?: boolean;
  uptime_24h_pct?: number | null;
  generated_at?: string | null;
  routing?: {
    enabled?: boolean;
    configured?: boolean;
    healthy?: boolean;
    default_exits_healthy?: number;
  } | null;
}

export interface TrafficPoint {
  ts?: string;
  t?: string;
  bucket_ts?: string;
  up_bytes?: number;
  down_bytes?: number;
  up?: number;
  down?: number;
  total_bytes?: number;
  total?: number;
}

export interface TrafficSeries {
  name?: string;
  label?: string;
  key?: string;
  server_id?: number;
  client_id?: number;
  points?: TrafficPoint[];
  data?: TrafficPoint[];
}

export interface TrafficResponse {
  series?: TrafficSeries[];
  points?: TrafficPoint[];
  range?: string;
  group_by?: string;
}

export interface AuditEvent {
  id: number;
  ts: string;
  kind: string;
  actor?: string | null;
  ip?: string | null;
  user_agent?: string | null;
  /** GET /events отдаёт payload; mock-режим — payload_json. */
  payload?: Record<string, unknown> | null;
  payload_json?: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: string;
  db?: string;
  version?: string;
}


export interface ConnectionVariant {
  id: string;
  label: string;
  description: string;
  type: "tcp" | "xhttp" | "grpc";
  port: number;
  flow: string | null;
  url: string;
}

export interface ClientConnection {
  server_id: number;
  server_name: string;
  variants: ConnectionVariant[];
  smart: { subscription_url: string };
}
