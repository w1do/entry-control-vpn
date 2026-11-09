import { useEffect, useState } from "react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";
import { RoutingGateBanner } from "@/components/common/routing-gate-banner";

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const isSites = pathname.startsWith("/sites");

  return (
    <div className="flex min-h-full">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header onMenuClick={() => setMobileOpen(true)} />
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <RoutingGateBanner hideOnSites={isSites} />
          {!isSites ? <div className="mb-2" /> : null}
          <Outlet />
        </main>
        <footer className="border-t px-4 py-4 text-xs text-muted-foreground sm:px-6 lg:px-8">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2">
            <span>VPN Control Panel · self-hosted</span>
            <span>Xray 26.11.9 · VLESS + REALITY</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
