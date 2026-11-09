import { createFileRoute, redirect } from "@tanstack/react-router";
import { meQuery } from "@/lib/queries";

export const Route = createFileRoute("/")({
  beforeLoad: async ({ context }) => {
    try {
      await context.queryClient.ensureQueryData(meQuery);
    } catch {
      throw redirect({ to: "/login" });
    }
    throw redirect({ to: "/dashboard" });
  },
});
