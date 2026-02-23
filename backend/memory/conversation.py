"""
memory/conversation.py — SQLite-backed conversation history.

Schema:
    conversations(id TEXT PK, created_at TEXT, title TEXT)
    messages(id TEXT PK, conversation_id TEXT FK, role TEXT, content TEXT,
             blocks_json TEXT, timestamp TEXT)

Uses aiosqlite for async access so it never blocks the event loop.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    p = Path(cfg.database.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Schema bootstrap (run once on startup)
# ---------------------------------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
    content         TEXT NOT NULL DEFAULT '',
    blocks_json     TEXT,           -- JSON list of OutputBlock dicts (assistant only)
    timestamp       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, timestamp);
"""


async def init_db() -> None:
    """Create tables if they don't exist. Called once at app startup."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Conversation DB initialized", extra={"path": str(_db_path())})


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

async def create_conversation(title: str = "") -> Dict[str, Any]:
    cid = str(uuid.uuid4())
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO conversations(id, title, created_at) VALUES (?, ?, ?)",
            (cid, title, now),
        )
        await db.commit()
    return {"id": cid, "title": title, "created_at": now}


async def list_conversations(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, created_at FROM conversations "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, created_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def update_conversation_title(conversation_id: str, title: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------

async def add_message(
    conversation_id: str,
    role: str,
    content: str,
    blocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Append a message to a conversation. Creates the conversation if missing."""
    # Ensure conversation exists
    if not await get_conversation(conversation_id):
        await create_conversation()

    mid = str(uuid.uuid4())
    now = _now_iso()
    blocks_json = json.dumps(blocks) if blocks else None

    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO messages(id, conversation_id, role, content, blocks_json, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, conversation_id, role, content, blocks_json, now),
        )
        await db.commit()

    return {
        "id": mid,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "blocks": blocks or [],
        "timestamp": now,
    }


async def get_messages(
    conversation_id: str,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, conversation_id, role, content, blocks_json, timestamp "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY timestamp ASC LIMIT ? OFFSET ?",
            (conversation_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["blocks"] = json.loads(d.pop("blocks_json") or "[]")
        result.append(d)
    return result


async def get_recent_messages_for_llm(
    conversation_id: str,
    limit: int = 20,
) -> List[Dict[str, str]]:
    """Return last `limit` messages in Anthropic role/content format."""
    msgs = await get_messages(conversation_id, limit=limit)
    return [{"role": m["role"], "content": m["content"]} for m in msgs
            if m["role"] in ("user", "assistant")]
