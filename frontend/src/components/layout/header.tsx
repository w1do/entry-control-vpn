import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  Bell,
  ChevronDown,
  LogOut,
  Menu,
  Moon,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sun,
  UserRound,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { Incident } from "@/lib/api-types";
import {
  computeRoutingStatus,
  exitsQuery,
  incidentsQuery,
  meQuery,
  routingProfileQuery,
} from "@/lib/queries";
import { useTheme } from "@/lib/theme";
import { useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";

interface HeaderProps {
  onMenuClick: () => void;
}

function RoutingStatusBadge() {
  const profile = useQuery(routingProfileQuery);
  const exits = useQuery(exitsQuery);
  if (profile.isLoading || exits.isLoading) {
    return <Badge variant="muted">Маршрутизация…</Badge>;
  }
  const status = computeRoutingStatus(profile.data, exits.data);
  if (status.ready) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="success" className="gap-1.5">
            <ShieldCheck className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">Маршрутизация активна</span>
            <span className="sm:hidden">Активна</span>
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          Профиль применён, выходы по умолчанию доступны
        </TooltipContent>
      </Tooltip>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link to="/sites" search={{ focus: "default" }}>
          <Badge variant="destructive" className="gap-1.5">
            <ShieldAlert className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">Проксирование не настроено</span>
            <span className="sm:hidden">Не настроено</span>
          </Badge>
        </Link>
      </TooltipTrigger>
      <TooltipContent>Откройте Sites и выберите выход по умолчанию</TooltipContent>
    </Tooltip>
  );
}

function IncidentsBell() {
  const incidents = useQuery(incidentsQuery);
  const openIncidents = (incidents.data ?? []).filter((incident: Incident) => incident.status !== "resolved");
  const critical = openIncidents.filter((incident) => incident.severity === "critical").length;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label="Инциденты">
          <Bell className="size-4.5" />
          {openIncidents.length > 0 ? (
            <span
              className={cn(
                "absolute right-1.5 top-1.5 flex min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold leading-4 text-white",
                critical > 0 ? "bg-destructive" : "bg-warning-foreground/80",
              )}
            >
              {openIncidents.length > 9 ? "9+" : openIncidents.length}
            </span>
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel>Открытые инциденты</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {openIncidents.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">Нет открытых инцидентов</p>
        ) : (
          openIncidents.slice(0, 5).map((incident) => (
            <DropdownMenuItem key={incident.id} asChild>
              <Link to="/dashboard" className="flex flex-col items-start gap-0.5">
                <span className="text-sm font-medium">
                  {incident.severity === "critical" ? "Критично" : incident.severity === "warning" ? "Внимание" : "Инфо"}
                  {incident.subject ? ` · ${incident.subject}` : ""}
                </span>
                <span className="text-xs text-muted-foreground">{incident.kind}</span>
              </Link>
            </DropdownMenuItem>
          ))
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/dashboard">Все инциденты на дашборде</Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function Header({ onMenuClick }: HeaderProps) {
  const { resolved, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { success, error } = useToast();
  const me = useQuery(meQuery);

  const logout = useMutation({
    mutationFn: () => api.auth.logout(),
    onSuccess: () => {
      queryClient.clear();
      success("Вы вышли из панели");
      void navigate({ to: "/login" });
    },
    onError: (mutationError) => error("Не удалось выйти", errorMessage(mutationError)),
  });

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur sm:px-6">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onMenuClick}
        aria-label="Открыть меню"
      >
        <Menu className="size-5" />
      </Button>
      <div className="hidden min-w-0 flex-1 sm:block">
        <RoutingStatusBadge />
      </div>
      <div className="flex flex-1 items-center justify-end gap-1.5 sm:flex-none">
        <div className="sm:hidden">
          <RoutingStatusBadge />
        </div>
        <IncidentsBell />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              aria-label={resolved === "dark" ? "Включить светлую тему" : "Включить тёмную тему"}
            >
              {resolved === "dark" ? <Sun className="size-4.5" /> : <Moon className="size-4.5" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>Сменить тему (light/dark)</TooltipContent>
        </Tooltip>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="gap-2 px-2 sm:px-3" aria-label="Меню администратора">
              <UserRound className="size-4.5" />
              <span className="hidden max-w-40 truncate text-sm md:inline">
                {me.data?.email ?? "Администратор"}
              </span>
              <ChevronDown className="size-3.5 opacity-60" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="truncate">{me.data?.email ?? "Администратор"}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link to="/settings" className="gap-2">
                <Settings className="size-4" />
                Настройки
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              destructive
              disabled={logout.isPending}
              onSelect={(event) => {
                event.preventDefault();
                logout.mutate();
              }}
            >
              <LogOut className="size-4" />
              {logout.isPending ? "Выход…" : "Выйти"}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
