import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ShieldAlert } from "lucide-react";
import { Link } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { computeRoutingStatus, exitsQuery, routingProfileQuery } from "@/lib/queries";

/** Гейт «Проксирование не настроено»: баннер на всех страницах панели. */
export function RoutingGateBanner({ hideOnSites = false, className }: { hideOnSites?: boolean; className?: string }) {
  const profile = useQuery(routingProfileQuery);
  const exits = useQuery(exitsQuery);
  if (hideOnSites) return null;
  if (profile.isLoading || exits.isLoading) return null;
  if (profile.isError || exits.isError) return null;
  const status = computeRoutingStatus(profile.data, exits.data);
  if (status.ready) return null;

  let reason = "Выход по умолчанию не выбран — трафик клиентов будет блокироваться (fail-closed).";
  if (status.enabled === false) {
    reason = "Маршрутизация выключена: все entry-узлы работают в режиме block-all.";
  } else if (status.issues.length > 0) {
    reason = status.issues.join("; ");
  } else if (status.defaultExits.length === 0) {
    reason = "Выход по умолчанию не выбран — трафик клиентов будет блокироваться (fail-closed).";
  } else if (status.healthyDefaultExits.length === 0) {
    reason = "Все выходы по умолчанию недоступны — трафик блокируется до восстановления выхода.";
  }

  return (
    <Alert variant="warning" className={className}>
      <ShieldAlert aria-hidden />
      <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <AlertTitle>Проксирование не настроено</AlertTitle>
          <AlertDescription>{reason}</AlertDescription>
        </div>
        <Button asChild size="sm" variant="outline" className="shrink-0">
          <Link to="/sites" search={{ focus: "default" }}>
            Настроить маршрутизацию
            <ArrowRight />
          </Link>
        </Button>
      </div>
    </Alert>
  );
}
