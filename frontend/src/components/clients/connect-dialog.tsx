import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertCircle, Download, Loader2, QrCode, ServerOff, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import type { Client } from "@/lib/api-types";
import { serversQuery } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import { downloadFile, regionLabel } from "@/lib/utils";

interface ConnectDialogProps {
  client: Client | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Модалка подключения. QR-коды отдаёт backend (GET /clients/{id}/qr.svg):
 * smart кодирует URL подписки (<PUBLIC_BASE_URL>/sub/<sub_token>), vless — vless://-ссылку.
 */
export function ConnectDialog({ client, onOpenChange }: ConnectDialogProps) {
  const { success, error: toastError } = useToast();
  const [mode, setMode] = useState<"smart" | "vless">("smart");
  const [serverId, setServerId] = useState<string>("");
  const [localQrFallback, setLocalQrFallback] = useState(false);
  const servers = useQuery({ ...serversQuery, enabled: Boolean(client) });

  const entryServers = (servers.data ?? []).filter(
    (server) => server.roles === "entry" || server.roles === "both",
  );
  const activeServerId = serverId || (entryServers[0] ? String(entryServers[0].id) : "");
  const activeServer = entryServers.find((server) => String(server.id) === activeServerId);

  const qr = useQuery({
    queryKey: ["clients", client?.id ?? 0, "qr", mode, activeServerId],
    queryFn: () => api.clients.qrSvgText(client!.id, mode, Number(activeServerId)),
    enabled: Boolean(client) && Boolean(activeServerId),
    retry: false,
  });

  const link = useQuery({
    queryKey: ["clients", client?.id ?? 0, "link", activeServerId],
    queryFn: () => api.clients.link(client!.id, Number(activeServerId)),
    enabled: Boolean(client) && Boolean(activeServerId),
    retry: false,
  });

  const qrDataUrl = useMemo(
    () => (qr.data ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(qr.data)}` : null),
    [qr.data],
  );

  const vlessUrl = link.data?.url ?? "";
  const subscriptionUrl = client?.sub_token
    ? `${window.location.origin}/sub/${client.sub_token}`
    : null;

  const downloadProfile = useMutation({
    mutationFn: async () => {
      if (!client) throw new Error("Клиент не выбран");
      const profile = await api.clients.profile(client.id, mode);
      const content = typeof profile === "string" ? profile : JSON.stringify(profile, null, 2);
      downloadFile(
        `${client.name}-profile-${mode}.json`,
        content,
        "application/json;charset=utf-8",
      );
    },
    onSuccess: () => success("Профиль скачан"),
    onError: (mutationError) => toastError("Не удалось скачать профиль", errorMessage(mutationError)),
  });

  function downloadQr() {
    if (qr.data) {
      downloadFile(`${client?.name ?? "client"}-${mode}-qr.svg`, qr.data, "image/svg+xml;charset=utf-8");
      return;
    }
    const svg = document.querySelector("#local-qr-fallback svg");
    if (svg) {
      downloadFile(
        `${client?.name ?? "client"}-vless-qr.svg`,
        new XMLSerializer().serializeToString(svg),
        "image/svg+xml;charset=utf-8",
      );
    }
  }

  return (
    <Dialog open={client !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <QrCode className="size-5 text-muted-foreground" />
            Подключение клиента «{client?.name}»
          </DialogTitle>
          <DialogDescription>
            «Умный профиль» не проксирует российские сайты (QR на URL подписки), «полный туннель» —
            простая VLESS-ссылка.
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
              Подключение доступно только для серверов с ролью entry (или both). Добавьте и
              провижиньте entry-узел с настроенными REALITY-ключами.
            </AlertDescription>
          </Alert>
        ) : (
          <>
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Entry-узел</p>
              <Select
                value={activeServerId}
                onValueChange={(value) => {
                  setServerId(value);
                  setLocalQrFallback(false);
                }}
              >
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

            <Tabs
              value={mode}
              onValueChange={(value) => {
                setMode(value as "smart" | "vless");
                setLocalQrFallback(false);
              }}
            >
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="smart">Умный профиль</TabsTrigger>
                <TabsTrigger value="vless">Полный туннель</TabsTrigger>
              </TabsList>

              <TabsContent value="smart" className="space-y-3">
                <Alert variant="info">
                  <AlertTitle>Российские сайты — напрямую</AlertTitle>
                  <AlertDescription>
                    Профиль применяет правила <code>geosite:category-ru</code> и{" "}
                    <code>geoip:ru → direct</code> на устройстве: российские сайты не заходят в
                    туннель.
                  </AlertDescription>
                </Alert>

                <QrBlock
                  isLoading={qr.isLoading}
                  isError={qr.isError}
                  error={qr.error}
                  dataUrl={qrDataUrl}
                  alt="QR URL подписки"
                />

                {subscriptionUrl ? (
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">URL подписки</p>
                    <code className="block break-all rounded-md border bg-muted/40 p-2 text-xs">
                      {subscriptionUrl}
                    </code>
                  </div>
                ) : null}

                <Alert variant="info">
                  <AlertCircle aria-hidden />
                  <AlertDescription>
                    QR кодирует абсолютный URL подписки{" "}
                    <code>&lt;PUBLIC_BASE_URL&gt;/sub/&lt;sub_token&gt;</code>, сформированный
                    backend'ом; отсканируйте его в клиенте sing-box / v2ray.
                  </AlertDescription>
                </Alert>

                <div className="flex flex-wrap gap-2">
                  {subscriptionUrl ? (
                    <CopyButton value={subscriptionUrl} label="Копировать URL подписки" />
                  ) : null}
                  <Button type="button" variant="outline" size="sm" onClick={downloadQr} disabled={!qrDataUrl}>
                    <Download />
                    Скачать .svg
                  </Button>
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
              </TabsContent>

              <TabsContent value="vless" className="space-y-3">
                <Alert variant="warning">
                  <AlertTitle>Полный туннель</AlertTitle>
                  <AlertDescription>
                    RU-трафик в этом режиме идёт через entry-IP, а не напрямую с устройства: entry
                    направляет RU-домены в direct, но они уже находятся в туннеле.
                  </AlertDescription>
                </Alert>

                {qr.isError && link.data?.url && localQrFallback ? (
                  <div id="local-qr-fallback" className="flex justify-center">
                    <div className="rounded-xl bg-white p-4 shadow-sm">
                      <QRCodeSVG value={link.data.url} size={216} level="M" marginSize={1} />
                    </div>
                  </div>
                ) : (
                  <QrBlock
                    isLoading={qr.isLoading}
                    isError={qr.isError}
                    error={qr.error}
                    dataUrl={qrDataUrl}
                    alt="QR VLESS-ссылки"
                  />
                )}

                {qr.isError && link.data?.url && !localQrFallback ? (
                  <Button variant="outline" size="sm" onClick={() => setLocalQrFallback(true)}>
                    Построить QR локально
                  </Button>
                ) : null}

                {link.isLoading ? (
                  <div className="flex items-center justify-center py-4">
                    <Spinner className="size-5" />
                  </div>
                ) : link.isError ? (
                  <Alert variant="destructive">
                    <AlertCircle aria-hidden />
                    <AlertTitle>Не удалось получить ссылку</AlertTitle>
                    <AlertDescription>{errorMessage(link.error)}</AlertDescription>
                  </Alert>
                ) : vlessUrl ? (
                  <>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">VLESS-ссылка</p>
                      <code className="block max-h-24 overflow-auto break-all rounded-md border bg-muted/40 p-2 text-xs">
                        {vlessUrl}
                      </code>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <CopyButton value={vlessUrl} label="Копировать vless://" />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          downloadFile(
                            `${client?.name}-vless.json`,
                            JSON.stringify({ url: vlessUrl }, null, 2),
                            "application/json;charset=utf-8",
                          )
                        }
                      >
                        <Download />
                        Скачать .json
                      </Button>
                    </div>
                  </>
                ) : null}
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

interface QrBlockProps {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  dataUrl: string | null;
  alt: string;
}

function QrBlock({ isLoading, isError, error, dataUrl, alt }: QrBlockProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Spinner className="size-6" />
      </div>
    );
  }
  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertCircle aria-hidden />
        <AlertTitle>Не удалось получить QR</AlertTitle>
        <AlertDescription>{errorMessage(error)}</AlertDescription>
      </Alert>
    );
  }
  if (!dataUrl) {
    return <p className="text-sm text-muted-foreground">QR-код недоступен.</p>;
  }
  return (
    <div className="flex justify-center">
      <div className="rounded-xl bg-white p-4 shadow-sm">
        <img src={dataUrl} alt={alt} width={216} height={216} />
      </div>
    </div>
  );
}
