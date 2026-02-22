"""
Tests for backend/llm/offline.py

All HTTP calls and Ollama checks are mocked.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.llm.offline import ConnectivityStatus, OfflineDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_detector(
    check_interval: float = 60.0,
    ollama_available: bool = True,
    anthropic_reachable: bool = True,
) -> OfflineDetector:
    """Create an OfflineDetector with mocked dependencies."""
    mock_ollama = MagicMock()
    mock_ollama.is_available = AsyncMock(return_value=ollama_available)

    detector = OfflineDetector(check_interval_seconds=check_interval, ollama=mock_ollama)

    # Patch the sync Anthropic check
    detector._check_anthropic_sync = MagicMock(return_value=anthropic_reachable)

    return detector


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests: check() returns correct ConnectivityStatus
# ---------------------------------------------------------------------------

class TestCheck:
    def test_check_returns_connectivity_status(self):
        """check() must return a ConnectivityStatus instance."""
        detector = _make_detector()
        status = run(detector.check())
        assert isinstance(status, ConnectivityStatus)

    def test_check_both_reachable_is_online(self):
        """When both anthropic and ollama are reachable, is_online is True."""
        detector = _make_detector(ollama_available=True, anthropic_reachable=True)
        status = run(detector.check())
        assert status.is_online is True
        assert status.can_reach_anthropic is True
        assert status.can_reach_ollama is True

    def test_check_only_anthropic_reachable(self):
        """When only anthropic is reachable, is_online is True."""
        detector = _make_detector(ollama_available=False, anthropic_reachable=True)
        status = run(detector.check())
        assert status.is_online is True
        assert status.can_reach_anthropic is True
        assert status.can_reach_ollama is False

    def test_check_only_ollama_reachable(self):
        """When only ollama is reachable, is_online is True."""
        detector = _make_detector(ollama_available=True, anthropic_reachable=False)
        status = run(detector.check())
        assert status.is_online is True
        assert status.can_reach_anthropic is False
        assert status.can_reach_ollama is True

    def test_check_neither_reachable_is_offline(self):
        """When neither is reachable, is_online is False."""
        detector = _make_detector(ollama_available=False, anthropic_reachable=False)
        status = run(detector.check())
        assert status.is_online is False

    def test_check_sets_checked_at(self):
        """check() populates checked_at with a recent datetime."""
        detector = _make_detector()
        before = datetime.now(tz=timezone.utc)
        status = run(detector.check())
        after = datetime.now(tz=timezone.utc)
        assert before <= status.checked_at <= after

    def test_check_sets_latency_ms(self):
        """check() populates latency_ms with a non-negative float."""
        detector = _make_detector()
        status = run(detector.check())
        assert status.latency_ms is not None
        assert status.latency_ms >= 0


# ---------------------------------------------------------------------------
# Tests: Anthropic reachability
# ---------------------------------------------------------------------------

class TestAnthropicCheck:
    def test_anthropic_200_returns_true(self):
        """HTTP 200 from anthropic endpoint means can_reach_anthropic=True."""
        detector = _make_detector(anthropic_reachable=True, ollama_available=False)
        status = run(detector.check())
        assert status.can_reach_anthropic is True

    def test_anthropic_401_returns_true(self):
        """HTTP 4xx (e.g., 401 Unauthorized) still means connection works."""
        # Simulate urllib HTTPError (4xx)
        mock_ollama = MagicMock()
        mock_ollama.is_available = AsyncMock(return_value=False)

        detector = OfflineDetector(check_interval_seconds=60.0, ollama=mock_ollama)

        def raise_http_error():
            raise urllib.error.HTTPError(
                url="https://api.anthropic.com",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )

        detector._check_anthropic_sync = raise_http_error
        status = run(detector.check())
        # HTTPError means connection reached the server — should be True
        assert status.can_reach_anthropic is True

    def test_anthropic_connection_error_returns_false(self):
        """Connection error means can_reach_anthropic=False."""
        detector = _make_detector(anthropic_reachable=False, ollama_available=False)
        status = run(detector.check())
        assert status.can_reach_anthropic is False


# ---------------------------------------------------------------------------
# Tests: Ollama reachability delegates to ollama_client.is_available()
# ---------------------------------------------------------------------------

class TestOllamaCheck:
    def test_ollama_available_true(self):
        """can_reach_ollama=True when ollama_client.is_available() returns True."""
        detector = _make_detector(ollama_available=True, anthropic_reachable=False)
        status = run(detector.check())
        assert status.can_reach_ollama is True

    def test_ollama_available_false(self):
        """can_reach_ollama=False when ollama_client.is_available() returns False."""
        detector = _make_detector(ollama_available=False, anthropic_reachable=False)
        status = run(detector.check())
        assert status.can_reach_ollama is False

    def test_ollama_delegates_to_is_available(self):
        """ollama_client.is_available() is called during check()."""
        mock_ollama = MagicMock()
        mock_ollama.is_available = AsyncMock(return_value=True)

        detector = OfflineDetector(check_interval_seconds=60.0, ollama=mock_ollama)
        detector._check_anthropic_sync = MagicMock(return_value=False)

        run(detector.check())
        mock_ollama.is_available.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: is_online() sync method
# ---------------------------------------------------------------------------

class TestIsOnline:
    def test_is_online_true_when_both_reachable(self):
        """is_online() returns True after a successful check."""
        detector = _make_detector(ollama_available=True, anthropic_reachable=True)
        run(detector.check())
        assert detector.is_online() is True

    def test_is_online_false_when_neither_reachable(self):
        """is_online() returns False when both providers are unreachable."""
        detector = _make_detector(ollama_available=False, anthropic_reachable=False)
        run(detector.check())
        assert detector.is_online() is False

    def test_is_online_default_true_before_check(self):
        """is_online() returns True (optimistic) before any check is done."""
        mock_ollama = MagicMock()
        mock_ollama.is_available = AsyncMock(return_value=False)
        detector = OfflineDetector(check_interval_seconds=60.0, ollama=mock_ollama)
        # No check performed yet
        assert detector.is_online() is True


# ---------------------------------------------------------------------------
# Tests: Caching in get_status()
# ---------------------------------------------------------------------------

class TestCaching:
    def test_get_status_caches_result(self):
        """Second call to get_status() within interval returns cached result."""
        mock_ollama = MagicMock()
        mock_ollama.is_available = AsyncMock(return_value=True)

        detector = OfflineDetector(check_interval_seconds=60.0, ollama=mock_ollama)
        detector._check_anthropic_sync = MagicMock(return_value=True)

        first = run(detector.get_status())
        second = run(detector.get_status())

        # Should only check once
        assert mock_ollama.is_available.call_count == 1
        assert first is second  # same object returned

    def test_get_status_rechecks_when_stale(self):
        """get_status() re-checks when cache is stale (interval elapsed)."""
        mock_ollama = MagicMock()
        mock_ollama.is_available = AsyncMock(return_value=True)

        detector = OfflineDetector(check_interval_seconds=0.01, ollama=mock_ollama)
        detector._check_anthropic_sync = MagicMock(return_value=True)

        run(detector.get_status())

        # Force cache to be stale
        detector._last_check_time = 0.0

        run(detector.get_status())

        # Should have checked twice
        assert mock_ollama.is_available.call_count >= 2


# ---------------------------------------------------------------------------
# Tests: wait_for_online()
# ---------------------------------------------------------------------------

class TestWaitForOnline:
    def test_wait_for_online_returns_true_when_online(self):
        """wait_for_online() returns True immediately if already online."""
        detector = _make_detector(ollama_available=True, anthropic_reachable=True)
        result = run(detector.wait_for_online(timeout=5.0))
        assert result is True

    def test_wait_for_online_returns_false_on_timeout(self):
        """wait_for_online() returns False if timeout expires before online."""
        detector = _make_detector(ollama_available=False, anthropic_reachable=False)
        start = time.monotonic()
        result = run(detector.wait_for_online(timeout=0.1))
        elapsed = time.monotonic() - start
        assert result is False
        # Should have returned near the timeout
        assert elapsed < 2.0  # reasonable upper bound

    def test_wait_for_online_returns_true_when_restored(self):
        """wait_for_online() returns True when connectivity is restored mid-wait."""
        mock_ollama = MagicMock()
        call_count = [0]

        async def flaky_available():
            call_count[0] += 1
            return call_count[0] >= 2  # False on first call, True after

        mock_ollama.is_available = flaky_available

        detector = OfflineDetector(check_interval_seconds=0.0, ollama=mock_ollama)
        detector._check_anthropic_sync = MagicMock(return_value=False)

        result = run(detector.wait_for_online(timeout=10.0))
        assert result is True
