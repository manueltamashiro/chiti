"""
Tests for backend/llm/ollama_client.py

All HTTP calls are mocked — no real network requests are made.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.llm.ollama_client import (
    OllamaClient,
    OllamaConfig,
    OllamaResponse,
    OllamaUnavailableError,
    ollama_client,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_client(host: str = "http://localhost:11434", model: str = "llama3") -> OllamaClient:
    return OllamaClient(OllamaConfig(host=host, model=model, timeout_seconds=5.0))


# ---------------------------------------------------------------------------
# OllamaConfig defaults
# ---------------------------------------------------------------------------

class TestOllamaConfigDefaults:
    def test_default_host(self):
        cfg = OllamaConfig()
        assert cfg.host == "http://localhost:11434"

    def test_default_model(self):
        cfg = OllamaConfig()
        assert cfg.model == "llama3"

    def test_default_timeout(self):
        cfg = OllamaConfig()
        assert cfg.timeout_seconds == 60.0

    def test_default_max_tokens(self):
        cfg = OllamaConfig()
        assert cfg.max_tokens == 2048


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_true_on_success(self):
        client = _make_client()
        with patch.object(client, "_get", new=AsyncMock(return_value={"version": "0.1.0"})):
            result = run(client.is_available())
        assert result is True

    def test_returns_false_on_connection_error(self):
        client = _make_client()
        with patch.object(client, "_get", new=AsyncMock(side_effect=OllamaUnavailableError("down"))):
            result = run(client.is_available())
        assert result is False

    def test_returns_false_on_generic_exception(self):
        client = _make_client()
        with patch.object(client, "_get", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = run(client.is_available())
        assert result is False

    def test_calls_version_endpoint(self):
        client = _make_client()
        mock_get = AsyncMock(return_value={"version": "0.1.0"})
        with patch.object(client, "_get", new=mock_get):
            run(client.is_available())
        mock_get.assert_called_once_with("/api/version")


# ---------------------------------------------------------------------------
# list_models()
# ---------------------------------------------------------------------------

class TestListModels:
    def test_parses_model_names(self):
        client = _make_client()
        payload = {"models": [{"name": "llama3"}, {"name": "mistral"}]}
        with patch.object(client, "_get", new=AsyncMock(return_value=payload)):
            result = run(client.list_models())
        assert result == ["llama3", "mistral"]

    def test_empty_models_list(self):
        client = _make_client()
        with patch.object(client, "_get", new=AsyncMock(return_value={"models": []})):
            result = run(client.list_models())
        assert result == []

    def test_missing_models_key(self):
        client = _make_client()
        with patch.object(client, "_get", new=AsyncMock(return_value={})):
            result = run(client.list_models())
        assert result == []

    def test_calls_tags_endpoint(self):
        client = _make_client()
        mock_get = AsyncMock(return_value={"models": []})
        with patch.object(client, "_get", new=mock_get):
            run(client.list_models())
        mock_get.assert_called_once_with("/api/tags")

    def test_skips_entries_without_name(self):
        client = _make_client()
        payload = {"models": [{"name": "llama3"}, {"size": 1234}]}
        with patch.object(client, "_get", new=AsyncMock(return_value=payload)):
            result = run(client.list_models())
        assert result == ["llama3"]


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------

class TestGenerate:
    _RESPONSE = {
        "model": "llama3",
        "response": "Paris",
        "prompt_eval_count": 10,
        "eval_count": 3,
        "total_duration": 500_000_000,  # 500 ms in nanoseconds
    }

    def test_returns_ollama_response(self):
        client = _make_client()
        with patch.object(client, "_post", new=AsyncMock(return_value=self._RESPONSE)):
            resp = run(client.generate("What is the capital of France?"))
        assert isinstance(resp, OllamaResponse)
        assert resp.content == "Paris"

    def test_sends_correct_model(self):
        client = _make_client(model="mistral")
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.generate("hello"))
        payload = mock_post.call_args[0][1]
        assert payload["model"] == "mistral"

    def test_sends_stream_false(self):
        client = _make_client()
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.generate("hello"))
        payload = mock_post.call_args[0][1]
        assert payload["stream"] is False

    def test_includes_system_prompt(self):
        client = _make_client()
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.generate("hello", system="You are helpful."))
        payload = mock_post.call_args[0][1]
        assert payload.get("system") == "You are helpful."

    def test_duration_from_total_duration(self):
        client = _make_client()
        with patch.object(client, "_post", new=AsyncMock(return_value=self._RESPONSE)):
            resp = run(client.generate("hi"))
        assert resp.duration_ms == pytest.approx(500.0, abs=1.0)

    def test_token_counts_parsed(self):
        client = _make_client()
        with patch.object(client, "_post", new=AsyncMock(return_value=self._RESPONSE)):
            resp = run(client.generate("hi"))
        assert resp.prompt_tokens == 10
        assert resp.completion_tokens == 3


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------

class TestChat:
    _RESPONSE = {
        "model": "llama3",
        "message": {"role": "assistant", "content": "Hello there!"},
        "prompt_eval_count": 5,
        "eval_count": 4,
        "total_duration": 200_000_000,
    }

    def test_returns_ollama_response(self):
        client = _make_client()
        messages = [{"role": "user", "content": "Hi"}]
        with patch.object(client, "_post", new=AsyncMock(return_value=self._RESPONSE)):
            resp = run(client.chat(messages))
        assert isinstance(resp, OllamaResponse)
        assert resp.content == "Hello there!"

    def test_sends_messages_array(self):
        client = _make_client()
        messages = [{"role": "user", "content": "Hi"}]
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.chat(messages))
        payload = mock_post.call_args[0][1]
        # user message present in payload
        assert any(m["content"] == "Hi" for m in payload["messages"])

    def test_system_message_prepended(self):
        client = _make_client()
        messages = [{"role": "user", "content": "Hi"}]
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.chat(messages, system="Be concise."))
        payload = mock_post.call_args[0][1]
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "Be concise."

    def test_calls_chat_endpoint(self):
        client = _make_client()
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.chat([{"role": "user", "content": "yo"}]))
        assert mock_post.call_args[0][0] == "/api/chat"

    def test_stream_is_false(self):
        client = _make_client()
        mock_post = AsyncMock(return_value=self._RESPONSE)
        with patch.object(client, "_post", new=mock_post):
            run(client.chat([{"role": "user", "content": "yo"}]))
        payload = mock_post.call_args[0][1]
        assert payload["stream"] is False


# ---------------------------------------------------------------------------
# Connection error / OllamaUnavailableError
# ---------------------------------------------------------------------------

class TestConnectionErrors:
    def test_generate_raises_on_connection_error(self):
        client = _make_client()
        with patch.object(client, "_post", new=AsyncMock(side_effect=OllamaUnavailableError("down"))):
            with pytest.raises(OllamaUnavailableError):
                run(client.generate("hello"))

    def test_chat_raises_on_connection_error(self):
        client = _make_client()
        with patch.object(client, "_post", new=AsyncMock(side_effect=OllamaUnavailableError("down"))):
            with pytest.raises(OllamaUnavailableError):
                run(client.chat([{"role": "user", "content": "hi"}]))

    def test_list_models_raises_on_connection_error(self):
        client = _make_client()
        with patch.object(client, "_get", new=AsyncMock(side_effect=OllamaUnavailableError("down"))):
            with pytest.raises(OllamaUnavailableError):
                run(client.list_models())


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

class TestModuleLevelInstance:
    def test_default_instance_exists(self):
        assert ollama_client is not None
        assert isinstance(ollama_client, OllamaClient)

    def test_default_instance_has_default_config(self):
        assert ollama_client.config.host == "http://localhost:11434"
        assert ollama_client.config.model == "llama3"
