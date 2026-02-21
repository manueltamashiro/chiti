"""
Docker Tool Suite

Tier 1 (no confirmation):
  docker_ps, docker_stats, docker_logs

Tier 3 (explicit confirmation):
  docker_restart, docker_start, docker_stop

All tools use asyncio.create_subprocess_exec (no shell=True).
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 100_000
_DEFAULT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _run_docker(
    *args: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Tuple[int, str, str]:
    """Run a docker sub-command safely (no shell)."""
    cmd = ["docker", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"docker {args[0]} timed out after {timeout}s")

    stdout = stdout_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES]
    stderr = stderr_b.decode(errors="replace")[:_MAX_OUTPUT_BYTES]
    return proc.returncode, stdout, stderr


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


# ---------------------------------------------------------------------------
# Tier 1 — Read-only
# ---------------------------------------------------------------------------


class DockerPsTool(ToolBase):
    """List Docker containers."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="docker_ps",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="List Docker containers (running by default, all with show_all=true).",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        show_all: bool = params.get("show_all", False)
        fmt = (
            '{"id":"{{.ID}}","name":"{{.Names}}","image":"{{.Image}}",'
            '"status":"{{.Status}}","ports":"{{.Ports}}","created":"{{.CreatedAt}}"}'
        )
        args = ["ps", f"--format={fmt}"]
        if show_all:
            args.append("-a")

        rc, stdout, stderr = await _run_docker(*args)
        if rc != 0:
            return _error_block("Docker PS Failed", stderr or stdout)

        if not stdout.strip():
            return OutputBlock(
                type="text",
                content="No containers found." if show_all else "No running containers.",
            )

        containers = []
        for line in stdout.strip().splitlines():
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        columns = ["ID", "Name", "Image", "Status", "Ports"]
        rows = [
            [c.get("id", ""), c.get("name", ""), c.get("image", ""),
             c.get("status", ""), c.get("ports", "")]
            for c in containers
        ]

        return OutputBlock(
            type="table",
            content={
                "columns": columns,
                "rows": rows,
                "metadata": {"container_count": len(rows), "show_all": show_all},
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "show_all": {
                    "type": "boolean",
                    "description": "Show all containers including stopped (default: false)",
                    "default": False,
                },
            },
            "required": [],
        }


class DockerStatsTool(ToolBase):
    """Snapshot resource usage for running containers."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="docker_stats",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Show a one-shot snapshot of CPU, memory, and network usage for containers.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        containers: List[str] = params.get("containers", [])
        fmt = (
            '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}",'
            '"mem_pct":"{{.MemPerc}}","net":"{{.NetIO}}","block":"{{.BlockIO}}"}'
        )
        args = ["stats", "--no-stream", f"--format={fmt}", *containers]

        rc, stdout, stderr = await _run_docker(*args, timeout=15)
        if rc != 0:
            return _error_block("Docker Stats Failed", stderr or stdout)

        if not stdout.strip():
            return OutputBlock(type="text", content="No running containers.")

        stats = []
        for line in stdout.strip().splitlines():
            try:
                stats.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        columns = ["Name", "CPU %", "Memory", "Mem %", "Net I/O", "Block I/O"]
        rows = [
            [s.get("name", ""), s.get("cpu", ""), s.get("mem", ""),
             s.get("mem_pct", ""), s.get("net", ""), s.get("block", "")]
            for s in stats
        ]

        return OutputBlock(
            type="table",
            content={"columns": columns, "rows": rows, "metadata": {"snapshot": True}},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "containers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Container names/IDs to inspect (default: all running)",
                    "default": [],
                },
            },
            "required": [],
        }


class DockerLogsTool(ToolBase):
    """Fetch recent logs from a container."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="docker_logs",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Fetch the last N lines of logs from a Docker container.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        container: str = params["container"]
        tail: int = min(params.get("tail", 100), 1000)
        timestamps: bool = params.get("timestamps", False)

        args = ["logs", f"--tail={tail}"]
        if timestamps:
            args.append("--timestamps")
        args.append(container)

        rc, stdout, stderr = await _run_docker(*args)
        combined = (stderr + stdout).strip()  # docker logs goes to stderr

        if rc != 0 and not combined:
            return _error_block("Docker Logs Failed", stderr or stdout)

        if not combined:
            return OutputBlock(
                type="text",
                content=f"No logs for container '{container}'.",
                metadata={"container": container},
            )

        return OutputBlock(
            type="code",
            content=combined[:_MAX_OUTPUT_BYTES],
            metadata={"container": container, "tail": tail, "language": "text"},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
                "tail": {
                    "type": "integer",
                    "description": "Number of log lines to fetch (default 100, max 1000)",
                    "default": 100,
                },
                "timestamps": {
                    "type": "boolean",
                    "description": "Include timestamps in output (default: false)",
                    "default": False,
                },
            },
            "required": ["container"],
        }


# ---------------------------------------------------------------------------
# Tier 3 — Explicit confirmation (affects running services)
# ---------------------------------------------------------------------------


class DockerRestartTool(ToolBase):
    """Restart a container."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="docker_restart",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Restart a Docker container. This interrupts the service briefly.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        container: str = params["container"]
        timeout_s: int = params.get("stop_timeout", 10)

        rc, stdout, stderr = await _run_docker(
            "restart", f"--time={timeout_s}", container,
            timeout=timeout_s + 30,
        )

        if rc != 0:
            return _error_block("Restart Failed", stderr or stdout)

        return _success_block(
            "Container Restarted",
            f"'{container}' restarted successfully",
            {"container": container},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
                "stop_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait before killing (default 10)",
                    "default": 10,
                },
            },
            "required": ["container"],
        }


class DockerStartTool(ToolBase):
    """Start a stopped container."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="docker_start",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description="Start a stopped Docker container.",
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        container: str = params["container"]
        rc, stdout, stderr = await _run_docker("start", container)

        if rc != 0:
            return _error_block("Start Failed", stderr or stdout)

        return _success_block(
            "Container Started",
            f"'{container}' started",
            {"container": container},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
            },
            "required": ["container"],
        }


class DockerStopTool(ToolBase):
    """Stop a running container."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="docker_stop",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Stop a running Docker container. The service will be unavailable "
                "until started again."
            ),
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        container: str = params["container"]
        timeout_s: int = params.get("stop_timeout", 10)

        rc, stdout, stderr = await _run_docker(
            "stop", f"--time={timeout_s}", container,
            timeout=timeout_s + 30,
        )

        if rc != 0:
            return _error_block("Stop Failed", stderr or stdout)

        return _success_block(
            "Container Stopped",
            f"'{container}' stopped",
            {"container": container},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
                "stop_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait before SIGKILL (default 10)",
                    "default": 10,
                },
            },
            "required": ["container"],
        }


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

docker_ps_tool = DockerPsTool()
docker_stats_tool = DockerStatsTool()
docker_logs_tool = DockerLogsTool()
docker_restart_tool = DockerRestartTool()
docker_start_tool = DockerStartTool()
docker_stop_tool = DockerStopTool()

ALL_DOCKER_TOOLS: List[ToolBase] = [
    docker_ps_tool,
    docker_stats_tool,
    docker_logs_tool,
    docker_restart_tool,
    docker_start_tool,
    docker_stop_tool,
]
