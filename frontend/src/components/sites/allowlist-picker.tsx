import { ArrowDown, ArrowUp, CircleAlert, X } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ExitHealthBadge } from "@/components/ui/status-badge";
import type { Exit } from "@/lib/api-types";
import { cn, regionLabel } from "@/lib/utils";

interface AllowlistPickerProps {
  exits: Exit[];
  value: number[];
  onChange: (ids: number[]) => void;
  disabled?: boolean;
  warningTitle?: string;
  warningText?: string;
}

/** Упорядоченный multi-select выходов: порядок = приоритет балансировщика. */
export function AllowlistPicker({
  exits,
  value,
  onChange,
  disabled = false,
  warningTitle = "Fail-closed: при недоступности всех выходов трафик блокируется",
  warningText = "Запросы не уйдут ни на выход по умолчанию, ни напрямую. Порядок задаёт приоритет: первый доступный выход используется первым.",
}: AllowlistPickerProps) {
  const selected = value
    .map((id) => exits.find((exit) => exit.id === id))
    .filter((exit): exit is Exit => Boolean(exit));

  function toggle(id: number) {
    if (disabled) return;
    if (value.includes(id)) onChange(value.filter((candidate) => candidate !== id));
    else onChange([...value, id]);
  }

  function move(id: number, direction: -1 | 1) {
    if (disabled) return;
    const index = value.indexOf(id);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= value.length) return;
    const next = [...value];
    [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
    onChange(next);
  }

  return (
    <div className="space-y-3">
      <Alert variant="warning">
        <CircleAlert aria-hidden />
        <AlertDescription>
          <span className="font-medium text-foreground">{warningTitle}</span>
          <br />
          {warningText}
        </AlertDescription>
      </Alert>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border">
          <div className="border-b px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Все выходы
          </div>
          <ul className="max-h-64 overflow-y-auto p-2">
            {exits.length === 0 ? (
              <li className="px-2 py-3 text-sm text-muted-foreground">
                Нет доступных выходов. Добавьте exit-узел или внешний прокси.
              </li>
            ) : (
              exits.map((exit) => {
                const checked = value.includes(exit.id);
                const order = value.indexOf(exit.id);
                return (
                  <li key={exit.id}>
                    <label
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors hover:bg-accent",
                        disabled && "cursor-not-allowed opacity-60",
                      )}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => toggle(exit.id)}
                        disabled={disabled}
                        aria-label={`Разрешить выход ${exit.label}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="font-medium">{exit.label}</span>
                          <ExitHealthBadge health={exit.health} />
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {regionLabel(exit.region)} · {exit.endpoint_host}:{exit.endpoint_port}
                        </span>
                      </span>
                      {checked ? (
                        <Badge variant="secondary" className="shrink-0">
                          #{order + 1}
                        </Badge>
                      ) : null}
                    </label>
                  </li>
                );
              })
            )}
          </ul>
        </div>

        <div className="rounded-lg border">
          <div className="border-b px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Порядок выбора ({selected.length})
          </div>
          {selected.length === 0 ? (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              Ни один выход не разрешён — правило будет блокировать трафик.
            </p>
          ) : (
            <ol className="space-y-1 p-2">
              {selected.map((exit, index) => (
                <li
                  key={exit.id}
                  className="flex items-center gap-2 rounded-md border bg-background px-2 py-1.5"
                >
                  <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{exit.label}</span>
                    <span className="block text-xs text-muted-foreground">
                      {regionLabel(exit.region)}
                    </span>
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-6"
                    disabled={disabled || index === 0}
                    onClick={() => move(exit.id, -1)}
                    aria-label={`Выше: ${exit.label}`}
                  >
                    <ArrowUp className="size-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-6"
                    disabled={disabled || index === selected.length - 1}
                    onClick={() => move(exit.id, 1)}
                    aria-label={`Ниже: ${exit.label}`}
                  >
                    <ArrowDown className="size-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-6 text-muted-foreground hover:text-destructive"
                    disabled={disabled}
                    onClick={() => toggle(exit.id)}
                    aria-label={`Убрать: ${exit.label}`}
                  >
                    <X className="size-3.5" />
                  </Button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
