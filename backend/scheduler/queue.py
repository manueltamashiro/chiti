"""
scheduler/queue.py — SQLite-backed job queue.

The API process enqueues jobs; the Worker process polls and executes them.

Schema:
    jobs(id, type, payload_json, status, created_at, started_at,
         completed_at, result_json, error)

Status flow: pending → running → done | failed
Polling interval: 500ms in Worker.
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

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    p = Path(cfg.database.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    result_json     TEXT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
"""


async def init_queue_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Job queue DB initialized")


async def enqueue(job_type: str, payload: Dict[str, Any]) -> str:
    """Add a job to the queue. Returns job ID."""
    jid = str(uuid.uuid4())
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO jobs(id, type, payload_json, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (jid, job_type, json.dumps(payload), STATUS_PENDING, _now_iso()),
        )
        await db.commit()
    logger.debug("Job enqueued", extra={"job_id": jid, "type": job_type})
    return jid


async def claim_next() -> Optional[Dict[str, Any]]:
    """Atomically claim the next pending job. Returns the job dict or None."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        # Find oldest pending job
        async with db.execute(
            "SELECT id FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1",
            (STATUS_PENDING,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        jid = row["id"]
        now = _now_iso()
        await db.execute(
            "UPDATE jobs SET status = ?, started_at = ? WHERE id = ? AND status = ?",
            (STATUS_RUNNING, now, jid, STATUS_PENDING),
        )
        await db.commit()

        # Re-fetch the full row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (jid,)) as cur:
            job_row = await cur.fetchone()
        if not job_row:
            return None

    job = dict(job_row)
    job["payload"] = json.loads(job.pop("payload_json", "{}"))
    return job


async def mark_done(job_id: str, result: Any = None) -> None:
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE jobs SET status = ?, completed_at = ?, result_json = ? WHERE id = ?",
            (STATUS_DONE, now, json.dumps(result), job_id),
        )
        await db.commit()


async def mark_failed(job_id: str, error: str) -> None:
    now = _now_iso()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
            (STATUS_FAILED, now, error[:2000], job_id),
        )
        await db.commit()


async def list_jobs(
    limit: int = 50,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if status:
            async with db.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

    for r in rows:
        try:
            r["payload"] = json.loads(r.pop("payload_json", "{}"))
            r["result"] = json.loads(r["result_json"]) if r.get("result_json") else None
        except Exception:
            r["payload"] = {}
            r["result"] = None
    return rows


async def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    r["payload"] = json.loads(r.pop("payload_json", "{}"))
    r["result"] = json.loads(r["result_json"]) if r.get("result_json") else None
    return r
