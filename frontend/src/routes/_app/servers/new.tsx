import { createFileRoute } from "@tanstack/react-router";
import { ServerWizardPage } from "@/pages/server-wizard-page";

export const Route = createFileRoute("/_app/servers/new")({
  component: ServerWizardPage,
});
