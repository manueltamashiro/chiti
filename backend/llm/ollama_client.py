"""
Async HTTP client for Ollama's local REST API.

Supports httpx (preferred) with urllib fallback.
Ollama runs locally at http://localhost:11434.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

if not _HTTPX_AVAILABLE:
    import urllib.request
    import urllib.error


class OllamaUnavailableError(Exception):
    """Raised when the Ollama service cannot be reached."""


@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"
    model: str = "llama3"
    timeout_seconds: float = 60.0
    max_tokens: int = 2048


@dataclass
class OllamaResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: float


class OllamaClient:
    """Async client for Ollama's REST API."""

    def __init__(self, config: Optional[OllamaConfig] = None) -> None:
        self.config = config or OllamaConfig()

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict:
        """Perform an async GET request, returning parsed JSON."""
        url = self.config.host.rstrip("/") + path
        if _HTTPX_AVAILABLE:
            return await self._httpx_get(url)
        return await self._urllib_get(url)

    async def _post(self, path: str, payload: dict) -> dict:
        """Perform an async POST request, returning parsed JSON."""
        url = self.config.host.rstrip("/") + path
        if _HTTPX_AVAILABLE:
            return await self._httpx_post(url, payload)
        return await self._urllib_post(url, payload)

    # ---- httpx variants ----

    async def _httpx_get(self, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(f"Cannot connect to Ollama at {url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError(f"Timeout connecting to Ollama at {url}: {exc}") from exc

    async def _httpx_post(self, url: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(f"Cannot connect to Ollama at {url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError(f"Timeout connecting to Ollama at {url}: {exc}") from exc

    # ---- urllib variants (sync wrapped in executor) ----

    def _urllib_get_sync(self, url: str) -> dict:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as exc:
            raise OllamaUnavailableError(f"Cannot connect to Ollama at {url}: {exc}") from exc

    def _urllib_post_sync(self, url: str, payload: dict) -> dict:
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as exc:
            raise OllamaUnavailableError(f"Cannot connect to Ollama at {url}: {exc}") from exc

    async def _urllib_get(self, url: str) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._urllib_get_sync, url)

    async def _urllib_post(self, url: str, payload: dict) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._urllib_post_sync, url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_available(self) -> bool:
        """Return True if Ollama is reachable, False otherwise."""
        try:
            await self._get("/api/version")
            return True
        except OllamaUnavailableError:
            return False
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        """Return a list of locally available model names."""
        data = await self._get("/api/tags")
        models = data.get("models", [])
        return [m["name"] for m in models if "name" in m]

    async def generate(self, prompt: str, system: str = "") -> OllamaResponse:
        """Single-turn text completion via POST /api/generate."""
        payload: dict = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": self.config.max_tokens},
        }
        if system:
            payload["system"] = system

        t0 = time.monotonic()
        data = await self._post("/api/generate", payload)
        duration_ms = (time.monotonic() - t0) * 1000.0

        content = data.get("response", "")
        model = data.get("model", self.config.model)
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        # Ollama reports total_duration in nanoseconds; prefer measured wall time
        if "total_duration" in data:
            duration_ms = data["total_duration"] / 1_000_000

        return OllamaResponse(
            content=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )

    async def chat(
        self,
        messages: List[dict],
        system: str = "",
    ) -> OllamaResponse:
        """Multi-turn chat completion via POST /api/chat.

        messages format: [{"role": "user"|"assistant", "content": "..."}]
        """
        all_messages: List[dict] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        payload = {
            "model": self.config.model,
            "messages": all_messages,
            "stream": False,
            "options": {"num_predict": self.config.max_tokens},
        }

        t0 = time.monotonic()
        data = await self._post("/api/chat", payload)
        duration_ms = (time.monotonic() - t0) * 1000.0

        message = data.get("message", {})
        content = message.get("content", "")
        model = data.get("model", self.config.model)
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        if "total_duration" in data:
            duration_ms = data["total_duration"] / 1_000_000

        return OllamaResponse(
            content=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )


# Module-level default instance
ollama_client = OllamaClient()
