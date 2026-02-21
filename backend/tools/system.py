"""
System Tool Suite

Tier 1 (no confirmation):
  system_stats, list_processes, get_process_info, check_service, get_logs,
  check_port

Tier 3 (explicit confirmation):
  start_service, stop_service, kill_process
"""

import asyncio
import logging
import os
import platform
import shutil
import socket
from typing import Any, Dict, List, Optional, Tuple

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 50_000
_DEFAULT_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run(*args: str, timeout: int = _DEFAULT_TIMEOUT) -> Tuple[int, str, str]:
    """Run a command with no shell, return (rc, stdout, stderr)."""
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
        stdout_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES],
        stderr_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES],
    )


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


def _success_block(title: str, message: str, metadata: Optional[dict] = None) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "success", "title": title, "message": message},
        metadata=metadata or {},
    )


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ---------------------------------------------------------------------------
# Tier 1 — Read-only
# ---------------------------------------------------------------------------


class SystemStatsTool(ToolBase):
    """Report CPU, memory, disk, and load averages."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="system_stats",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Show system resource usage: CPU, memory, disk, and load average.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        rows: List[List[str]] = []

        # Platform info
        rows.append(["Platform", platform.system()])
        rows.append(["Hostname", socket.gethostname()])
        rows.append(["Python", platform.python_version()])

        # Load average (Unix only)
        if hasattr(os, "getloadavg"):
            la = os.getloadavg()
            rows.append(["Load avg (1/5/15 min)", f"{la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}"])

        # Memory from /proc/meminfo (Linux) or vm_stat (macOS)
        mem_info = await self._get_memory()
        rows.extend(mem_info)

        # Disk usage for /
        usage = shutil.disk_usage("/")
        rows.append(["Disk total (/)", _format_size(usage.total)])
        rows.append(["Disk used (/)", _format_size(usage.used)])
        rows.append(["Disk free (/)", _format_size(usage.free)])
        rows.append(["Disk usage (%)", f"{usage.used / usage.total * 100:.1f}%"])

        return OutputBlock(
            type="table",
            content={"columns": ["Metric", "Value"], "rows": rows},
            metadata={"platform": platform.system()},
        )

    async def _get_memory(self) -> List[List[str]]:
        rows: List[List[str]] = []
        if platform.system() == "Linux":
            try:
                _rc, stdout, _err = await _run("cat", "/proc/meminfo")
                info: Dict[str, int] = {}
                for line in stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        try:
                            info[key] = int(parts[1]) * 1024  # kB → bytes
                        except ValueError:
                            pass
                total = info.get("MemTotal", 0)
                available = info.get("MemAvailable", 0)
                used = total - available
                if total:
                    rows.append(["Memory total", _format_size(total)])
                    rows.append(["Memory used", _format_size(used)])
                    rows.append(["Memory free", _format_size(available)])
                    rows.append(["Memory usage (%)", f"{used / total * 100:.1f}%"])
            except Exception:
                pass
        return rows

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}


class ListProcessesTool(ToolBase):
    """List running processes."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="list_processes",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="List running processes sorted by CPU usage.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        limit: int = min(params.get("limit", 30), 100)
        filter_name: Optional[str] = params.get("filter_name")

        if platform.system() == "Darwin":
            rc, stdout, stderr = await _run(
                "ps", "aux", "-r"
            )
        else:
            rc, stdout, stderr = await _run(
                "ps", "aux", "--sort=-%cpu"
            )

        if rc != 0:
            return _error_block("Process List Failed", stderr or stdout)

        lines = stdout.strip().splitlines()
        if not lines:
            return OutputBlock(type="text", content="No processes found.")

        header = lines[0].split()
        data_lines = lines[1:]

        if filter_name:
            data_lines = [l for l in data_lines if filter_name.lower() in l.lower()]

        data_lines = data_lines[:limit]

        columns = ["PID", "User", "CPU%", "Mem%", "Command"]
        rows = []
        for line in data_lines:
            parts = line.split(None, 10)
            if len(parts) >= 11:
                rows.append([parts[1], parts[0], parts[2], parts[3], parts[10][:60]])

        return OutputBlock(
            type="table",
            content={
                "columns": columns,
                "rows": rows,
                "metadata": {"shown": len(rows), "filter": filter_name},
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max processes to show (default 30)",
                    "default": 30,
                },
                "filter_name": {
                    "type": "string",
                    "description": "Filter processes by name substring",
                },
            },
            "required": [],
        }


class GetProcessInfoTool(ToolBase):
    """Get detailed info for a specific PID."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="get_process_info",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Get detailed information about a specific process by PID.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        pid: int = params["pid"]

        rc, stdout, stderr = await _run(
            "ps", "-p", str(pid), "-o",
            "pid,ppid,user,%cpu,%mem,vsz,rss,stat,start,time,comm",
        )

        if rc != 0:
            return _error_block("Process Not Found", f"No process with PID {pid}")

        lines = stdout.strip().splitlines()
        if len(lines) < 2:
            return _error_block("No Data", f"No info for PID {pid}")

        headers = lines[0].split()
        values = lines[1].split()

        rows = [[h, v] for h, v in zip(headers, values)]

        return OutputBlock(
            type="table",
            content={"columns": ["Field", "Value"], "rows": rows},
            metadata={"pid": pid},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "Process ID to inspect"},
            },
            "required": ["pid"],
        }


class CheckServiceTool(ToolBase):
    """Check the status of a systemd service."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="check_service",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Check the status of a systemd service (Linux only).",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        service: str = params["service"]

        if not shutil.which("systemctl"):
            return _error_block("Unsupported", "systemctl not available on this system")

        rc, stdout, stderr = await _run("systemctl", "status", service, "--no-pager", "-l")
        combined = (stdout + stderr).strip()

        level = "success" if rc == 0 else "error"
        status_label = "active" if rc == 0 else "inactive / failed"

        return OutputBlock(
            type="notification",
            content={
                "level": level,
                "title": f"Service: {service}",
                "message": f"Status: {status_label}\n\n{combined[:2000]}",
            },
            metadata={"service": service, "active": rc == 0},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "systemd service name (e.g. 'nginx')"},
            },
            "required": ["service"],
        }


class GetLogsTool(ToolBase):
    """Tail recent logs from the system journal or a log file."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="get_logs",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description=(
                "Get recent log entries. Pass a service name for journald logs, "
                "or a file path for file-based logs."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        service: Optional[str] = params.get("service")
        log_file: Optional[str] = params.get("file")
        lines: int = min(params.get("lines", 100), 1000)

        if log_file:
            from backend.storage.local import _is_blocked, _resolve
            resolved = _resolve(log_file)
            if _is_blocked(resolved):
                return _error_block("Access Denied", f"Blocked path: {resolved}")

            rc, stdout, stderr = await _run(
                "tail", f"-n{lines}", str(resolved)
            )
            source = str(resolved)
        elif service and shutil.which("journalctl"):
            rc, stdout, stderr = await _run(
                "journalctl", "-u", service, f"-n{lines}", "--no-pager"
            )
            source = f"journald:{service}"
        else:
            return _error_block(
                "No Source",
                "Provide either 'service' (for journald) or 'file' (for log file)",
            )

        combined = (stdout + stderr).strip()
        if not combined:
            return OutputBlock(type="text", content="No log entries found.", metadata={"source": source})

        return OutputBlock(
            type="code",
            content=combined[:_MAX_OUTPUT_BYTES],
            metadata={"source": source, "lines": lines, "language": "text"},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "systemd service name for journald logs",
                },
                "file": {
                    "type": "string",
                    "description": "Path to a log file",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to fetch (default 100, max 1000)",
                    "default": 100,
                },
            },
            "required": [],
        }


class CheckPortTool(ToolBase):
    """Check if a TCP port is open on a host."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="check_port",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Check if a TCP port is open on a host.",
            requires_network=True,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        host: str = params.get("host", "127.0.0.1")
        port: int = params["port"]
        timeout_s: float = params.get("timeout", 3.0)

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout_s,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            open_flag = True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            open_flag = False

        level = "success" if open_flag else "warning"
        status = "OPEN" if open_flag else "CLOSED / FILTERED"

        return OutputBlock(
            type="notification",
            content={
                "level": level,
                "title": f"Port {port} on {host}",
                "message": f"Status: {status}",
            },
            metadata={"host": host, "port": port, "open": open_flag},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Host to check (default: 127.0.0.1)",
                    "default": "127.0.0.1",
                },
                "port": {"type": "integer", "description": "TCP port number"},
                "timeout": {
                    "type": "number",
                    "description": "Connection timeout in seconds (default 3.0)",
                    "default": 3.0,
                },
            },
            "required": ["port"],
        }


# ---------------------------------------------------------------------------
# Tier 3 — Explicit confirmation
# ---------------------------------------------------------------------------


class StartServiceTool(ToolBase):
    """Start a systemd service."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="start_service",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Start a systemd service (requires sudo or appropriate privileges).",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        service: str = params["service"]

        if not shutil.which("systemctl"):
            return _error_block("Unsupported", "systemctl not available")

        rc, stdout, stderr = await _run("systemctl", "start", service)

        if rc != 0:
            return _error_block("Start Failed", stderr or stdout)

        return _success_block("Service Started", f"'{service}' started", {"service": service})

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "systemd service name"},
            },
            "required": ["service"],
        }


class StopServiceTool(ToolBase):
    """Stop a systemd service."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="stop_service",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Stop a systemd service. The service will be unavailable until restarted.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        service: str = params["service"]

        if not shutil.which("systemctl"):
            return _error_block("Unsupported", "systemctl not available")

        rc, stdout, stderr = await _run("systemctl", "stop", service)

        if rc != 0:
            return _error_block("Stop Failed", stderr or stdout)

        return _success_block("Service Stopped", f"'{service}' stopped", {"service": service})

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "systemd service name"},
            },
            "required": ["service"],
        }


class KillProcessTool(ToolBase):
    """Send a signal to a process by PID."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="kill_process",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Send a signal to a process by PID. "
                "Default signal is TERM (15). Use KILL (9) only as a last resort."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        pid: int = params["pid"]
        signal: int = params.get("signal", 15)  # SIGTERM

        # Only allow safe signals
        allowed_signals = {2, 9, 10, 12, 15, 1}  # INT, KILL, USR1, USR2, TERM, HUP
        if signal not in allowed_signals:
            return _error_block(
                "Signal Not Allowed",
                f"Signal {signal} is not permitted. Allowed: {sorted(allowed_signals)}",
            )

        rc, stdout, stderr = await _run("kill", f"-{signal}", str(pid))

        if rc != 0:
            return _error_block("Kill Failed", stderr or stdout or f"No process with PID {pid}")

        return _success_block(
            "Signal Sent",
            f"Sent signal {signal} to PID {pid}",
            {"pid": pid, "signal": signal},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "Target process ID"},
                "signal": {
                    "type": "integer",
                    "description": "Signal number (default 15=TERM, 9=KILL, 1=HUP)",
                    "default": 15,
                },
            },
            "required": ["pid"],
        }


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

system_stats_tool = SystemStatsTool()
list_processes_tool = ListProcessesTool()
get_process_info_tool = GetProcessInfoTool()
check_service_tool = CheckServiceTool()
get_logs_tool = GetLogsTool()
check_port_tool = CheckPortTool()
start_service_tool = StartServiceTool()
stop_service_tool = StopServiceTool()
kill_process_tool = KillProcessTool()

ALL_SYSTEM_TOOLS: List[ToolBase] = [
    system_stats_tool,
    list_processes_tool,
    get_process_info_tool,
    check_service_tool,
    get_logs_tool,
    check_port_tool,
    start_service_tool,
    stop_service_tool,
    kill_process_tool,
]
