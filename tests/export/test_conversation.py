"""Tests for conversation export — P8-06."""

import json
from datetime import datetime

import pytest

from backend.export.conversation import (
    ConversationExporter,
    ConversationMessage,
    ExportFormat,
    conversation_exporter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def exporter() -> ConversationExporter:
    return ConversationExporter()


@pytest.fixture
def single_user_message():
    return [ConversationMessage(role="user", content="Hello, world!")]


@pytest.fixture
def multi_turn():
    return [
        ConversationMessage(role="user", content="What is Python?"),
        ConversationMessage(role="assistant", content="Python is a programming language."),
        ConversationMessage(role="user", content="Can you give an example?"),
        ConversationMessage(role="assistant", content="Sure: print('hello')"),
    ]


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


class TestMarkdownExport:
    def test_returns_string(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.MARKDOWN)
        assert isinstance(result, str)

    def test_default_format_is_markdown(self, exporter, single_user_message):
        result = exporter.export(single_user_message)
        # Default is markdown — should contain the heading marker
        assert result.startswith("#")

    def test_has_custom_title(self, exporter, single_user_message):
        result = exporter.export(single_user_message, title="My Chat")
        assert "# My Chat" in result

    def test_default_title(self, exporter, single_user_message):
        result = exporter.export(single_user_message)
        assert "# Conversation" in result

    def test_has_exported_timestamp(self, exporter, single_user_message):
        result = exporter.export(single_user_message)
        assert "Exported:" in result

    def test_user_content_present(self, exporter, single_user_message):
        result = exporter.export(single_user_message)
        assert "Hello, world!" in result

    def test_multi_turn_all_content_present(self, exporter, multi_turn):
        result = exporter.export(multi_turn)
        assert "What is Python?" in result
        assert "Python is a programming language." in result
        assert "Can you give an example?" in result
        assert "Sure: print('hello')" in result

    def test_role_labels_capitalised(self, exporter, multi_turn):
        result = exporter.export(multi_turn)
        assert "**User:**" in result
        assert "**Assistant:**" in result

    def test_system_prompt_section_included(self, exporter, single_user_message):
        result = exporter.export(
            single_user_message,
            system_prompt="You are a helpful assistant.",
        )
        assert "System Prompt" in result
        assert "You are a helpful assistant." in result

    def test_no_system_prompt_section_when_none(self, exporter, single_user_message):
        result = exporter.export(single_user_message, system_prompt=None)
        assert "System Prompt" not in result

    def test_empty_conversation_still_has_title(self, exporter):
        result = exporter.export([], title="Empty Session")
        assert "# Empty Session" in result

    def test_empty_conversation_no_conversation_section(self, exporter):
        # No messages → no "## Conversation" section
        result = exporter.export([])
        assert "## Conversation" not in result


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


class TestJsonExport:
    def test_returns_valid_json(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_has_messages_key(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert "messages" in data

    def test_has_title(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.JSON, title="Test Chat")
        data = json.loads(result)
        assert data["title"] == "Test Chat"

    def test_has_exported_at(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert "exported_at" in data

    def test_message_has_role_and_content(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.JSON)
        data = json.loads(result)
        msg = data["messages"][0]
        assert msg["role"] == "user"
        assert msg["content"] == "Hello, world!"

    def test_system_prompt_included(self, exporter, single_user_message):
        result = exporter.export(
            single_user_message,
            fmt=ExportFormat.JSON,
            system_prompt="Be concise.",
        )
        data = json.loads(result)
        assert data["system_prompt"] == "Be concise."

    def test_system_prompt_null_when_none(self, exporter, single_user_message):
        result = exporter.export(single_user_message, fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert data["system_prompt"] is None

    def test_multi_turn_message_count(self, exporter, multi_turn):
        result = exporter.export(multi_turn, fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert len(data["messages"]) == 4

    def test_timestamp_none_when_not_set(self, exporter):
        msg = ConversationMessage(role="user", content="Hi", timestamp=None)
        result = exporter.export([msg], fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert data["messages"][0]["timestamp"] is None

    def test_timestamp_iso_format(self, exporter):
        ts = datetime(2025, 6, 15, 10, 30, 0)
        msg = ConversationMessage(role="user", content="Hi", timestamp=ts)
        result = exporter.export([msg], fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert "2025-06-15" in data["messages"][0]["timestamp"]

    def test_empty_messages_returns_empty_list(self, exporter):
        result = exporter.export([], fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert data["messages"] == []


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_singleton_exists(self):
        assert conversation_exporter is not None

    def test_singleton_can_export_markdown(self):
        msg = ConversationMessage(role="user", content="Hello singleton")
        result = conversation_exporter.export([msg])
        assert "Hello singleton" in result

    def test_singleton_can_export_json(self):
        msg = ConversationMessage(role="assistant", content="I am the singleton")
        result = conversation_exporter.export([msg], fmt=ExportFormat.JSON)
        data = json.loads(result)
        assert data["messages"][0]["content"] == "I am the singleton"
