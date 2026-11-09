import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  Activity,
  Ellipsis,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  Stethoscope,
} from "lucide-react";
import { useState } from "react";
import { JobDialog } from "@/components/common/job-dialog";
import { QueryState } from "@/components/common/query-state";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ServerStatusBadge } from "@/components/ui/status-badge";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { ServerAction, Server as ServerModel } from "@/lib/api-types";
import { queryKeys, serversQuery } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import {
  formatBytes,
  formatPercent,
  formatRelative,
  normalizeTraffic,
  regionLabel,
  roleLabel,
} from "@/lib/utils";

const ACTION_TITLES: Record<ServerAction, string> = {
  reapply: "Переприменение конфигурации",
  restart: "Перезапуск сервисов",
  reinstall: "Переустановка узла",
  diagnose: "Диагностика узла",
};

export function ServersPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();
  const servers = useQuery(serversQuery);
  const [job, setJob] = useState<{ id: number; title: string } | null>(null);

  const action = useMutation({
    mutationFn: ({ id, actionName }: { id: number; actionName: ServerAction }) =>
      api.servers.action(id, actionName),
    onSuccess: (response, variables) => {
      setJob({ id: response.job_id, title: ACTION_TITLES[variables.actionName] });
    },
    onError: (mutationError) => toastError("Не удалось запустить задачу", errorMessage(mutationError)),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Серверы"
        description="Узлы Xray: entry принимает клиентов, exit выпускает трафик сайтов."
        actions={
          <Button asChild>
            <Link to="/servers/new">
              <Plus />
              Добавить сервер
            </Link>
          </Button>
        }
      />

      <QueryState
        isLoading={servers.isLoading}
        isError={servers.isError}
        error={servers.error}
        onRetry={() => void servers.refetch()}
        rows={4}
      >
        {(servers.data ?? []).length === 0 ? (
          <EmptyState
            icon={Server}
            title="Серверов пока нет"
            description="Добавьте первый VPS по SSH — панель сама установит Xray, агента, юниты systemd и настроит firewall."
            action={
              <Button asChild>
                <Link to="/servers/new">
                  <Plus />
                  Добавить сервер
                </Link>
              </Button>
            }
          />
        ) : (
          <div className="overflow-hidden rounded-xl border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Сервер</TableHead>
                  <TableHead>Роль / регион</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead>Версии</TableHead>
                  <TableHead>Heartbeat</TableHead>
                  <TableHead title="Отдаётся backend, когда реализован расчёт uptime">Uptime 24 ч</TableHead>
                  <TableHead>Трафик 24 ч</TableHead>
                  <TableHead className="w-10" aria-label="Действия" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(servers.data ?? []).map((server: ServerModel) => (
                  <TableRow
                    key={server.id}
                    className="cursor-pointer"
                    onClick={() => void navigate({ to: "/servers/$serverId", params: { serverId: String(server.id) } })}
                  >
                    <TableCell>
                      <div className="font-medium">{server.name}</div>
                      <div className="text-xs text-muted-foreground">{server.host}</div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline">{roleLabel(server.roles)}</Badge>
                        <span className="text-sm text-muted-foreground">{regionLabel(server.region)}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <ServerStatusBadge status={server.status} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      <div>agent {server.agent_version ?? "—"}</div>
                      <div>xray {server.xray_version ?? "—"}</div>
                    </TableCell>
                    <TableCell className="text-sm">{formatRelative(server.last_seen_at)}</TableCell>
                    <TableCell>
                      <span
                        className={
                          (server.uptime_24h_pct ?? 0) >= 99
                            ? "text-success"
                            : "text-warning"
                        }
                      >
                        {formatPercent(server.uptime_24h_pct)}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm">
                      {formatBytes(normalizeTraffic(server.traffic_24h).total)}
                    </TableCell>
                    <TableCell onClick={(event) => event.stopPropagation()}>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" aria-label={`Действия для ${server.name}`}>
                            <Ellipsis className="size-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onClick={() =>
                              void navigate({
                                to: "/servers/$serverId",
                                params: { serverId: String(server.id) },
                              })
                            }
                          >
                            <Activity className="size-4" />
                            Открыть карточку
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onClick={() => action.mutate({ id: server.id, actionName: "reapply" })}
                          >
                            <RefreshCw className="size-4" />
                            Переприменить конфиг
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => action.mutate({ id: server.id, actionName: "restart" })}
                          >
                            <RotateCcw className="size-4" />
                            Перезапустить сервисы
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => action.mutate({ id: server.id, actionName: "diagnose" })}
                          >
                            <Stethoscope className="size-4" />
                            Диагностика
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>

      <JobDialog
        open={job !== null}
        onOpenChange={(open) => {
          if (!open) setJob(null);
        }}
        jobId={job?.id ?? null}
        title={job?.title ?? "Задача"}
        onDone={() => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
          success("Задача выполнена");
        }}
      />
    </div>
  );
}
