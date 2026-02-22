"""
Log Tail Tool Suite

P6-04: Log file tail tool — watches service log files for errors/patterns.

Tier 1 (no confirmation):
  log_tail — read last N lines from a log file with optional regex/severity filter

Also provides:
  LogWatcher — background async watcher (not a tool) that continuously tails a file
"""

import asyncio
import collections
import fnmatch
import logging
import os
import re
from dataclasses import dataclass
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

# ---------------------------------------------------------------------------
# Security: blocked file patterns (always refuse)
# ---------------------------------------------------------------------------

_BLOCKED_PATTERNS = [".env", "*.key", "*.pem", "id_rsa*"]

_SEVERITY_PATTERNS = {
    "error": re.compile(r"\b(error|err|critical|fatal|exception|traceback)\b", re.IGNORECASE),
    "warning": re.compile(r"\b(warning|warn)\b", re.IGNORECASE),
    "info": re.compile(r"\b(info|notice|debug)\b", re.IGNORECASE),
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LogPattern:
    """A named regex pattern with severity."""
    name: str
    pattern: str  # regex pattern string
    severity: str  # "error" | "warning" | "info"


@dataclass
class LogEntry:
    """A parsed log line with metadata."""
    line: str
    line_number: int
    matched_pattern: Optional[str]
    severity: str
    timestamp: Optional[datetime]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


def _is_blocked_path(path: str) -> bool:
    """Return True if the path matches any blocked pattern."""
    basename = os.path.basename(path)
    for pat in _BLOCKED_PATTERNS:
        if fnmatch.fnmatch(basename, pat):
            return True
    return False


def _detect_severity(line: str) -> str:
    """Detect severity from log line content."""
    for severity, pattern in _SEVERITY_PATTERNS.items():
        if pattern.search(line):
            return severity
    return "info"


def _try_parse_timestamp(line: str) -> Optional[datetime]:
    """Try to extract a timestamp from the beginning of a log line."""
    # Common log timestamp formats
    patterns = [
        (r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S"),
        (r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "%Y-%m-%d %H:%M:%S"),
    ]
    for regex, fmt in patterns:
        match = re.match(regex, line)
        if match:
            ts_str = match.group(0)
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------------------
# LogTailTool — Tier 1
# ---------------------------------------------------------------------------


class LogTailTool(ToolBase):
    """Read the last N lines of a log file, with optional regex/severity filtering."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="log_tail",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description=(
                "Read the last N lines of a log file. "
                "Supports regex pattern matching and severity filtering (error/warning/info)."
            ),
            allowed_paths=["**/*.log", "/var/log/**"],
            requires_network=False,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path: str = params.get("path", "")
        lines: int = int(params.get("lines", 100))
        pattern_str: Optional[str] = params.get("pattern")
        severity_filter: Optional[str] = params.get("severity_filter")

        # Clamp lines
        lines = max(1, min(lines, 10_000))

        # Security: refuse blocked paths
        if _is_blocked_path(path):
            return _error_block(
                "Blocked Path",
                f"Reading '{os.path.basename(path)}' is not permitted for security reasons.",
            )

        # Validate file exists and is a file
        if not os.path.exists(path):
            return _error_block("File Not Found", f"No such file: {path}")
        if not os.path.isfile(path):
            return _error_block("Not a File", f"Path is not a regular file: {path}")

        # Compile regex pattern if provided
        compiled_pattern: Optional[re.Pattern] = None
        if pattern_str:
            try:
                compiled_pattern = re.compile(pattern_str)
            except re.error as exc:
                return _error_block("Invalid Pattern", f"Regex error: {exc}")

        # Read last N lines efficiently
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                tail = collections.deque(
                    ((i + 1, line.rstrip("\n")) for i, line in enumerate(fh)),
                    maxlen=lines,
                )
        except PermissionError:
            return _error_block("Permission Denied", f"Cannot read: {path}")
        except OSError as exc:
            return _error_block("Read Error", str(exc))

        # Build LogEntry objects
        entries: List[LogEntry] = []
        for line_number, line_text in tail:
            severity = _detect_severity(line_text)
            matched = None

            if compiled_pattern:
                m = compiled_pattern.search(line_text)
                if m:
                    matched = m.group(0)
            else:
                matched = line_text  # no filter — all lines match

            # Apply severity filter
            if severity_filter and severity != severity_filter:
                continue

            # Apply pattern filter
            if compiled_pattern and matched is None:
                continue

            entries.append(
                LogEntry(
                    line=line_text,
                    line_number=line_number,
                    matched_pattern=matched if compiled_pattern else None,
                    severity=severity,
                    timestamp=_try_parse_timestamp(line_text),
                )
            )

        if not entries:
            return OutputBlock(
                type="notification",
                content={
                    "level": "info",
                    "title": "No Matching Lines",
                    "message": (
                        f"No lines matched the given filter(s) in the last {lines} lines of {path}."
                    ),
                },
                metadata={"path": path, "lines_scanned": len(tail)},
            )

        rows = [
            [entry.line_number, entry.severity, entry.line]
            for entry in entries
        ]

        return OutputBlock(
            type="table",
            content={
                "columns": ["line_number", "severity", "line"],
                "rows": rows,
            },
            metadata={
                "path": path,
                "lines_scanned": len(tail),
                "lines_matched": len(entries),
                "pattern": pattern_str,
                "severity_filter": severity_filter,
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the log file to read.",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to read from the end of the file (default 100).",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 10000,
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional regular expression to filter lines. Only matching lines are returned.",
                },
                "severity_filter": {
                    "type": "string",
                    "enum": ["error", "warning", "info"],
                    "description": "Optional severity level to filter by.",
                },
            },
            "required": ["path"],
        }


# ---------------------------------------------------------------------------
# LogWatcher — continuous background watcher (not a ToolBase)
# ---------------------------------------------------------------------------


class LogWatcher:
    """
    Continuously tails a log file and invokes a callback for lines matching
    any of the given patterns.

    Handles log rotation by detecting when the file shrinks (inode change
    or size decrease) and re-opening it.

    Usage:
        watcher = LogWatcher()
        task = await watcher.watch(
            "/var/log/app.log",
            patterns=[LogPattern("errors", r"ERROR", "error")],
            callback=my_callback,
        )
        # Later…
        watcher.stop()
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    async def watch(
        self,
        path: str,
        patterns: List[LogPattern],
        callback: Callable[[LogEntry], None],
        poll_interval: float = 1.0,
    ) -> asyncio.Task:
        """
        Start async tailing task.

        Args:
            path: Path to the log file to watch.
            patterns: List of LogPattern objects to match against.
            callback: Called for each line matching any pattern.
            poll_interval: How many seconds to sleep between polls.

        Returns:
            The asyncio.Task for the watcher loop.
        """
        compiled = [
            (lp, re.compile(lp.pattern)) for lp in patterns
        ]

        async def _tail_loop() -> None:
            file_position = 0
            last_inode: Optional[int] = None

            while True:
                try:
                    # Detect rotation: inode change or file shrinkage
                    try:
                        stat = os.stat(path)
                        current_inode = stat.st_ino
                        current_size = stat.st_size
                    except FileNotFoundError:
                        await asyncio.sleep(poll_interval)
                        continue

                    rotated = (
                        last_inode is not None and current_inode != last_inode
                    ) or current_size < file_position

                    if rotated:
                        file_position = 0

                    last_inode = current_inode

                    if current_size <= file_position:
                        await asyncio.sleep(poll_interval)
                        continue

                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(file_position)
                        line_number = 0
                        for line_number, line in enumerate(fh, start=1):
                            stripped = line.rstrip("\n")
                            for lp, compiled_re in compiled:
                                m = compiled_re.search(stripped)
                                if m:
                                    entry = LogEntry(
                                        line=stripped,
                                        line_number=line_number,
                                        matched_pattern=lp.name,
                                        severity=lp.severity,
                                        timestamp=_try_parse_timestamp(stripped),
                                    )
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback(entry)
                                    else:
                                        callback(entry)
                                    break  # avoid duplicate callbacks for same line

                        file_position = fh.tell()

                    await asyncio.sleep(poll_interval)

                except asyncio.CancelledError:
                    break
                except Exception as exc:  # pragma: no cover
                    logger.warning("LogWatcher error: %s", exc)
                    await asyncio.sleep(poll_interval)

        self._task = asyncio.ensure_future(_tail_loop())
        return self._task

    def stop(self) -> None:
        """Cancel the watcher task."""
        if self._task and not self._task.done():
            self._task.cancel()


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

log_tail_tool = LogTailTool()
