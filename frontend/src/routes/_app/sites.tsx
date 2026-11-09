import { createFileRoute } from "@tanstack/react-router";
import { SitesPage } from "@/pages/sites-page";

export interface SitesSearch {
  focus?: "default";
}

export const Route = createFileRoute("/_app/sites")({
  validateSearch: (search: Record<string, unknown>): SitesSearch => ({
    focus: search.focus === "default" ? "default" : undefined,
  }),
  component: SitesPage,
});
