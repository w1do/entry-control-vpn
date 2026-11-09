import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Loader2,
  RefreshCw,
  ScrollText,
  ServerCog,
  ShieldCheck,
} from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { QueryState } from "@/components/common/query-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { RevisionStatusBadge } from "@/components/ui/status-badge";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, setCsrfToken } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { AuditEvent, RoutingRevision } from "@/lib/api-types";
import {
  eventsQuery,
  healthQuery,
  meQuery,
  queryKeys,
  routingRevisionsQuery,
} from "@/lib/queries";
import { useToast } from "@/lib/toast";
import {
  cn,
  diffLines,
  formatDateTime,
  formatDateTimeSeconds,
  jsonPretty,
} from "@/lib/utils";

const AUDIT_KIND_LABELS: Record<string, string> = {
  admin_bootstrapped: "Создание администратора",
  login_succeeded: "Вход",
  login_failed: "Неудачная попытка входа",
  logout: "Выход",
  password_changed: "Смена пароля",
  routing_applied: "Применение маршрутизации",
  routing_changed: "Изменение маршрутизации",
  server_created: "Создание сервера",
  server_action: "Действие с сервером",
  server_delete_requested: "Запрошено удаление сервера",
  server_force_deleted: "Принудительное удаление сервера",
  server_deleted: "Удаление сервера",
  client_created: "Создание клиента",
  client_deleted: "Удаление клиента",
  exit_created: "Создание выхода",
  exit_deleted: "Удаление выхода",
  site_created: "Создание правила",
  site_deleted: "Удаление правила",
  agent_diagnose: "Диагностика узла",
  // mock-режим
  "auth.login": "Вход",
  "auth.logout": "Выход",
  "password.change": "Смена пароля",
  "routing.apply": "Применение маршрутизации",
  "server.delete": "Удаление сервера",
};

function PasswordCard() {
  const queryClient = useQueryClient();
  const { success } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: () => api.auth.changePassword(current, next),
    onSuccess: (me) => {
      // backend возвращает новый CSRF-токен для текущей сессии
      setCsrfToken(me.csrf_token);
      queryClient.setQueryData(queryKeys.me, me);
      success("Пароль изменён", "Остальные сессии инвалидированы");
      setCurrent("");
      setNext("");
      setConfirm("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.me });
    },
    onError: (mutationError) => setError(errorMessage(mutationError)),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!current) {
      setError("Введите текущий пароль");
      return;
    }
    if (next.length < 12) {
      setError("Новый пароль должен быть не короче 12 символов");
      return;
    }
    if (next !== confirm) {
      setError("Пароли не совпадают");
      return;
    }
    change.mutate();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4 text-muted-foreground" />
          Смена пароля
        </CardTitle>
        <CardDescription>
          Пароль хранится как argon2id-хеш. <code>.env</code> не меняется — при следующем деплое
          останется новый пароль.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={handleSubmit} noValidate>
          <FormField label="Текущий пароль" htmlFor="password-current" required>
            <Input
              id="password-current"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
            />
          </FormField>
          <FormField
            label="Новый пароль"
            htmlFor="password-new"
            required
            hint="Минимум 12 символов. После смены все другие сессии будут инвалидированы."
          >
            <Input
              id="password-new"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
            />
          </FormField>
          <FormField label="Повторите новый пароль" htmlFor="password-confirm" required>
            <Input
              id="password-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
          </FormField>
          {error ? (
            <Alert variant="destructive">
              <AlertTriangle aria-hidden />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Button type="submit" disabled={change.isPending}>
            {change.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            Сменить пароль
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ServiceCard() {
  const health = useQuery(healthQuery);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ServerCog className="size-4 text-muted-foreground" />
          Сервис и версии
        </CardTitle>
        <CardDescription>Состояние backend и зафиксированные версии компонентов.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">API панели</span>
          {health.isLoading ? (
            <Badge variant="muted">проверка…</Badge>
          ) : health.isError ? (
            <Badge variant="destructive">недоступен</Badge>
          ) : (
            <Badge variant="success">
              <CheckCircle2 className="size-3" />
              {health.data?.status ?? "ok"}
            </Badge>
          )}
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">База данных</span>
          <Badge variant={health.data?.db === "ok" ? "success" : "muted"}>
            {health.data?.db ?? "—"}
          </Badge>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">Версия панели</span>
          <span className="font-mono text-xs">{health.data?.version ?? "—"}</span>
        </div>
        <Separator />
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">Xray-core</span>
          <span className="font-mono text-xs">26.11.9</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">vpn-agent</span>
          <span className="font-mono text-xs">0.1.0</span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void health.refetch()}
          disabled={health.isFetching}
        >
          <RefreshCw className={cn(health.isFetching && "animate-spin")} />
          Обновить состояние
        </Button>
      </CardContent>
    </Card>
  );
}

function RevisionDetailDialog({
  revisionId,
  onOpenChange,
}: {
  revisionId: number | null;
  onOpenChange: (open: boolean) => void;
}) {
  const detail = useQuery({
    queryKey: queryKeys.routingRevision(revisionId ?? 0),
    queryFn: () => api.routing.revision(revisionId!),
    enabled: revisionId !== null,
  });

  return (
    <Dialog open={revisionId !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Ревизия #{revisionId}</DialogTitle>
          <DialogDescription>
            Профиль маршрутизации, хеши конфигов и результат применения.
          </DialogDescription>
        </DialogHeader>
        <QueryState
          isLoading={detail.isLoading}
          isError={detail.isError}
          error={detail.error}
          onRetry={() => void detail.refetch()}
          rows={2}
        >
          {detail.data ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <RevisionStatusBadge status={detail.data.status} />
                <span className="text-sm text-muted-foreground">
                  создана {formatDateTimeSeconds(detail.data.created_at)}
                </span>
                {detail.data.applied_at ? (
                  <span className="text-sm text-muted-foreground">
                    применена {formatDateTimeSeconds(detail.data.applied_at)}
                  </span>
                ) : null}
              </div>
              {detail.data.error ? (
                <Alert variant="destructive">
                  <AlertTitle>Ошибка применения</AlertTitle>
                  <AlertDescription>{detail.data.error}</AlertDescription>
                </Alert>
              ) : null}
              {detail.data.config_hashes_json ? (
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">SHA-256 конфигов</p>
                  <pre className="max-h-32 overflow-auto rounded-md border bg-muted/40 p-2 text-xs">
                    {jsonPretty(detail.data.config_hashes_json)}
                  </pre>
                </div>
              ) : null}
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">profile_json</p>
                <pre className="max-h-80 overflow-auto rounded-md border bg-muted/40 p-3 text-xs">
                  {jsonPretty(detail.data.profile_json ?? null)}
                </pre>
              </div>
            </div>
          ) : null}
        </QueryState>
      </DialogContent>
    </Dialog>
  );
}

interface CompareState {
  before: RoutingRevision;
  after: RoutingRevision;
  beforeJson: string;
  afterJson: string;
}

function CompareDialog({ state, onOpenChange }: { state: CompareState | null; onOpenChange: (open: boolean) => void }) {
  const lines = useMemo(
    () => (state ? diffLines(state.beforeJson, state.afterJson) : []),
    [state],
  );
  return (
    <Dialog open={state !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            Сравнение ревизий #{state?.before.id} → #{state?.after.id}
          </DialogTitle>
          <DialogDescription>
            Построчный diff профиля маршрутизации: зелёные строки добавлены, красные — удалены.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-auto rounded-lg border bg-muted/20">
          <table className="w-full border-collapse font-mono text-xs">
            <tbody>
              {lines.map((line, index) => (
                <tr
                  key={index}
                  className={cn(
                    line.type === "add" && "bg-success/10 text-success",
                    line.type === "del" && "bg-destructive/10 text-destructive",
                  )}
                >
                  <td className="w-10 select-none border-r px-2 py-0.5 text-right text-muted-foreground">
                    {index + 1}
                  </td>
                  <td className="w-5 select-none px-1 py-0.5 text-center">
                    {line.type === "add" ? "+" : line.type === "del" ? "−" : " "}
                  </td>
                  <td className="whitespace-pre-wrap break-all px-2 py-0.5">{line.text || " "}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Закрыть
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RevisionsTab() {
  const revisions = useQuery(routingRevisionsQuery);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [compare, setCompare] = useState<CompareState | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);

  const toggleSelected = (id: number) => {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((candidate) => candidate !== id);
      if (current.length >= 2) return [current[1], id];
      return [...current, id];
    });
  };

  async function runCompare() {
    setCompareError(null);
    if (selected.length !== 2) return;
    const list = revisions.data ?? [];
    const before = list.find((revision) => revision.id === selected[0]);
    const after = list.find((revision) => revision.id === selected[1]);
    if (!before || !after) return;
    try {
      const [beforeFull, afterFull] = await Promise.all([
        api.routing.revision(before.id),
        api.routing.revision(after.id),
      ]);
      setCompare({
        before: { ...before, ...beforeFull },
        after: { ...after, ...afterFull },
        beforeJson: jsonPretty(beforeFull.profile_json ?? before.profile_json ?? null),
        afterJson: jsonPretty(afterFull.profile_json ?? after.profile_json ?? null),
      });
    } catch (error) {
      setCompareError(errorMessage(error));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          История изменения профиля маршрутизации. Выберите две ревизии для сравнения.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void runCompare()}
          disabled={selected.length !== 2}
        >
          Сравнить выбранные ({selected.length}/2)
        </Button>
      </div>

      {compareError ? (
        <Alert variant="destructive">
          <AlertDescription>{compareError}</AlertDescription>
        </Alert>
      ) : null}

      <QueryState
        isLoading={revisions.isLoading}
        isError={revisions.isError}
        error={revisions.error}
        onRetry={() => void revisions.refetch()}
        rows={3}
      >
        {(revisions.data ?? []).length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            Ревизий ещё нет — примените профиль на странице «Сайты».
          </p>
        ) : (
          <div className="overflow-hidden rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" aria-label="Выбор" />
                  <TableHead>Ревизия</TableHead>
                  <TableHead>Создана</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead>Применена</TableHead>
                  <TableHead>Ошибка</TableHead>
                  <TableHead className="w-24" aria-label="Действия" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(revisions.data ?? []).map((revision) => (
                  <TableRow key={revision.id}>
                    <TableCell>
                      <Checkbox
                        checked={selected.includes(revision.id)}
                        onCheckedChange={() => toggleSelected(revision.id)}
                        aria-label={`Выбрать ревизию ${revision.id}`}
                      />
                    </TableCell>
                    <TableCell className="font-medium">#{revision.id}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDateTime(revision.created_at)}
                    </TableCell>
                    <TableCell>
                      <RevisionStatusBadge status={revision.status} />
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {revision.applied_at ? formatDateTime(revision.applied_at) : "—"}
                    </TableCell>
                    <TableCell className="max-w-64 truncate text-sm text-destructive" title={revision.error ?? undefined}>
                      {revision.error ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" onClick={() => setDetailId(revision.id)}>
                        Детали
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>

      <RevisionDetailDialog
        revisionId={detailId}
        onOpenChange={(open) => {
          if (!open) setDetailId(null);
        }}
      />
      <CompareDialog
        state={compare}
        onOpenChange={(open) => {
          if (!open) setCompare(null);
        }}
      />
    </div>
  );
}

function AuditTab() {
  const events = useQuery(eventsQuery);
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Журнал безопасности: входы, смена пароля, изменения маршрутизации и серверов. Без секретов.
      </p>
      {events.isError ? (
        <Alert variant="warning">
          <AlertTriangle aria-hidden />
          <div>
            <AlertTitle>Журнал аудита недоступен</AlertTitle>
            <AlertDescription>
              Backend не отдал журнал событий: {errorMessage(events.error)}. Проверьте, что endpoint
              аудита реализован.
            </AlertDescription>
          </div>
        </Alert>
      ) : (
        <QueryState
          isLoading={events.isLoading}
          isError={false}
          onRetry={() => void events.refetch()}
          rows={3}
        >
          {(events.data ?? []).length === 0 ? (
            <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              Событий пока нет.
            </p>
          ) : (
            <div className="overflow-hidden rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Время (UTC)</TableHead>
                    <TableHead>Событие</TableHead>
                    <TableHead>Админ</TableHead>
                    <TableHead>IP</TableHead>
                    <TableHead>Детали</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(events.data ?? []).map((event: AuditEvent) => (
                    <TableRow key={event.id}>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatDateTimeSeconds(event.ts)}
                      </TableCell>
                      <TableCell className="text-sm font-medium">
                        {AUDIT_KIND_LABELS[event.kind] ?? event.kind}
                      </TableCell>
                      <TableCell className="text-sm">{event.actor ?? "—"}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {event.ip ?? "—"}
                      </TableCell>
                      <TableCell className="max-w-72 truncate text-xs text-muted-foreground" title={jsonPretty(event.payload_json ?? {})}>
                        {event.payload_json && Object.keys(event.payload_json).length > 0
                          ? JSON.stringify(event.payload_json)
                          : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </QueryState>
      )}
    </div>
  );
}

export function SettingsPage() {
  const me = useQuery(meQuery);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Настройки"
        description="Администратор, безопасность сессии, ревизии маршрутизации и журнал аудита."
      />

      {me.data?.weak_password ? (
        <Alert variant="warning">
          <AlertTriangle aria-hidden />
          <div>
            <AlertTitle>Слабый пароль</AlertTitle>
            <AlertDescription>
              Текущий пароль короче 12 символов. Смените его, чтобы убрать предупреждение с
              дашборда.
            </AlertDescription>
          </div>
        </Alert>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <PasswordCard />
        <ServiceCard />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ScrollText className="size-4 text-muted-foreground" />
            История и аудит
          </CardTitle>
          <CardDescription>
            Ревизии профиля маршрутизации и журнал действий администратора.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="revisions">
            <TabsList>
              <TabsTrigger value="revisions">Ревизии маршрутизации</TabsTrigger>
              <TabsTrigger value="audit">Журнал аудита</TabsTrigger>
            </TabsList>
            <TabsContent value="revisions">
              <RevisionsTab />
            </TabsContent>
            <TabsContent value="audit">
              <AuditTab />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
