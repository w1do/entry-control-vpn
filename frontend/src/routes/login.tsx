import { createFileRoute, isRedirect, redirect } from "@tanstack/react-router";
import { LoginPage } from "@/pages/login-page";
import { meQuery } from "@/lib/queries";

interface LoginSearch {
  redirect?: string;
}

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  beforeLoad: async ({ context }) => {
    try {
      await context.queryClient.ensureQueryData(meQuery);
    } catch (error) {
      if (isRedirect(error)) throw error;
      return;
    }
    throw redirect({ to: "/dashboard" });
  },
  component: LoginPage,
});
