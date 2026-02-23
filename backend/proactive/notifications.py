"""
proactive/notifications.py — Notification bus and SQLite store.

Schema:
    notifications(id, title, body, priority, source, created_at, read_at,
                  snoozed_until, dismissed_at)

Priority: "info" | "warning" | "urgent"
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)

PRIORITY_INFO = "info"
PRIORITY_WARNING = "warning"
PRIORITY_URGENT = "urgent"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    p = Path(cfg.database.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    priority        TEXT NOT NULL DEFAULT 'info',
    source          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    read_at         TEXT,
    snoozed_until   TEXT,
    dismissed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notif_dismissed ON notifications(dismissed_at);
"""


async def init_notifications_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Notifications DB initialized")


async def create_notification(
    title: str,
    body: str = "",
    priority: str = PRIORITY_INFO,
    source: str = "",
) -> Dict[str, Any]:
    """Create and broadcast a new notification."""
    nid = str(uuid.uuid4())
    now = _now_iso()

    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO notifications(id, title, body, priority, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (nid, title, body, priority, source, now),
        )
        await db.commit()

    notification = {
        "id": nid,
        "title": title,
        "body": body,
        "priority": priority,
        "source": source,
        "created_at": now,
        "read_at": None,
        "snoozed_until": None,
        "dismissed_at": None,
    }

    logger.info("Notification created", extra={"id": nid, "priority": priority, "title": title})

    # Broadcast via WebSocket (non-blocking)
    try:
        from backend.ws.hub import ws_hub
        await ws_hub.broadcast("notification.new", notification)
    except Exception as exc:
        logger.warning("Failed to broadcast notification", extra={"error": str(exc)})

    # Web Push for urgent notifications
    if priority == PRIORITY_URGENT:
        try:
            from backend.proactive.push import send_push_notification
            await send_push_notification(title=title, body=body[:100], notification_id=nid)
        except Exception as exc:
            logger.warning("Failed to send web push", extra={"error": str(exc)})

    return notification


async def list_notifications(
    limit: int = 50,
    include_dismissed: bool = False,
) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if include_dismissed:
            async with db.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT * FROM notifications WHERE dismissed_at IS NULL "
                "ORDER BY priority DESC, created_at DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    return rows


async def get_unread_count() -> int:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL AND dismissed_at IS NULL"
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def mark_read(notification_id: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (_now_iso(), notification_id),
        )
        await db.commit()


async def dismiss(notification_id: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE notifications SET dismissed_at = ? WHERE id = ?",
            (_now_iso(), notification_id),
        )
        await db.commit()


async def snooze(notification_id: str, until: str) -> None:
    """Snooze a notification until an ISO datetime string."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE notifications SET snoozed_until = ? WHERE id = ?",
            (until, notification_id),
        )
        await db.commit()
