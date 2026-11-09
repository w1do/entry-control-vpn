import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, Info, TriangleAlert, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastVariant = "default" | "success" | "error" | "warning";

interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
}

interface ToastItem extends ToastInput {
  id: number;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (input: ToastInput) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const ICONS = {
  default: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  error: XCircle,
} as const;

const TONE_CLASSES: Record<ToastVariant, string> = {
  default: "border-border bg-card text-card-foreground",
  success: "border-emerald-500/30 bg-emerald-500/10 text-foreground",
  warning: "border-amber-500/40 bg-amber-500/10 text-foreground",
  error: "border-destructive/40 bg-destructive/10 text-foreground",
};

const ICON_CLASSES: Record<ToastVariant, string> = {
  default: "text-muted-foreground",
  success: "text-emerald-500",
  warning: "text-amber-500",
  error: "text-destructive",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const toast = useCallback(
    (input: ToastInput) => {
      counter.current += 1;
      const id = counter.current;
      const item: ToastItem = { ...input, id, variant: input.variant ?? "default" };
      setItems((current) => [...current.slice(-4), item]);
      const duration = input.duration ?? (item.variant === "error" ? 8000 : 5000);
      const timer = setTimeout(() => dismiss(id), duration);
      timers.current.set(id, timer);
    },
    [dismiss],
  );

  const helpers = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (title, description) => toast({ title, description, variant: "success" }),
      error: (title, description) => toast({ title, description, variant: "error" }),
      warning: (title, description) => toast({ title, description, variant: "warning" }),
    }),
    [toast],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const timer of map.values()) clearTimeout(timer);
      map.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={helpers}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:items-end"
      >
        {items.map((item) => {
          const Icon = ICONS[item.variant];
          return (
            <div
              key={item.id}
              role="status"
              className={cn(
                "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border p-4 shadow-lg backdrop-blur transition-all animate-in slide-in-from-bottom-2",
                TONE_CLASSES[item.variant],
              )}
            >
              <Icon className={cn("mt-0.5 size-5 shrink-0", ICON_CLASSES[item.variant])} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{item.title}</p>
                {item.description ? (
                  <p className="mt-1 break-words text-sm text-muted-foreground">{item.description}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                aria-label="Закрыть уведомление"
              >
                <X className="size-4" aria-hidden />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast должен использоваться внутри ToastProvider");
  return context;
}
