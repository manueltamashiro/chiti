"""
scheduler/jobs.py — SQLite-backed scheduled job definitions managed by the LLM.

This table stores LLM-managed jobs (create/list/update/delete via tools).
The APScheduler in engine.py reads this table on startup and re-registers jobs.

Schema:
    scheduled_jobs(id, name, cron, task_prompt, allowed_tools, enabled, created_at, updated_at)
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    p = Path(cfg.database.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    cron            TEXT NOT NULL,
    task_prompt     TEXT NOT NULL,
    allowed_tools   TEXT NOT NULL DEFAULT '[]',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT,
    last_result     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


async def init_jobs_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Scheduled jobs DB initialized")


async def create_job(
    name: str,
    cron: str,
    task_prompt: str,
    allowed_tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    jid = str(uuid.uuid4())
    now = _now_iso()
    tools_json = json.dumps(allowed_tools or [])
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO scheduled_jobs(id, name, cron, task_prompt, allowed_tools, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (jid, name, cron, task_prompt, tools_json, now, now),
        )
        await db.commit()
    logger.info("Created scheduled job", extra={"job_id": jid, "name": name, "cron": cron})
    return await get_job(jid)


async def list_jobs() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_jobs ORDER BY created_at DESC"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["allowed_tools"] = json.loads(r.get("allowed_tools") or "[]")
        r["enabled"] = bool(r["enabled"])
    return rows


async def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    r["allowed_tools"] = json.loads(r.get("allowed_tools") or "[]")
    r["enabled"] = bool(r["enabled"])
    return r


async def update_job(
    job_id: str,
    cron: Optional[str] = None,
    task_prompt: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    job = await get_job(job_id)
    if not job:
        return None

    updates: List[str] = []
    args: List[Any] = []

    if cron is not None:
        updates.append("cron = ?")
        args.append(cron)
    if task_prompt is not None:
        updates.append("task_prompt = ?")
        args.append(task_prompt)
    if allowed_tools is not None:
        updates.append("allowed_tools = ?")
        args.append(json.dumps(allowed_tools))
    if enabled is not None:
        updates.append("enabled = ?")
        args.append(int(enabled))

    if not updates:
        return job

    updates.append("updated_at = ?")
    args.append(_now_iso())
    args.append(job_id)

    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"UPDATE scheduled_jobs SET {', '.join(updates)} WHERE id = ?",
            args,
        )
        await db.commit()

    return await get_job(job_id)


async def delete_job(job_id: str) -> bool:
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
        deleted = cur.rowcount
        await db.commit()
    return deleted > 0


async def record_run(job_id: str, result: str) -> None:
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE scheduled_jobs SET last_run_at = ?, last_result = ? WHERE id = ?",
            (now, result[:500], job_id),
        )
        await db.commit()
