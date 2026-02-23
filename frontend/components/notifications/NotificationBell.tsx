"use client";

import { useState } from "react";
import { useWsStore } from "@/lib/ws";
import { NotificationPanel } from "./NotificationPanel";

export function NotificationBell() {
  const unreadCount = useWsStore((s) => s.unreadCount);
  const markAllRead = useWsStore((s) => s.markAllRead);
  const [open, setOpen] = useState(false);

  const toggle = () => {
    if (!open) markAllRead();
    setOpen((v) => !v);
  };

  return (
    <div className="relative">
      <button
        onClick={toggle}
        className="relative p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors"
        aria-label="Notifications"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 bg-rose-500 text-white text-[10px] font-bold rounded-full min-w-[16px] h-4 flex items-center justify-center px-1">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-50">
          <NotificationPanel onClose={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
