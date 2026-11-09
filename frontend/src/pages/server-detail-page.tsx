import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  Download,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Stethoscope,
  Trash2,
  UserRound,
} from "lucide-react";
import { useState } from "react";
import { JobDialog } from "@/components/common/job-dialog";
import { QueryState } from "@/components/common/query-state";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { ServerStatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { ServerAction } from "@/lib/api-types";
import { queryKeys, serverQuery, serversQuery } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import {
  formatBytes,
  formatDateTime,
  formatPercent,
  formatRelative,
  jsonPretty,
  normalizeTraffic,
  regionLabel,
  roleLabel,
} from "@/lib/utils";
import { JobProgress } from "@/components/common/job-progress";

const ACTION_TITLES: Record<ServerAction, string> = {
  reapply: "Переприменение конфигурации",
  restart: "Перезапуск сервисов",
  reinstall: "Переустановка узла",
  diagnose: "Диагностика узла",
};

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <span className="shrink-0 text-sm text-muted-foreground">{label}</span>
      <span className="min-w-0 break-all text-right text-sm font-medium">{value}</span>
    </div>
  );
}

export function ServerDetailPage() {
  const params = useParams({ from: "/_app/servers/$serverId" });
  const serverId = Number(params.serverId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();

  const server = useQuery(serverQuery(serverId));
  const [job, setJob] = useState<{ id: number; title: string } | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleteJobId, setDeleteJobId] = useState<number | null>(null);
  const [deleteFailed, setDeleteFailed] = useState(false);

  const action = useMutation({
    mutationFn: (actionName: ServerAction) => api.servers.action(serverId, actionName),
    onSuccess: (response, actionName) => {
      setJob({ id: response.job_id, title: ACTION_TITLES[actionName] });
    },
    onError: (mutationError) => toastError("Не удалось запустить задачу", errorMessage(mutationError)),
  });

  const remove = useMutation({
    mutationFn: (force: boolean) => api.servers.remove(serverId, deleteConfirm, force),
    onSuccess: (response) => {
      if (response?.job_id) {
        setDeleteJobId(response.job_id);
        setDeleteFailed(false);
        return;
      }
      // force-удаление записи не создаёт job: backend отвечает {deleted: true, force: true}
      success("Запись сервера удалена");
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
      void navigate({ to: "/servers" });
    },
    onError: (mutationError) => {
      setDeleteFailed(true);
      toastError("Не удалось запустить удаление", errorMessage(mutationError));
    },
  });

  const data = server.data;

  function closeDelete() {
    if (remove.isPending) return;
    setDeleteOpen(false);
    setDeleteConfirm("");
    setDeleteJobId(null);
    setDeleteFailed(false);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2 mb-2">
          <Link to="/servers">
            <ArrowLeft />
            К списку серверов
          </Link>
        </Button>
      </div>

      <QueryState
        isLoading={server.isLoading}
        isError={server.isError}
        error={server.error}
        onRetry={() => void server.refetch()}
        rows={4}
      >
        {data ? (
          <>
            <PageHeader
              title={data.name}
              description={
                <div className="flex flex-wrap items-center gap-2">
                  <ServerStatusBadge status={data.status} />
                  <Badge variant="outline">{roleLabel(data.roles)}</Badge>
                  <Badge variant="muted">{regionLabel(data.region)}</Badge>
                  {data.last_seen_at ? (
                    <span className="text-xs text-muted-foreground">
                      heartbeat {formatRelative(data.last_seen_at)}
                    </span>
                  ) : null}
                </div>
              }
              actions={
                <>
                  <Button
                    variant="outline"
                    onClick={() => action.mutate("reapply")}
                    disabled={action.isPending}
                  >
                    <RefreshCw />
                    Переприменить
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => action.mutate("restart")}
                    disabled={action.isPending}
                  >
                    <RotateCcw />
                    Перезапустить
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => action.mutate("diagnose")}
                    disabled={action.isPending}
                  >
                    <Stethoscope />
                    Диагностика
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => setDeleteOpen(true)}
                    disabled={remove.isPending}
                  >
                    <Trash2 />
                    Удалить
                  </Button>
                </>
              }
            />

            {data.status === "offline" ? (
              <Alert variant="destructive">
                <ShieldAlert aria-hidden />
                <div>
                  <AlertTitle>Узел офлайн</AlertTitle>
                  <AlertDescription>
                    Нет heartbeat дольше 90 секунд{data.degraded_reason ? `: ${data.degraded_reason}` : ""}.
                    Выходы этого узла исключены из балансировки, правила с ними блокируются (fail-closed).
                  </AlertDescription>
                </div>
              </Alert>
            ) : null}

            {data.status === "degraded" ? (
              <Alert variant="warning">
                <ShieldAlert aria-hidden />
                <div>
                  <AlertTitle>Дрейф конфигурации</AlertTitle>
                  <AlertDescription>
                    Фактический конфиг на узле отличается от утверждённой ревизии. Перепримените
                    конфигурацию, чтобы восстановить соответствие.
                  </AlertDescription>
                </div>
              </Alert>
            ) : null}

            {data.status === "failed" ? (
              <Alert variant="destructive">
                <ShieldAlert aria-hidden />
                <div>
                  <AlertTitle>Провижининг завершился ошибкой</AlertTitle>
                  <AlertDescription>
                    Проверьте журнал задач и запустите переустановку или удалите запись.
                  </AlertDescription>
                </div>
              </Alert>
            ) : null}

            <div className="grid gap-6 lg:grid-cols-3">
              <Card className="lg:col-span-2">
                <CardHeader>
                  <CardTitle>Подключение и состояние</CardTitle>
                  <CardDescription>Секреты SSH не возвращаются API — только тип и отпечаток.</CardDescription>
                </CardHeader>
                <CardContent className="divide-y">
                  <InfoRow label="Host" value={`${data.host}:${data.ssh_port ?? 22}`} />
                  <InfoRow
                    label="SSH-пользователь"
                    value={
                      <span className="flex items-center gap-1.5">
                        <UserRound className="size-4 text-muted-foreground" />
                        {data.ssh_user ?? "—"}
                      </span>
                    }
                  />
                  <InfoRow
                    label="Аутентификация"
                    value={data.auth_type === "key" ? "Приватный ключ" : "Пароль"}
                  />
                  <InfoRow
                    label="Отпечаток ключа"
                    value={
                      <span className="font-mono text-xs">
                        {data.host_fingerprint ?? "—"}
                      </span>
                    }
                  />
                  <InfoRow label="Base dir" value={<code className="text-xs">{data.base_dir ?? "~/config/vpn"}</code>} />
                  <InfoRow label="Создан" value={formatDateTime(data.created_at)} />
                </CardContent>
              </Card>

              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle>Версии и ревизия</CardTitle>
                  </CardHeader>
                  <CardContent className="divide-y">
                    <InfoRow label="Агент" value={data.agent_version ?? "—"} />
                    <InfoRow label="Xray" value={data.xray_version ?? "—"} />
                    <InfoRow label="Ревизия" value={data.revision_id !== null && data.revision_id !== undefined ? `#${data.revision_id}` : "—"} />
                    <InfoRow label="Uptime 24 ч" value={formatPercent(data.uptime_24h_pct)} />
                    <InfoRow label="Трафик 24 ч" value={formatBytes(normalizeTraffic(data.traffic_24h).total)} />
                    <InfoRow label="Heartbeat" value={formatRelative(data.last_seen_at)} />
                  </CardContent>
                </Card>

                {data.roles === "entry" || data.roles === "both" ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Клиенты</CardTitle>
                      <CardDescription>Узел принимает VLESS+REALITY подключения.</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <Button asChild variant="outline" size="sm" className="w-full">
                        <Link to="/clients">
                          Открыть клиентов
                          <Download className="rotate-[-90deg]" />
                        </Link>
                      </Button>
                    </CardContent>
                  </Card>
                ) : null}
              </div>
            </div>

            <Dialog
              open={deleteOpen}
              onOpenChange={(open) => {
                if (open) setDeleteOpen(true);
                else closeDelete();
              }}
            >
              <DialogContent className="max-w-xl">
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2 text-destructive">
                    <Trash2 className="size-5" />
                    Удалить сервер «{data.name}»
                  </DialogTitle>
                  <DialogDescription>
                    Будут остановлены и отключены юниты vpn-agent и xray, удалён каталог{" "}
                    <code>~/config/vpn</code>, сняты правила firewall и отозван токен агента. Запись
                    сервера удаляется после успешной проверки очистки.
                  </DialogDescription>
                </DialogHeader>

                {deleteJobId ? (
                  <JobProgress
                    jobId={deleteJobId}
                    title="Удаление сервера"
                    successLabel="Сервер удалён"
                    onDone={() => {
                      void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
                      success("Сервер удалён");
                      void navigate({ to: "/servers" });
                    }}
                    onError={() => setDeleteFailed(true)}
                  />
                ) : (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="delete-confirm">
                        Введите имя сервера <span className="font-mono">{data.name}</span> для подтверждения
                      </Label>
                      <Input
                        id="delete-confirm"
                        value={deleteConfirm}
                        onChange={(event) => setDeleteConfirm(event.target.value)}
                        placeholder={data.name}
                        autoComplete="off"
                      />
                    </div>
                    {deleteFailed ? (
                      <Alert variant="destructive">
                        <AlertTitle>Удаление не удалось</AlertTitle>
                        <AlertDescription>
                          SSH недоступен или очистка не подтверждена. Повторите попытку; если узел
                          потерян безвозвратно, удалите запись принудительно — он может остаться
                          настроенным.
                        </AlertDescription>
                      </Alert>
                    ) : null}
                  </div>
                )}

                <DialogFooter>
                  {deleteJobId && deleteFailed ? (
                    <>
                      <Button
                        variant="outline"
                        onClick={() => {
                          void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
                        }}
                      >
                        Обновить список
                      </Button>
                      <Button
                        variant="destructive"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(true)}
                      >
                        Удалить запись принудительно
                      </Button>
                    </>
                  ) : deleteJobId ? null : (
                    <>
                      <Button variant="outline" onClick={closeDelete}>
                        Отмена
                      </Button>
                      <Button
                        variant="destructive"
                        disabled={deleteConfirm !== data.name || remove.isPending}
                        onClick={() => remove.mutate(false)}
                      >
                        <Trash2 />
                        Удалить сервер
                      </Button>
                    </>
                  )}
                </DialogFooter>
              </DialogContent>
            </Dialog>

            <JobDialog
              open={job !== null}
              onOpenChange={(open) => {
                if (!open) setJob(null);
              }}
              jobId={job?.id ?? null}
              title={job?.title ?? "Задача"}
              onDone={() => {
                void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
                void queryClient.invalidateQueries({ queryKey: serversQuery.queryKey });
                success("Задача выполнена");
              }}
              renderResult={(result) => (
                <pre className="max-h-64 overflow-auto rounded-lg border bg-muted/40 p-3 text-xs">
                  {jsonPretty(result)}
                </pre>
              )}
            />
          </>
        ) : null}
      </QueryState>
    </div>
  );
}
