"use client";

import { useState, useEffect } from "react";
import { useWsStore, type Notification } from "@/lib/ws";

const priorityColor: Record<string, string> = {
  info: "bg-indigo-900/40 border-indigo-700",
  warning: "bg-amber-900/40 border-amber-700",
  urgent: "bg-rose-900/40 border-rose-700",
};

const priorityBadge: Record<string, string> = {
  info: "text-indigo-300",
  warning: "text-amber-300",
  urgent: "text-rose-300",
};

async function dismissNotification(id: string) {
  await fetch(`/api/notifications/${id}/dismiss`, { method: "POST" });
}

async function snoozeNotification(id: string, minutes: number) {
  const until = new Date(Date.now() + minutes * 60_000).toISOString();
  await fetch(`/api/notifications/${id}/snooze?until=${encodeURIComponent(until)}`, { method: "POST" });
}

function NotificationCard({ n, onAction }: { n: Notification; onAction: () => void }) {
  const [expanded, setExpanded] = useState(false);

  const handleDismiss = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await dismissNotification(n.id);
    onAction();
  };

  const handleSnooze = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await snoozeNotification(n.id, 60);
    onAction();
  };

  return (
    <div
      className={`p-3 rounded-lg border mb-2 cursor-pointer ${priorityColor[n.priority] ?? "bg-zinc-800 border-zinc-700"}`}
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-start gap-2">
        <span className={`text-[10px] font-bold uppercase mt-0.5 ${priorityBadge[n.priority] ?? "text-zinc-400"}`}>
          {n.priority}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-zinc-200 truncate">{n.title}</p>
          <p className={`text-xs text-zinc-400 mt-0.5 ${expanded ? "" : "line-clamp-2"}`}>{n.body}</p>
          <p className="text-[10px] text-zinc-600 mt-1">
            {new Date(n.created_at).toLocaleString()}
          </p>
          {expanded && (
            <div className="flex gap-2 mt-2">
              <button
                onClick={handleDismiss}
                className="text-[10px] px-2 py-0.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300"
              >
                Dismiss
              </button>
              <button
                onClick={handleSnooze}
                className="text-[10px] px-2 py-0.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300"
              >
                Snooze 1h
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

type Props = { onClose: () => void };

export function NotificationPanel({ onClose }: Props) {
  const notifications = useWsStore((s) => s.notifications);
  const [apiNotifs, setApiNotifs] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchNotifs = async () => {
    try {
      const res = await fetch("/api/notifications");
      if (res.ok) {
        const data = await res.json();
        setApiNotifs(data.notifications ?? []);
      }
    } catch {
      // fall back to ws store
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNotifs();
  }, []);

  // Merge API notifs with realtime WS notifs (dedupe by id)
  const wsIds = new Set(notifications.map((n) => n.id));
  const merged = [
    ...notifications,
    ...apiNotifs.filter((n) => !wsIds.has(n.id)),
  ].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  return (
    <div className="w-80 max-h-[500px] bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-700">
        <h3 className="text-sm font-semibold text-zinc-200">Notifications</h3>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 text-xs">✕</button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <p className="text-xs text-zinc-500 text-center py-8">Loading…</p>
        ) : merged.length === 0 ? (
          <p className="text-xs text-zinc-500 text-center py-8">No notifications</p>
        ) : (
          merged.map((n) => <NotificationCard key={n.id} n={n} onAction={fetchNotifs} />)
        )}
      </div>
    </div>
  );
}
