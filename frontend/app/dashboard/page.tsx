"use client";

import { useState, useEffect, useCallback } from "react";
import { useWsStore } from "@/lib/ws";

type ScheduledJob = {
  id: string;
  name: string;
  cron: string;
  enabled: boolean;
  last_run_at: string | null;
  last_result: string | null;
};

type QueueJob = {
  id: string;
  type: string;
  status: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
};

type Notification = {
  id: string;
  title: string;
  body: string;
  priority: string;
  source: string;
  created_at: string;
};

const statusColor: Record<string, string> = {
  pending: "text-zinc-400",
  running: "text-indigo-300",
  done: "text-emerald-400",
  failed: "text-rose-400",
};

const priorityColor: Record<string, string> = {
  info: "text-indigo-300 bg-indigo-900/30",
  warning: "text-amber-300 bg-amber-900/30",
  urgent: "text-rose-300 bg-rose-900/30",
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-3">{title}</h2>
      {children}
    </div>
  );
}

export default function DashboardPage() {
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [queue, setQueue] = useState<QueueJob[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const wsNotifs = useWsStore((s) => s.notifications);

  const fetchData = useCallback(async () => {
    try {
      const [jRes, qRes, nRes] = await Promise.all([
        fetch("/api/jobs").then((r) => r.json()),
        fetch("/api/jobs/queue?limit=10").then((r) => r.json()),
        fetch("/api/notifications?limit=20").then((r) => r.json()),
      ]);
      setJobs(jRes.jobs ?? []);
      setQueue(qRes.jobs ?? []);
      setNotifications(nRes.notifications ?? []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Merge live WS notifications
  const wsIds = new Set(notifications.map((n) => n.id));
  const allNotifs = [
    ...wsNotifs.filter((n) => !wsIds.has(n.id)),
    ...notifications,
  ]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 10);

  const activeAlerts = allNotifs.filter((n) => n.priority !== "info").slice(0, 5);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Dashboard</h1>
          <div className="flex gap-3">
            <button
              onClick={fetchData}
              className="text-xs px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-300 transition-colors"
            >
              Refresh
            </button>
            <a href="/" className="text-sm text-indigo-400 hover:text-indigo-300">← Back to chat</a>
          </div>
        </div>

        {loading ? (
          <p className="text-center text-zinc-500 py-16">Loading dashboard…</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            {/* Active Alerts */}
            <Card title="Active Alerts">
              {activeAlerts.length === 0 ? (
                <p className="text-xs text-zinc-600 py-3">No active alerts</p>
              ) : (
                <div className="space-y-2">
                  {activeAlerts.map((n) => (
                    <div key={n.id} className="flex items-start gap-2 p-2 rounded-lg bg-zinc-800/50">
                      <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0 ${priorityColor[n.priority] ?? "text-zinc-400"}`}>
                        {n.priority}
                      </span>
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-zinc-200 truncate">{n.title}</p>
                        <p className="text-[11px] text-zinc-500 line-clamp-2 mt-0.5">{n.body}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            {/* Scheduled Jobs */}
            <Card title={`Scheduled Jobs (${jobs.length})`}>
              {jobs.length === 0 ? (
                <p className="text-xs text-zinc-600 py-3">No scheduled jobs. Ask the assistant to create one.</p>
              ) : (
                <div className="space-y-2">
                  {jobs.slice(0, 6).map((j) => (
                    <div key={j.id} className="flex items-center gap-3 py-1.5 border-b border-zinc-800 last:border-0">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-xs font-medium text-zinc-200 truncate">{j.name}</p>
                          {!j.enabled && (
                            <span className="text-[10px] text-zinc-600 bg-zinc-800 px-1 rounded">disabled</span>
                          )}
                        </div>
                        <p className="text-[10px] text-zinc-500 font-mono mt-0.5">{j.cron}</p>
                      </div>
                      <div className="text-right shrink-0">
                        <p className="text-[10px] text-zinc-600">
                          {j.last_run_at ? `Last: ${j.last_run_at.slice(0, 10)}` : "Never run"}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            {/* Recent Queue Jobs */}
            <Card title="Recent Job Queue">
              {queue.length === 0 ? (
                <p className="text-xs text-zinc-600 py-3">No recent queue jobs</p>
              ) : (
                <div className="space-y-1.5">
                  {queue.slice(0, 8).map((j) => (
                    <div key={j.id} className="flex items-center gap-2 py-1">
                      <span className={`text-[10px] font-mono ${statusColor[j.status] ?? "text-zinc-400"}`}>
                        {j.status}
                      </span>
                      <span className="text-xs text-zinc-400 flex-1 truncate">{j.type}</span>
                      <span className="text-[10px] text-zinc-600 shrink-0">
                        {j.created_at.slice(11, 16)} UTC
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            {/* Recent Notifications */}
            <Card title="Recent Notifications">
              {allNotifs.length === 0 ? (
                <p className="text-xs text-zinc-600 py-3">No notifications yet</p>
              ) : (
                <div className="space-y-2">
                  {allNotifs.slice(0, 6).map((n) => (
                    <div key={n.id} className="flex items-start gap-2 py-1 border-b border-zinc-800 last:border-0">
                      <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0 ${priorityColor[n.priority] ?? "text-zinc-400"}`}>
                        {n.priority}
                      </span>
                      <div className="min-w-0">
                        <p className="text-xs text-zinc-300 truncate">{n.title}</p>
                        <p className="text-[10px] text-zinc-600">{n.created_at.slice(0, 16).replace("T", " ")}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <a href="/audit-log" className="block text-[10px] text-indigo-400 hover:text-indigo-300 mt-2">
                View audit log →
              </a>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
