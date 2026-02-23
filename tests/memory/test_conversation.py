"""
Tests for backend/memory/conversation.py — SQLite conversation history
"""

import pytest
import tempfile
import os
from unittest.mock import patch
from pathlib import Path

from backend.memory import conversation as conv_mod


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """Redirect DB to a temp file for each test."""
    db_file = tmp_path / "test_assistant.db"
    with patch.object(conv_mod, "_db_path", return_value=db_file):
        yield db_file


class TestConversations:
    async def test_init_creates_tables(self):
        await conv_mod.init_db()

    async def test_create_conversation(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation(title="Test chat")
        assert c["id"]
        assert c["title"] == "Test chat"
        assert c["created_at"]

    async def test_list_empty(self):
        await conv_mod.init_db()
        result = await conv_mod.list_conversations()
        assert result == []

    async def test_list_after_create(self):
        await conv_mod.init_db()
        await conv_mod.create_conversation(title="First")
        await conv_mod.create_conversation(title="Second")
        result = await conv_mod.list_conversations()
        assert len(result) == 2

    async def test_get_conversation_found(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation()
        found = await conv_mod.get_conversation(c["id"])
        assert found is not None
        assert found["id"] == c["id"]

    async def test_get_conversation_not_found(self):
        await conv_mod.init_db()
        found = await conv_mod.get_conversation("nonexistent")
        assert found is None


class TestMessages:
    async def test_add_message(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation()
        msg = await conv_mod.add_message(c["id"], "user", "Hello!")
        assert msg["role"] == "user"
        assert msg["content"] == "Hello!"

    async def test_get_messages_empty(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation()
        msgs = await conv_mod.get_messages(c["id"])
        assert msgs == []

    async def test_get_messages_ordered(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation()
        await conv_mod.add_message(c["id"], "user", "First")
        await conv_mod.add_message(c["id"], "assistant", "Second")
        msgs = await conv_mod.get_messages(c["id"])
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    async def test_message_with_blocks(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation()
        blocks = [{"type": "text", "content": "hello"}]
        msg = await conv_mod.add_message(c["id"], "assistant", "hi", blocks=blocks)
        fetched = await conv_mod.get_messages(c["id"])
        assert fetched[0]["blocks"] == blocks

    async def test_get_recent_for_llm(self):
        await conv_mod.init_db()
        c = await conv_mod.create_conversation()
        await conv_mod.add_message(c["id"], "user", "Hi")
        await conv_mod.add_message(c["id"], "assistant", "Hello")
        msgs = await conv_mod.get_recent_messages_for_llm(c["id"])
        assert all(m["role"] in ("user", "assistant") for m in msgs)
        assert len(msgs) == 2
