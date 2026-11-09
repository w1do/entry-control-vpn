import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: LucideIcon;
  tone?: "default" | "success" | "warning" | "destructive" | "primary";
  className?: string;
}

const TONES: Record<NonNullable<StatCardProps["tone"]>, string> = {
  default: "bg-muted text-muted-foreground",
  success: "bg-success/12 text-success",
  warning: "bg-warning/15 text-warning",
  destructive: "bg-destructive/10 text-destructive",
  primary: "bg-primary/10 text-primary",
};

export function StatCard({ label, value, hint, icon: Icon, tone = "default", className }: StatCardProps) {
  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="text-2xl font-semibold tracking-tight">{value}</p>
          {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
        </div>
        <div className={cn("flex size-10 shrink-0 items-center justify-center rounded-lg", TONES[tone])}>
          <Icon className="size-5" aria-hidden />
        </div>
      </div>
    </Card>
  );
}
