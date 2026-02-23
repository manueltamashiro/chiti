"""
proactive/push.py — Web Push notifications via VAPID.

Generates a VAPID key pair on first run (stored in config.yml fields via env).
Sends notifications only to subscribed clients.

Subscriptions stored in SQLite:
    push_subscriptions(id, endpoint, auth, p256dh, created_at)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)


def _db_path() -> Path:
    p = Path(cfg.database.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          TEXT PRIMARY KEY,
    endpoint    TEXT NOT NULL UNIQUE,
    auth        TEXT NOT NULL,
    p256dh      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


async def init_push_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Push subscriptions DB initialized")


async def save_subscription(endpoint: str, auth: str, p256dh: str) -> str:
    """Save or update a push subscription. Returns subscription ID."""
    sid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO push_subscriptions(id, endpoint, auth, p256dh, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET auth = excluded.auth, p256dh = excluded.p256dh
            """,
            (sid, endpoint, auth, p256dh, now),
        )
        await db.commit()
    return sid


async def delete_subscription(endpoint: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.commit()


async def _list_subscriptions() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM push_subscriptions") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def send_push_notification(
    title: str,
    body: str,
    notification_id: str,
) -> int:
    """
    Send a web push notification to all subscribed clients.
    Returns number of successful pushes.
    Silently skips if pywebpush is not installed or VAPID keys not configured.
    """
    if not cfg.notifications.push_enabled:
        return 0

    if not cfg.notifications.vapid_private_key or not cfg.notifications.vapid_email:
        logger.debug("Web push not configured — skipping")
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.debug("pywebpush not installed — skipping push")
        return 0

    subscriptions = await _list_subscriptions()
    if not subscriptions:
        return 0

    payload = json.dumps({"notification_id": notification_id, "title": title, "body": body})
    sent = 0
    stale: List[str] = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"auth": sub["auth"], "p256dh": sub["p256dh"]},
                },
                data=payload,
                vapid_private_key=cfg.notifications.vapid_private_key,
                vapid_claims={
                    "sub": f"mailto:{cfg.notifications.vapid_email}",
                },
            )
            sent += 1
        except Exception as exc:
            msg = str(exc)
            if "410" in msg or "404" in msg:
                # Subscription expired — remove it
                stale.append(sub["endpoint"])
            else:
                logger.warning("Push send failed", extra={"error": msg[:200]})

    for endpoint in stale:
        await delete_subscription(endpoint)

    logger.info("Web push sent", extra={"sent": sent, "stale_removed": len(stale)})
    return sent
