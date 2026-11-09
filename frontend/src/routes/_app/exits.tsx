import { createFileRoute } from "@tanstack/react-router";
import { ExitsPage } from "@/pages/exits-page";

export const Route = createFileRoute("/_app/exits")({
  component: ExitsPage,
});
