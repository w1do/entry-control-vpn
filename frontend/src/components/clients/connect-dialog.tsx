import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertCircle, Copy, Download, Loader2, QrCode, ServerOff, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { Client, ConnectionVariant } from "@/lib/api-types";
import { serversQuery } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import { downloadFile, regionLabel } from "@/lib/utils";

interface ConnectDialogProps {
  client: Client | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Модалка подключения: отдельный QR на каждый транспорт (TCP+Vision, TCP, XHTTP, gRPC)
 * и «умный профиль» с RU-bypass. QR рендерится локально из ссылки варианта,
 * поэтому сканирование добавляет именно выбранный профиль.
 */
export function ConnectDialog({ client, onOpenChange }: ConnectDialogProps) {
  const { success, error: toastError } = useToast();
  const [tab, setTab] = useState<"profiles" | "smart">("profiles");
  const [serverId, setServerId] = useState<string>("");
  const servers = useQuery({ ...serversQuery, enabled: Boolean(client) });

  const entryServers = (servers.data ?? []).filter(
    (server) => server.roles === "entry" || server.roles === "both",
  );
  const activeServerId = serverId || (entryServers[0] ? String(entryServers[0].id) : "");
  const activeServer = entryServers.find((server) => String(server.id) === activeServerId);

  const connection = useQuery({
    queryKey: ["clients", client?.id ?? 0, "connection", activeServerId],
    queryFn: () => api.clients.connection(client!.id, Number(activeServerId)),
    enabled: Boolean(client) && Boolean(activeServerId),
    retry: false,
  });

  const variants = connection.data?.variants ?? [];
  const subscriptionUrl = connection.data?.smart?.subscription_url ?? "";

  const downloadProfile = useMutation({
    mutationFn: async () => {
      if (!client) throw new Error("Клиент не выбран");
      const profile = await api.clients.profile(client.id, "smart");
      const content = typeof profile === "string" ? profile : JSON.stringify(profile, null, 2);
      downloadFile(
        client.name + "-profile-smart.json",
        content,
        "application/json;charset=utf-8",
      );
    },
    onSuccess: () => success("Умный профиль скачан"),
    onError: (mutationError) => toastError("Не удалось скачать профиль", errorMessage(mutationError)),
  });

  return (
    <Dialog open={client !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <QrCode className="size-5 text-muted-foreground" />
            Подключение клиента «{client?.name}»
          </DialogTitle>
          <DialogDescription>
            Выберите транспорт, который поддерживает ваш клиент, и отсканируйте QR. Для Incy
            начните с XHTTP или gRPC; для sing-box/Hiddify — TCP + Vision.
          </DialogDescription>
        </DialogHeader>

        {client && !client.enabled ? (
          <Alert variant="warning">
            <AlertCircle aria-hidden />
            <AlertTitle>Клиент отключён</AlertTitle>
            <AlertDescription>
              Пока клиент выключен, подключения отклоняются. Включите его в карточке клиента.
            </AlertDescription>
          </Alert>
        ) : null}

        {entryServers.length === 0 ? (
          <Alert variant="destructive">
            <ServerOff aria-hidden />
            <AlertTitle>Нет entry-узлов</AlertTitle>
            <AlertDescription>
              Подключение доступно только для серверов с ролью entry (или both).
            </AlertDescription>
          </Alert>
        ) : (
          <>
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Entry-узел</p>
              <Select value={activeServerId} onValueChange={setServerId}>
                <SelectTrigger aria-label="Entry-узел для подключения">
                  <SelectValue placeholder="Выберите entry-узел" />
                </SelectTrigger>
                <SelectContent>
                  {entryServers.map((server) => (
                    <SelectItem key={server.id} value={String(server.id)}>
                      {server.name} · {regionLabel(server.region)} · {server.host}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {activeServer && activeServer.status !== "online" ? (
                <p className="flex items-center gap-1.5 text-xs text-warning">
                  <TriangleAlert className="size-3.5" aria-hidden />
                  Узел «{activeServer.name}» сейчас не в статусе online — подключение может не работать.
                </p>
              ) : null}
            </div>

            <Tabs value={tab} onValueChange={(value) => setTab(value as "profiles" | "smart")}>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="profiles">QR по протоколам</TabsTrigger>
                <TabsTrigger value="smart">Умный профиль</TabsTrigger>
              </TabsList>

              <TabsContent value="profiles" className="space-y-3">
                {connection.isLoading ? (
                  <div className="flex items-center justify-center py-10">
                    <Spinner className="size-6" />
                  </div>
                ) : connection.isError ? (
                  <Alert variant="destructive">
                    <AlertCircle aria-hidden />
                    <AlertTitle>Не удалось получить профили</AlertTitle>
                    <AlertDescription>{errorMessage(connection.error)}</AlertDescription>
                  </Alert>
                ) : variants.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Нет доступных профилей.</p>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {variants.map((variant) => (
                      <VariantCard key={variant.id} variant={variant} clientName={client?.name ?? "client"} />
                    ))}
                  </div>
                )}
              </TabsContent>

              <TabsContent value="smart" className="space-y-3">
                <Alert variant="info">
                  <AlertTitle>Российские сайты — напрямую</AlertTitle>
                  <AlertDescription>
                    Профиль применяет <code>geosite:category-ru</code> и <code>geoip:ru → direct</code>{" "}
                    на устройстве: российские сайты не заходят в туннель.
                  </AlertDescription>
                </Alert>

                {subscriptionUrl ? (
                  <>
                    <div className="flex justify-center">
                      <div className="rounded-xl bg-white p-4 shadow-sm">
                        <QRCodeSVG value={subscriptionUrl} size={208} level="M" marginSize={1} />
                      </div>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">URL подписки</p>
                      <code className="block break-all rounded-md border bg-muted/40 p-2 text-xs">
                        {subscriptionUrl}
                      </code>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <CopyButton value={subscriptionUrl} label="Копировать URL подписки" />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => downloadProfile.mutate()}
                        disabled={downloadProfile.isPending}
                      >
                        {downloadProfile.isPending ? <Loader2 className="animate-spin" /> : <Download />}
                        Скачать .json
                      </Button>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">URL подписки недоступен.</p>
                )}
              </TabsContent>
            </Tabs>
          </>
        )}

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="muted">UUID {client?.uuid.slice(0, 8)}…</Badge>
          <Badge variant="muted">{client?.email_tag}</Badge>
        </div>
      </DialogContent>
    </Dialog>
  );
}

interface VariantCardProps {
  variant: ConnectionVariant;
  clientName: string;
}

function VariantCard({ variant, clientName }: VariantCardProps) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border bg-muted/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{variant.label}</p>
          <p className="text-xs text-muted-foreground">{variant.description}</p>
        </div>
        <Badge variant="muted">{variant.port}/tcp</Badge>
      </div>

      <div className="flex justify-center py-1">
        <div className="rounded-lg bg-white p-2 shadow-sm">
          <QRCodeSVG value={variant.url} size={168} level="M" marginSize={1} />
        </div>
      </div>

      <code className="max-h-20 overflow-auto break-all rounded-md border bg-background/60 p-2 text-[10px]">
        {variant.url}
      </code>

      <div className="flex flex-wrap gap-1.5">
        <CopyButton value={variant.url} label="Копировать ссылку" />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            downloadFile(
              clientName + "-" + variant.id + ".json",
              JSON.stringify({ url: variant.url }, null, 2),
              "application/json;charset=utf-8",
            )
          }
        >
          <Download />
          .json
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void navigator.clipboard?.writeText(variant.url)}
          aria-label="Скопировать ссылку варианта"
        >
          <Copy />
        </Button>
      </div>
    </div>
  );
}
