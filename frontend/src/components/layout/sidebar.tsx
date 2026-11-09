import {
  Gauge,
  Globe2,
  Server,
  Settings,
  ShieldCheck,
  Users,
  Waypoints,
  X,
} from "lucide-react";
import { Link, useRouterState } from "@tanstack/react-router";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Gauge;
  match: (pathname: string) => boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Дашборд", icon: Gauge, match: (path) => path === "/dashboard" },
  { to: "/servers", label: "Серверы", icon: Server, match: (path) => path.startsWith("/servers") },
  { to: "/exits", label: "Выходы", icon: Waypoints, match: (path) => path.startsWith("/exits") },
  { to: "/sites", label: "Сайты", icon: Globe2, match: (path) => path.startsWith("/sites") },
  { to: "/clients", label: "Клиенты", icon: Users, match: (path) => path.startsWith("/clients") },
  { to: "/settings", label: "Настройки", icon: Settings, match: (path) => path.startsWith("/settings") },
];

interface SidebarProps {
  mobileOpen: boolean;
  onClose: () => void;
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <div className="flex h-full w-64 flex-col bg-card">
      <div className="flex h-16 items-center gap-2.5 border-b px-5">
        <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
          <ShieldCheck className="size-5" aria-hidden />
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold leading-tight">VPN Panel</p>
          <p className="truncate text-xs text-muted-foreground">control plane</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Основная навигация">
        {NAV_ITEMS.map((item) => {
          const active = item.match(pathname);
          const Icon = item.icon;
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-primary/10 text-primary dark:bg-primary/15"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <Icon className="size-4.5" aria-hidden />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t p-4 text-xs text-muted-foreground">
        <p>Xray 26.11.9</p>
        <p className="mt-0.5 opacity-70">Версия панели 0.1.0</p>
      </div>
    </div>
  );
}

export function Sidebar({ mobileOpen, onClose }: SidebarProps) {
  return (
    <>
      <aside className="hidden border-r lg:block">
        <SidebarContent />
      </aside>
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Меню">
          <button
            type="button"
            className="absolute inset-0 bg-black/50 backdrop-blur-[1px]"
            aria-label="Закрыть меню"
            onClick={onClose}
          />
          <div className="absolute inset-y-0 left-0 shadow-xl">
            <SidebarContent onNavigate={onClose} />
          </div>
          <button
            type="button"
            onClick={onClose}
            className="absolute right-3 top-3 rounded-md bg-card p-1.5 text-muted-foreground shadow"
            aria-label="Закрыть меню"
          >
            <X className="size-5" />
          </button>
        </div>
      ) : null}
    </>
  );
}
