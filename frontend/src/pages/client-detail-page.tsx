import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  Link2,
  Loader2,
  Pencil,
  Trash2,
  TriangleAlert,
  Users,
} from "lucide-react";
import { useState } from "react";
import { ClientFormDialog } from "@/components/clients/client-form-dialog";
import { ConnectDialog } from "@/components/clients/connect-dialog";
import { QueryState } from "@/components/common/query-state";
import { StatCard } from "@/components/common/stat-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/ui/page-header";
import { Progress } from "@/components/ui/progress";
import { ServerStatusBadge } from "@/components/ui/status-badge";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { ClientStats } from "@/lib/api-types";
import { clientsQuery, clientStatsQuery, queryKeys, serversQuery } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import {
  formatBytes,
  formatDateTime,
  formatRelative,
  normalizeTraffic,
  percent,
} from "@/lib/utils";

function rangeTotal(value: ClientStats["traffic_24h"]): number {
  return normalizeTraffic(value).total;
}

export function ClientDetailPage() {
  const params = useParams({ from: "/_app/clients/$clientId" });
  const clientId = Number(params.clientId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();
  const clients = useQuery(clientsQuery);
  const data = clients.data?.find((candidate) => candidate.id === clientId);
  const stats = useQuery(clientStatsQuery(clientId));
  const servers = useQuery(serversQuery);
  const [, setConnectOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.clients });
  };

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.clients.update(clientId, { enabled }),
    onSuccess: (_data, enabled) => {
      success(enabled ? "Клиент включён" : "Клиент отключён");
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось изменить клиента", errorMessage(mutationError)),
  });

  const remove = useMutation({
    mutationFn: () => api.clients.remove(clientId),
    onSuccess: () => {
      success("Клиент удалён");
      void queryClient.invalidateQueries({ queryKey: clientsQuery.queryKey });
      void navigate({ to: "/clients" });
    },
    onError: (mutationError) => toastError("Не удалось удалить клиента", errorMessage(mutationError)),
  });

  const statsData = stats.data;
  const total30 = rangeTotal(statsData?.traffic_30d) || statsData?.total_bytes || 0;
  const usedTotal = data?.traffic_bytes ?? total30;
  const limit = data?.traffic_limit_bytes ?? null;
  const limitPercent = limit ? percent(usedTotal, limit) : 0;
  const limitReached = Boolean(limit && usedTotal >= limit);
  const entryNodes = (servers.data ?? []).filter(
    (server) => server.roles === "entry" || server.roles === "both",
  );

  return (
    <div className="space-y-6">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2 mb-2">
          <Link to="/clients">
            <ArrowLeft />
            К списку клиентов
          </Link>
        </Button>
      </div>

      <QueryState
        isLoading={clients.isLoading}
        isError={clients.isError}
        error={clients.error}
        onRetry={() => void clients.refetch()}
        rows={4}
      >
        {data ? (
          <>
            <PageHeader
              title={data.name}
              description={
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={!data.enabled ? "muted" : data.status === "limit" ? "destructive" : "success"}>
                    {!data.enabled ? "Отключён" : data.status === "limit" ? "Лимит исчерпан" : "Активен"}
                  </Badge>
                  <Badge variant="muted">{data.email_tag}</Badge>
                  {data.last_seen_at ? (
                    <span className="text-xs text-muted-foreground">
                      последняя активность {formatRelative(data.last_seen_at)}
                    </span>
                  ) : null}
                </div>
              }
              actions={
                <>
                  <Button onClick={() => setConnectOpen(true)}>
                    <Link2 />
                    Подключиться
                  </Button>
                  <Button variant="outline" onClick={() => setEditOpen(true)}>
                    <Pencil />
                    Изменить
                  </Button>
                  <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
                    <Trash2 />
                    Удалить
                  </Button>
                </>
              }
            />

            {!data.enabled ? (
              <Alert variant="warning">
                <TriangleAlert aria-hidden />
                <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <AlertTitle>Клиент отключён</AlertTitle>
                    <AlertDescription>
                      Пользователь удалён со всех entry-узлов, существующие сессии прекращены.
                    </AlertDescription>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => toggle.mutate(true)}
                    disabled={toggle.isPending}
                  >
                    Включить клиента
                  </Button>
                </div>
              </Alert>
            ) : null}

            {limitReached ? (
              <Alert variant="destructive">
                <TriangleAlert aria-hidden />
                <div>
                  <AlertTitle>Лимит трафика исчерпан</AlertTitle>
                  <AlertDescription>
                    Клиент автоматически отключён на всех узлах. Поднимите лимит или сбросьте счётчик.
                  </AlertDescription>
                </div>
              </Alert>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard
                label="Трафик за 24 часа"
                value={formatBytes(rangeTotal(statsData?.traffic_24h))}
                icon={Users}
                tone="primary"
              />
              <StatCard
                label="Трафик за 7 дней"
                value={formatBytes(rangeTotal(statsData?.traffic_7d))}
                icon={Users}
              />
              <StatCard
                label="Трафик за 30 дней"
                value={formatBytes(total30)}
                icon={Users}
              />
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
              <Card className="lg:col-span-2">
                <CardHeader>
                  <CardTitle>Реквизиты клиента</CardTitle>
                  <CardDescription>
                    UUID и email-тег используются в конфигурациях Xray. QR-коды и VLESS-ссылку формирует backend.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">UUID</p>
                    <div className="flex items-center gap-2">
                      <code className="min-w-0 flex-1 break-all rounded-md border bg-muted/40 p-2 text-xs">
                        {data.uuid}
                      </code>
                      <CopyButton value={data.uuid} label="Копировать" />
                    </div>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <p className="text-xs text-muted-foreground">Email-тег (учёт трафика)</p>
                      <p className="mt-1 font-mono text-sm">{data.email_tag}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Создан</p>
                      <p className="mt-1 text-sm">{formatDateTime(data.created_at)}</p>
                    </div>
                    {data.sub_token ? (
                      <div className="sm:col-span-2">
                        <p className="text-xs text-muted-foreground">Токен подписки</p>
                        <div className="mt-1 flex items-center gap-2">
                          <code className="min-w-0 flex-1 break-all rounded-md border bg-muted/40 p-2 text-xs">
                            /sub/{data.sub_token}
                          </code>
                          <CopyButton value={data.sub_token} label="Копировать" />
                        </div>
                      </div>
                    ) : null}
                  </div>
                </CardContent>
              </Card>

              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Доступ</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium">Клиент включён</p>
                        <p className="text-xs text-muted-foreground">Отключение мгновенно отзывает доступ</p>
                      </div>
                      <Switch
                        checked={data.enabled}
                        onCheckedChange={(checked) => toggle.mutate(checked)}
                        disabled={toggle.isPending}
                        aria-label="Клиент включён"
                      />
                    </div>
                    {limit ? (
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>Лимит</span>
                          <span>
                            {formatBytes(usedTotal)} / {formatBytes(limit)}
                          </span>
                        </div>
                        <Progress value={limitPercent} />
                        <p className="text-xs text-muted-foreground">
                          Сброс счётчика — через повторное применение конфигурации.
                        </p>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">Лимит не задан — трафик не ограничен.</p>
                    )}
                  </CardContent>
                </Card>

                {entryNodes.length > 0 ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Entry-узлы</CardTitle>
                      <CardDescription>
                        Клиент включён на всех entry-узлах с применённой ревизией
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      {entryNodes.map((node) => (
                        <div key={node.id} className="flex items-center justify-between gap-2 text-sm">
                          <span>{node.name}</span>
                          <ServerStatusBadge status={node.status} />
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                ) : null}
              </div>
            </div>

            <ClientFormDialog
              open={editOpen}
              onOpenChange={setEditOpen}
              client={data}
              onSaved={() => {
                invalidate();
              }}
            />

            <ConnectDialog
              client={data}
              onOpenChange={(open) => setConnectOpen(open)}
            />

            <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Удалить клиента «{data.name}»?</DialogTitle>
                  <DialogDescription>
                    Пользователь будет удалён со всех entry-узлов, подписка и QR перестанут работать.
                    Действие необратимо.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setDeleteOpen(false)}>
                    Отмена
                  </Button>
                  <Button variant="destructive" onClick={() => remove.mutate()} disabled={remove.isPending}>
                    {remove.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
                    Удалить клиента
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        ) : null}
      </QueryState>
    </div>
  );
}
