import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import type { Client } from "@/lib/api-types";
import { useToast } from "@/lib/toast";
import { bytesToGbString, humanFileSizeInputToBytes } from "@/lib/utils";

interface ClientFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client?: Client | null;
  onSaved: (client: Client) => void;
}

export function ClientFormDialog({ open, onOpenChange, client, onSaved }: ClientFormDialogProps) {
  const { success } = useToast();
  const [name, setName] = useState("");
  const [limitGb, setLimitGb] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(client?.name ?? "");
    setLimitGb(client?.traffic_limit_bytes ? bytesToGbString(client.traffic_limit_bytes) : "");
    setEnabled(client?.enabled ?? true);
    setError(null);
  }, [open, client]);

  const save = useMutation({
    mutationFn: () => {
      const limit = limitGb.trim() ? humanFileSizeInputToBytes(limitGb) : null;
      if (limit !== null && limit <= 0) throw new Error("Лимит должен быть больше нуля");
      return client
        ? api.clients.update(client.id, {
            name: name.trim(),
            enabled,
            traffic_limit_bytes: limit,
          })
        : api.clients.create({
            name: name.trim(),
            traffic_limit_bytes: limit,
            enabled,
          });
    },
    onSuccess: (saved) => {
      success(client ? "Клиент обновлён" : "Клиент создан");
      onSaved(saved);
      onOpenChange(false);
    },
    onError: (mutationError) => setError(errorMessage(mutationError)),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Укажите имя клиента");
      return;
    }
    save.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{client ? `Клиент «${client.name}»` : "Новый клиент"}</DialogTitle>
          <DialogDescription>
            UUID и email-тег генерируются автоматически. Клиент добавляется на все entry-узлы.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={handleSubmit} noValidate>
          <FormField label="Имя" htmlFor="client-name" required hint="Например: iPhone, MacBook, роутер">
            <Input
              id="client-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="iPhone"
              autoComplete="off"
            />
          </FormField>
          <FormField
            label="Лимит трафика, ГБ"
            htmlFor="client-limit"
            hint="Необязательно. При достижении клиент автоматически отключается."
          >
            <Input
              id="client-limit"
              inputMode="decimal"
              value={limitGb}
              onChange={(event) => setLimitGb(event.target.value)}
              placeholder="100"
            />
          </FormField>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">Клиент включён</p>
              <p className="text-xs text-muted-foreground">Отключённый клиент немедленно теряет доступ</p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Клиент включён" />
          </div>
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : null}
              {client ? "Сохранить" : "Создать клиента"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
