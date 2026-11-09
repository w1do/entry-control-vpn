import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { useToast } from "@/lib/toast";
import { copyToClipboard } from "@/lib/utils";

interface CopyButtonProps extends Omit<ButtonProps, "onClick"> {
  value: string;
  label?: string;
  copiedLabel?: string;
}

function CopyButton({ value, label = "Копировать", copiedLabel = "Скопировано", ...props }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const { error } = useToast();

  async function handleCopy() {
    try {
      await copyToClipboard(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      error("Не удалось скопировать", "Скопируйте значение вручную");
    }
  }

  return (
    <Button type="button" variant="outline" size="sm" onClick={handleCopy} {...props}>
      {copied ? <Check className="text-success" /> : <Copy />}
      {copied ? copiedLabel : label}
    </Button>
  );
}

export { CopyButton };
