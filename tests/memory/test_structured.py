"""Tests for structured memory module."""

import asyncio
import os
import tempfile
import pytest

# Point to a temp DB for tests
_tmp_db = tempfile.mktemp(suffix=".db")
os.environ.setdefault("CHITI_CONFIG", "")

# Patch db path before importing
import backend.config.loader as loader_mod
from unittest.mock import patch


@pytest.fixture(autouse=True)
def patch_db_path(tmp_path):
    db = str(tmp_path / "test.db")
    with patch("backend.memory.structured._db_path", return_value=__import__("pathlib").Path(db)):
        yield db


@pytest.mark.asyncio
async def test_upsert_and_list_facts():
    from backend.memory.structured import init_structured_db, upsert_fact, list_facts
    await init_structured_db()
    f = await upsert_fact("Alice", "likes", "Python", confidence=0.9)
    assert f["subject"] == "Alice"
    facts = await list_facts()
    assert any(x["subject"] == "Alice" for x in facts)


@pytest.mark.asyncio
async def test_fact_upsert_deduplicates():
    from backend.memory.structured import init_structured_db, upsert_fact, list_facts
    await init_structured_db()
    await upsert_fact("Alice", "likes", "Python")
    await upsert_fact("Alice", "likes", "Rust")  # same subject+predicate → update
    facts = await list_facts()
    alice_facts = [f for f in facts if f["subject"] == "Alice" and f["predicate"] == "likes"]
    assert len(alice_facts) == 1
    assert alice_facts[0]["object"] == "Rust"


@pytest.mark.asyncio
async def test_delete_fact():
    from backend.memory.structured import init_structured_db, upsert_fact, delete_fact, list_facts
    await init_structured_db()
    f = await upsert_fact("Bob", "works_at", "Acme")
    await delete_fact(f["id"])
    facts = await list_facts()
    assert not any(x["id"] == f["id"] for x in facts)


@pytest.mark.asyncio
async def test_preferences():
    from backend.memory.structured import init_structured_db, upsert_preference, list_preferences, delete_preference
    await init_structured_db()
    p = await upsert_preference("theme", "dark")
    prefs = await list_preferences()
    assert any(x["key"] == "theme" for x in prefs)
    await delete_preference(p["id"])
    prefs = await list_preferences()
    assert not any(x["key"] == "theme" for x in prefs)


@pytest.mark.asyncio
async def test_people():
    from backend.memory.structured import init_structured_db, upsert_person, list_people, delete_person
    await init_structured_db()
    person = await upsert_person("Carol", relationship="colleague", notes="Works on ML")
    people = await list_people()
    assert any(x["name"] == "Carol" for x in people)
    await delete_person(person["id"])
    people = await list_people()
    assert not any(x["name"] == "Carol" for x in people)


@pytest.mark.asyncio
async def test_events():
    from backend.memory.structured import init_structured_db, create_event, list_events, delete_event
    await init_structured_db()
    ev = await create_event("Team standup", "2026-03-01T09:00:00", recurring=True)
    events = await list_events()
    assert any(x["title"] == "Team standup" for x in events)
    await delete_event(ev["id"])


@pytest.mark.asyncio
async def test_low_confidence_facts():
    from backend.memory.structured import init_structured_db, upsert_fact, get_low_confidence_facts
    await init_structured_db()
    await upsert_fact("X", "maybe", "Y", confidence=0.3)
    await upsert_fact("A", "definitely", "B", confidence=0.9)
    low = await get_low_confidence_facts(threshold=0.5)
    assert any(x["subject"] == "X" for x in low)
    assert not any(x["subject"] == "A" for x in low)
