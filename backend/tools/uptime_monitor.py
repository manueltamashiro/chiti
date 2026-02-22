"""
Uptime Monitor Tool Suite

P6-05: URL/service uptime monitor — heartbeat checks for URLs and services.

Tier 1 (no confirmation):
  uptime_check — concurrent HTTP health check for a list of URLs

Also provides:
  UptimeMonitor — background monitoring loop (not a tool)
"""

import asyncio
import ipaddress
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

# Optional: try to import httpx for async HTTP; fall back to urllib
try:
    import httpx as _httpx  # type: ignore
    _HAS_HTTPX = True
except ImportError:
    _httpx = None
    _HAS_HTTPX = False

# ---------------------------------------------------------------------------
# SSRF protection: private IP ranges to block
# ---------------------------------------------------------------------------

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique-local
]


def _is_private_ip(host: str) -> bool:
    """Return True if host resolves to a private/loopback IP (basic SSRF check)."""
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass
    # Not an IP literal — skip DNS resolution in sync code; just check obvious cases
    lower = host.lower()
    return lower in ("localhost",) or lower.endswith(".local")


def _extract_host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class UptimeTarget:
    """A URL/service to monitor."""
    name: str
    url: str
    expected_status: int = 200
    timeout_seconds: float = 10.0
    method: str = "GET"


@dataclass
class UptimeResult:
    """Result from a single uptime check."""
    target: UptimeTarget
    is_up: bool
    status_code: Optional[int]
    response_time_ms: Optional[float]
    error: Optional[str]
    checked_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


async def _check_url_urllib(
    url: str,
    method: str,
    expected_status: int,
    timeout: float,
) -> UptimeResult:
    """
    Check a URL using urllib (stdlib fallback).
    Runs the blocking call in a thread executor.
    """
    target = UptimeTarget(
        name=url,
        url=url,
        expected_status=expected_status,
        timeout_seconds=timeout,
        method=method,
    )

    def _do_request() -> UptimeResult:
        import time
        start = time.monotonic()
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                elapsed_ms = (time.monotonic() - start) * 1000
                is_up = status == expected_status
                return UptimeResult(
                    target=target,
                    is_up=is_up,
                    status_code=status,
                    response_time_ms=round(elapsed_ms, 2),
                    error=None if is_up else f"Unexpected status {status}",
                    checked_at=datetime.utcnow(),
                )
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            status = exc.code
            is_up = status == expected_status
            return UptimeResult(
                target=target,
                is_up=is_up,
                status_code=status,
                response_time_ms=round(elapsed_ms, 2),
                error=None if is_up else str(exc),
                checked_at=datetime.utcnow(),
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return UptimeResult(
                target=target,
                is_up=False,
                status_code=None,
                response_time_ms=round(elapsed_ms, 2),
                error=str(exc),
                checked_at=datetime.utcnow(),
            )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_request)


async def _check_single(
    url: str,
    timeout: float,
    method: str = "GET",
    expected_status: int = 200,
) -> UptimeResult:
    """
    Check a single URL. Uses httpx if available, otherwise urllib.
    Includes basic SSRF protection.
    """
    target = UptimeTarget(
        name=url,
        url=url,
        expected_status=expected_status,
        timeout_seconds=timeout,
        method=method,
    )

    # Validate URL scheme
    if not url.startswith(("http://", "https://")):
        return UptimeResult(
            target=target,
            is_up=False,
            status_code=None,
            response_time_ms=None,
            error="Only http:// and https:// URLs are supported",
            checked_at=datetime.utcnow(),
        )

    # Basic SSRF protection: block private IPs in URL
    host = _extract_host(url)
    if host and _is_private_ip(host):
        return UptimeResult(
            target=target,
            is_up=False,
            status_code=None,
            response_time_ms=None,
            error=f"SSRF protection: '{host}' is a private/loopback address",
            checked_at=datetime.utcnow(),
        )

    if _HAS_HTTPX:
        import time
        start = time.monotonic()
        try:
            async with _httpx.AsyncClient(
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                resp = await client.request(method, url)
                elapsed_ms = (time.monotonic() - start) * 1000
                is_up = resp.status_code == expected_status
                return UptimeResult(
                    target=target,
                    is_up=is_up,
                    status_code=resp.status_code,
                    response_time_ms=round(elapsed_ms, 2),
                    error=None if is_up else f"Unexpected status {resp.status_code}",
                    checked_at=datetime.utcnow(),
                )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return UptimeResult(
                target=target,
                is_up=False,
                status_code=None,
                response_time_ms=round(elapsed_ms, 2),
                error=str(exc),
                checked_at=datetime.utcnow(),
            )
    else:
        return await _check_url_urllib(url, method, expected_status, timeout)


# ---------------------------------------------------------------------------
# UptimeCheckTool — Tier 1
# ---------------------------------------------------------------------------


class UptimeCheckTool(ToolBase):
    """Check if a list of URLs are reachable (concurrent health check)."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="uptime_check",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description=(
                "Check the uptime/reachability of one or more URLs concurrently. "
                "Returns a table with status, response time, and any errors."
            ),
            requires_network=True,
            allowed_domains=[],  # domain check at call time
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        urls: List[str] = params.get("urls", [])
        timeout: float = float(params.get("timeout", 10.0))

        if not urls:
            return _error_block("No URLs Provided", "The 'urls' parameter must be a non-empty list.")

        # Cap timeout sanely
        timeout = max(1.0, min(timeout, 120.0))

        # Run all checks concurrently
        tasks = [_check_single(url, timeout) for url in urls]
        results: List[UptimeResult] = await asyncio.gather(*tasks, return_exceptions=False)

        rows = []
        for result in results:
            status_label = "UP" if result.is_up else "DOWN"
            rt = f"{result.response_time_ms:.1f} ms" if result.response_time_ms is not None else "—"
            rows.append([
                result.target.url,
                status_label,
                result.status_code if result.status_code is not None else "—",
                rt,
                result.error or "",
            ])

        return OutputBlock(
            type="table",
            content={
                "columns": ["url", "status", "status_code", "response_time_ms", "error"],
                "rows": rows,
            },
            metadata={
                "checked": len(results),
                "up": sum(1 for r in results if r.is_up),
                "down": sum(1 for r in results if not r.is_up),
                "timeout": timeout,
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to check.",
                    "minItems": 1,
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds per request (default 10.0).",
                    "default": 10.0,
                },
            },
            "required": ["urls"],
        }


# ---------------------------------------------------------------------------
# UptimeMonitor — background monitoring loop (not a ToolBase)
# ---------------------------------------------------------------------------


class UptimeMonitor:
    """
    Background service that periodically checks registered targets.

    Usage:
        monitor = UptimeMonitor()
        monitor.add_target(UptimeTarget("MyApp", "https://example.com"))
        monitor.set_alert_callback(lambda result: print(result))
        await monitor.start(interval_seconds=60)
        # Later…
        monitor.stop()
        results = monitor.get_results()
    """

    def __init__(self) -> None:
        self._targets: List[UptimeTarget] = []
        self._latest_results: Dict[str, UptimeResult] = {}  # url -> result
        self._alert_callback: Optional[Callable[[UptimeResult], None]] = None
        self._task: Optional[asyncio.Task] = None

    def add_target(self, target: UptimeTarget) -> None:
        """Register a URL/service to monitor."""
        self._targets.append(target)

    def set_alert_callback(self, callback: Callable[[UptimeResult], None]) -> None:
        """
        Set the alert callback.

        The callback is invoked when a target transitions between up/down states.
        """
        self._alert_callback = callback

    def get_results(self) -> List[UptimeResult]:
        """Return the latest check result for each registered target."""
        return list(self._latest_results.values())

    async def _check_all(self) -> None:
        """Check all registered targets and update results."""
        if not self._targets:
            return

        tasks = [
            _check_single(
                t.url,
                t.timeout_seconds,
                t.method,
                t.expected_status,
            )
            for t in self._targets
        ]
        results: List[UptimeResult] = await asyncio.gather(*tasks, return_exceptions=False)

        for result in results:
            url = result.target.url
            prev = self._latest_results.get(url)
            self._latest_results[url] = result

            # Fire alert when status changes (up→down or down→up)
            if self._alert_callback and prev is not None:
                if prev.is_up != result.is_up:
                    if asyncio.iscoroutinefunction(self._alert_callback):
                        await self._alert_callback(result)
                    else:
                        self._alert_callback(result)

    async def start(self, interval_seconds: float = 300.0) -> None:
        """
        Start the monitoring loop.

        Performs an immediate check, then repeats every `interval_seconds`.
        """

        async def _loop() -> None:
            while True:
                try:
                    await self._check_all()
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # pragma: no cover
                    logger.warning("UptimeMonitor check failed: %s", exc)
                try:
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    break

        self._task = asyncio.ensure_future(_loop())

    def stop(self) -> None:
        """Stop the monitoring loop."""
        if self._task and not self._task.done():
            self._task.cancel()


# ---------------------------------------------------------------------------
# Module-level singleton and exported tool instance
# ---------------------------------------------------------------------------

uptime_monitor = UptimeMonitor()
uptime_check_tool = UptimeCheckTool()
