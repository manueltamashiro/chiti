"""
Offline mode detector — checks internet and Ollama availability,
caches results, and supports waiting for connectivity to be restored.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.llm.ollama_client import ollama_client, OllamaClient


@dataclass
class ConnectivityStatus:
    is_online: bool
    can_reach_anthropic: bool
    can_reach_ollama: bool
    checked_at: datetime
    latency_ms: Optional[float]


class OfflineDetector:
    """Detects internet and Ollama availability with caching."""

    _ANTHROPIC_CHECK_URL = "https://api.anthropic.com"
    _ANTHROPIC_TIMEOUT = 5.0

    def __init__(
        self,
        check_interval_seconds: float = 60.0,
        ollama: Optional[OllamaClient] = None,
    ) -> None:
        self._check_interval = check_interval_seconds
        self._ollama = ollama if ollama is not None else ollama_client
        self._cached_status: Optional[ConnectivityStatus] = None
        self._last_check_time: float = 0.0

    async def check(self) -> ConnectivityStatus:
        """Perform a live connectivity check (always hits the network)."""
        t0 = time.monotonic()

        can_reach_anthropic = await self._check_anthropic()
        can_reach_ollama = await self._ollama.is_available()

        duration_ms = (time.monotonic() - t0) * 1000.0
        is_online = can_reach_anthropic or can_reach_ollama

        status = ConnectivityStatus(
            is_online=is_online,
            can_reach_anthropic=can_reach_anthropic,
            can_reach_ollama=can_reach_ollama,
            checked_at=datetime.now(tz=timezone.utc),
            latency_ms=round(duration_ms, 2),
        )

        self._cached_status = status
        self._last_check_time = time.monotonic()
        return status

    async def get_status(self) -> ConnectivityStatus:
        """Return cached status, re-checking only if the cache is stale."""
        now = time.monotonic()
        if (
            self._cached_status is None
            or (now - self._last_check_time) >= self._check_interval
        ):
            return await self.check()
        return self._cached_status

    def is_online(self) -> bool:
        """Synchronous check — returns last known state (True if cache is empty)."""
        if self._cached_status is None:
            return True  # Optimistic default when no check has been done yet
        return self._cached_status.is_online

    async def wait_for_online(self, timeout: float = 300.0) -> bool:
        """Poll until connectivity is restored or timeout expires.

        Returns:
            True if online within timeout, False if timed out.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.check()
            if status.is_online:
                return True
            # Wait before next poll, but don't exceed deadline
            remaining = deadline - time.monotonic()
            sleep_time = min(5.0, max(0.0, remaining))
            if sleep_time <= 0:
                break
            await asyncio.sleep(sleep_time)
        return False

    async def _check_anthropic(self) -> bool:
        """Return True if the Anthropic API endpoint is reachable.

        A 200 or 4xx response indicates the connection works (auth may fail but
        the network path is open). Only a connection error returns False.
        Uses stdlib urllib to avoid unconditional httpx/aiohttp imports.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_anthropic_sync)

    def _check_anthropic_sync(self) -> bool:
        """Synchronous Anthropic reachability check using urllib."""
        try:
            req = urllib.request.Request(
                self._ANTHROPIC_CHECK_URL,
                method="GET",
                headers={"User-Agent": "chiti-offline-detector/1.0"},
            )
            with urllib.request.urlopen(req, timeout=self._ANTHROPIC_TIMEOUT) as resp:
                # Any response (including 200) means we reached the server
                _ = resp.status
                return True
        except urllib.error.HTTPError:
            # 4xx / 5xx from the server — connection works, auth may fail
            return True
        except urllib.error.URLError:
            # Connection refused, DNS failure, timeout via URLError wrapper
            return False
        except OSError:
            # Catch-all for socket-level errors
            return False


# Module-level singleton
offline_detector = OfflineDetector()
