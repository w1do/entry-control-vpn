import { useState, type ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { JobProgress } from "@/components/common/job-progress";

interface JobDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: number | string | null;
  title: string;
  description?: string;
  onDone?: (result: unknown) => void;
  onError?: (message: string) => void;
  renderResult?: (result: unknown) => ReactNode;
  footer?: ReactNode;
}

export function JobDialog({
  open,
  onOpenChange,
  jobId,
  title,
  description,
  onDone,
  onError,
  renderResult,
  footer,
}: JobDialogProps) {
  const [result, setResult] = useState<unknown>(null);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setResult(null);
      }}
    >
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <JobProgress
          jobId={jobId}
          onDone={(value) => {
            setResult(value ?? null);
            onDone?.(value);
          }}
          onError={onError}
        />
        {result !== null && renderResult ? renderResult(result) : null}
        {footer}
        {!jobId ? <p className="text-sm text-muted-foreground">Задача ещё не создана.</p> : null}
      </DialogContent>
    </Dialog>
  );
}
