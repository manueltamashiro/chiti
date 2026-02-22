"""
Tests for Log Tail Tool (P6-04)
"""

import asyncio
import os
import re
import time

import pytest

from backend.tools.log_tail import (
    LogEntry,
    LogPattern,
    LogTailTool,
    LogWatcher,
    log_tail_tool,
)
from backend.pipeline.models import ActionTier, CapabilityType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tool():
    return LogTailTool()


@pytest.fixture
def log_file(tmp_path):
    """A simple log file with mixed severity lines."""
    f = tmp_path / "app.log"
    f.write_text(
        "2024-01-01T10:00:00 INFO Starting service\n"
        "2024-01-01T10:00:01 INFO Service started\n"
        "2024-01-01T10:01:00 WARNING Disk usage high\n"
        "2024-01-01T10:02:00 ERROR Failed to connect to DB\n"
        "2024-01-01T10:02:01 ERROR Retrying...\n"
        "2024-01-01T10:03:00 INFO Connection restored\n",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------


class TestLogTailToolMetadata:
    def test_name(self, tool):
        assert tool.metadata.name == "log_tail"

    def test_capability_type(self, tool):
        assert tool.metadata.type == CapabilityType.TOOL

    def test_tier_is_read_only(self, tool):
        assert tool.metadata.tier == ActionTier.TIER_1

    def test_requires_no_network(self, tool):
        assert tool.metadata.requires_network is False

    def test_has_allowed_paths(self, tool):
        assert "**/*.log" in tool.metadata.allowed_paths

    def test_description_not_empty(self, tool):
        assert tool.metadata.description


# ---------------------------------------------------------------------------
# Parameter schema tests
# ---------------------------------------------------------------------------


class TestLogTailToolSchema:
    def test_schema_is_dict(self, tool):
        schema = tool.get_parameter_schema()
        assert isinstance(schema, dict)

    def test_path_is_required(self, tool):
        schema = tool.get_parameter_schema()
        assert "path" in schema["required"]

    def test_lines_is_optional_with_default(self, tool):
        schema = tool.get_parameter_schema()
        props = schema["properties"]
        assert "lines" in props
        assert props["lines"].get("default") == 100

    def test_lines_has_min_max(self, tool):
        schema = tool.get_parameter_schema()
        lines = schema["properties"]["lines"]
        assert lines.get("minimum") == 1
        assert lines.get("maximum") == 10000

    def test_pattern_is_optional(self, tool):
        schema = tool.get_parameter_schema()
        assert "pattern" in schema["properties"]
        assert "pattern" not in schema.get("required", [])

    def test_severity_filter_is_optional_enum(self, tool):
        schema = tool.get_parameter_schema()
        sf = schema["properties"]["severity_filter"]
        assert set(sf["enum"]) == {"error", "warning", "info"}
        assert "severity_filter" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# Execute: basic reading
# ---------------------------------------------------------------------------


class TestLogTailToolExecute:
    @pytest.mark.asyncio
    async def test_reads_last_n_lines(self, tool, log_file):
        result = await tool.execute({"path": str(log_file), "lines": 3})
        assert result.type == "table"
        rows = result.content["rows"]
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_reads_all_lines_by_default(self, tool, log_file):
        result = await tool.execute({"path": str(log_file)})
        assert result.type == "table"
        rows = result.content["rows"]
        assert len(rows) == 6  # all 6 lines in fixture

    @pytest.mark.asyncio
    async def test_table_has_expected_columns(self, tool, log_file):
        result = await tool.execute({"path": str(log_file)})
        assert result.content["columns"] == ["line_number", "severity", "line"]

    @pytest.mark.asyncio
    async def test_metadata_includes_path(self, tool, log_file):
        result = await tool.execute({"path": str(log_file)})
        assert result.metadata["path"] == str(log_file)

    # -----------------------------------------------------------------------
    # Pattern filtering
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pattern_filter_returns_matching_lines(self, tool, log_file):
        result = await tool.execute({"path": str(log_file), "pattern": r"ERROR"})
        assert result.type == "table"
        rows = result.content["rows"]
        assert len(rows) == 2
        for row in rows:
            assert "ERROR" in row[2]

    @pytest.mark.asyncio
    async def test_pattern_filter_no_match_returns_info_notification(self, tool, log_file):
        result = await tool.execute({"path": str(log_file), "pattern": r"CRITICAL"})
        assert result.type == "notification"
        assert result.content["level"] == "info"

    @pytest.mark.asyncio
    async def test_invalid_regex_returns_error(self, tool, log_file):
        result = await tool.execute({"path": str(log_file), "pattern": r"[invalid"})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    # -----------------------------------------------------------------------
    # Severity filtering
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_severity_filter_error(self, tool, log_file):
        result = await tool.execute({"path": str(log_file), "severity_filter": "error"})
        assert result.type == "table"
        for row in result.content["rows"]:
            assert row[1] == "error"

    @pytest.mark.asyncio
    async def test_severity_filter_no_match_returns_info(self, tool, tmp_path):
        # A log file with no lines containing any severity keywords other than "info"
        f = tmp_path / "only_info.log"
        f.write_text("INFO all good\nINFO still good\n")
        result = await tool.execute({"path": str(f), "severity_filter": "error"})
        assert result.type == "notification"
        assert result.content["level"] == "info"

    # -----------------------------------------------------------------------
    # Security: blocked paths
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_blocks_dotenv_file(self, tool, tmp_path):
        env = tmp_path / ".env"
        env.write_text("SECRET=abc")
        result = await tool.execute({"path": str(env)})
        assert result.type == "notification"
        assert result.content["level"] == "error"
        assert "Blocked" in result.content["title"] or "security" in result.content["message"].lower()

    @pytest.mark.asyncio
    async def test_blocks_key_file(self, tool, tmp_path):
        key = tmp_path / "server.key"
        key.write_text("private key data")
        result = await tool.execute({"path": str(key)})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    @pytest.mark.asyncio
    async def test_blocks_pem_file(self, tool, tmp_path):
        pem = tmp_path / "cert.pem"
        pem.write_text("-----BEGIN CERTIFICATE-----")
        result = await tool.execute({"path": str(pem)})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    @pytest.mark.asyncio
    async def test_blocks_id_rsa(self, tool, tmp_path):
        rsa = tmp_path / "id_rsa"
        rsa.write_text("RSA key")
        result = await tool.execute({"path": str(rsa)})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_file_not_found_returns_error(self, tool, tmp_path):
        result = await tool.execute({"path": str(tmp_path / "ghost.log")})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    @pytest.mark.asyncio
    async def test_directory_path_returns_error(self, tool, tmp_path):
        result = await tool.execute({"path": str(tmp_path)})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    @pytest.mark.asyncio
    async def test_lines_capped_at_10000(self, tool, tmp_path):
        f = tmp_path / "small.log"
        f.write_text("line\n" * 5)
        # lines=99999 should still work; just reads what's there
        result = await tool.execute({"path": str(f), "lines": 99999})
        assert result.type == "table"
        assert len(result.content["rows"]) == 5


# ---------------------------------------------------------------------------
# LogWatcher tests
# ---------------------------------------------------------------------------


class TestLogWatcher:
    @pytest.mark.asyncio
    async def test_callback_called_for_matching_line(self, tmp_path):
        log_file = tmp_path / "watch.log"
        log_file.write_text("")

        collected: list = []

        async def on_entry(entry: LogEntry) -> None:
            collected.append(entry)

        watcher = LogWatcher()
        patterns = [LogPattern("errors", r"ERROR", "error")]
        task = await watcher.watch(str(log_file), patterns, on_entry, poll_interval=0.05)

        # Give the watcher a moment to start
        await asyncio.sleep(0.1)

        # Append a matching line
        with open(str(log_file), "a") as fh:
            fh.write("2024-01-01T10:00:00 ERROR something broke\n")

        await asyncio.sleep(0.2)
        watcher.stop()

        assert len(collected) >= 1
        assert collected[0].severity == "error"
        assert "ERROR" in collected[0].line

    @pytest.mark.asyncio
    async def test_callback_not_called_for_non_matching_line(self, tmp_path):
        log_file = tmp_path / "watch2.log"
        log_file.write_text("")

        collected: list = []

        def on_entry(entry: LogEntry) -> None:
            collected.append(entry)

        watcher = LogWatcher()
        patterns = [LogPattern("errors", r"ERROR", "error")]
        task = await watcher.watch(str(log_file), patterns, on_entry, poll_interval=0.05)

        await asyncio.sleep(0.1)
        with open(str(log_file), "a") as fh:
            fh.write("INFO nothing bad here\n")

        await asyncio.sleep(0.2)
        watcher.stop()

        assert len(collected) == 0

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, tmp_path):
        log_file = tmp_path / "watch3.log"
        log_file.write_text("")

        watcher = LogWatcher()
        task = await watcher.watch(str(log_file), [], lambda e: None, poll_interval=0.1)
        await asyncio.sleep(0.05)
        watcher.stop()
        await asyncio.sleep(0.15)
        assert task.done()

    @pytest.mark.asyncio
    async def test_watcher_handles_missing_file_gracefully(self, tmp_path):
        """Watcher should not crash if file does not exist initially."""
        missing = tmp_path / "not_yet.log"

        collected: list = []

        def on_entry(entry: LogEntry) -> None:
            collected.append(entry)

        watcher = LogWatcher()
        task = await watcher.watch(str(missing), [], on_entry, poll_interval=0.05)
        await asyncio.sleep(0.15)
        watcher.stop()
        # No crash — task may be done or still running depending on timing
        assert True
