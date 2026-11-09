import { createFileRoute } from "@tanstack/react-router";
import { ServerDetailPage } from "@/pages/server-detail-page";

export const Route = createFileRoute("/_app/servers/$serverId")({
  component: ServerDetailPage,
});
