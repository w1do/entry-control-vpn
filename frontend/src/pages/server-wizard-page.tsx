import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleAlert,
  Loader2,
  RefreshCw,
  Server,
  ShieldCheck,
  Terminal,
  Trash2,
  TriangleAlert,
  Wifi,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { JobProgress } from "@/components/common/job-progress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type {
  AuthType,
  PreflightResult,
  ServerCreateRequest,
  ServerRole,
} from "@/lib/api-types";
import { queryKeys } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import { cn, regionLabel } from "@/lib/utils";

const STEPS = [
  { key: "ssh", label: "SSH-доступ" },
  { key: "preflight", label: "Проверка" },
  { key: "roles", label: "Роли и регион" },
  { key: "provision", label: "Провижининг" },
  { key: "result", label: "Результат" },
] as const;

const REGIONS = ["RU", "SG", "US", "NL", "DE", "FI", "TR", "KZ", "JP", "GB", "FR", "AE", "OTHER"];

const PREFLIGHT_ERRORS: Record<string, string> = {
  host_unreachable: "Хост недоступен: проверьте адрес, порт и сетевую доступность.",
  auth_failed: "Ошибка SSH-аутентификации: неверный пароль или приватный ключ.",
  sudo_unavailable: "Пользователь не имеет sudo: провижининг невозможен.",
  permission_denied: "Недостаточно прав для провижининга.",
  port_in_use: "Порт 443 занят другим сервисом.",
};

interface SshFormState {
  host: string;
  port: string;
  user: string;
  authType: AuthType;
  password: string;
  privateKey: string;
}

interface RolesFormState {
  name: string;
  roles: ServerRole;
  region: string;
}

function StepIndicator({ current }: { current: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
      {STEPS.map((step, index) => {
        const number = index + 1;
        const done = current > number;
        const active = current === number;
        return (
          <li key={step.key} className="flex items-center gap-2">
            <span
              className={cn(
                "flex size-6 items-center justify-center rounded-full border text-xs font-semibold",
                done
                  ? "border-success bg-success text-success-foreground"
                  : active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-muted text-muted-foreground",
              )}
            >
              {done ? <Check className="size-3.5" /> : number}
            </span>
            <span className={cn(done || active ? "font-medium" : "text-muted-foreground")}>
              {step.label}
            </span>
            {index < STEPS.length - 1 ? (
              <span className="mx-1 hidden h-px w-6 bg-border sm:block" aria-hidden />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export function ServerWizardPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();

  const [step, setStep] = useState(1);
  const [ssh, setSsh] = useState<SshFormState>({
    host: "",
    port: "22",
    user: "root",
    authType: "key",
    password: "",
    privateKey: "",
  });
  const [sshErrors, setSshErrors] = useState<Record<string, string>>({});
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [rolesForm, setRolesForm] = useState<RolesFormState>({ name: "", roles: "entry", region: "RU" });
  const [rolesErrors, setRolesErrors] = useState<Record<string, string>>({});
  const [serverId, setServerId] = useState<number | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [provisionError, setProvisionError] = useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");

  const preflightMutation = useMutation({
    mutationFn: () => {
      const payload = {
        host: ssh.host.trim(),
        port: Number(ssh.port),
        user: ssh.user.trim(),
        auth_type: ssh.authType,
        ...(ssh.authType === "password" ? { password: ssh.password } : { private_key: ssh.privateKey }),
      };
      return api.servers.preflight(payload);
    },
    onSuccess: (result) => {
      setPreflight(result);
      setPreflightError(null);
      if (!rolesForm.name) setRolesForm((current) => ({ ...current, name: ssh.host.trim() }));
      setStep(2);
    },
    onError: (mutationError) => {
      const message = errorMessage(mutationError);
      const code = (mutationError as { code?: string }).code ?? "";
      setPreflightError(PREFLIGHT_ERRORS[code] ?? message);
    },
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const payload: ServerCreateRequest = {
        name: rolesForm.name.trim(),
        host: ssh.host.trim(),
        port: Number(ssh.port),
        user: ssh.user.trim(),
        auth_type: ssh.authType,
        roles: rolesForm.roles,
        region: rolesForm.region,
        ...(ssh.authType === "password" ? { password: ssh.password } : { private_key: ssh.privateKey }),
      };
      return api.servers.create(payload);
    },
    onSuccess: (result) => {
      setServerId(result.server_id);
      setJobId(result.job_id);
      setProvisionError(null);
      setStep(4);
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
    },
    onError: (mutationError) => toastError("Не удалось создать сервер", errorMessage(mutationError)),
  });

  const retryMutation = useMutation({
    mutationFn: () => {
      if (!serverId) throw new Error("Сервер не создан");
      return api.servers.action(serverId, "reinstall");
    },
    onSuccess: (result) => {
      setJobId(result.job_id);
      setProvisionError(null);
    },
    onError: (mutationError) => toastError("Не удалось повторить провижининг", errorMessage(mutationError)),
  });

  const forceDelete = useMutation({
    mutationFn: () => {
      if (!serverId) throw new Error("Сервер не создан");
      return api.servers.remove(serverId, rolesForm.name.trim(), true);
    },
    onSuccess: () => {
      setDeleteDialogOpen(false);
      success("Запись сервера удалена");
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
      void navigate({ to: "/servers" });
    },
    onError: (mutationError) => toastError("Не удалось удалить запись", errorMessage(mutationError)),
  });

  function validateSsh(): boolean {
    const errors: Record<string, string> = {};
    if (!ssh.host.trim()) errors.host = "Укажите host или IP-адрес";
    else if (!/^[a-zA-Z0-9.:[\]-]+$/.test(ssh.host.trim())) errors.host = "Некорректный host";
    const port = Number(ssh.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) errors.port = "Порт должен быть числом 1–65535";
    if (!ssh.user.trim()) errors.user = "Укажите SSH-пользователя";
    if (ssh.authType === "password" && !ssh.password) errors.password = "Введите SSH-пароль";
    if (ssh.authType === "key" && !ssh.privateKey.trim()) errors.privateKey = "Вставьте приватный ключ";
    setSshErrors(errors);
    return Object.keys(errors).length === 0;
  }

  function validateRoles(): boolean {
    const errors: Record<string, string> = {};
    if (!rolesForm.name.trim()) errors.name = "Укажите имя сервера";
    if (!/^[a-zA-Z0-9._-]+$/.test(rolesForm.name.trim())) {
      errors.name = "Имя может содержать латиницу, цифры, точку, дефис и подчёркивание";
    }
    if (!rolesForm.region) errors.region = "Выберите регион";
    if ((rolesForm.roles === "exit" || rolesForm.roles === "both") && rolesForm.region === "RU") {
      errors.region = "Выход не может быть в России: RU-IP запрещён как exit (fail-closed)";
    }
    setRolesErrors(errors);
    return Object.keys(errors).length === 0;
  }

  function handleSshSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPreflightError(null);
    if (!validateSsh()) return;
    preflightMutation.mutate();
  }

  function handleRolesSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!validateRoles()) return;
    createMutation.mutate();
  }

  const isExitRole = rolesForm.roles === "exit" || rolesForm.roles === "both";

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2 mb-2">
          <Link to="/servers">
            <ArrowLeft />
            К серверам
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">Добавить сервер</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Панель подключится по SSH, проверит сервер, установит Xray и агента — вручную ничего
          настраивать не нужно.
        </p>
      </div>

      <StepIndicator current={step} />
      <Separator />

      {step === 1 ? (
        <Card>
          <CardHeader>
            <CardTitle>Шаг 1. SSH-доступ</CardTitle>
            <CardDescription>
              Доступ нужен только на время провижининга и последующих действий. Секреты шифруются
              и не возвращаются через API.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSshSubmit} noValidate>
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Host / IP" htmlFor="wizard-host" error={sshErrors.host} required className="sm:col-span-2">
                  <Input
                    id="wizard-host"
                    value={ssh.host}
                    placeholder="203.0.113.10"
                    autoComplete="off"
                    onChange={(event) => setSsh((current) => ({ ...current, host: event.target.value }))}
                  />
                </FormField>
                <FormField label="SSH-порт" htmlFor="wizard-port" error={sshErrors.port} required>
                  <Input
                    id="wizard-port"
                    inputMode="numeric"
                    value={ssh.port}
                    onChange={(event) => setSsh((current) => ({ ...current, port: event.target.value }))}
                  />
                </FormField>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Пользователь" htmlFor="wizard-user" error={sshErrors.user} required>
                  <Input
                    id="wizard-user"
                    value={ssh.user}
                    autoComplete="off"
                    onChange={(event) => setSsh((current) => ({ ...current, user: event.target.value }))}
                  />
                </FormField>
                <div className="space-y-1.5">
                  <Label>Аутентификация</Label>
                  <div className="grid grid-cols-2 gap-2">
                    {(
                      [
                        { value: "key", label: "Ключ" },
                        { value: "password", label: "Пароль" },
                      ] as const
                    ).map((option) => (
                      <Button
                        key={option.value}
                        type="button"
                        variant={ssh.authType === option.value ? "default" : "outline"}
                        onClick={() => setSsh((current) => ({ ...current, authType: option.value }))}
                      >
                        {option.label}
                      </Button>
                    ))}
                  </div>
                </div>
              </div>

              {ssh.authType === "password" ? (
                <FormField label="SSH-пароль" htmlFor="wizard-password" error={sshErrors.password} required>
                  <Input
                    id="wizard-password"
                    type="password"
                    autoComplete="new-password"
                    value={ssh.password}
                    onChange={(event) => setSsh((current) => ({ ...current, password: event.target.value }))}
                  />
                </FormField>
              ) : (
                <FormField
                  label="Приватный ключ"
                  htmlFor="wizard-key"
                  error={sshErrors.privateKey}
                  required
                  hint="OpenSSH-формат, целиком. Ключ будет зашифрован и не попадёт в логи."
                >
                  <Textarea
                    id="wizard-key"
                    rows={7}
                    className="font-mono text-xs"
                    placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n..."}
                    value={ssh.privateKey}
                    onChange={(event) => setSsh((current) => ({ ...current, privateKey: event.target.value }))}
                  />
                </FormField>
              )}

              {preflightError ? (
                <Alert variant="destructive">
                  <CircleAlert aria-hidden />
                  <div>
                    <AlertTitle>Проверка не прошла</AlertTitle>
                    <AlertDescription>{preflightError}</AlertDescription>
                  </div>
                </Alert>
              ) : null}

              <div className="flex justify-end gap-2">
                <Button asChild variant="outline">
                  <Link to="/servers">Отмена</Link>
                </Button>
                <Button type="submit" disabled={preflightMutation.isPending}>
                  {preflightMutation.isPending ? <Loader2 className="animate-spin" /> : <Wifi />}
                  {preflightMutation.isPending ? "Проверка SSH…" : "Проверить SSH"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {step === 2 ? (
        <Card>
          <CardHeader>
            <CardTitle>Шаг 2. Проверка и предпросмотр</CardTitle>
            <CardDescription>
              Данные получены по SSH. Вернитесь назад, если нужно исправить доступ.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {preflight ? (
              <>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Операционная система</p>
                    <p className="mt-1 text-sm font-medium">{preflight.os ?? "—"}</p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Архитектура</p>
                    <p className="mt-1 text-sm font-medium">{preflight.arch ?? "—"}</p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Sudo</p>
                    <p className="mt-1 text-sm font-medium">
                      {preflight.sudo ? "Доступен" : "Недоступен"}
                    </p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Порт 443/tcp</p>
                    <p className="mt-1 text-sm font-medium">
                      {preflight.port_443_free ? "Свободен" : "Занят"}
                    </p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Существующая установка</p>
                    <p className="mt-1 text-sm font-medium">
                      {preflight.existing_install ? "Обнаружена (будет переиспользована)" : "Нет"}
                    </p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Отпечаток host key</p>
                    <p className="mt-1 break-all font-mono text-xs">{preflight.host_fingerprint ?? "—"}</p>
                  </div>
                </div>

                {(preflight.warnings ?? []).length > 0 ? (
                  <Alert variant="warning">
                    <TriangleAlert aria-hidden />
                    <div>
                      <AlertTitle>Предупреждения</AlertTitle>
                      <AlertDescription>
                        <ul className="list-inside list-disc">
                          {(preflight.warnings ?? []).map((warning) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      </AlertDescription>
                    </div>
                  </Alert>
                ) : (
                  <Alert variant="success">
                    <CheckCircle2 aria-hidden />
                    <div>
                      <AlertTitle>Сервер готов к провижинингу</AlertTitle>
                      <AlertDescription>
                        Xray, vpn-agent, юниты systemd и firewall будут установлены автоматически.
                      </AlertDescription>
                    </div>
                  </Alert>
                )}
              </>
            ) : null}

            <div className="flex justify-between gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                <ArrowLeft />
                Назад
              </Button>
              <Button onClick={() => setStep(3)}>
                Далее
                <ArrowRight />
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {step === 3 ? (
        <Card>
          <CardHeader>
            <CardTitle>Шаг 3. Роли и регион</CardTitle>
            <CardDescription>
              Entry принимает клиентов, exit выпускает трафик сайтов. Роль both объединяет оба
              назначения.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleRolesSubmit} noValidate>
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Имя сервера" htmlFor="wizard-name" error={rolesErrors.name} required>
                  <Input
                    id="wizard-name"
                    value={rolesForm.name}
                    autoComplete="off"
                    onChange={(event) => setRolesForm((current) => ({ ...current, name: event.target.value }))}
                  />
                </FormField>
                <FormField label="Регион" error={rolesErrors.region} required>
                  <Select
                    value={rolesForm.region}
                    onValueChange={(value) => setRolesForm((current) => ({ ...current, region: value }))}
                  >
                    <SelectTrigger aria-label="Регион сервера">
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

              <div className="space-y-2">
                <Label>Роль</Label>
                <div className="grid gap-2 sm:grid-cols-3">
                  {(
                    [
                      { value: "entry", label: "Entry", hint: "inbound VLESS+REALITY для клиентов" },
                      { value: "exit", label: "Exit", hint: "выпуск трафика сайтов" },
                      { value: "both", label: "Both", hint: "entry и exit на одном узле" },
                    ] as const
                  ).map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setRolesForm((current) => ({ ...current, roles: option.value }))}
                      className={cn(
                        "rounded-lg border p-3 text-left transition-colors",
                        rolesForm.roles === option.value
                          ? "border-primary bg-primary/5 ring-1 ring-primary"
                          : "hover:bg-accent",
                      )}
                    >
                      <p className="text-sm font-medium">{option.label}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">{option.hint}</p>
                    </button>
                  ))}
                </div>
              </div>

              {isExitRole && rolesForm.region === "RU" ? (
                <Alert variant="destructive">
                  <CircleAlert aria-hidden />
                  <div>
                    <AlertTitle>RU-регион недопустим для выхода</AlertTitle>
                    <AlertDescription>
                      Российский IP не может использоваться как exit — правило fail-closed. Выберите
                      зарубежный регион или роль entry.
                    </AlertDescription>
                  </div>
                </Alert>
              ) : null}

              <div className="flex justify-between gap-2">
                <Button type="button" variant="outline" onClick={() => setStep(2)}>
                  <ArrowLeft />
                  Назад
                </Button>
                <Button type="submit" disabled={createMutation.isPending}>
                  {createMutation.isPending ? <Loader2 className="animate-spin" /> : <Server />}
                  {createMutation.isPending ? "Создание…" : "Начать провижининг"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {step === 4 ? (
        <Card>
          <CardHeader>
            <CardTitle>Шаг 4. Провижининг</CardTitle>
            <CardDescription>
              Прогресс приходит через SSE. На время установки панель можно не закрывать.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <JobProgress
              jobId={jobId}
              title="Провижининг узла"
              successLabel="Провижининг завершён"
              onDone={() => setStep(5)}
              onError={(message) => {
                setProvisionError(message);
                void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
              }}
            />

            {provisionError ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => retryMutation.mutate()} disabled={retryMutation.isPending}>
                  <RefreshCw />
                  Повторить провижининг
                </Button>
                <Button variant="destructive" onClick={() => setDeleteDialogOpen(true)}>
                  <Trash2 />
                  Удалить запись
                </Button>
              </div>
            ) : null}

            <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Удалить запись сервера «{rolesForm.name}»?</DialogTitle>
                  <DialogDescription>
                    Если узел частично настроен, он может остаться в таком состоянии. Панель
                    перестанет им управлять. Удаление выполняется принудительно.
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-1.5">
                  <Label htmlFor="wizard-delete-confirm">Введите имя сервера для подтверждения</Label>
                  <Input
                    id="wizard-delete-confirm"
                    value={deleteConfirm}
                    onChange={(event) => setDeleteConfirm(event.target.value)}
                    placeholder={rolesForm.name}
                  />
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
                    Отмена
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={deleteConfirm !== rolesForm.name.trim() || forceDelete.isPending}
                    onClick={() => forceDelete.mutate()}
                  >
                    {forceDelete.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
                    Удалить запись
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </CardContent>
        </Card>
      ) : null}

      {step === 5 ? (
        <Card>
          <CardHeader>
            <CardTitle>Шаг 5. Результат</CardTitle>
            <CardDescription>Узел готов к работе.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Alert variant="success">
              <CheckCircle2 aria-hidden />
              <div>
                <AlertTitle>Сервер «{rolesForm.name}» добавлен</AlertTitle>
                <AlertDescription>
                  Xray и агент установлены, сервисы запущены. Узел появится в статусе online после
                  первого heartbeat.
                </AlertDescription>
              </div>
            </Alert>

            <ol className="space-y-2 text-sm text-muted-foreground">
              <li className="flex gap-2">
                <Terminal className="mt-0.5 size-4 shrink-0" />
                Настройте выходы и профиль маршрутизации — без него клиентский трафик блокируется.
              </li>
              <li className="flex gap-2">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" />
                Создайте клиента и выдайте QR/ссылку.
              </li>
            </ol>

            <div className="flex flex-wrap gap-2">
              <Button asChild>
                <Link to="/servers/$serverId" params={{ serverId: String(serverId) }}>
                  Перейти к серверу
                  <ArrowRight />
                </Link>
              </Button>
              <Button asChild variant="outline">
                <Link to="/sites" search={{ focus: "default" }}>
                  Настроить маршрутизацию
                </Link>
              </Button>
              <Button asChild variant="outline">
                <Link to="/clients">Создать клиента</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

