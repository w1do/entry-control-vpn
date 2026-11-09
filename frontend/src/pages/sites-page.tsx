import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ChevronRight,
  CircleAlert,
  Cog,
  Globe2,
  Loader2,
  Lock,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { JobDialog } from "@/components/common/job-dialog";
import { QueryState } from "@/components/common/query-state";
import { AllowlistPicker } from "@/components/sites/allowlist-picker";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
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
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { RouteCheckResult, SiteMatchType, SitePreset, SiteRule } from "@/lib/api-types";
import {
  computeRoutingStatus,
  exitsQuery,
  presetsQuery,
  queryKeys,
  routingProfileQuery,
  routingRevisionsQuery,
  sitesQuery,
} from "@/lib/queries";
import { useToast } from "@/lib/toast";
import { cn, matchTypeLabel, regionLabel } from "@/lib/utils";

const ROUTE_KIND_LABELS: Record<string, { label: string; variant: "success" | "default" | "warning" | "destructive" | "muted" }> = {
  ru_direct: { label: "RU → direct", variant: "success" },
  site_rule: { label: "Правило сайта", variant: "default" },
  default: { label: "Выход по умолчанию", variant: "warning" },
  block: { label: "Блокировка", variant: "destructive" },
};

/* ---------- Диалог добавления сайта ---------- */

interface AddSiteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  presets: SitePreset[];
  exits: Parameters<typeof AllowlistPicker>[0]["exits"];
  onSaved: () => void;
}

type AddMode = { type: "preset"; preset: SitePreset } | { type: "custom" } | null;

function presetDomains(preset: SitePreset): string[] {
  return preset.domains ?? preset.values ?? [];
}

function AddSiteDialog({ open, onOpenChange, presets, exits, onSaved }: AddSiteDialogProps) {
  const { success } = useToast();
  const [mode, setMode] = useState<AddMode>(null);
  const [name, setName] = useState("");
  const [matchType, setMatchType] = useState<SiteMatchType>("domain");
  const [valuesText, setValuesText] = useState("");
  const [allowed, setAllowed] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setMode(null);
      setName("");
      setMatchType("domain");
      setValuesText("");
      setAllowed([]);
      setError(null);
    }
  }, [open]);

  const values = useMemo(
    () =>
      valuesText
        .split(/[\n,;]+/)
        .map((value) => value.trim())
        .filter(Boolean),
    [valuesText],
  );

  const save = useMutation({
    mutationFn: () => {
      const preset = mode?.type === "preset" ? mode.preset : null;
      return api.sites.create({
        name: name.trim(),
        match_type: matchType,
        values: preset ? presetDomains(preset) : values,
        allowed_exit_ids: allowed,
        preset_key: preset?.key ?? null,
        enabled: true,
      });
    },
    onSuccess: () => {
      success("Правило добавлено");
      onSaved();
      onOpenChange(false);
    },
    onError: (mutationError) => {
      setError(errorMessage(mutationError));
    },
  });

  function pickPreset(preset: SitePreset) {
    setMode({ type: "preset", preset });
    setName(preset.label ?? preset.name ?? preset.key);
    setMatchType(preset.match_type ?? "domain");
    setValuesText(presetDomains(preset).join("\n"));
    setAllowed([]);
    setError(null);
  }

  function pickCustom() {
    setMode({ type: "custom" });
    setName("");
    setMatchType("domain");
    setValuesText("");
    setAllowed([]);
    setError(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!mode) return;
    if (!name.trim()) {
      setError("Укажите название правила");
      return;
    }
    if (values.length === 0) {
      setError("Добавьте хотя бы один домен");
      return;
    }
    if (allowed.length === 0) {
      setError("Выберите хотя бы один разрешённый выход — иначе правило заблокирует трафик");
      return;
    }
    save.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{mode ? "Настроить правило" : "Добавить сайт"}</DialogTitle>
          <DialogDescription>
            {mode
              ? "Выберите разрешённые выходы в порядке приоритета. Трафик не уйдёт дальше allowlist."
              : "Выберите пресет сервиса или задайте свои домены. Домены будут проксироваться только через выбранные выходы."}
          </DialogDescription>
        </DialogHeader>

        {!mode ? (
          <div className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-2">
              {presets.map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  onClick={() => pickPreset(preset)}
                  className="flex items-center justify-between gap-3 rounded-lg border p-3 text-left transition-colors hover:border-primary/60 hover:bg-accent"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{preset.label ?? preset.name ?? preset.key}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {presetDomains(preset).slice(0, 3).join(", ")}
                      {presetDomains(preset).length > 3 ? ` и ещё ${presetDomains(preset).length - 3}` : ""}
                    </p>
                  </div>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                </button>
              ))}
              <button
                type="button"
                onClick={pickCustom}
                className="flex items-center justify-between gap-3 rounded-lg border border-dashed p-3 text-left transition-colors hover:border-primary/60 hover:bg-accent"
              >
                <div>
                  <p className="text-sm font-medium">Свои домены</p>
                  <p className="text-xs text-muted-foreground">domain / wildcard / regexp / geosite</p>
                </div>
                <Plus className="size-4 shrink-0 text-muted-foreground" />
              </button>
            </div>
            <div className="flex justify-end">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Отмена
              </Button>
            </div>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={handleSubmit} noValidate>
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField label="Название" htmlFor="site-name" required>
                <Input
                  id="site-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Anthropic"
                />
              </FormField>
              <FormField label="Тип совпадения">
                <Select value={matchType} onValueChange={(value) => setMatchType(value as SiteMatchType)}>
                  <SelectTrigger aria-label="Тип совпадения">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="domain">domain</SelectItem>
                    <SelectItem value="wildcard">wildcard</SelectItem>
                    <SelectItem value="regexp">regexp</SelectItem>
                    <SelectItem value="geosite">geosite</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>
            </div>

            <FormField
              label="Домены"
              htmlFor="site-values"
              hint="По одному в строке или через запятую. Для wildcard используйте *.example.com"
            >
              <Textarea
                id="site-values"
                rows={4}
                className="font-mono text-xs"
                value={valuesText}
                onChange={(event) => setValuesText(event.target.value)}
              />
            </FormField>

            <AllowlistPicker exits={exits} value={allowed} onChange={setAllowed} />

            {error ? (
              <Alert variant="destructive">
                <CircleAlert aria-hidden />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setMode(null)}>
                Назад
              </Button>
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
                Добавить правило
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ---------- Диалог allowlist правила ---------- */

interface AllowlistDialogProps {
  rule: SiteRule | null;
  onOpenChange: (open: boolean) => void;
  exits: Parameters<typeof AllowlistPicker>[0]["exits"];
  onSaved: () => void;
}

function AllowlistDialog({ rule, onOpenChange, exits, onSaved }: AllowlistDialogProps) {
  const { success } = useToast();
  const [allowed, setAllowed] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (rule) {
      setAllowed(rule.allowed_exit_ids);
      setError(null);
    }
  }, [rule]);

  const save = useMutation({
    mutationFn: () => {
      if (!rule) throw new Error("Правило не выбрано");
      return api.sites.update(rule.id, { allowed_exit_ids: allowed });
    },
    onSuccess: () => {
      success("Allowlist обновлён");
      onSaved();
      onOpenChange(false);
    },
    onError: (mutationError) => setError(errorMessage(mutationError)),
  });

  return (
    <Dialog open={rule !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Cog className="size-5 text-muted-foreground" />
            Разрешённые выходы: {rule?.name}
          </DialogTitle>
          <DialogDescription>
            Порядок важен: при недоступности первого выхода используется следующий из списка.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap gap-1.5">
          {(rule?.values ?? []).map((value) => (
            <Badge key={value} variant="muted" className="font-mono font-normal">
              {value}
            </Badge>
          ))}
        </div>

        <AllowlistPicker exits={exits} value={allowed} onChange={setAllowed} />

        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending || allowed.length === 0}>
            {save.isPending ? <Loader2 className="animate-spin" /> : null}
            Сохранить allowlist
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ---------- Проверка маршрута ---------- */

function RouteCheckCard() {
  const [domain, setDomain] = useState("");
  const [result, setResult] = useState<RouteCheckResult | null>(null);
  const check = useMutation({
    mutationFn: (value: string) => api.routing.check(value),
    onSuccess: (data) => setResult(data),
    onError: () => setResult(null),
  });

  const info = result ? ROUTE_KIND_LABELS[result.kind] ?? { label: result.kind, variant: "muted" as const } : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Search className="size-4 text-muted-foreground" />
          Проверить маршрут
        </CardTitle>
        <CardDescription>
          Показывает, куда уйдёт запрос для домена до применения: правило, выход или block.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            if (domain.trim()) check.mutate(domain.trim());
          }}
        >
          <Input
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
            placeholder="api.anthropic.com"
            aria-label="Домен для проверки"
            className="sm:flex-1"
          />
          <Button type="submit" variant="outline" disabled={check.isPending || !domain.trim()}>
            {check.isPending ? <Loader2 className="animate-spin" /> : <Search />}
            Проверить
          </Button>
        </form>

        {check.isError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden />
            <AlertDescription>{errorMessage(check.error)}</AlertDescription>
          </Alert>
        ) : null}

        {result && info ? (
          <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={info.variant}>{info.label}</Badge>
              {result.rule_id ? <span className="text-sm">правило #{result.rule_id}</span> : null}
              {result.exit_ids.length > 0 ? (
                <span className="text-sm text-muted-foreground">
                  выходы: {result.exit_ids.join(" → ")}
                </span>
              ) : null}
            </div>
            <p className="text-sm text-muted-foreground">{result.explanation}</p>
            {result.compiled_rule ? (
              <pre className="max-h-40 overflow-auto rounded-md border bg-background p-2 text-xs">
                {JSON.stringify(result.compiled_rule, null, 2)}
              </pre>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/* ---------- Страница ---------- */

export function SitesPage() {
  const search = useSearch({ from: "/_app/sites" });
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();
  const sites = useQuery(sitesQuery);
  const presets = useQuery(presetsQuery);
  const profile = useQuery(routingProfileQuery);
  const exits = useQuery(exitsQuery);
  const revisions = useQuery(routingRevisionsQuery);

  const [addOpen, setAddOpen] = useState(false);
  const [allowlistRule, setAllowlistRule] = useState<SiteRule | null>(null);
  const [deleteRule, setDeleteRule] = useState<SiteRule | null>(null);
  const [defaultIds, setDefaultIds] = useState<number[] | null>(null);
  const [enabledDraft, setEnabledDraft] = useState<boolean | null>(null);
  const [applyJob, setApplyJob] = useState<number | null>(null);
  const defaultSectionRef = useRef<HTMLDivElement | null>(null);

  const serverIds = profile.data?.default_exit_ids ?? [];
  const serverEnabled = profile.data?.enabled ?? false;
  const currentIds = defaultIds ?? serverIds;
  const currentEnabled = enabledDraft ?? serverEnabled;
  const dirty = defaultIds !== null || enabledDraft !== null;

  const availableExits = exits.data ?? [];
  const exitById = useMemo(() => {
    const map = new Map<number, (typeof availableExits)[number]>();
    for (const exit of availableExits) map.set(exit.id, exit);
    return map;
  }, [availableExits]);

  const routingStatus = computeRoutingStatus(profile.data, exits.data);
  const readyToApply = routingStatus.ready && !dirty;

  const latestRevision = revisions.data?.[0] ?? null;
  const revisionBadge = !latestRevision
    ? { text: "Ревизий нет", variant: "muted" as const }
    : latestRevision.status === "applied"
      ? { text: `Применено · ревизия #${latestRevision.id}`, variant: "success" as const }
      : latestRevision.status === "pending"
        ? { text: `Применяется · ревизия #${latestRevision.id}`, variant: "warning" as const }
        : { text: `Ошибка ревизии #${latestRevision.id}`, variant: "destructive" as const };
  const canSaveProfile = !currentEnabled || currentIds.length > 0;

  useEffect(() => {
    if (search.focus === "default") {
      const timer = setTimeout(() => {
        defaultSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [search.focus]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.sites });
    void queryClient.invalidateQueries({ queryKey: queryKeys.routingProfile });
    void queryClient.invalidateQueries({ queryKey: queryKeys.routingRevisions });
  };

  const saveProfile = useMutation({
    mutationFn: () => api.routing.saveProfile({ default_exit_ids: currentIds, enabled: currentEnabled }),
    onSuccess: () => {
      success("Профиль маршрутизации сохранён");
      setDefaultIds(null);
      setEnabledDraft(null);
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось сохранить профиль", errorMessage(mutationError)),
  });

  const applyProfile = useMutation({
    mutationFn: () => api.routing.apply(),
    onSuccess: (response) => {
      setApplyJob(response.job_id);
    },
    onError: (mutationError) => toastError("Не удалось применить профиль", errorMessage(mutationError)),
  });

  const toggleRule = useMutation({
    mutationFn: ({ rule, enabled }: { rule: SiteRule; enabled: boolean }) =>
      api.sites.update(rule.id, { enabled }),
    onSuccess: () => {
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось изменить правило", errorMessage(mutationError)),
  });

  const removeRule = useMutation({
    mutationFn: (rule: SiteRule) => api.sites.remove(rule.id),
    onSuccess: () => {
      success("Правило удалено");
      setDeleteRule(null);
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось удалить правило", errorMessage(mutationError)),
  });

  function moveDefault(id: number, direction: -1 | 1) {
    const index = currentIds.indexOf(id);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= currentIds.length) return;
    const next = [...currentIds];
    [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
    setDefaultIds(next);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Сайты и маршрутизация"
        description="Профиль обязателен: без выхода по умолчанию клиентский трафик блокируется."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => saveProfile.mutate()}
              disabled={!dirty || !canSaveProfile || saveProfile.isPending}
            >
              {saveProfile.isPending ? <Loader2 className="animate-spin" /> : null}
              Сохранить профиль
            </Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <span>
                  <Button
                    onClick={() => applyProfile.mutate()}
                    disabled={!readyToApply || applyProfile.isPending}
                  >
                    {applyProfile.isPending ? <Loader2 className="animate-spin" /> : <ArrowRight />}
                    Применить
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {dirty
                  ? "Сначала сохраните изменения профиля"
                  : !routingStatus.enabled
                    ? "Маршрутизация выключена"
                    : routingStatus.defaultExits.length === 0
                      ? "Не выбран выход по умолчанию"
                      : routingStatus.healthyDefaultExits.length === 0
                        ? "Ни один выход по умолчанию не доступен"
                        : "Скомпилировать и применить ревизию на entry-узлах"}
              </TooltipContent>
            </Tooltip>
          </>
        }
      />

      {!routingStatus.loading && !routingStatus.ready ? (
        <Alert variant="warning">
          <ShieldCheck aria-hidden />
          <div>
            <AlertTitle>Проксирование не настроено</AlertTitle>
            <AlertDescription>
              {routingStatus.issues.length > 0
                ? routingStatus.issues.join("; ")
                : routingStatus.enabled === false
                  ? "Маршрутизация выключена — entry-узлы работают в режиме block-all."
                  : routingStatus.defaultExits.length === 0
                    ? "Выход по умолчанию не выбран — клиентский трафик блокируется (fail-closed)."
                    : "Все выходы по умолчанию недоступны — трафик блокируется до восстановления выхода."}
            </AlertDescription>
          </div>
        </Alert>
      ) : null}

      <Card className="border-locked/40">
        <CardContent className="flex flex-col gap-3 p-5 sm:flex-row sm:items-start">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
            <Lock className="size-5" aria-hidden />
          </div>
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">Российские сайты — как есть (не проксируются)</h2>
              <Badge variant="muted" className="gap-1">
                <Lock className="size-3" />
                locked
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              Правила <code className="rounded bg-muted px-1">geosite:category-ru</code> и{" "}
              <code className="rounded bg-muted px-1">geoip:ru</code> жёстко зашиты в конфиг Xray и
              всегда направлены в <code className="rounded bg-muted px-1">direct</code>. Изменить
              это или добавить RU-домены в site rule нельзя (ошибка{" "}
              <code className="rounded bg-muted px-1">ru_direct_locked</code>).
            </p>
            <p className="text-xs text-muted-foreground">
              В умном профиле RU-сайты открываются напрямую с устройства (российский IP). В режиме
              полного туннеля (<code className="rounded bg-muted px-1">vless://</code>) RU-трафик
              выпускается напрямую с entry-узла — через exit-выходы он не идёт никогда.
            </p>
          </div>
        </CardContent>
      </Card>

      <div
        ref={defaultSectionRef}
        className={cn(
          "scroll-mt-20 rounded-xl transition-shadow",
          search.focus === "default" && !routingStatus.ready && "ring-2 ring-warning ring-offset-2 ring-offset-background",
        )}
      >
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>Выход по умолчанию</CardTitle>
                <CardDescription>
                  Зарубежные сайты без явного правила идут через этот упорядоченный список выходов.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant={revisionBadge.variant}>{revisionBadge.text}</Badge>
                <label className="flex items-center gap-2 text-sm">
                  <Switch
                    checked={currentEnabled}
                    onCheckedChange={(checked) => setEnabledDraft(checked)}
                    aria-label="Маршрутизация включена"
                  />
                  Включена
                </label>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {currentIds.length === 0 ? (
              <Alert variant="warning">
                <TriangleAlert aria-hidden />
                <div>
                  <AlertTitle>Выход по умолчанию не выбран</AlertTitle>
                  <AlertDescription>
                    Пока список пуст, маршрутизацию нельзя включить: весь клиентский трафик будет
                    блокироваться. Добавьте хотя бы один доступный выход.
                  </AlertDescription>
                </div>
              </Alert>
            ) : null}

            {availableExits.length === 0 ? (
              <EmptyState
                icon={Globe2}
                title="Нет доступных выходов"
                description="Сначала добавьте exit-узел или внешний прокси в разделе «Выходы»."
                action={
                  <Button asChild variant="outline" size="sm">
                    <a href="/exits">Перейти к выходам</a>
                  </Button>
                }
              />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {availableExits.map((exit) => {
                  const checked = currentIds.includes(exit.id);
                  const order = currentIds.indexOf(exit.id);
                  return (
                    <label
                      key={exit.id}
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-lg border p-2.5 text-sm transition-colors hover:bg-accent",
                        checked && "border-primary/60 bg-primary/5",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="size-4 accent-[var(--primary)]"
                        checked={checked}
                        onChange={() => {
                          setDefaultIds(
                            checked
                              ? currentIds.filter((id) => id !== exit.id)
                              : [...currentIds, exit.id],
                          );
                        }}
                        aria-label={`Выход по умолчанию: ${exit.label}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="font-medium">{exit.label}</span>
                          <span className="text-xs text-muted-foreground">{regionLabel(exit.region)}</span>
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {exit.enabled ? (exit.health === "up" ? "доступен" : exit.health === "down" ? "недоступен" : "не проверен") : "отключён"}
                          {exit.last_delay_ms ? ` · ${exit.last_delay_ms} мс` : ""}
                        </span>
                      </span>
                      {checked ? (
                        <span className="flex shrink-0 items-center gap-0.5">
                          <Badge variant="secondary">#{order + 1}</Badge>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-6"
                            onClick={(event) => {
                              event.preventDefault();
                              moveDefault(exit.id, -1);
                            }}
                            disabled={order === 0}
                            aria-label={`Выше: ${exit.label}`}
                          >
                            <ArrowUp className="size-3.5" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-6"
                            onClick={(event) => {
                              event.preventDefault();
                              moveDefault(exit.id, 1);
                            }}
                            disabled={order === currentIds.length - 1}
                            aria-label={`Ниже: ${exit.label}`}
                          >
                            <ArrowDown className="size-3.5" />
                          </Button>
                        </span>
                      ) : null}
                    </label>
                  );
                })}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">
                {dirty
                  ? "Изменения не сохранены — нажмите «Сохранить профиль»."
                  : "Порядок выходов применяется как приоритет балансировщика leastPing."}
              </p>
              <div className="flex gap-2">
                {dirty ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setDefaultIds(null);
                      setEnabledDraft(null);
                    }}
                  >
                    <X />
                    Отменить изменения
                  </Button>
                ) : null}
                <Button size="sm" onClick={() => saveProfile.mutate()} disabled={!dirty || !canSaveProfile || saveProfile.isPending}>
                  {saveProfile.isPending ? <Loader2 className="animate-spin" /> : null}
                  Сохранить
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-3">
          <div>
            <CardTitle>Правила по сайтам</CardTitle>
            <CardDescription>
              Каждое правило разрешает только выбранные выходы. Если все они недоступны — трафик
              блокируется (fail-closed).
            </CardDescription>
          </div>
          <Button onClick={() => setAddOpen(true)}>
            <Plus />
            Добавить сайт
          </Button>
        </CardHeader>
        <CardContent>
          <QueryState
            isLoading={sites.isLoading}
            isError={sites.isError}
            error={sites.error}
            onRetry={() => void sites.refetch()}
            rows={3}
          >
            {(sites.data ?? []).length === 0 ? (
              <EmptyState
                icon={Globe2}
                title="Правил пока нет"
                description="Добавьте Anthropic, OpenAI, Google, Telegram, GitHub или свои домены — трафик пойдёт только через выбранные выходы."
                action={
                  <Button onClick={() => setAddOpen(true)}>
                    <Plus />
                    Добавить сайт
                  </Button>
                }
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Правило</TableHead>
                      <TableHead>Домены</TableHead>
                      <TableHead>Разрешённые выходы</TableHead>
                      <TableHead>Статус</TableHead>
                      <TableHead className="w-20" aria-label="Действия" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(sites.data ?? []).map((rule) => {
                      const allowedExits = rule.allowed_exit_ids
                        .map((id) => exitById.get(id))
                        .filter((exit): exit is NonNullable<typeof exit> => Boolean(exit));
                      const hasHealthy = allowedExits.some(
                        (exit) => exit.enabled && exit.health !== "down",
                      );
                      return (
                        <TableRow key={rule.id}>
                          <TableCell>
                            <div className="font-medium">{rule.name}</div>
                            <div className="text-xs text-muted-foreground">
                              {matchTypeLabel(rule.match_type)}
                              {rule.preset_key ? ` · пресет ${rule.preset_key}` : ""}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex max-w-64 flex-wrap gap-1">
                              {rule.values.slice(0, 3).map((value) => (
                                <Badge key={value} variant="muted" className="font-mono font-normal">
                                  {value}
                                </Badge>
                              ))}
                              {rule.values.length > 3 ? (
                                <Badge variant="outline">+{rule.values.length - 3}</Badge>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap items-center gap-1">
                              {allowedExits.length === 0 ? (
                                <span className="text-xs text-destructive">нет выходов — block</span>
                              ) : (
                                allowedExits.map((exit) => (
                                  <span
                                    key={exit.id}
                                    className={cn(
                                      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                                      exit.enabled && exit.health !== "down"
                                        ? "border-success/40 text-success"
                                        : "border-border text-muted-foreground",
                                    )}
                                    title={
                                      !exit.enabled
                                        ? "Выход отключён"
                                        : exit.health === "down"
                                          ? "Выход недоступен"
                                          : "Выход доступен"
                                    }
                                  >
                                    <span
                                      className={cn(
                                        "size-1.5 rounded-full",
                                        exit.enabled && exit.health !== "down" ? "bg-success" : "bg-muted-foreground",
                                      )}
                                    />
                                    {exit.label}
                                  </span>
                                ))
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="space-y-1">
                              <Switch
                                checked={rule.enabled}
                                onCheckedChange={(checked) => toggleRule.mutate({ rule, enabled: checked })}
                                aria-label={`Включить правило ${rule.name}`}
                              />
                              {!hasHealthy && rule.enabled ? (
                                <p className="text-xs text-warning">правило блокирует трафик</p>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => setAllowlistRule(rule)}
                                    aria-label={`Разрешённые выходы для ${rule.name}`}
                                  >
                                    <Cog className="size-4" />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Разрешённые выходы (allowlist)</TooltipContent>
                              </Tooltip>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="text-muted-foreground hover:text-destructive"
                                onClick={() => setDeleteRule(rule)}
                                aria-label={`Удалить правило ${rule.name}`}
                              >
                                <Trash2 className="size-4" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </QueryState>
        </CardContent>
      </Card>

      <RouteCheckCard />

      <AddSiteDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        presets={presets.data ?? []}
        exits={availableExits}
        onSaved={invalidate}
      />

      <AllowlistDialog
        rule={allowlistRule}
        onOpenChange={(open) => {
          if (!open) setAllowlistRule(null);
        }}
        exits={availableExits}
        onSaved={invalidate}
      />

      <Dialog open={deleteRule !== null} onOpenChange={(open) => !open && setDeleteRule(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить правило «{deleteRule?.name}»?</DialogTitle>
            <DialogDescription>
              Домены правила перестанут проксироваться через allowlist и пойдут по маршруту по
              умолчанию (или будут заблокированы, если профиль не настроен).
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteRule(null)}>
              Отмена
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteRule && removeRule.mutate(deleteRule)}
              disabled={removeRule.isPending}
            >
              {removeRule.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <JobDialog
        open={applyJob !== null}
        onOpenChange={(open) => !open && setApplyJob(null)}
        jobId={applyJob}
        title="Применение ревизии маршрутизации"
        description="Компиляция, валидация xray -test и атомарная раскатка на entry-узлы."
        onDone={() => {
          success("Ревизия применена");
          invalidate();
        }}
        onError={(message) => toastError("Ревизия не применена", message)}
      />
    </div>
  );
}
