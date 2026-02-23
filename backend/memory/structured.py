"""
memory/structured.py — SQLite-backed structured memory.

Tables:
    facts(id, subject, predicate, object, confidence, source_conversation_id, created_at)
    preferences(id, key, value, updated_at)
    people(id, name, relationship, notes, updated_at)
    events(id, title, date, description, recurring, created_at)

All writes are upserts to avoid duplicates.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    p = Path(cfg.database.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id                      TEXT PRIMARY KEY,
    subject                 TEXT NOT NULL,
    predicate               TEXT NOT NULL,
    object                  TEXT NOT NULL,
    confidence              REAL NOT NULL DEFAULT 0.8,
    source_conversation_id  TEXT,
    created_at              TEXT NOT NULL,
    UNIQUE(subject, predicate)
);

CREATE TABLE IF NOT EXISTS preferences (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    relationship    TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    date        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    recurring   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
"""


async def init_structured_db() -> None:
    """Create structured memory tables. Called at startup."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Structured memory DB initialized")


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

async def upsert_fact(
    subject: str,
    predicate: str,
    obj: str,
    confidence: float = 0.8,
    source_conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not subject or not predicate:
        raise ValueError("fact subject and predicate are required")
    fid = str(uuid.uuid4())
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO facts(id, subject, predicate, object, confidence, source_conversation_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject, predicate) DO UPDATE SET
                object = excluded.object,
                confidence = excluded.confidence,
                source_conversation_id = excluded.source_conversation_id
            """,
            (fid, subject, predicate, obj, confidence, source_conversation_id, now),
        )
        await db.commit()
    return {"id": fid, "subject": subject, "predicate": predicate, "object": obj,
            "confidence": confidence, "created_at": now}


async def list_facts(limit: int = 200) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM facts ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_fact(fact_id: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        await db.commit()


async def get_low_confidence_facts(threshold: float = 0.5) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM facts WHERE confidence < ? ORDER BY confidence ASC", (threshold,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

async def upsert_preference(key: str, value: str) -> Dict[str, Any]:
    if not key:
        raise ValueError("preference key is required")
    pid = str(uuid.uuid4())
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO preferences(id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (pid, key, value, now),
        )
        await db.commit()
    return {"id": pid, "key": key, "value": value, "updated_at": now}


async def list_preferences() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM preferences ORDER BY key ASC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_preference(pref_id: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM preferences WHERE id = ?", (pref_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

async def upsert_person(
    name: str,
    relationship: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    if not name:
        raise ValueError("person name is required")
    pid = str(uuid.uuid4())
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO people(id, name, relationship, notes, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                relationship = excluded.relationship,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (pid, name, relationship, notes, now),
        )
        await db.commit()
    return {"id": pid, "name": name, "relationship": relationship, "notes": notes, "updated_at": now}


async def list_people() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM people ORDER BY name ASC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_person(person_id: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM people WHERE id = ?", (person_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

async def create_event(
    title: str,
    date: str,
    description: str = "",
    recurring: bool = False,
) -> Dict[str, Any]:
    eid = str(uuid.uuid4())
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO events(id, title, date, description, recurring, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, title, date, description, int(recurring), now),
        )
        await db.commit()
    return {"id": eid, "title": title, "date": date, "description": description,
            "recurring": recurring, "created_at": now}


async def list_events(upcoming_only: bool = False) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if upcoming_only:
            now = _now_iso()
            async with db.execute(
                "SELECT * FROM events WHERE date >= ? ORDER BY date ASC LIMIT 20", (now,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute("SELECT * FROM events ORDER BY date DESC LIMIT 100") as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["recurring"] = bool(r["recurring"])
    return rows


async def delete_event(event_id: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await db.commit()
