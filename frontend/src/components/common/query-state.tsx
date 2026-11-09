import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api-error";

interface QueryStateProps {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  rows?: number;
  children: ReactNode;
}

/** Скелетоны при загрузке и понятная ошибка с повтором. */
export function QueryState({ isLoading, isError, error, onRetry, rows = 3, children }: QueryStateProps) {
  if (isLoading) {
    return (
      <div className="space-y-3" aria-busy="true" aria-live="polite">
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    );
  }
  if (isError) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-5">
        <div className="flex items-center gap-2 font-medium text-destructive">
          <AlertTriangle className="size-4" aria-hidden />
          Не удалось загрузить данные
        </div>
        <p className="text-sm text-muted-foreground">{errorMessage(error)}</p>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Повторить
          </Button>
        ) : null}
      </div>
    );
  }
  return <>{children}</>;
}

export function InlineError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
      <div className="flex items-center gap-2 font-medium text-destructive">
        <AlertTriangle className="size-4" aria-hidden />
        Ошибка
      </div>
      <p className="text-muted-foreground">{errorMessage(error)}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Повторить
        </Button>
      ) : null}
    </div>
  );
}
