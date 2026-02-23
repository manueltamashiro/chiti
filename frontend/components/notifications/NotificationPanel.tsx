"use client";

import { useWsStore, type Notification } from "@/lib/ws";

const priorityColor = {
  info: "bg-indigo-900/40 border-indigo-700",
  warning: "bg-amber-900/40 border-amber-700",
  urgent: "bg-rose-900/40 border-rose-700",
};

const priorityBadge = {
  info: "text-indigo-300",
  warning: "text-amber-300",
  urgent: "text-rose-300",
};

function NotificationCard({ n }: { n: Notification }) {
  return (
    <div className={`p-3 rounded-lg border mb-2 ${priorityColor[n.priority]}`}>
      <div className="flex items-start gap-2">
        <span className={`text-[10px] font-bold uppercase mt-0.5 ${priorityBadge[n.priority]}`}>
          {n.priority}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-zinc-200 truncate">{n.title}</p>
          <p className="text-xs text-zinc-400 mt-0.5 line-clamp-2">{n.body}</p>
          <p className="text-[10px] text-zinc-600 mt-1">
            {new Date(n.created_at).toLocaleTimeString()}
          </p>
        </div>
      </div>
    </div>
  );
}

type Props = { onClose: () => void };

export function NotificationPanel({ onClose }: Props) {
  const notifications = useWsStore((s) => s.notifications);

  return (
    <div className="w-80 max-h-[500px] bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-700">
        <h3 className="text-sm font-semibold text-zinc-200">Notifications</h3>
        <button
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-300 text-xs"
        >
          ✕
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {notifications.length === 0 ? (
          <p className="text-xs text-zinc-500 text-center py-8">No notifications</p>
        ) : (
          notifications.map((n) => <NotificationCard key={n.id} n={n} />)
        )}
      </div>
    </div>
  );
}
