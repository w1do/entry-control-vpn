import { queryOptions } from "@tanstack/react-query";
import { api, setCsrfToken } from "./api";
import type { Exit, MetricsOverview, RoutingProfile, Server, SiteRule } from "./api-types";

export const queryKeys = {
  me: ["auth", "me"] as const,
  servers: ["servers"] as const,
  server: (id: number) => ["servers", id] as const,
  exits: ["exits"] as const,
  sites: ["sites"] as const,
  presets: ["sites", "presets"] as const,
  routingProfile: ["routing", "profile"] as const,
  routingRevisions: ["routing", "revisions"] as const,
  routingRevision: (id: number) => ["routing", "revisions", id] as const,
  clients: ["clients"] as const,
  client: (id: number) => ["clients", id] as const,
  clientStats: (id: number) => ["clients", id, "stats"] as const,
  overview: ["metrics", "overview"] as const,
  traffic: (range: string, groupBy: string) => ["metrics", "traffic", range, groupBy] as const,
  incidents: ["incidents"] as const,
  jobs: (serverId?: number) => ["jobs", serverId ?? "all"] as const,
  job: (id: number | string) => ["jobs", "detail", String(id)] as const,
  events: ["events"] as const,
  health: ["health"] as const,
};

export const meQuery = queryOptions({
  queryKey: queryKeys.me,
  queryFn: async () => {
    const me = await api.auth.me();
    setCsrfToken(me.csrf_token);
    return me;
  },
  staleTime: 60_000,
  retry: false,
});

export const serversQuery = queryOptions({
  queryKey: queryKeys.servers,
  queryFn: () => api.servers.list(),
  refetchInterval: 30_000,
});

export const serverQuery = (id: number) =>
  queryOptions({
    queryKey: queryKeys.server(id),
    queryFn: () => api.servers.get(id),
    refetchInterval: 20_000,
  });

export const exitsQuery = queryOptions({
  queryKey: queryKeys.exits,
  queryFn: () => api.exits.list(),
  refetchInterval: 30_000,
});

export const sitesQuery = queryOptions({
  queryKey: queryKeys.sites,
  queryFn: () => api.sites.list(),
  refetchInterval: 30_000,
});

export const presetsQuery = queryOptions({
  queryKey: queryKeys.presets,
  queryFn: () => api.sites.presets(),
  staleTime: 5 * 60_000,
});

export const routingProfileQuery = queryOptions({
  queryKey: queryKeys.routingProfile,
  queryFn: () => api.routing.profile(),
  refetchInterval: 30_000,
});

export const routingRevisionsQuery = queryOptions({
  queryKey: queryKeys.routingRevisions,
  queryFn: () => api.routing.revisions(),
  refetchInterval: 30_000,
});

export const routingRevisionQuery = (id: number) =>
  queryOptions({
    queryKey: queryKeys.routingRevision(id),
    queryFn: () => api.routing.revision(id),
    staleTime: 60_000,
  });

export const clientsQuery = queryOptions({
  queryKey: queryKeys.clients,
  queryFn: () => api.clients.list(),
  refetchInterval: 30_000,
});

export const clientStatsQuery = (id: number) =>
  queryOptions({
    queryKey: queryKeys.clientStats(id),
    queryFn: () => api.clients.stats(id),
    refetchInterval: 60_000,
  });

export const overviewQuery = queryOptions({
  queryKey: queryKeys.overview,
  queryFn: () => api.metrics.overview(),
  refetchInterval: 20_000,
});

export const trafficQuery = (range: string, groupBy: "server" | "client") =>
  queryOptions({
    queryKey: queryKeys.traffic(range, groupBy),
    queryFn: () => api.metrics.traffic(range, groupBy),
    refetchInterval: 60_000,
  });

export const incidentsQuery = queryOptions({
  queryKey: queryKeys.incidents,
  queryFn: () => api.incidents.list(),
  refetchInterval: 30_000,
});

export const jobsQuery = (serverId?: number) =>
  queryOptions({
    queryKey: queryKeys.jobs(serverId),
    queryFn: () => api.jobs.list(serverId),
    refetchInterval: 15_000,
  });

export const jobQuery = (id: number | string) =>
  queryOptions({
    queryKey: queryKeys.job(id),
    queryFn: () => api.jobs.get(id),
  });

export const eventsQuery = queryOptions({
  queryKey: queryKeys.events,
  queryFn: () => api.events.list(),
  refetchInterval: 60_000,
  retry: false,
});

export const healthQuery = queryOptions({
  queryKey: queryKeys.health,
  queryFn: () => api.health(),
  refetchInterval: 60_000,
  retry: false,
});

export interface RoutingStatus {
  ready: boolean;
  profile: RoutingProfile | undefined;
  exits: Exit[];
  defaultExits: Exit[];
  healthyDefaultExits: Exit[];
  enabled: boolean;
  loading: boolean;
  error: Error | null;
  issues: string[];
}

/**
 * Гейт маршрутизации. Основной источник — ответ GET /routing/profile ({ready, issues}),
 * дополнительно считаем здоровье выходов по /exits.
 */
export function computeRoutingStatus(
  profile: RoutingProfile | undefined,
  exits: Exit[] | undefined,
): RoutingStatus {
  const readyExits = (exits ?? []).filter((exit) => exit.enabled);
  const defaultExits = (profile?.default_exit_ids ?? [])
    .map((id) => readyExits.find((exit) => exit.id === id))
    .filter((exit): exit is Exit => Boolean(exit));
  const healthyDefaultExits = defaultExits.filter((exit) => exit.health === "up");
  const enabled = Boolean(profile?.enabled);
  const issues = (profile?.issues ?? []).map((issue) => issue.message);
  const ready =
    typeof profile?.ready === "boolean"
      ? profile.ready
      : enabled && defaultExits.length > 0 && healthyDefaultExits.length > 0;
  return {
    ready,
    profile,
    exits: readyExits,
    defaultExits,
    healthyDefaultExits,
    enabled,
    loading: profile === undefined,
    error: null,
    issues,
  };
}

export type { Server, SiteRule, MetricsOverview };
