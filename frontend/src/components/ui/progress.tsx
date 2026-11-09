import { cn } from "@/lib/utils";

interface ProgressProps extends React.ComponentProps<"div"> {
  value?: number | null;
}

function Progress({ className, value = 0, ...props }: ProgressProps) {
  const safeValue = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(safeValue)}
      data-slot="progress"
      className={cn("relative h-2 w-full overflow-hidden rounded-full bg-secondary", className)}
      {...props}
    >
      <div
        className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
        style={{ width: `${safeValue}%` }}
      />
    </div>
  );
}

export { Progress };
