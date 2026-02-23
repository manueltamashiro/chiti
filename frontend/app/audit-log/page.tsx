"use client";

import { useState, useEffect, useCallback } from "react";

type AuditEntry = {
  id: string;
  correlation_id: string;
  tool_name: string;
  tier: string;
  params: Record<string, unknown>;
  result_summary: string;
  user_approved: boolean | null;
  timestamp: string;
};

const tierStyle: Record<string, string> = {
  read_only:        "text-emerald-400 bg-emerald-900/30",
  reversible_write: "text-amber-400 bg-amber-900/30",
  high_impact:      "text-rose-400 bg-rose-900/30",
};

const tierLabel: Record<string, string> = {
  read_only:        "T1",
  reversible_write: "T2",
  high_impact:      "T3",
};

function TierBadge({ tier }: { tier: string }) {
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${tierStyle[tier] ?? "text-zinc-400 bg-zinc-800"}`}>
      {tierLabel[tier] ?? tier}
    </span>
  );
}

function ApprovedBadge({ value }: { value: boolean | null }) {
  if (value === null) return <span className="text-[10px] text-zinc-600">—</span>;
  return (
    <span className={`text-[10px] font-bold ${value ? "text-emerald-400" : "text-rose-400"}`}>
      {value ? "✓ approved" : "✗ denied"}
    </span>
  );
}

export default function LogsPage() {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  // Filters
  const [toolFilter, setToolFilter] = useState("");
  const [tierFilter, setTierFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const LIMIT = 50;

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: String(LIMIT),
        offset: String(offset),
      });
      if (toolFilter) params.set("tool_name", toolFilter);
      if (tierFilter) params.set("tier", tierFilter);

      const res = await fetch(`/api/audit?${params}`);
      if (res.ok) {
        const data = await res.json();
        setLogs(data.logs ?? []);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [toolFilter, tierFilter, offset]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Audit Log</h1>
          <a href="/" className="text-sm text-indigo-400 hover:text-indigo-300">← Back to chat</a>
        </div>

        {/* Filters */}
        <div className="flex gap-3 mb-4">
          <input
            type="text"
            placeholder="Filter by tool name…"
            value={toolFilter}
            onChange={(e) => { setToolFilter(e.target.value); setOffset(0); }}
            className="flex-1 px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-indigo-500"
          />
          <select
            value={tierFilter}
            onChange={(e) => { setTierFilter(e.target.value); setOffset(0); }}
            className="px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">All tiers</option>
            <option value="read_only">Tier 1 (read)</option>
            <option value="reversible_write">Tier 2 (write)</option>
            <option value="high_impact">Tier 3 (destructive)</option>
          </select>
          <button
            onClick={() => fetchLogs()}
            className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-sm text-zinc-300"
          >
            Refresh
          </button>
        </div>

        {/* Table */}
        {loading ? (
          <p className="text-center text-zinc-500 py-12">Loading…</p>
        ) : logs.length === 0 ? (
          <p className="text-center text-zinc-500 py-12">No audit entries found.</p>
        ) : (
          <div className="space-y-1">
            {logs.map((entry) => (
              <div key={entry.id}>
                <div
                  className="flex items-center gap-3 px-4 py-2.5 bg-zinc-900 rounded-lg border border-zinc-800 cursor-pointer hover:border-zinc-600 transition-colors"
                  onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                >
                  <TierBadge tier={entry.tier} />
                  <span className="text-sm font-mono text-zinc-300 min-w-[160px] truncate">{entry.tool_name}</span>
                  <span className="text-xs text-zinc-500 flex-1 truncate">{entry.result_summary}</span>
                  <ApprovedBadge value={entry.user_approved} />
                  <span className="text-[10px] text-zinc-600 shrink-0">
                    {entry.timestamp.slice(0, 16).replace("T", " ")}
                  </span>
                  <span className="text-zinc-600 text-xs">{expanded === entry.id ? "▲" : "▼"}</span>
                </div>

                {expanded === entry.id && (
                  <div className="mx-2 px-4 py-3 bg-zinc-800/60 border border-zinc-700 border-t-0 rounded-b-lg">
                    <div className="grid grid-cols-2 gap-4 text-xs">
                      <div>
                        <p className="text-zinc-500 mb-1 font-medium uppercase tracking-wider text-[10px]">Parameters</p>
                        <pre className="text-zinc-300 bg-zinc-900 rounded p-2 overflow-auto max-h-40 text-[11px]">
                          {JSON.stringify(entry.params, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <p className="text-zinc-500 mb-1 font-medium uppercase tracking-wider text-[10px]">Details</p>
                        <div className="space-y-1 text-zinc-400">
                          <p><span className="text-zinc-600">ID:</span> {entry.id}</p>
                          <p><span className="text-zinc-600">Correlation:</span> {entry.correlation_id || "—"}</p>
                          <p><span className="text-zinc-600">Result:</span> {entry.result_summary}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Pagination */}
        <div className="flex justify-between mt-4">
          <button
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            disabled={offset === 0}
            className="text-xs px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-300 disabled:opacity-40"
          >
            ← Previous
          </button>
          <span className="text-xs text-zinc-500 py-2">Showing {offset + 1}–{offset + logs.length}</span>
          <button
            onClick={() => setOffset(offset + LIMIT)}
            disabled={logs.length < LIMIT}
            className="text-xs px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-300 disabled:opacity-40"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  );
}
