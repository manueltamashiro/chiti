"use client";

import { LineChart, Line, ResponsiveContainer } from "recharts";

type MetricData = {
  label: string;
  value: string | number;
  delta?: string;
  sparkline?: number[];
  unit?: string;
};

type Props = { content: MetricData | unknown };

export function MetricBlock({ content }: Props) {
  const m = content as MetricData;
  const isPositive = String(m?.delta ?? "").startsWith("+");
  const isNegative = String(m?.delta ?? "").startsWith("-");
  const sparkData = (m?.sparkline ?? []).map((v) => ({ v }));

  return (
    <div className="inline-flex flex-col bg-zinc-800 rounded-lg border border-zinc-700 px-4 py-3 min-w-[140px] my-1 mr-2">
      <span className="text-xs text-zinc-400 mb-1 truncate">{m?.label ?? "Metric"}</span>
      <div className="flex items-end gap-2">
        <span className="text-2xl font-bold text-white tabular-nums">
          {String(m?.value ?? "—")}
          {m?.unit && <span className="text-sm text-zinc-400 ml-0.5">{m.unit}</span>}
        </span>
        {m?.delta && (
          <span
            className={`text-xs font-medium mb-0.5 ${
              isPositive ? "text-emerald-400" : isNegative ? "text-rose-400" : "text-zinc-400"
            }`}
          >
            {m.delta}
          </span>
        )}
      </div>
      {sparkData.length > 1 && (
        <div className="mt-2 h-8">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparkData}>
              <Line
                type="monotone"
                dataKey="v"
                stroke="#6366f1"
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
