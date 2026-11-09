import { createFileRoute, isRedirect, redirect } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/app-shell";
import { isApiError } from "@/lib/api-error";
import { meQuery } from "@/lib/queries";

export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.ensureQueryData(meQuery);
    } catch (error) {
      if (isRedirect(error)) throw error;
      if (isApiError(error) && error.status === 401) {
        throw redirect({ to: "/login", search: { redirect: location.href } });
      }
      throw error;
    }
  },
  component: AppShell,
});
