import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const BYTE_UNITS = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"];

/** Форматирование байтов в русской локали: 1,4 ГБ. */
export function formatBytes(value: number | null | undefined, fractionDigits?: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const bytes = Math.max(0, Number(value));
  if (bytes < 1024) return `${Math.round(bytes)} Б`;
  let exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), BYTE_UNITS.length - 1);
  const scaled = bytes / 1024 ** exp;
  const digits = fractionDigits ?? (scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2);
  return `${scaled.toLocaleString("ru-RU", { maximumFractionDigits: digits })} ${BYTE_UNITS[exp]}`;
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: digits })}%`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDateTimeSeconds(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(date);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

/** «5 минут назад», «2 ч назад», иначе дата. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const diffSec = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diffSec);
  const past = diffSec <= 0;
  const format = (n: number, unit: "second" | "minute" | "hour" | "day") => {
    const formatted = new Intl.RelativeTimeFormat("ru-RU", { numeric: "auto" }).format(
      past ? -n : n,
      unit,
    );
    return formatted;
  };
  if (abs < 45) return "только что";
  if (abs < 60 * 45) return format(Math.round(abs / 60), "minute");
  if (abs < 60 * 60 * 22) return format(Math.round(abs / 3600), "hour");
  if (abs < 60 * 60 * 24 * 7) return format(Math.round(abs / 86400), "day");
  return formatDate(iso);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days} дн ${hours} ч`;
  if (hours > 0) return `${hours} ч ${minutes} мин`;
  if (minutes > 0) return `${minutes} мин`;
  return `${total} с`;
}

export interface NormalizedTraffic {
  up: number;
  down: number;
  total: number;
}

/** Приводит произвольное представление трафика из API к {up, down, total}. */
export function normalizeTraffic(value: unknown): NormalizedTraffic {
  if (value === null || value === undefined) return { up: 0, down: 0, total: 0 };
  if (typeof value === "number") return { up: 0, down: 0, total: value };
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
    return { up: 0, down: 0, total: Number(value) };
  }
  if (typeof value !== "object") return { up: 0, down: 0, total: 0 };
  const raw = value as Record<string, unknown>;
  const pick = (...keys: string[]): number => {
    for (const key of keys) {
      const candidate = raw[key];
      if (typeof candidate === "number" && Number.isFinite(candidate)) return candidate;
      if (typeof candidate === "string" && candidate.trim() !== "" && !Number.isNaN(Number(candidate))) {
        return Number(candidate);
      }
    }
    return 0;
  };
  const up = pick("up", "up_bytes", "uplink");
  const down = pick("down", "down_bytes", "downlink");
  const total = pick("total", "total_bytes") || up + down;
  return { up, down, total };
}

export function regionLabel(code: string | null | undefined): string {
  if (!code) return "—";
  const map: Record<string, string> = {
    RU: "Россия",
    SG: "Сингапур",
    US: "США",
    NL: "Нидерланды",
    DE: "Германия",
    FI: "Финляндия",
    TR: "Турция",
    KZ: "Казахстан",
    JP: "Япония",
    GB: "Великобритания",
    FR: "Франция",
    SE: "Швеция",
    CH: "Швейцария",
    AE: "ОАЭ",
    HK: "Гонконг",
    CA: "Канада",
    PL: "Польша",
    CZ: "Чехия",
    EE: "Эстония",
    LV: "Латвия",
    LT: "Литва",
    AM: "Армения",
    GE: "Грузия",
    OTHER: "Другое",
  };
  return map[code.toUpperCase()] ?? code;
}

export function roleLabel(role: string): string {
  switch (role) {
    case "entry":
      return "Entry";
    case "exit":
      return "Exit";
    case "both":
      return "Entry + Exit";
    default:
      return role;
  }
}

export function serverStatusLabel(status: string): string {
  const map: Record<string, string> = {
    new: "Новый",
    provisioning: "Провижининг",
    online: "Онлайн",
    degraded: "Деградация",
    offline: "Офлайн",
    delete_pending: "Удаляется",
    failed: "Ошибка",
  };
  return map[status] ?? status;
}

export function exitHealthLabel(health: string): string {
  const map: Record<string, string> = {
    up: "Доступен",
    down: "Недоступен",
    unknown: "Не проверен",
  };
  return map[health] ?? health;
}

export function exitProtocolLabel(protocol: string): string {
  const map: Record<string, string> = {
    vless_reality: "VLESS + REALITY",
    http: "HTTP CONNECT",
    socks5: "SOCKS5",
  };
  return map[protocol] ?? protocol;
}

export function matchTypeLabel(type: string): string {
  const map: Record<string, string> = {
    domain: "Домен",
    wildcard: "Wildcard",
    regexp: "Regexp",
    geosite: "Geosite",
  };
  return map[type] ?? type;
}

export function revisionStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: "Ожидает",
    applied: "Применена",
    failed: "Ошибка",
  };
  return map[status] ?? status;
}

export function jobStatusLabel(status: string): string {
  const map: Record<string, string> = {
    queued: "В очереди",
    running: "Выполняется",
    succeeded: "Успешно",
    failed: "Ошибка",
    cancelled: "Отменено",
  };
  return map[status] ?? status;
}

export function ruPlural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

export function shortId(value: string | number, length = 8): string {
  const text = String(value ?? "");
  return text.length <= length ? text : `${text.slice(0, length)}…`;
}

export function percent(part: number | null | undefined, total: number | null | undefined): number {
  if (!part || !total || total <= 0) return 0;
  return Math.min(100, Math.round((part / total) * 1000) / 10);
}

const DOMAIN_RE = /^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/i;

export function isValidDomain(value: string): boolean {
  return DOMAIN_RE.test(value.trim().replace(/^\*\./, "").replace(/^domain:/, ""));
}

export function jsonPretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export interface DiffLine {
  type: "same" | "add" | "del";
  text: string;
}

/** Построчный diff (LCS) для JSON-представлений ревизий. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      result.push({ type: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ type: "del", text: a[i] });
      i += 1;
    } else {
      result.push({ type: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < n) {
    result.push({ type: "del", text: a[i] });
    i += 1;
  }
  while (j < m) {
    result.push({ type: "add", text: b[j] });
    j += 1;
  }
  return result;
}

export function downloadFile(filename: string, content: string, mime = "text/plain;charset=utf-8"): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

export function humanFileSizeInputToBytes(gb: string): number | null {
  const value = Number(gb.replace(",", "."));
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * 1024 ** 3);
}

export function bytesToGbString(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "";
  return String(Math.round((bytes / 1024 ** 3) * 100) / 100);
}
