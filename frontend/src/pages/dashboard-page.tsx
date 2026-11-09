import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  ArrowRight,
  CircleAlert,
  Gauge,
  Globe2,
  Link2,
  Plus,
  Server,
  ShieldCheck,
  TriangleAlert,
  Users,
  Waypoints,
} from "lucide-react";
import { useState } from "react";
import { QueryState } from "@/components/common/query-state";
import { StatCard } from "@/components/common/stat-card";
import { TrafficChart } from "@/components/common/traffic-chart";
import { IncidentsList } from "@/components/common/incidents-list";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  computeRoutingStatus,
  exitsQuery,
  meQuery,
  overviewQuery,
  routingProfileQuery,
  trafficQuery,
} from "@/lib/queries";
import { formatBytes, formatNumber, formatPercent, normalizeTraffic } from "@/lib/utils";

type TrafficRange = "24h" | "7d" | "30d";

const RANGE_LABELS: Record<TrafficRange, string> = {
  "24h": "24 часа",
  "7d": "7 дней",
  "30d": "30 дней",
};

export function DashboardPage() {
  const [range, setRange] = useState<TrafficRange>("24h");
  const overview = useQuery(overviewQuery);
  const traffic = useQuery(trafficQuery(range, "server"));
  const me = useQuery(meQuery);
  const profile = useQuery(routingProfileQuery);
  const exits = useQuery(exitsQuery);

  const routing = computeRoutingStatus(profile.data, exits.data);
  const data = overview.data;
  const nodesOnline = data?.nodes?.online ?? data?.nodes_online ?? data?.servers_online ?? 0;
  const nodesTotal = data?.nodes?.total ?? data?.nodes_total ?? data?.servers_total ?? 0;
  const trafficTotal = normalizeTraffic(
    data?.traffic_today_bytes ?? data?.traffic_24h_bytes ?? data?.traffic_24h,
  ).total;
  const connections = data?.active_connections ?? data?.connections ?? data?.online_clients ?? 0;
  const errors = data?.errors_last_hour ?? data?.errors_1h ?? data?.errors ?? 0;
  const uptime = data?.uptime_24h_pct;

  const routingValue = routing.ready
    ? "Активна"
    : routing.enabled
      ? "Не настроена"
      : "Выключена";
  const routingHint = routing.ready
    ? `Выходы по умолчанию: ${routing.healthyDefaultExits.length} доступно`
    : "Трафик блокируется (fail-closed)";

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Дашборд</h1>
        <p className="text-sm text-muted-foreground">
          Сводка состояния парка, трафик и последние инциденты.
        </p>
      </div>

      {me.data?.weak_password ? (
        <Alert variant="warning">
          <TriangleAlert aria-hidden />
          <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <AlertTitle>Слабый пароль администратора</AlertTitle>
              <AlertDescription>
                Пароль из <code>.env</code> короче 12 символов. Смените его — предупреждение исчезнет.
              </AlertDescription>
            </div>
            <Button asChild size="sm" variant="outline" className="shrink-0">
              <Link to="/settings">
                Сменить пароль
                <ArrowRight />
              </Link>
            </Button>
          </div>
        </Alert>
      ) : null}

      {!routing.loading && !routing.ready ? (
        <Alert variant="warning">
          <CircleAlert aria-hidden />
          <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <AlertTitle>Проксирование не настроено</AlertTitle>
              <AlertDescription>
                Пока профиль невалиден, entry-узлы работают в режиме block-all.
              </AlertDescription>
            </div>
            <Button asChild size="sm" className="shrink-0">
              <Link to="/sites" search={{ focus: "default" }}>
                Настроить маршрутизацию
                <ArrowRight />
              </Link>
            </Button>
          </div>
        </Alert>
      ) : null}

      <QueryState
        isLoading={overview.isLoading}
        isError={overview.isError}
        error={overview.error}
        onRetry={() => void overview.refetch()}
        rows={2}
      >
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <StatCard
            label="Узлы онлайн"
            value={`${formatNumber(nodesOnline)} / ${formatNumber(nodesTotal)}`}
            icon={Server}
            tone={nodesTotal > 0 && nodesOnline === nodesTotal ? "success" : "warning"}
            hint="по heartbeat за последние 90 секунд"
          />
          <StatCard
            label="Трафик за сегодня"
            value={formatBytes(trafficTotal)}
            icon={Activity}
            tone="primary"
            hint="uplink + downlink по всем узлам"
          />
          <StatCard
            label="Активные соединения"
            value={formatNumber(connections)}
            icon={Link2}
            hint="accepted за последние 15 секунд"
          />
          <StatCard
            label="Ошибки за час"
            value={formatNumber(errors)}
            icon={CircleAlert}
            tone={errors > 0 ? "warning" : "success"}
            hint="routing_blocked, rejected, health_failed"
          />
          <StatCard
            label="Маршрутизация"
            value={routingValue}
            icon={ShieldCheck}
            tone={routing.ready ? "success" : "destructive"}
            hint={routingHint}
          />
        </div>
      </QueryState>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle>Трафик</CardTitle>
              <CardDescription>
                Разбивка по узлам · {RANGE_LABELS[range]} · данные обновляются каждую минуту
              </CardDescription>
            </div>
            <Select value={range} onValueChange={(value) => setRange(value as TrafficRange)}>
              <SelectTrigger className="w-32" aria-label="Период графика">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(RANGE_LABELS) as TrafficRange[]).map((value) => (
                  <SelectItem key={value} value={value}>
                    {RANGE_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            <QueryState
              isLoading={traffic.isLoading}
              isError={traffic.isError}
              error={traffic.error}
              onRetry={() => void traffic.refetch()}
              rows={2}
            >
              <TrafficChart data={traffic.data} range={range} height={300} />
            </QueryState>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Последние инциденты</CardTitle>
              <CardDescription>Критичные события и деградации узлов и выходов.</CardDescription>
            </CardHeader>
            <CardContent>
              <IncidentsList limit={5} compact />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Быстрые действия</CardTitle>
              <CardDescription>Типовые шаги настройки панели.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2">
              <Button asChild variant="outline" className="justify-start">
                <Link to="/servers/new">
                  <Plus />
                  Добавить сервер
                </Link>
              </Button>
              <Button asChild variant="outline" className="justify-start">
                <Link to="/sites" search={{ focus: "default" }}>
                  <Globe2 />
                  Настроить маршрутизацию
                </Link>
              </Button>
              <Button asChild variant="outline" className="justify-start">
                <Link to="/clients">
                  <Users />
                  Создать клиента
                </Link>
              </Button>
              <Button asChild variant="outline" className="justify-start">
                <Link to="/exits">
                  <Waypoints />
                  Проверить выходы
                </Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Состояние профиля</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Профиль</span>
                <Badge variant={routing.enabled ? "default" : "muted"}>
                  {routing.enabled ? "Включён" : "Выключен"}
                </Badge>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Выходов по умолчанию</span>
                <span className="font-medium">{formatNumber(routing.defaultExits.length)}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Доступно из них</span>
                <span className="font-medium">{formatNumber(routing.healthyDefaultExits.length)}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Uptime 24 ч</span>
                <span className="font-medium">{formatPercent(uptime)}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Gauge className="size-4" /> Вошёл как
                </span>
                <span className="max-w-40 truncate font-medium">{me.data?.email ?? "—"}</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
