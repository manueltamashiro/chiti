"""
LLM Router — routes queries to Ollama (local) or Claude API (cloud)
based on query classification, with mutual fallback on failure.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

from backend.llm.ollama_client import OllamaClient, OllamaUnavailableError, ollama_client
from backend.llm.query_classifier import QueryClassifier, query_classifier


@dataclass
class LLMResponse:
    content: str
    model: str           # e.g. "llama3" or "claude-sonnet-4-6"
    provider: str        # "local" | "cloud"
    routed_by: str       # "classifier" | "forced" | "offline_fallback"
    prompt_tokens: int
    completion_tokens: int
    duration_ms: float


class LLMRouter:
    """Routes LLM queries to either a local Ollama model or the Claude cloud API."""

    _CLOUD_MODEL = "claude-sonnet-4-6"

    def __init__(
        self,
        ollama: Optional[OllamaClient] = None,
        classifier: Optional[QueryClassifier] = None,
    ) -> None:
        self._ollama = ollama if ollama is not None else ollama_client
        self._classifier = classifier if classifier is not None else query_classifier

    async def route(
        self,
        messages: List[dict],
        system: str = "",
        force: Optional[str] = None,  # "local" | "cloud" | None
    ) -> LLMResponse:
        """Route a conversation to the appropriate LLM provider.

        Args:
            messages: List of chat messages [{"role": "user"|"assistant", "content": "..."}]
            system: Optional system prompt
            force: Force a specific provider ("local" or "cloud"), bypassing classifier

        Returns:
            LLMResponse with content and routing metadata
        """
        if force is not None:
            if force == "local":
                return await self._call_local(messages, system, routed_by="forced")
            elif force == "cloud":
                return await self._call_cloud(messages, system, routed_by="forced")
            else:
                raise ValueError(f"Invalid force value: {force!r}. Must be 'local', 'cloud', or None.")

        # Extract last user message for classification
        last_user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_message = msg.get("content", "")
                break

        result = self._classifier.classify(last_user_message, context=messages)

        if result.route == "local":
            try:
                return await self._call_local(messages, system, routed_by="classifier")
            except OllamaUnavailableError:
                # Fall back to cloud
                return await self._call_cloud(messages, system, routed_by="offline_fallback")
        else:
            # route == "cloud"
            try:
                return await self._call_cloud(messages, system, routed_by="classifier")
            except Exception as exc:
                # Fall back to local on network/cloud errors
                # Re-raise if it's an ImportError (anthropic not installed)
                if isinstance(exc, ImportError):
                    raise
                try:
                    return await self._call_local(messages, system, routed_by="offline_fallback")
                except OllamaUnavailableError:
                    # Both providers failed — re-raise the original cloud error
                    raise exc

    async def _call_cloud(
        self,
        messages: List[dict],
        system: str,
        routed_by: str = "classifier",
    ) -> LLMResponse:
        """Call the Anthropic Claude API."""
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for cloud routing. "
                "Install it with: pip install anthropic"
            ) from exc

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(api_key=api_key)

        t0 = time.monotonic()

        kwargs: dict = {
            "model": self._CLOUD_MODEL,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)

        duration_ms = (time.monotonic() - t0) * 1000.0

        content = ""
        if response.content:
            content = response.content[0].text

        prompt_tokens = response.usage.input_tokens if response.usage else 0
        completion_tokens = response.usage.output_tokens if response.usage else 0

        return LLMResponse(
            content=content,
            model=self._CLOUD_MODEL,
            provider="cloud",
            routed_by=routed_by,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )

    async def _call_local(
        self,
        messages: List[dict],
        system: str,
        routed_by: str = "classifier",
    ) -> LLMResponse:
        """Call the local Ollama model."""
        t0 = time.monotonic()
        ollama_resp = await self._ollama.chat(messages, system=system)
        duration_ms = (time.monotonic() - t0) * 1000.0

        return LLMResponse(
            content=ollama_resp.content,
            model=ollama_resp.model,
            provider="local",
            routed_by=routed_by,
            prompt_tokens=ollama_resp.prompt_tokens,
            completion_tokens=ollama_resp.completion_tokens,
            duration_ms=ollama_resp.duration_ms if ollama_resp.duration_ms else duration_ms,
        )


# Module-level singleton
llm_router = LLMRouter()
