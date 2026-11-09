import { CheckCircle2, CircleDot, Loader2, Terminal, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Progress } from "@/components/ui/progress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useJobStream, type JobLogEntry } from "@/lib/use-job-stream";
import { cn } from "@/lib/utils";

interface JobProgressProps {
  jobId: number | string | null | undefined;
  title?: string;
  successLabel?: string;
  errorTitle?: string;
  onDone?: (result: unknown) => void;
  onError?: (message: string) => void;
  children?: ReactNode;
  className?: string;
}

function LogRow({ entry }: { entry: JobLogEntry }) {
  const Icon = entry.level === "error" ? XCircle : entry.level === "warning" ? CircleDot : CheckCircle2;
  return (
    <li className="flex items-start gap-2 py-1 text-sm">
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          entry.level === "error"
            ? "text-destructive"
            : entry.level === "warning"
              ? "text-warning"
              : "text-success",
        )}
        aria-hidden
      />
      <div className="min-w-0">
        {entry.step ? <span className="mr-2 font-medium">{entry.step}</span> : null}
        <span className="text-muted-foreground">{entry.message}</span>
      </div>
    </li>
  );
}

/** Блок прогресса job'а: статус, прогресс-бар и список шагов из SSE. */
export function JobProgress({
  jobId,
  title = "Выполнение",
  successLabel = "Готово",
  errorTitle = "Ошибка выполнения",
  onDone,
  onError,
  children,
  className,
}: JobProgressProps) {
  const stream = useJobStream(jobId, { onDone, onError });
  const isRunning = stream.status === "running" || stream.status === "connecting";
  const success = stream.status === "succeeded";
  const failed = stream.status === "failed" || stream.status === "cancelled";

  if (!jobId) return null;

  return (
    <div className={cn("space-y-4", className)}>
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span className="flex items-center gap-2 font-medium">
            {isRunning ? <Loader2 className="size-4 animate-spin text-primary" aria-hidden /> : null}
            {success ? <CheckCircle2 className="size-4 text-success" aria-hidden /> : null}
            {failed ? <XCircle className="size-4 text-destructive" aria-hidden /> : null}
            {success ? successLabel : failed ? errorTitle : title}
          </span>
          <span className="text-muted-foreground">{stream.progress}%</span>
        </div>
        <Progress value={stream.progress} />
      </div>

      {stream.events.length > 0 ? (
        <div className="rounded-lg border bg-muted/30 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            <Terminal className="size-3.5" aria-hidden />
            Шаги ({stream.events.length})
          </div>
          <ul className="max-h-56 space-y-0 overflow-y-auto scrollbar-thin" aria-live="polite">
            {stream.events.map((entry) => (
              <LogRow key={entry.key} entry={entry} />
            ))}
          </ul>
        </div>
      ) : null}

      {stream.connectionLost ? (
        <p className="text-xs text-muted-foreground">
          Соединение SSE прервано, статус обновляется опросом.
        </p>
      ) : null}

      {failed ? (
        <Alert variant="destructive">
          <AlertTitle>{errorTitle}</AlertTitle>
          <AlertDescription>{stream.error ?? "Job завершился ошибкой"}</AlertDescription>
        </Alert>
      ) : null}

      {success ? (
        <Alert variant="success">
          <AlertTitle>Успешно</AlertTitle>
          <AlertDescription>Операция завершена.</AlertDescription>
        </Alert>
      ) : null}

      {children}
    </div>
  );
}
