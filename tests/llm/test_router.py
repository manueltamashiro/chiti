"""
Tests for backend/llm/router.py

All external dependencies (OllamaClient, anthropic SDK) are mocked.
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out the anthropic module before importing router so the ImportError
# guard inside _call_cloud can be tested without actually installing it.
# ---------------------------------------------------------------------------

@dataclass
class _FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 20


@dataclass
class _FakeContent:
    text: str = "Hello from cloud"


@dataclass
class _FakeMessage:
    content: list
    usage: _FakeUsage
    model: str = "claude-sonnet-4-6"


def _make_fake_anthropic(response_text: str = "Hello from cloud"):
    """Build a minimal fake anthropic module."""
    fake_anthropic = types.ModuleType("anthropic")

    class FakeAnthropicClient:
        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            return _FakeMessage(
                content=[_FakeContent(text=response_text)],
                usage=_FakeUsage(input_tokens=10, output_tokens=20),
            )

    fake_anthropic.Anthropic = FakeAnthropicClient
    return fake_anthropic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ollama_response(content: str = "Hello from local", model: str = "llama3"):
    from backend.llm.ollama_client import OllamaResponse
    return OllamaResponse(
        content=content,
        model=model,
        prompt_tokens=5,
        completion_tokens=15,
        duration_ms=123.0,
    )


def _make_classifier_result(route: str = "local"):
    from backend.llm.query_classifier import ClassificationResult
    return ClassificationResult(
        route=route,
        reason="test reason",
        confidence=0.9,
        complexity_score=0.2,
        is_sensitive=False,
    )


def _make_mock_ollama(response: Optional[object] = None, raise_exc: Optional[Exception] = None):
    mock = MagicMock()
    if raise_exc:
        mock.chat = AsyncMock(side_effect=raise_exc)
    else:
        mock.chat = AsyncMock(return_value=response or _make_ollama_response())
    return mock


def _make_mock_classifier(route: str = "local"):
    mock = MagicMock()
    mock.classify.return_value = _make_classifier_result(route=route)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestForceRouting:
    """Tests for force= parameter — no classifier should be called."""

    def test_force_local_routes_to_local(self):
        """force='local' must call local and skip classifier."""
        from backend.llm.router import LLMRouter
        from backend.llm.ollama_client import OllamaUnavailableError

        mock_ollama = _make_mock_ollama()
        mock_classifier = _make_mock_classifier(route="cloud")  # would route cloud if used

        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [{"role": "user", "content": "Hello"}]

        result = asyncio.get_event_loop().run_until_complete(
            router.route(messages, force="local")
        )

        assert result.provider == "local"
        assert result.routed_by == "forced"
        mock_classifier.classify.assert_not_called()
        mock_ollama.chat.assert_called_once()

    def test_force_cloud_routes_to_cloud(self):
        """force='cloud' must call cloud and skip classifier."""
        fake_anthropic = _make_fake_anthropic("Cloud response")

        from backend.llm.router import LLMRouter
        mock_ollama = _make_mock_ollama()
        mock_classifier = _make_mock_classifier(route="local")  # would route local if used

        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [{"role": "user", "content": "Hello"}]

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            result = asyncio.get_event_loop().run_until_complete(
                router.route(messages, force="cloud")
            )

        assert result.provider == "cloud"
        assert result.routed_by == "forced"
        mock_classifier.classify.assert_not_called()
        mock_ollama.chat.assert_not_called()

    def test_force_local_response_fields(self):
        """force='local' response has correct fields populated."""
        from backend.llm.router import LLMRouter

        ollama_resp = _make_ollama_response(content="Local answer", model="llama3")
        mock_ollama = _make_mock_ollama(response=ollama_resp)
        router = LLMRouter(ollama=mock_ollama, classifier=MagicMock())
        messages = [{"role": "user", "content": "Hi"}]

        result = asyncio.get_event_loop().run_until_complete(
            router.route(messages, force="local")
        )

        assert result.content == "Local answer"
        assert result.model == "llama3"
        assert result.provider == "local"
        assert result.routed_by == "forced"
        assert result.prompt_tokens == 5
        assert result.completion_tokens == 15


class TestClassifierRouting:
    """Tests where the classifier determines the route."""

    def test_classifier_local_calls_local(self):
        """When classifier returns 'local', _call_local is used."""
        from backend.llm.router import LLMRouter

        mock_ollama = _make_mock_ollama()
        mock_classifier = _make_mock_classifier(route="local")
        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [{"role": "user", "content": "Simple question"}]

        result = asyncio.get_event_loop().run_until_complete(
            router.route(messages)
        )

        assert result.provider == "local"
        assert result.routed_by == "classifier"
        mock_classifier.classify.assert_called_once()
        mock_ollama.chat.assert_called_once()

    def test_classifier_cloud_calls_cloud(self):
        """When classifier returns 'cloud', _call_cloud is used."""
        fake_anthropic = _make_fake_anthropic("Cloud answer")

        from backend.llm.router import LLMRouter
        mock_ollama = _make_mock_ollama()
        mock_classifier = _make_mock_classifier(route="cloud")
        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [{"role": "user", "content": "Complex coding task"}]

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            result = asyncio.get_event_loop().run_until_complete(
                router.route(messages)
            )

        assert result.provider == "cloud"
        assert result.routed_by == "classifier"
        mock_classifier.classify.assert_called_once()
        mock_ollama.chat.assert_not_called()

    def test_classifier_receives_last_user_message(self):
        """Classifier is called with the last user message."""
        from backend.llm.router import LLMRouter

        mock_ollama = _make_mock_ollama()
        mock_classifier = _make_mock_classifier(route="local")
        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "Last user message"},
        ]

        asyncio.get_event_loop().run_until_complete(router.route(messages))

        call_args = mock_classifier.classify.call_args
        assert call_args[0][0] == "Last user message"


class TestFallbackBehavior:
    """Tests for mutual fallback on provider failure."""

    def test_local_unavailable_falls_back_to_cloud(self):
        """OllamaUnavailableError on local → falls back to cloud with offline_fallback."""
        from backend.llm.router import LLMRouter
        from backend.llm.ollama_client import OllamaUnavailableError

        fake_anthropic = _make_fake_anthropic("Fallback cloud response")

        mock_ollama = _make_mock_ollama(raise_exc=OllamaUnavailableError("Ollama down"))
        mock_classifier = _make_mock_classifier(route="local")
        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [{"role": "user", "content": "Hello"}]

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            result = asyncio.get_event_loop().run_until_complete(
                router.route(messages)
            )

        assert result.provider == "cloud"
        assert result.routed_by == "offline_fallback"

    def test_cloud_error_falls_back_to_local(self):
        """Network error on cloud → falls back to local with offline_fallback."""
        from backend.llm.router import LLMRouter

        # Make anthropic raise a connection error
        fake_anthropic = types.ModuleType("anthropic")

        class FailingClient:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                raise ConnectionError("No internet")

        fake_anthropic.Anthropic = FailingClient

        mock_ollama = _make_mock_ollama(response=_make_ollama_response("Local fallback"))
        mock_classifier = _make_mock_classifier(route="cloud")
        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)
        messages = [{"role": "user", "content": "Hello"}]

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            result = asyncio.get_event_loop().run_until_complete(
                router.route(messages)
            )

        assert result.provider == "local"
        assert result.routed_by == "offline_fallback"
        assert result.content == "Local fallback"

    def test_cloud_fallback_routed_by_is_offline_fallback(self):
        """Verify routed_by is 'offline_fallback' when falling back from local to cloud."""
        from backend.llm.router import LLMRouter
        from backend.llm.ollama_client import OllamaUnavailableError

        fake_anthropic = _make_fake_anthropic("Cloud fallback content")

        mock_ollama = _make_mock_ollama(raise_exc=OllamaUnavailableError("down"))
        mock_classifier = _make_mock_classifier(route="local")
        router = LLMRouter(ollama=mock_ollama, classifier=mock_classifier)

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            result = asyncio.get_event_loop().run_until_complete(
                router.route([{"role": "user", "content": "test"}])
            )

        assert result.routed_by == "offline_fallback"


class TestSystemPrompt:
    """Tests that system prompts are passed through correctly."""

    def test_system_prompt_passed_to_local(self):
        """System prompt is forwarded to Ollama chat."""
        from backend.llm.router import LLMRouter

        mock_ollama = _make_mock_ollama()
        router = LLMRouter(ollama=mock_ollama, classifier=_make_mock_classifier(route="local"))
        messages = [{"role": "user", "content": "Hi"}]
        system = "You are a helpful assistant."

        asyncio.get_event_loop().run_until_complete(
            router.route(messages, system=system, force="local")
        )

        mock_ollama.chat.assert_called_once_with(messages, system=system)

    def test_system_prompt_passed_to_cloud(self):
        """System prompt is forwarded to Anthropic messages.create."""
        from backend.llm.router import LLMRouter

        captured_kwargs = {}

        fake_anthropic = types.ModuleType("anthropic")

        class CapturingClient:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                captured_kwargs.update(kwargs)
                return _FakeMessage(
                    content=[_FakeContent(text="response")],
                    usage=_FakeUsage(),
                )

        fake_anthropic.Anthropic = CapturingClient

        mock_ollama = _make_mock_ollama()
        router = LLMRouter(ollama=mock_ollama, classifier=MagicMock())
        messages = [{"role": "user", "content": "Hi"}]
        system = "You are a pirate."

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            asyncio.get_event_loop().run_until_complete(
                router.route(messages, system=system, force="cloud")
            )

        assert captured_kwargs.get("system") == system


class TestLLMResponseFields:
    """Tests that LLMResponse fields are correctly populated."""

    def test_cloud_response_fields(self):
        """Cloud response has correct model, provider, and token counts."""
        fake_anthropic = _make_fake_anthropic("Cloud content")

        from backend.llm.router import LLMRouter
        mock_ollama = _make_mock_ollama()
        router = LLMRouter(ollama=mock_ollama, classifier=MagicMock())
        messages = [{"role": "user", "content": "Hi"}]

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            result = asyncio.get_event_loop().run_until_complete(
                router.route(messages, force="cloud")
            )

        assert result.model == "claude-sonnet-4-6"
        assert result.provider == "cloud"
        assert result.content == "Cloud content"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 20
        assert result.duration_ms >= 0

    def test_local_response_fields(self):
        """Local response has correct model, provider, and token counts."""
        from backend.llm.router import LLMRouter

        ollama_resp = _make_ollama_response(content="Local content", model="mistral")
        mock_ollama = _make_mock_ollama(response=ollama_resp)
        router = LLMRouter(ollama=mock_ollama, classifier=MagicMock())
        messages = [{"role": "user", "content": "Hi"}]

        result = asyncio.get_event_loop().run_until_complete(
            router.route(messages, force="local")
        )

        assert result.model == "mistral"
        assert result.provider == "local"
        assert result.content == "Local content"
        assert result.prompt_tokens == 5
        assert result.completion_tokens == 15

    def test_anthropic_not_installed_raises_import_error(self):
        """If anthropic is not installed, _call_cloud raises ImportError."""
        from backend.llm.router import LLMRouter

        mock_ollama = _make_mock_ollama()
        router = LLMRouter(ollama=mock_ollama, classifier=MagicMock())

        # Temporarily remove anthropic from sys.modules
        orig = sys.modules.pop("anthropic", None)
        try:
            with pytest.raises(ImportError, match="anthropic"):
                asyncio.get_event_loop().run_until_complete(
                    router._call_cloud([{"role": "user", "content": "test"}], "")
                )
        finally:
            if orig is not None:
                sys.modules["anthropic"] = orig
