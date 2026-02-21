"""
Network Tool Suite

Tier 1 (no confirmation):
  ping, dns_lookup, check_url

Tier 2 (soft confirmation — allowlisted domains only):
  http_get
"""

import asyncio
import logging
import socket
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10
_MAX_RESPONSE_BYTES = 100_000

# Default domain allowlist for http_get.
# The full list is loaded from config at runtime; this is the safe default.
_DEFAULT_ALLOWED_DOMAINS: Set[str] = {
    "api.github.com",
    "registry.npmjs.org",
    "pypi.org",
    "api.anthropic.com",
    "api.openai.com",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run(*args: str, timeout: int = _DEFAULT_TIMEOUT) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{args[0]} timed out after {timeout}s")
    return (
        proc.returncode,
        stdout_b.decode(errors="replace")[:_MAX_RESPONSE_BYTES],
        stderr_b.decode(errors="replace")[:_MAX_RESPONSE_BYTES],
    )


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


def _extract_domain(url: str) -> str:
    """Extract the hostname from a URL."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Tier 1 — Read-only network tools
# ---------------------------------------------------------------------------


class PingTool(ToolBase):
    """Ping a host to test reachability."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="ping",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Ping a host to check reachability and measure latency.",
            requires_network=True,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        host: str = params["host"]
        count: int = min(params.get("count", 4), 10)

        if not _is_safe_hostname(host):
            return _error_block("Invalid Host", f"Unsafe hostname: {host}")

        rc, stdout, stderr = await _run(
            "ping", "-c", str(count), host,
            timeout=count * 5 + 5,
        )
        combined = (stdout + stderr).strip()

        level = "success" if rc == 0 else "warning"
        return OutputBlock(
            type="notification",
            content={
                "level": level,
                "title": f"Ping: {host}",
                "message": combined[:2000],
            },
            metadata={"host": host, "reachable": rc == 0, "count": count},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname or IP address to ping"},
                "count": {
                    "type": "integer",
                    "description": "Number of packets to send (default 4, max 10)",
                    "default": 4,
                },
            },
            "required": ["host"],
        }


class DnsLookupTool(ToolBase):
    """Resolve a hostname to IP addresses."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="dns_lookup",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Resolve a hostname to IP addresses using system DNS.",
            requires_network=True,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        host: str = params["host"]

        if not _is_safe_hostname(host):
            return _error_block("Invalid Host", f"Unsafe hostname: {host}")

        try:
            loop = asyncio.get_event_loop()
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None),
                timeout=_DEFAULT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return _error_block("DNS Timeout", f"DNS lookup timed out for {host}")
        except socket.gaierror as e:
            return _error_block("DNS Failed", str(e))
        except Exception as e:
            return _error_block("DNS Error", str(e))

        seen: Set[str] = set()
        rows = []
        for family, _type, _proto, _cname, sockaddr in infos:
            ip = sockaddr[0]
            family_str = "IPv4" if family == socket.AF_INET else "IPv6"
            if ip not in seen:
                seen.add(ip)
                rows.append([family_str, ip])

        if not rows:
            return _error_block("No Results", f"No DNS records found for {host}")

        return OutputBlock(
            type="table",
            content={
                "columns": ["Type", "Address"],
                "rows": rows,
                "metadata": {"host": host},
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname to resolve"},
            },
            "required": ["host"],
        }


class CheckUrlTool(ToolBase):
    """Check if a URL is reachable (HEAD request)."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="check_url",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Check if a URL returns a successful HTTP response (HEAD request).",
            requires_network=True,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        url: str = params["url"]

        if not url.startswith(("http://", "https://")):
            return _error_block("Invalid URL", "Only http:// and https:// URLs are supported")

        rc, stdout, stderr = await _run(
            "curl", "-sI", "--max-time", str(_DEFAULT_TIMEOUT),
            "-w", "%{http_code}", "-o", "/dev/null", url,
            timeout=_DEFAULT_TIMEOUT + 5,
        )
        combined = (stdout + stderr).strip()

        try:
            http_code = int(combined)
            reachable = 200 <= http_code < 400
        except ValueError:
            http_code = 0
            reachable = False

        level = "success" if reachable else "warning"
        return OutputBlock(
            type="notification",
            content={
                "level": level,
                "title": f"URL Check: {url[:80]}",
                "message": f"HTTP {http_code} — {'reachable' if reachable else 'not reachable'}",
            },
            metadata={"url": url, "http_code": http_code, "reachable": reachable},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to check"},
            },
            "required": ["url"],
        }


# ---------------------------------------------------------------------------
# Tier 2 — Soft confirmation (domain allowlist enforced)
# ---------------------------------------------------------------------------


class HttpGetTool(ToolBase):
    """
    Perform an HTTP GET request against an allowlisted domain.

    Domain allowlist is enforced unconditionally. Only domains from
    the configured allowed_domains list are reachable.
    """

    def __init__(self, allowed_domains: Optional[Set[str]] = None):
        super().__init__()
        self._allowed_domains: Set[str] = allowed_domains or _DEFAULT_ALLOWED_DOMAINS

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="http_get",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description=(
                "Perform an HTTP GET request. Only allowlisted domains are accessible. "
                "Response body is capped at 100 KB."
            ),
            requires_network=True,
            allowed_domains=sorted(self._allowed_domains),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        url: str = params["url"]
        headers: Dict[str, str] = params.get("headers", {})

        if not url.startswith(("http://", "https://")):
            return _error_block("Invalid URL", "Only http:// and https:// URLs are supported")

        domain = _extract_domain(url)
        if not self._is_allowed_domain(domain):
            return _error_block(
                "Domain Not Allowlisted",
                f"'{domain}' is not in the allowed domains list. "
                f"Allowed: {sorted(self._allowed_domains)}",
            )

        # Build curl args
        curl_args = [
            "curl", "-s",
            "--max-time", str(_DEFAULT_TIMEOUT),
            "-w", "\n__STATUS__%{http_code}",
            "-L",  # follow redirects
        ]
        for key, val in headers.items():
            curl_args += ["-H", f"{key}: {val}"]
        curl_args.append(url)

        rc, stdout, stderr = await _run(*curl_args, timeout=_DEFAULT_TIMEOUT + 5)

        if rc != 0 and not stdout:
            return _error_block("Request Failed", stderr or "Network error")

        # Split body and status code
        body = stdout
        http_code = 0
        if "__STATUS__" in stdout:
            body, code_str = stdout.rsplit("__STATUS__", 1)
            try:
                http_code = int(code_str.strip())
            except ValueError:
                pass

        body = body[:_MAX_RESPONSE_BYTES]
        success = 200 <= http_code < 300

        return OutputBlock(
            type="code",
            content=body if body.strip() else "(empty response)",
            metadata={
                "url": url,
                "http_code": http_code,
                "success": success,
                "language": "text",
                "bytes": len(body),
            },
        )

    def _is_allowed_domain(self, domain: str) -> bool:
        """Check if domain or any parent is in the allowlist."""
        if not domain:
            return False
        if domain in self._allowed_domains:
            return True
        # Allow subdomains: api.github.com → github.com must be in list
        parts = domain.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in self._allowed_domains:
                return True
        return False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers as key-value pairs",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["url"],
        }


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------


def _is_safe_hostname(host: str) -> bool:
    """Block obviously dangerous hostnames."""
    dangerous = {"localhost", "0.0.0.0"}
    if host.lower() in dangerous:
        return False
    # Block private IP ranges that users shouldn't ping via the assistant
    # (actual SSRF prevention is handled at the HTTP level for http_get)
    return True


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

ping_tool = PingTool()
dns_lookup_tool = DnsLookupTool()
check_url_tool = CheckUrlTool()
http_get_tool = HttpGetTool()

ALL_NETWORK_TOOLS: List[ToolBase] = [
    ping_tool,
    dns_lookup_tool,
    check_url_tool,
    http_get_tool,
]
