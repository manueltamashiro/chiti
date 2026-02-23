"""Tests for the audit log module."""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def patch_db_path(tmp_path):
    db = tmp_path / "test.db"
    with patch("backend.memory.audit._db_path", return_value=db):
        yield db


@pytest.mark.asyncio
async def test_log_and_list():
    from backend.memory.audit import init_audit_db, log_tool_execution, list_audit_logs
    await init_audit_db()
    eid = await log_tool_execution(
        tool_name="read_file",
        tier="read_only",
        params={"path": "/tmp/test.txt"},
        result_summary="File read OK",
        correlation_id="abc123",
    )
    assert eid

    logs = await list_audit_logs()
    assert any(e["id"] == eid for e in logs)


@pytest.mark.asyncio
async def test_filter_by_tool():
    from backend.memory.audit import init_audit_db, log_tool_execution, list_audit_logs
    await init_audit_db()
    await log_tool_execution("read_file", "read_only", {}, "ok")
    await log_tool_execution("write_file", "reversible_write", {}, "ok")

    logs = await list_audit_logs(tool_name="write_file")
    assert all(e["tool_name"] == "write_file" for e in logs)


@pytest.mark.asyncio
async def test_get_entry():
    from backend.memory.audit import init_audit_db, log_tool_execution, get_audit_entry
    await init_audit_db()
    eid = await log_tool_execution("ping", "read_only", {"host": "8.8.8.8"}, "ok", user_approved=True)
    entry = await get_audit_entry(eid)
    assert entry is not None
    assert entry["tool_name"] == "ping"
    assert entry["user_approved"] is True


@pytest.mark.asyncio
async def test_purge_old_entries():
    from backend.memory.audit import init_audit_db, log_tool_execution, purge_old_entries, list_audit_logs
    import aiosqlite
    from backend.memory.audit import _db_path

    await init_audit_db()

    # Insert an old entry manually
    old_ts = "2020-01-01T00:00:00+00:00"
    async with aiosqlite.connect(_db_path()) as db:
        import uuid
        await db.execute(
            "INSERT INTO audit_log(id, tool_name, tier, params_json, result_summary, timestamp) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), "old_tool", "read_only", "{}", "old", old_ts),
        )
        await db.commit()

    deleted = await purge_old_entries()
    assert deleted >= 1
