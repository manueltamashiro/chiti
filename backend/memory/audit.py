"""
memory/audit.py — Audit log for all tool executions.

Schema:
    audit_log(id, correlation_id, tool_name, tier, params_json,
              result_summary, user_approved, timestamp)

Written by ToolResultPipeline after every tool execution.
Retention: cfg.security.audit_log_retention_days (default 90 days).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    correlation_id  TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL,
    tier            TEXT NOT NULL,
    params_json     TEXT NOT NULL DEFAULT '{}',
    result_summary  TEXT NOT NULL DEFAULT '',
    user_approved   INTEGER,   -- NULL=N/A, 1=approved, 0=denied
    timestamp       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_audit_tier ON audit_log(tier);
CREATE INDEX IF NOT EXISTS idx_audit_corr ON audit_log(correlation_id);
"""


async def init_audit_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()
    logger.info("Audit log DB initialized")


async def log_tool_execution(
    tool_name: str,
    tier: str,
    params: Dict[str, Any],
    result_summary: str,
    correlation_id: str = "",
    user_approved: Optional[bool] = None,
) -> str:
    """Write one audit log entry. Returns the entry ID."""
    eid = str(uuid.uuid4())
    approved_int = None if user_approved is None else int(user_approved)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO audit_log(id, correlation_id, tool_name, tier, params_json,
                                  result_summary, user_approved, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (eid, correlation_id, tool_name, tier, json.dumps(params),
             result_summary[:2000], approved_int, _now_iso()),
        )
        await db.commit()
    return eid


async def list_audit_logs(
    limit: int = 100,
    offset: int = 0,
    tool_name: Optional[str] = None,
    tier: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conditions = []
    args: List[Any] = []

    if tool_name:
        conditions.append("tool_name = ?")
        args.append(tool_name)
    if tier:
        conditions.append("tier = ?")
        args.append(tier)
    if date_from:
        conditions.append("timestamp >= ?")
        args.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        args.append(date_to)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    args += [limit, offset]

    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            args,
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    for r in rows:
        try:
            r["params"] = json.loads(r.pop("params_json", "{}"))
        except Exception:
            r["params"] = {}
        r["user_approved"] = None if r["user_approved"] is None else bool(r["user_approved"])
    return rows


async def get_audit_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM audit_log WHERE id = ?", (entry_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    try:
        r["params"] = json.loads(r.pop("params_json", "{}"))
    except Exception:
        r["params"] = {}
    r["user_approved"] = None if r["user_approved"] is None else bool(r["user_approved"])
    return r


async def purge_old_entries() -> int:
    """Delete entries older than retention period. Returns number deleted."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=cfg.security.audit_log_retention_days)
    ).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff,))
        deleted = cur.rowcount
        await db.commit()
    if deleted:
        logger.info("Audit log purged", extra={"deleted": deleted, "cutoff": cutoff})
    return deleted
