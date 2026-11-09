import { useMemo } from "react";
import { LineChart as LineChartIcon } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EmptyState } from "@/components/ui/empty-state";
import type { TrafficPoint, TrafficResponse } from "@/lib/api-types";
import { formatBytes, formatDate, formatDateTime, formatTime, normalizeTraffic } from "@/lib/utils";

const COLORS = ["#6366f1", "#14b8a6", "#f59e0b", "#ec4899", "#22d3ee", "#a855f7", "#84cc16", "#f97316"];

interface ChartRow {
  ts: string;
  [series: string]: string | number;
}

interface TrafficChartProps {
  data: TrafficResponse | undefined;
  range: string;
  height?: number;
}

interface TooltipPayloadItem {
  name?: string;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
}

interface ChartTooltipProps {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<TooltipPayloadItem>;
}

function ChartTooltip({ active, label, payload }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium">{formatDateTime(String(label))}</p>
      <ul className="space-y-0.5">
        {payload.map((item) => (
          <li key={String(item.dataKey ?? item.name)} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              <span
                className="inline-block size-2 rounded-full"
                style={{ backgroundColor: item.color ?? "#6366f1" }}
              />
              {item.name}
            </span>
            <span className="font-medium">{formatBytes(Number(item.value ?? 0))}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function pickTs(point: TrafficPoint): string | null {
  return point.ts ?? point.t ?? point.bucket_ts ?? null;
}

export function TrafficChart({ data, range, height = 300 }: TrafficChartProps) {
  const { chartData, seriesNames } = useMemo(() => {
    const rawSeries =
      data?.series && data.series.length > 0
        ? data.series
        : data?.points && data.points.length > 0
          ? [{ name: "Трафик", points: data.points }]
          : [];
    const rows = new Map<string, ChartRow>();
    const names: string[] = [];
    for (const series of rawSeries) {
      const name = series.name ?? series.label ?? series.key ?? "Трафик";
      names.push(name);
      const points = series.points ?? series.data ?? [];
      for (const point of points) {
        const ts = pickTs(point);
        if (!ts) continue;
        const traffic = normalizeTraffic(point);
        const row = rows.get(ts) ?? { ts };
        const previous = Number(row[name] ?? 0);
        row[name] = previous + traffic.total;
        rows.set(ts, row);
      }
    }
    const sorted = [...rows.values()].sort((left, right) => String(left.ts).localeCompare(String(right.ts)));
    return { chartData: sorted, seriesNames: names };
  }, [data]);

  if (chartData.length === 0) {
    return (
      <EmptyState
        icon={LineChartIcon}
        title="Данных пока нет"
        description="График появится после первых батчей метрик с узлов (интервал — 15 секунд)."
        className="py-10"
      />
    );
  }

  const tickFormatter = (value: string) => (range === "24h" ? formatTime(value) : formatDate(value));

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            {seriesNames.map((name, index) => (
              <linearGradient key={name} id={`traffic-gradient-${index}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLORS[index % COLORS.length]} stopOpacity={0.35} />
                <stop offset="95%" stopColor={COLORS[index % COLORS.length]} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
          <XAxis
            dataKey="ts"
            tickFormatter={tickFormatter}
            tick={{ fontSize: 11 }}
            stroke="currentColor"
            className="text-muted-foreground"
            minTickGap={24}
          />
          <YAxis
            tickFormatter={(value: number) => formatBytes(value)}
            tick={{ fontSize: 11 }}
            width={72}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <Tooltip content={<ChartTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {seriesNames.map((name, index) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stackId="traffic"
              stroke={COLORS[index % COLORS.length]}
              strokeWidth={2}
              fill={`url(#traffic-gradient-${index})`}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
