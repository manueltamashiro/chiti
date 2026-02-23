"use client";

import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";

type Dataset = { label: string; data: number[]; color?: string };
type ChartData = {
  labels?: string[];
  datasets?: Dataset[];
  data?: Array<Record<string, unknown>>;
};

const COLORS = ["#6366f1", "#22d3ee", "#f59e0b", "#10b981", "#f43f5e", "#a78bfa"];

type Props = {
  content: ChartData | unknown;
  metadata?: Record<string, string>;
};

export function ChartBlock({ content, metadata }: Props) {
  const chartType = metadata?.type ?? "line";
  const raw = content as ChartData;

  // Normalise to recharts-friendly format
  const chartData =
    raw?.data ??
    (raw?.labels ?? []).map((label, i) => {
      const point: Record<string, unknown> = { label };
      (raw?.datasets ?? []).forEach((ds) => {
        point[ds.label] = ds.data?.[i];
      });
      return point;
    });

  if (!chartData.length) {
    return <p className="text-zinc-500 text-sm">No chart data</p>;
  }

  const dataKeys = raw?.datasets?.map((d) => d.label) ??
    Object.keys(chartData[0] ?? {}).filter((k) => k !== "label");

  return (
    <div className="w-full h-56 my-2">
      <ResponsiveContainer width="100%" height="100%">
        {chartType === "bar" ? (
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
            <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
            <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} />
            <Legend />
            {dataKeys.map((k, i) => (
              <Bar key={k} dataKey={k} fill={COLORS[i % COLORS.length]} />
            ))}
          </BarChart>
        ) : chartType === "pie" ? (
          <PieChart>
            <Pie data={chartData} dataKey={dataKeys[0] ?? "value"} nameKey="label" cx="50%" cy="50%" outerRadius={80}>
              {chartData.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} />
            <Legend />
          </PieChart>
        ) : chartType === "scatter" ? (
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
            <XAxis dataKey="x" name="x" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
            <YAxis dataKey="y" name="y" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} />
            <Scatter data={chartData} fill={COLORS[0]} />
          </ScatterChart>
        ) : (
          /* default: line */
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
            <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
            <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} />
            <Legend />
            {dataKeys.map((k, i) => (
              <Line key={k} type="monotone" dataKey={k} stroke={COLORS[i % COLORS.length]} dot={false} />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
