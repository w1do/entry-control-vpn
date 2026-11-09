import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, CheckCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { IncidentSeverityBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { incidentsQuery, queryKeys } from "@/lib/queries";
import { formatDateTime, formatRelative } from "@/lib/utils";
import { useToast } from "@/lib/toast";
import { errorMessage } from "@/lib/api-error";
import type { Incident } from "@/lib/api-types";
import { cn } from "@/lib/utils";

const KIND_LABELS: Record<string, string> = {
  node_offline: "Узел offline",
  node_degraded: "Деградация узла",
  exit_down: "Выход недоступен",
  apply_failed: "Ошибка применения конфигурации",
  routing_blocked: "Блокировка маршрутизации",
  revision_applied: "Ревизия применена",
  budget_warning: "Расход бюджета выхода",
  weak_password: "Слабый пароль администратора",
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

interface IncidentsListProps {
  limit?: number;
  compact?: boolean;
  className?: string;
  showEmpty?: boolean;
}

export function IncidentsList({ limit, compact = false, className, showEmpty = true }: IncidentsListProps) {
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();
  const incidents = useQuery(incidentsQuery);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.incidents });

  const ack = useMutation({
    mutationFn: (id: number) => api.incidents.ack(id),
    onSuccess: () => {
      success("Инцидент подтверждён");
      void invalidate();
    },
    onError: (mutationError) => toastError("Не удалось подтвердить инцидент", errorMessage(mutationError)),
  });

  const resolve = useMutation({
    mutationFn: (id: number) => api.incidents.resolve(id),
    onSuccess: () => {
      success("Инцидент закрыт");
      void invalidate();
    },
    onError: (mutationError) => toastError("Не удалось закрыть инцидент", errorMessage(mutationError)),
  });

  const items = (incidents.data ?? []).slice(0, limit ?? incidents.data?.length ?? 0);
  if (incidents.isLoading) {
    return <p className="text-sm text-muted-foreground">Загрузка инцидентов…</p>;
  }
  if (incidents.isError) {
    return <p className="text-sm text-destructive">Не удалось загрузить инциденты</p>;
  }
  if (items.length === 0) {
    return showEmpty ? (
      <EmptyState
        icon={BellRing}
        title="Инцидентов нет"
        description="Серьёзные события и деградации появятся здесь."
        className={compact ? "py-8" : undefined}
      />
    ) : null;
  }

  return (
    <ul className={cn("divide-y", className)}>
      {items.map((incident: Incident) => (
        <li key={incident.id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <IncidentSeverityBadge severity={incident.severity} />
              <span className="text-sm font-medium">{kindLabel(incident.kind)}</span>
              {incident.subject ? (
                <span className="truncate text-sm text-muted-foreground">{incident.subject}</span>
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground" title={formatDateTime(incident.created_at)}>
              {formatRelative(incident.created_at)}
              {incident.acked_at && incident.status === "ack"
                ? ` · подтверждён ${formatRelative(incident.acked_at)}`
                : null}
              {incident.resolved_at ? ` · закрыт ${formatRelative(incident.resolved_at)}` : null}
            </p>
          </div>
          {incident.status === "resolved" ? (
            <span className="text-xs text-muted-foreground">Закрыт</span>
          ) : (
            <div className="flex shrink-0 gap-2">
              {incident.status === "open" ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => ack.mutate(incident.id)}
                  disabled={ack.isPending}
                >
                  <Check />
                  Подтвердить
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="ghost"
                onClick={() => resolve.mutate(incident.id)}
                disabled={resolve.isPending}
              >
                <CheckCheck />
                Закрыть
              </Button>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
