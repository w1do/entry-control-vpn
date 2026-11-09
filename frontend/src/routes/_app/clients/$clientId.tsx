import { createFileRoute } from "@tanstack/react-router";
import { ClientDetailPage } from "@/pages/client-detail-page";

export const Route = createFileRoute("/_app/clients/$clientId")({
  component: ClientDetailPage,
});
