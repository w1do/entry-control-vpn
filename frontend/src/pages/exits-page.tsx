import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleAlert,
  Ellipsis,
  HeartPulse,
  Loader2,
  Pencil,
  Plus,
  Power,
  Trash2,
  TriangleAlert,
  Waypoints,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { JobDialog } from "@/components/common/job-dialog";
import { QueryState } from "@/components/common/query-state";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ExitHealthBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { Exit, ExitCreateRequest, ExitProtocol } from "@/lib/api-types";
import { exitsQuery, queryKeys, serversQuery, sitesQuery } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import {
  bytesToGbString,
  cn,
  exitProtocolLabel,
  formatBytes,
  formatPercent,
  formatRelative,
  humanFileSizeInputToBytes,
  percent,
  regionLabel,
} from "@/lib/utils";

const REGIONS = ["US", "SG", "NL", "DE", "FI", "TR", "KZ", "JP", "GB", "FR", "AE", "SE", "CH", "CA", "PL", "OTHER"];

interface ExitFormState {
  kind: "node" | "external";
  serverId: string;
  label: string;
  region: string;
  endpointHost: string;
  endpointPort: string;
  protocol: ExitProtocol;
  username: string;
  password: string;
  expectedExitIp: string;
  budgetGb: string;
  enabled: boolean;
}

const EMPTY_FORM: ExitFormState = {
  kind: "node",
  serverId: "",
  label: "",
  region: "US",
  endpointHost: "",
  endpointPort: "8080",
  protocol: "http",
  username: "",
  password: "",
  expectedExitIp: "",
  budgetGb: "",
  enabled: true,
};

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24);
}

function exitToForm(exit: Exit): ExitFormState {
  return {
    kind: exit.kind,
    serverId: exit.server_id ? String(exit.server_id) : "",
    label: exit.label,
    region: exit.region,
    endpointHost: exit.endpoint_host,
    endpointPort: String(exit.endpoint_port),
    protocol: exit.protocol,
    username: exit.username ?? "",
    password: "",
    expectedExitIp: exit.expected_exit_ip ?? "",
    budgetGb: exit.bandwidth_budget_bytes ? bytesToGbString(exit.bandwidth_budget_bytes) : "",
    enabled: exit.enabled,
  };
}

interface ExitDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  exit?: Exit | null;
  onSaved: () => void;
}

function ExitDialog({ open, onOpenChange, exit, onSaved }: ExitDialogProps) {
  const { success, error: toastError } = useToast();
  const servers = useQuery({ ...serversQuery, enabled: open });
  const [form, setForm] = useState<ExitFormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    setErrors({});
    setForm(exit ? exitToForm(exit) : EMPTY_FORM);
  }, [open, exit]);

  const nodeServers = useMemo(
    () =>
      (servers.data ?? []).filter(
        (server) => server.roles === "exit" || server.roles === "both",
      ),
    [servers.data],
  );

  function update(patch: Partial<ExitFormState>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  const save = useMutation({
    mutationFn: () => {
      const budget = form.budgetGb.trim() ? humanFileSizeInputToBytes(form.budgetGb) : null;
      if (exit) {
        // PATCH /exits/{id} поддерживает enabled, region, expected_exit_ip и bandwidth_budget_bytes.
        return api.exits.update(exit.id, {
          enabled: form.enabled,
          region: form.region,
          expected_exit_ip: form.expectedExitIp.trim() || undefined,
          bandwidth_budget_bytes: budget,
          // PATCH принимает username/password для внешних выходов
          ...(form.kind === "external"
            ? {
                username: form.username.trim() || undefined,
                password: form.password || undefined,
              }
            : {}),
        });
      }
      const payload: ExitCreateRequest = {
        kind: form.kind,
        label: form.label.trim(),
        region: form.region,
        enabled: form.enabled,
        bandwidth_budget_bytes: budget,
        ...(form.kind === "node"
          ? { server_id: Number(form.serverId), protocol: "vless_reality" as ExitProtocol }
          : {
              endpoint_host: form.endpointHost.trim(),
              endpoint_port: Number(form.endpointPort),
              protocol: form.protocol,
              username: form.username.trim() || undefined,
              password: form.password || undefined,
              expected_exit_ip: form.expectedExitIp.trim() || undefined,
            }),
      };
      return api.exits.create(payload);
    },
    onSuccess: () => {
      success(exit ? "Выход обновлён" : "Выход добавлен");
      onSaved();
      onOpenChange(false);
    },
    onError: (mutationError) => toastError("Не удалось сохранить выход", errorMessage(mutationError)),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors: Record<string, string> = {};
    if (!exit) {
      if (!form.label.trim()) nextErrors.label = "Укажите метку (используется в тегах Xray)";
      else if (!/^[a-z0-9-]{1,24}$/.test(form.label.trim())) {
        nextErrors.label = "Метка: латиница в нижнем регистре, цифры и дефис, до 24 символов";
      }
      if (form.kind === "node" && !form.serverId) nextErrors.serverId = "Выберите сервер с ролью exit";
      if (form.kind === "external") {
        if (!form.endpointHost.trim()) nextErrors.endpointHost = "Укажите host прокси";
        const port = Number(form.endpointPort);
        if (!Number.isInteger(port) || port < 1 || port > 65535) nextErrors.endpointPort = "Порт 1–65535";
      }
    }
    if (form.region === "RU") nextErrors.region = "Российский регион запрещён для выхода (fail-closed)";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    save.mutate();
  }

  const isNode = form.kind === "node";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{exit ? `Выход «${exit.label}»` : "Новый выход"}</DialogTitle>
          <DialogDescription>
            Exit-узел или внешний резидентский прокси. Сайты с allowlist будут ходить только через
            выбранные выходы; при их недоступности — block.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={handleSubmit} noValidate>
          {!exit ? (
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  { value: "node", label: "Exit-узел", hint: "VLESS+REALITY interconnect" },
                  { value: "external", label: "Внешний прокси", hint: "HTTP CONNECT / SOCKS5" },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => update({ kind: option.value })}
                  className={cn(
                    "rounded-lg border p-3 text-left transition-colors",
                    form.kind === option.value
                      ? "border-primary bg-primary/5 ring-1 ring-primary"
                      : "hover:bg-accent",
                  )}
                >
                  <p className="text-sm font-medium">{option.label}</p>
                  <p className="text-xs text-muted-foreground">{option.hint}</p>
                </button>
              ))}
            </div>
          ) : null}

          {exit ? (
            <Alert variant="info">
              <AlertDescription>
                Через API можно изменить регион, ожидаемый exit-IP, бюджет трафика, включённость, а
                для внешних прокси — логин/пароль. Метка, endpoint и протокол неизменяемы — удалите
                выход и создайте заново.
              </AlertDescription>
            </Alert>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Метка" htmlFor="exit-label" error={errors.label} required hint="slug: a-z0-9-, до 24 символов">
              <Input
                id="exit-label"
                value={form.label}
                disabled={Boolean(exit)}
                onChange={(event) => update({ label: slugify(event.target.value) })}
                placeholder="res-us-1"
              />
            </FormField>
            <FormField label="Регион" error={errors.region} required>
              <Select value={form.region} onValueChange={(value) => update({ region: value })}>
                <SelectTrigger aria-label="Регион выхода">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {REGIONS.map((region) => (
                    <SelectItem key={region} value={region}>
                      {regionLabel(region)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>
          </div>

          {isNode ? (
            <FormField label="Сервер с ролью exit" error={errors.serverId} required>
              <Select
                value={form.serverId}
                onValueChange={(value) => {
                  const server = nodeServers.find((candidate) => String(candidate.id) === value);
                  update({
                    serverId: value,
                    label: form.label || slugify(server?.name ?? ""),
                    region: server?.region ?? form.region,
                  });
                }}
              >
                <SelectTrigger aria-label="Сервер выхода">
                  <SelectValue placeholder="Выберите сервер" />
                </SelectTrigger>
                <SelectContent>
                  {nodeServers.length === 0 ? (
                    <div className="px-2 py-3 text-sm text-muted-foreground">
                      Нет серверов с ролью exit — добавьте сервер на вкладке «Серверы».
                    </div>
                  ) : (
                    nodeServers.map((server) => (
                      <SelectItem key={server.id} value={String(server.id)}>
                        {server.name} · {regionLabel(server.region)}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </FormField>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Host" htmlFor="exit-host" error={errors.endpointHost} required className="sm:col-span-2">
                  <Input
                    id="exit-host"
                    value={form.endpointHost}
                    disabled={Boolean(exit)}
                    placeholder="proxy.provider.net"
                    onChange={(event) => update({ endpointHost: event.target.value, label: form.label || slugify(event.target.value) })}
                  />
                </FormField>
                <FormField label="Порт" htmlFor="exit-port" error={errors.endpointPort} required>
                  <Input
                    id="exit-port"
                    inputMode="numeric"
                    value={form.endpointPort}
                    disabled={Boolean(exit)}
                    onChange={(event) => update({ endpointPort: event.target.value })}
                  />
                </FormField>
              </div>
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Протокол">
                  <Select
                    value={form.protocol}
                    disabled={Boolean(exit)}
                    onValueChange={(value) => update({ protocol: value as ExitProtocol })}
                  >
                    <SelectTrigger aria-label="Протокол прокси">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="http">HTTP CONNECT</SelectItem>
                      <SelectItem value="socks5">SOCKS5</SelectItem>
                    </SelectContent>
                  </Select>
                </FormField>
                <FormField label="Логин" htmlFor="exit-user">
                  <Input
                    id="exit-user"
                    value={form.username}
                    autoComplete="off"
                    onChange={(event) => update({ username: event.target.value })}
                  />
                </FormField>
                <FormField label="Пароль" htmlFor="exit-password" hint={exit ? "Пусто — не менять" : undefined}>
                  <Input
                    id="exit-password"
                    type="password"
                    autoComplete="new-password"
                    value={form.password}
                    onChange={(event) => update({ password: event.target.value })}
                  />
                </FormField>
              </div>
              <FormField
                label="Ожидаемый exit-IP"
                htmlFor="exit-ip"
                hint="Health-check сравнит фактический IP; при несовпадении выход станет down"
              >
                <Input
                  id="exit-ip"
                  value={form.expectedExitIp}
                  placeholder="198.51.100.7"
                  onChange={(event) => update({ expectedExitIp: event.target.value })}
                />
              </FormField>
            </>
          )}

          <FormField
            label="Бюджет трафика, ГБ"
            htmlFor="exit-budget"
            hint="Опционально: при приближении к лимиту появится инцидент, при исчерпании выход отключится"
          >
            <Input
              id="exit-budget"
              inputMode="decimal"
              value={form.budgetGb}
              placeholder="200"
              onChange={(event) => update({ budgetGb: event.target.value })}
            />
          </FormField>

          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">Выход включён</p>
              <p className="text-xs text-muted-foreground">Отключённый выход сразу исключается из конфигурации</p>
            </div>
            <Switch checked={form.enabled} onCheckedChange={(checked) => update({ enabled: checked })} aria-label="Включить выход" />
          </div>

          <Alert variant="warning">
            <TriangleAlert aria-hidden />
            <AlertDescription>
              Российский IP недопустим как выход: health-check помечает такой выход down, а сайты с
              ним блокируются (fail-closed).
            </AlertDescription>
          </Alert>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : null}
              {exit ? "Сохранить" : "Добавить выход"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface DeleteExitState {
  exit: Exit;
}

export function ExitsPage() {
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();
  const exits = useQuery(exitsQuery);
  const sites = useQuery(sitesQuery);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Exit | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteExitState | null>(null);
  const [toggleTarget, setToggleTarget] = useState<Exit | null>(null);
  const [job, setJob] = useState<{ id: number; title: string } | null>(null);

  const exitById = useMemo(() => {
    const map = new Map<number, Exit>();
    for (const exit of exits.data ?? []) map.set(exit.id, exit);
    return map;
  }, [exits.data]);

  const affectedRules = (exitId: number) =>
    (sites.data ?? []).filter((rule) => rule.allowed_exit_ids.includes(exitId));

  /** Имена правил, где выход разрешён: backend отдаёт used_by_sites, иначе считаем по /sites. */
  const inUseNames = (exit: Exit): string[] =>
    exit.used_by_sites && exit.used_by_sites.length > 0
      ? exit.used_by_sites
      : affectedRules(exit.id).map((rule) => rule.name);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.exits });
    void queryClient.invalidateQueries({ queryKey: queryKeys.sites });
  };

  const remove = useMutation({
    mutationFn: (id: number) => api.exits.remove(id),
    onSuccess: () => {
      success("Выход удалён");
      setDeleteTarget(null);
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось удалить выход", errorMessage(mutationError)),
  });

  const toggle = useMutation({
    mutationFn: ({ exit, enabled }: { exit: Exit; enabled: boolean }) =>
      api.exits.update(exit.id, { enabled }),
    onSuccess: (_data, variables) => {
      success(variables.enabled ? "Выход включён" : "Выход отключён");
      setToggleTarget(null);
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось изменить выход", errorMessage(mutationError)),
  });

  const check = useMutation({
    mutationFn: (exit: Exit) => api.exits.check(exit.id),
    onSuccess: (result, exit) => {
      if (result.job_id) {
        setJob({ id: result.job_id, title: `Проверка выхода «${exit.label}»` });
      } else if (result.health === "up") {
        success(
          `Выход «${exit.label}» доступен`,
          result.last_delay_ms ? `Задержка ${result.last_delay_ms} мс` : undefined,
        );
      } else {
        toastError(`Выход «${exit.label}» недоступен`, "Сайты с этим выходом блокируются (fail-closed)");
      }
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось проверить выход", errorMessage(mutationError)),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Выходы"
        description="Реестр exit-узлов и внешних прокси. Сайты с allowlist ходят только через разрешённые выходы."
        actions={
          <Button
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
          >
            <Plus />
            Добавить выход
          </Button>
        }
      />

      <QueryState
        isLoading={exits.isLoading}
        isError={exits.isError}
        error={exits.error}
        onRetry={() => void exits.refetch()}
        rows={4}
      >
        {(exits.data ?? []).length === 0 ? (
          <EmptyState
            icon={Waypoints}
            title="Выходов пока нет"
            description="Добавьте exit-узел (сервер с ролью exit) или внешний резидентский прокси, чтобы выпускать трафик сайтов."
            action={
              <Button onClick={() => setDialogOpen(true)}>
                <Plus />
                Добавить выход
              </Button>
            }
          />
        ) : (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Выход</TableHead>
                    <TableHead>Тип / протокол</TableHead>
                    <TableHead>Endpoint</TableHead>
                    <TableHead>Здоровье</TableHead>
                    <TableHead>Трафик</TableHead>
                    <TableHead>Включён</TableHead>
                    <TableHead className="w-10" aria-label="Действия" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(exits.data ?? []).map((exit) => {
                    const rules = affectedRules(exit.id);
                    const budget = exit.bandwidth_budget_bytes ?? null;
                    const used = exit.used_bytes ?? 0;
                    return (
                      <TableRow key={exit.id}>
                        <TableCell>
                          <div className="font-medium">{exit.label}</div>
                          <div className="text-xs text-muted-foreground">{regionLabel(exit.region)}</div>
                        </TableCell>
                        <TableCell>
                          <div className="text-sm">
                            {exit.kind === "node" ? "Exit-узел" : "Внешний прокси"}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {exitProtocolLabel(exit.protocol)}
                          </div>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {exit.endpoint_host}:{exit.endpoint_port}
                        </TableCell>
                        <TableCell>
                          <div className="space-y-1">
                            <ExitHealthBadge health={exit.health} />
                            <div className="text-xs text-muted-foreground">
                              {exit.last_check_at ? formatRelative(exit.last_check_at) : "не проверялся"}
                              {exit.last_delay_ms ? ` · ${exit.last_delay_ms} мс` : ""}
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          {budget ? (
                            <div className="w-36 space-y-1">
                              <div className="flex justify-between text-xs text-muted-foreground">
                                <span>{formatBytes(used)}</span>
                                <span>{formatPercent(percent(used, budget))}</span>
                              </div>
                              <Progress value={percent(used, budget)} />
                              <p className="text-[11px] text-muted-foreground">бюджет {formatBytes(budget)}</p>
                            </div>
                          ) : (
                            <span className="text-sm">{formatBytes(used)}</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Switch
                            checked={exit.enabled}
                            onCheckedChange={(checked) => {
                              if (!checked && rules.length > 0) setToggleTarget(exit);
                              else toggle.mutate({ exit, enabled: checked });
                            }}
                            disabled={toggle.isPending}
                            aria-label={`Включить выход ${exit.label}`}
                          />
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="icon" aria-label={`Действия для ${exit.label}`}>
                                <Ellipsis className="size-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem onClick={() => check.mutate(exit)}>
                                <HeartPulse className="size-4" />
                                Проверить сейчас
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() => {
                                  setEditing(exit);
                                  setDialogOpen(true);
                                }}
                              >
                                <Pencil className="size-4" />
                                Изменить
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                destructive
                                onClick={() => setDeleteTarget({ exit })}
                              >
                                <Trash2 className="size-4" />
                                Удалить
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </QueryState>

      <ExitDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setEditing(null);
        }}
        exit={editing}
        onSaved={invalidate}
      />

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить выход «{deleteTarget?.exit.label}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Выход исчезнет из конфигураций entry-узлов. Сайты, у которых он был единственным
              разрешённым, начнут блокироваться (fail-closed).
            </AlertDialogDescription>
            {deleteTarget && inUseNames(deleteTarget.exit).length > 0 ? (
              <Alert variant="warning">
                <CircleAlert aria-hidden />
                <AlertDescription>
                  Выход используется в правилах: {inUseNames(deleteTarget.exit).join(", ")}. Backend
                  отклонит удаление (exit_in_use) — сначала уберите его из allowlist этих правил.
                </AlertDescription>
              </Alert>
            ) : null}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              destructive
              disabled={Boolean(deleteTarget && inUseNames(deleteTarget.exit).length > 0) || remove.isPending}
              onClick={() => {
                if (deleteTarget) remove.mutate(deleteTarget.exit.id);
              }}
            >
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={toggleTarget !== null} onOpenChange={(open) => !open && setToggleTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Отключить выход «{toggleTarget?.label}»?</AlertDialogTitle>
            <AlertDialogDescription>
              <span className="flex gap-2">
                <CircleAlert className="mt-0.5 size-4 shrink-0" />
                <span>
                  Правила, где он разрешён:{" "}
                  {toggleTarget
                    ? affectedRules(toggleTarget.id)
                        .map((rule) => `«${rule.name}»`)
                        .join(", ")
                    : ""}
                  . Если других доступных выходов не останется, трафик этих сайтов будет
                  блокироваться — это ожидаемое поведение fail-closed.
                </span>
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              destructive
              onClick={() => {
                if (toggleTarget) toggle.mutate({ exit: toggleTarget, enabled: false });
              }}
            >
              Отключить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <JobDialog
        open={job !== null}
        onOpenChange={(open) => !open && setJob(null)}
        jobId={job?.id ?? null}
        title={job?.title ?? "Проверка выхода"}
        onDone={() => {
          invalidate();
          success("Проверка завершена");
        }}
      />

      {exits.data ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Power className="size-3.5" aria-hidden /> Всего выходов: {(exits.data ?? []).length} ·
          отключено: {(exits.data ?? []).filter((exit) => !exit.enabled).length}
          {exitById.size === 0 ? " · реестр пуст" : ""}
        </p>
      ) : null}
    </div>
  );
}
