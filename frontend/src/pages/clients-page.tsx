import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Ellipsis, Link2, Pencil, Plus, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { ConnectDialog } from "@/components/clients/connect-dialog";
import { ClientFormDialog } from "@/components/clients/client-form-dialog";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { Client } from "@/lib/api-types";
import { clientsQuery, queryKeys } from "@/lib/queries";
import { useToast } from "@/lib/toast";
import { formatBytes, formatRelative, normalizeTraffic } from "@/lib/utils";

function clientStatus(client: Client): { label: string; variant: "success" | "muted" | "destructive" } {
  if (client.status === "limit") return { label: "Лимит исчерпан", variant: "destructive" };
  if (!client.enabled) return { label: "Отключён", variant: "muted" };
  if (client.status === "disabled") return { label: "Отключён", variant: "muted" };
  if (
    client.traffic_limit_bytes &&
    (client.traffic_bytes ?? 0) >= client.traffic_limit_bytes
  ) {
    return { label: "Лимит исчерпан", variant: "destructive" };
  }
  return { label: "Активен", variant: "success" };
}

export function ClientsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { success, error: toastError } = useToast();
  const clients = useQuery(clientsQuery);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [connectClient, setConnectClient] = useState<Client | null>(null);
  const [deleteClient, setDeleteClient] = useState<Client | null>(null);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.clients });
  };

  const toggle = useMutation({
    mutationFn: ({ client, enabled }: { client: Client; enabled: boolean }) =>
      api.clients.update(client.id, { enabled }),
    onSuccess: (_data, variables) => {
      success(variables.enabled ? "Клиент включён" : "Клиент отключён");
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось изменить клиента", errorMessage(mutationError)),
  });

  const remove = useMutation({
    mutationFn: (client: Client) => api.clients.remove(client.id),
    onSuccess: () => {
      success("Клиент удалён");
      setDeleteClient(null);
      invalidate();
    },
    onError: (mutationError) => toastError("Не удалось удалить клиента", errorMessage(mutationError)),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Клиенты"
        description="Учётные записи VLESS+REALITY. UUID и email-тег создаются автоматически, трафик считается по email."
        actions={
          <Button
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus />
            Новый клиент
          </Button>
        }
      />

      <QueryState
        isLoading={clients.isLoading}
        isError={clients.isError}
        error={clients.error}
        onRetry={() => void clients.refetch()}
        rows={4}
      >
        {(clients.data ?? []).length === 0 ? (
          <EmptyState
            icon={Users}
            title="Клиентов пока нет"
            description="Создайте клиента — панель выдаст UUID, QR-код и VLESS-ссылку для штатного клиента."
            action={
              <Button
                onClick={() => {
                  setEditing(null);
                  setFormOpen(true);
                }}
              >
                <Plus />
                Новый клиент
              </Button>
            }
          />
        ) : (
          <div className="overflow-hidden rounded-xl border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Клиент</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead>Трафик</TableHead>
                  <TableHead>Лимит</TableHead>
                  <TableHead>Последняя активность</TableHead>
                  <TableHead>Включён</TableHead>
                  <TableHead className="w-10" aria-label="Действия" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(clients.data ?? []).map((client) => {
                  const status = clientStatus(client);
                  return (
                    <TableRow key={client.id}>
                      <TableCell>
                        <Link
                          to="/clients/$clientId"
                          params={{ clientId: String(client.id) }}
                          className="font-medium hover:underline"
                        >
                          {client.name}
                        </Link>
                        <div className="font-mono text-xs text-muted-foreground">{client.email_tag}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        {formatBytes(normalizeTraffic(client.traffic_bytes ?? client.traffic_24h).total)}
                      </TableCell>
                      <TableCell className="text-sm">
                        {client.traffic_limit_bytes ? formatBytes(client.traffic_limit_bytes) : "—"}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {client.last_seen_at ? formatRelative(client.last_seen_at) : "—"}
                      </TableCell>
                      <TableCell>
                        <Switch
                          checked={client.enabled}
                          onCheckedChange={(enabled) => toggle.mutate({ client, enabled })}
                          disabled={toggle.isPending}
                          aria-label={`Включить клиента ${client.name}`}
                        />
                      </TableCell>
                      <TableCell>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" aria-label={`Действия для ${client.name}`}>
                              <Ellipsis className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setConnectClient(client)}>
                              <Link2 className="size-4" />
                              Подключиться
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() =>
                                void navigate({
                                  to: "/clients/$clientId",
                                  params: { clientId: String(client.id) },
                                })
                              }
                            >
                              Открыть карточку
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => {
                                setEditing(client);
                                setFormOpen(true);
                              }}
                            >
                              <Pencil className="size-4" />
                              Изменить
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem destructive onClick={() => setDeleteClient(client)}>
                              <Trash2 className="size-4" />
                              Удалить
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>

      <ClientFormDialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditing(null);
        }}
        client={editing}
        onSaved={invalidate}
      />

      <ConnectDialog
        client={connectClient}
        onOpenChange={(open) => {
          if (!open) setConnectClient(null);
        }}
      />

      <Dialog open={deleteClient !== null} onOpenChange={(open) => !open && setDeleteClient(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить клиента «{deleteClient?.name}»?</DialogTitle>
            <DialogDescription>
              Пользователь будет удалён со всех entry-узлов без перезапуска Xray, подписка и QR
              перестанут работать. Действие необратимо.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteClient(null)}>
              Отмена
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteClient && remove.mutate(deleteClient)}
              disabled={remove.isPending}
            >
              <Trash2 />
              Удалить клиента
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
