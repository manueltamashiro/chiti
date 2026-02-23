"""Tests for the SQLite job queue."""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def patch_db_path(tmp_path):
    db = tmp_path / "test.db"
    with patch("backend.scheduler.queue._db_path", return_value=db):
        yield db


@pytest.mark.asyncio
async def test_enqueue_and_claim():
    from backend.scheduler.queue import init_queue_db, enqueue, claim_next, STATUS_RUNNING
    await init_queue_db()

    jid = await enqueue("heartbeat", {"foo": "bar"})
    assert jid

    job = await claim_next()
    assert job is not None
    assert job["id"] == jid
    assert job["status"] == STATUS_RUNNING
    assert job["payload"] == {"foo": "bar"}


@pytest.mark.asyncio
async def test_claim_returns_none_when_empty():
    from backend.scheduler.queue import init_queue_db, claim_next
    await init_queue_db()
    job = await claim_next()
    assert job is None


@pytest.mark.asyncio
async def test_mark_done():
    from backend.scheduler.queue import init_queue_db, enqueue, claim_next, mark_done, get_job, STATUS_DONE
    await init_queue_db()
    jid = await enqueue("test_job", {})
    await claim_next()
    await mark_done(jid, {"result": "ok"})
    job = await get_job(jid)
    assert job["status"] == STATUS_DONE
    assert job["result"] == {"result": "ok"}


@pytest.mark.asyncio
async def test_mark_failed():
    from backend.scheduler.queue import init_queue_db, enqueue, claim_next, mark_failed, get_job, STATUS_FAILED
    await init_queue_db()
    jid = await enqueue("bad_job", {})
    await claim_next()
    await mark_failed(jid, "Something went wrong")
    job = await get_job(jid)
    assert job["status"] == STATUS_FAILED
    assert "Something" in job["error"]


@pytest.mark.asyncio
async def test_list_jobs():
    from backend.scheduler.queue import init_queue_db, enqueue, list_jobs
    await init_queue_db()
    await enqueue("job_a", {"x": 1})
    await enqueue("job_b", {"x": 2})
    jobs = await list_jobs()
    assert len(jobs) == 2
