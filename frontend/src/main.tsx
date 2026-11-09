import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { ThemeProvider, applyInitialTheme } from "@/lib/theme";
import { ToastProvider } from "@/lib/toast";
import { onUnauthorized } from "@/lib/api";
import { queryClient } from "@/lib/query-client";
import { router } from "@/router";
import { TooltipProvider } from "@/components/ui/tooltip";
import "@/styles.css";

applyInitialTheme();

onUnauthorized(() => {
  queryClient.clear();
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
});

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Не найден элемент #root");

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <TooltipProvider delayDuration={200}>
            <RouterProvider router={router} />
          </TooltipProvider>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
