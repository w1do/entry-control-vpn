import { createRootRouteWithContext, Link, Outlet } from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface RouterContext {
  queryClient: QueryClient;
}

function RootComponent() {
  return <Outlet />;
}

function RootNotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
        <ShieldCheck className="size-7" aria-hidden />
      </div>
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">Страница не найдена</h1>
        <p className="text-sm text-muted-foreground">Такого раздела в панели нет.</p>
      </div>
      <Button asChild>
        <Link to="/dashboard">На дашборд</Link>
      </Button>
    </div>
  );
}

function RootError() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
        <AlertTriangle className="size-7" aria-hidden />
      </div>
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">Что-то пошло не так</h1>
        <p className="mx-auto max-w-md text-sm text-muted-foreground">
          Не удалось отобразить панель. Обновите страницу — если ошибка повторяется, проверьте
          состояние backend и логи контейнера.
        </p>
      </div>
      <Button onClick={() => window.location.reload()}>Обновить страницу</Button>
    </div>
  );
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
  notFoundComponent: RootNotFound,
  errorComponent: RootError,
});
