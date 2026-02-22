"""
Health Checker — P8-08

Aggregates health status of all assistant subsystems and exposes a single
:func:`check` call that returns a :class:`HealthReport`.

Built-in components checked:
  • ollama   — local LLM service reachability
  • anthropic — Anthropic API reachability (HTTP HEAD, no auth required)
  • storage   — local filesystem write-ability

Custom checks can be registered via :meth:`HealthChecker.register_check`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional


@dataclass
class ComponentStatus:
    """Health status of a single subsystem component."""
    name: str
    healthy: bool
    latency_ms: Optional[float] = None
    detail: Optional[str] = None    # Human-readable note or error message


@dataclass
class HealthReport:
    """Aggregated health report across all registered components."""
    healthy: bool                       # True only when ALL components are healthy
    components: List[ComponentStatus]
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def get_component(self, name: str) -> Optional[ComponentStatus]:
        """Look up a component by name, or return None if not found."""
        for c in self.components:
            if c.name == name:
                return c
        return None


class HealthChecker:
    """
    Runs health checks for all registered subsystem components.

    Usage::

        report = health_checker.check()
        if not report.healthy:
            for c in report.components:
                if not c.healthy:
                    print(c.name, c.detail)
    """

    def __init__(self) -> None:
        self._checks: Dict[str, Callable[[], ComponentStatus]] = {}
        self._register_builtin_checks()

    def _register_builtin_checks(self) -> None:
        self._checks["ollama"] = self._check_ollama
        self._checks["anthropic"] = self._check_anthropic
        self._checks["storage"] = self._check_storage

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_check(self, name: str, fn: Callable[[], ComponentStatus]) -> None:
        """Register a custom health-check function for component *name*."""
        self._checks[name] = fn

    def registered_components(self) -> List[str]:
        """Return names of all registered components."""
        return list(self._checks.keys())

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def check_component(self, name: str) -> ComponentStatus:
        """Run a single named health check and return its :class:`ComponentStatus`."""
        fn = self._checks.get(name)
        if fn is None:
            return ComponentStatus(
                name=name,
                healthy=False,
                detail=f"No check registered for component '{name}'",
            )
        return fn()

    def check(self) -> HealthReport:
        """Run all registered health checks and return an aggregated :class:`HealthReport`."""
        statuses: List[ComponentStatus] = []
        for fn in self._checks.values():
            statuses.append(fn())
        overall = all(s.healthy for s in statuses)
        return HealthReport(healthy=overall, components=statuses)

    # ------------------------------------------------------------------
    # Built-in checks
    # ------------------------------------------------------------------

    def _check_ollama(self) -> ComponentStatus:
        """Check whether the Ollama service is reachable (sync urllib check)."""
        import urllib.error
        import urllib.request
        from backend.llm.ollama_client import OllamaConfig, ollama_client

        base_url = ollama_client.config.base_url.rstrip("/")
        url = f"{base_url}/api/version"
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=5) as _resp:
                pass
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(name="ollama", healthy=True, latency_ms=latency_ms)
        except urllib.error.HTTPError as exc:
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            # Any HTTP response means Ollama is up
            return ComponentStatus(
                name="ollama",
                healthy=True,
                latency_ms=latency_ms,
                detail=f"HTTP {exc.code}",
            )
        except Exception as exc:
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(
                name="ollama",
                healthy=False,
                latency_ms=latency_ms,
                detail=str(exc),
            )

    def _check_anthropic(self) -> ComponentStatus:
        """
        Check whether the Anthropic API endpoint is reachable.

        A 4xx/5xx HTTP response still means the server is up; only a
        connection-level error counts as unhealthy.
        """
        import urllib.error
        import urllib.request

        url = "https://api.anthropic.com"
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5):
                pass
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(name="anthropic", healthy=True, latency_ms=latency_ms)
        except urllib.error.HTTPError as exc:
            # Server responded → it is reachable
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(
                name="anthropic",
                healthy=True,
                latency_ms=latency_ms,
                detail=f"HTTP {exc.code}",
            )
        except Exception as exc:
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(
                name="anthropic",
                healthy=False,
                latency_ms=latency_ms,
                detail=str(exc),
            )

    def _check_storage(self) -> ComponentStatus:
        """Check whether local storage is writable."""
        import os

        test_path = "/tmp/.chiti_health_check"
        t0 = time.monotonic()
        try:
            with open(test_path, "w") as fh:
                fh.write("ok")
            os.unlink(test_path)
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(name="storage", healthy=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            return ComponentStatus(
                name="storage",
                healthy=False,
                latency_ms=latency_ms,
                detail=str(exc),
            )


# Module-level singleton
health_checker = HealthChecker()
