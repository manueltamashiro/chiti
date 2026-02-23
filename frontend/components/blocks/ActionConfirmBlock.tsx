"use client";

import { useState } from "react";
import { approveToolCall, denyToolCall } from "@/lib/api";

type ActionConfirmData = {
  tool_call_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  tier: string;
  justification: string;
  confirmation_type: "soft" | "explicit";
};

type Props = { content: ActionConfirmData | unknown };

export function ActionConfirmBlock({ content }: Props) {
  const d = content as ActionConfirmData;
  const [status, setStatus] = useState<"pending" | "approved" | "denied">("pending");
  const [loading, setLoading] = useState(false);

  const isHighImpact = d?.tier === "high_impact";

  const handle = async (approve: boolean) => {
    setLoading(true);
    try {
      if (approve) {
        await approveToolCall(d.tool_call_id);
        setStatus("approved");
      } else {
        await denyToolCall(d.tool_call_id);
        setStatus("denied");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className={`my-2 rounded-lg border p-4 ${
        isHighImpact
          ? "border-rose-700 bg-rose-950/30"
          : "border-amber-700 bg-amber-950/30"
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
            isHighImpact
              ? "bg-rose-800 text-rose-100"
              : "bg-amber-800 text-amber-100"
          }`}
        >
          {isHighImpact ? "⚠ TIER 3 — Explicit Confirmation" : "▸ TIER 2 — Confirmation"}
        </span>
        <code className="text-sm font-mono text-zinc-300">{d?.tool_name}</code>
      </div>

      {/* Params */}
      <pre className="bg-zinc-900 rounded p-3 text-xs text-zinc-300 overflow-auto mb-3 max-h-40">
        {JSON.stringify(d?.params ?? {}, null, 2)}
      </pre>

      {/* Justification */}
      <p className="text-xs text-zinc-400 mb-4 leading-relaxed">{d?.justification}</p>

      {/* Actions */}
      {status === "pending" ? (
        <div className="flex gap-2">
          <button
            disabled={loading}
            onClick={() => handle(true)}
            className="px-4 py-1.5 text-sm rounded bg-indigo-600 hover:bg-indigo-500 text-white font-medium disabled:opacity-50 transition-colors"
          >
            {loading ? "…" : "Approve"}
          </button>
          <button
            disabled={loading}
            onClick={() => handle(false)}
            className="px-4 py-1.5 text-sm rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-200 font-medium disabled:opacity-50 transition-colors"
          >
            Deny
          </button>
        </div>
      ) : (
        <p
          className={`text-sm font-medium ${
            status === "approved" ? "text-emerald-400" : "text-rose-400"
          }`}
        >
          {status === "approved" ? "✓ Approved" : "✗ Denied"}
        </p>
      )}
    </div>
  );
}
