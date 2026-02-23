"""Tests for the notification bus."""

import pytest
from unittest.mock import patch, AsyncMock


@pytest.fixture(autouse=True)
def patch_db_path(tmp_path):
    db = tmp_path / "test.db"
    with patch("backend.proactive.notifications._db_path", return_value=db):
        yield db


@pytest.fixture(autouse=True)
def mock_ws_hub():
    with patch("backend.ws.hub.ws_hub") as hub:
        hub.broadcast = AsyncMock()
        yield hub


@pytest.fixture(autouse=True)
def mock_push():
    with patch("backend.proactive.push.send_push_notification", new_callable=AsyncMock):
        yield


@pytest.mark.asyncio
async def test_create_and_list():
    from backend.proactive.notifications import init_notifications_db, create_notification, list_notifications
    await init_notifications_db()

    n = await create_notification("Test alert", "Something happened", priority="warning", source="test")
    assert n["id"]

    notifs = await list_notifications()
    assert any(x["id"] == n["id"] for x in notifs)


@pytest.mark.asyncio
async def test_dismiss():
    from backend.proactive.notifications import init_notifications_db, create_notification, dismiss, list_notifications
    await init_notifications_db()

    n = await create_notification("Dismissed", priority="info")
    await dismiss(n["id"])

    notifs = await list_notifications(include_dismissed=False)
    assert not any(x["id"] == n["id"] for x in notifs)


@pytest.mark.asyncio
async def test_unread_count():
    from backend.proactive.notifications import init_notifications_db, create_notification, get_unread_count, mark_read
    await init_notifications_db()

    n1 = await create_notification("A")
    n2 = await create_notification("B")
    count = await get_unread_count()
    assert count == 2

    await mark_read(n1["id"])
    count = await get_unread_count()
    assert count == 1


@pytest.mark.asyncio
async def test_snooze():
    from backend.proactive.notifications import init_notifications_db, create_notification, snooze, list_notifications
    import aiosqlite
    from backend.proactive.notifications import _db_path

    await init_notifications_db()
    n = await create_notification("Snoozed")
    await snooze(n["id"], "2099-12-31T00:00:00+00:00")

    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT snoozed_until FROM notifications WHERE id = ?", (n["id"],)) as cur:
            row = await cur.fetchone()
    assert row["snoozed_until"] is not None
