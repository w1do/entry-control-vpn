import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  exitHealthLabel,
  jobStatusLabel,
  revisionStatusLabel,
  serverStatusLabel,
} from "@/lib/utils";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

const SERVER_VARIANTS: Record<string, BadgeVariant> = {
  new: "muted",
  provisioning: "default",
  online: "success",
  degraded: "warning",
  offline: "destructive",
  delete_pending: "warning",
  failed: "destructive",
};

const HEALTH_VARIANTS: Record<string, BadgeVariant> = {
  up: "success",
  down: "destructive",
  unknown: "muted",
};

const JOB_VARIANTS: Record<string, BadgeVariant> = {
  queued: "muted",
  running: "default",
  succeeded: "success",
  failed: "destructive",
  cancelled: "warning",
};

const REVISION_VARIANTS: Record<string, BadgeVariant> = {
  pending: "warning",
  applied: "success",
  failed: "destructive",
};

const SEVERITY_VARIANTS: Record<string, BadgeVariant> = {
  info: "muted",
  warning: "warning",
  critical: "destructive",
};

const SEVERITY_LABELS: Record<string, string> = {
  info: "Инфо",
  warning: "Внимание",
  critical: "Критично",
};

export function ServerStatusBadge({ status }: { status: string }) {
  return <Badge variant={SERVER_VARIANTS[status] ?? "muted"}>{serverStatusLabel(status)}</Badge>;
}

export function ExitHealthBadge({ health }: { health: string }) {
  return <Badge variant={HEALTH_VARIANTS[health] ?? "muted"}>{exitHealthLabel(health)}</Badge>;
}

export function JobStatusBadge({ status }: { status: string }) {
  return <Badge variant={JOB_VARIANTS[status] ?? "muted"}>{jobStatusLabel(status)}</Badge>;
}

export function RevisionStatusBadge({ status }: { status: string }) {
  return <Badge variant={REVISION_VARIANTS[status] ?? "muted"}>{revisionStatusLabel(status)}</Badge>;
}

export function IncidentSeverityBadge({ severity }: { severity: string }) {
  return (
    <Badge variant={SEVERITY_VARIANTS[severity] ?? "muted"}>
      {SEVERITY_LABELS[severity] ?? severity}
    </Badge>
  );
}
