"""
Tests for Uptime Monitor Tool (P6-05)
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tools.uptime_monitor import (
    UptimeCheckTool,
    UptimeMonitor,
    UptimeResult,
    UptimeTarget,
    _check_single,
    _is_private_ip,
    uptime_check_tool,
    uptime_monitor,
)
from backend.pipeline.models import ActionTier, CapabilityType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tool():
    return UptimeCheckTool()


def _make_result(url: str, is_up: bool, status: int = 200, error: str = None) -> UptimeResult:
    target = UptimeTarget(name=url, url=url)
    return UptimeResult(
        target=target,
        is_up=is_up,
        status_code=status if is_up else None,
        response_time_ms=42.0 if is_up else None,
        error=error,
        checked_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------


class TestUptimeCheckToolMetadata:
    def test_name(self, tool):
        assert tool.metadata.name == "uptime_check"

    def test_capability_type(self, tool):
        assert tool.metadata.type == CapabilityType.TOOL

    def test_tier_is_read_only(self, tool):
        assert tool.metadata.tier == ActionTier.TIER_1

    def test_requires_network(self, tool):
        assert tool.metadata.requires_network is True

    def test_description_not_empty(self, tool):
        assert tool.metadata.description


# ---------------------------------------------------------------------------
# Parameter schema tests
# ---------------------------------------------------------------------------


class TestUptimeCheckToolSchema:
    def test_schema_is_dict(self, tool):
        schema = tool.get_parameter_schema()
        assert isinstance(schema, dict)

    def test_urls_is_required(self, tool):
        schema = tool.get_parameter_schema()
        assert "urls" in schema["required"]

    def test_urls_is_array_type(self, tool):
        schema = tool.get_parameter_schema()
        assert schema["properties"]["urls"]["type"] == "array"

    def test_timeout_is_optional_with_default(self, tool):
        schema = tool.get_parameter_schema()
        assert "timeout" in schema["properties"]
        assert schema["properties"]["timeout"].get("default") == 10.0
        assert "timeout" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# Execute tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestUptimeCheckToolExecute:
    @pytest.mark.asyncio
    async def test_returns_table_block(self, tool):
        up_result = _make_result("https://example.com", True)
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=up_result),
        ):
            result = await tool.execute({"urls": ["https://example.com"]})
        assert result.type == "table"

    @pytest.mark.asyncio
    async def test_table_has_expected_columns(self, tool):
        up_result = _make_result("https://example.com", True)
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=up_result),
        ):
            result = await tool.execute({"urls": ["https://example.com"]})
        assert "url" in result.content["columns"]
        assert "status" in result.content["columns"]
        assert "response_time_ms" in result.content["columns"]

    @pytest.mark.asyncio
    async def test_up_service_shows_up_status(self, tool):
        up_result = _make_result("https://example.com", True)
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=up_result),
        ):
            result = await tool.execute({"urls": ["https://example.com"]})
        row = result.content["rows"][0]
        assert row[1] == "UP"

    @pytest.mark.asyncio
    async def test_down_service_shows_down_status(self, tool):
        down_result = _make_result(
            "https://down.example.com", False, error="Connection refused"
        )
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=down_result),
        ):
            result = await tool.execute({"urls": ["https://down.example.com"]})
        row = result.content["rows"][0]
        assert row[1] == "DOWN"

    @pytest.mark.asyncio
    async def test_concurrent_checks_called_for_all_urls(self, tool):
        urls = [
            "https://a.example.com",
            "https://b.example.com",
            "https://c.example.com",
        ]
        results = [_make_result(u, True) for u in urls]

        call_order = []

        async def fake_check(url, timeout, method="GET", expected_status=200):
            call_order.append(url)
            return _make_result(url, True)

        with patch("backend.tools.uptime_monitor._check_single", side_effect=fake_check):
            result = await tool.execute({"urls": urls})

        assert result.type == "table"
        assert len(result.content["rows"]) == 3
        assert set(call_order) == set(urls)

    @pytest.mark.asyncio
    async def test_metadata_includes_up_down_count(self, tool):
        results_map = {
            "https://up.example.com": _make_result("https://up.example.com", True),
            "https://down.example.com": _make_result("https://down.example.com", False, error="err"),
        }

        async def fake_check(url, timeout, method="GET", expected_status=200):
            return results_map[url]

        with patch("backend.tools.uptime_monitor._check_single", side_effect=fake_check):
            result = await tool.execute({
                "urls": ["https://up.example.com", "https://down.example.com"]
            })

        assert result.metadata["up"] == 1
        assert result.metadata["down"] == 1

    @pytest.mark.asyncio
    async def test_empty_urls_returns_error(self, tool):
        result = await tool.execute({"urls": []})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    @pytest.mark.asyncio
    async def test_error_message_included_in_row(self, tool):
        down_result = _make_result("https://err.example.com", False, error="Timeout")
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=down_result),
        ):
            result = await tool.execute({"urls": ["https://err.example.com"]})
        row = result.content["rows"][0]
        # error column (last) should contain the error message
        assert "Timeout" in str(row[-1])


# ---------------------------------------------------------------------------
# SSRF protection tests
# ---------------------------------------------------------------------------


class TestSSRFProtection:
    def test_localhost_is_private(self):
        assert _is_private_ip("localhost") is True

    def test_127_0_0_1_is_private(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_10_x_x_x_is_private(self):
        assert _is_private_ip("10.0.0.1") is True

    def test_192_168_is_private(self):
        assert _is_private_ip("192.168.1.1") is True

    def test_public_ip_is_not_private(self):
        assert _is_private_ip("8.8.8.8") is False

    @pytest.mark.asyncio
    async def test_check_single_blocks_private_url(self):
        result = await _check_single("http://127.0.0.1/secret", timeout=5.0)
        assert result.is_up is False
        assert "SSRF" in result.error or "private" in result.error.lower()

    @pytest.mark.asyncio
    async def test_check_single_blocks_10_x_url(self):
        result = await _check_single("http://10.0.0.1/admin", timeout=5.0)
        assert result.is_up is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# UptimeMonitor background monitor tests
# ---------------------------------------------------------------------------


class TestUptimeMonitor:
    def test_add_target_registers_target(self):
        monitor = UptimeMonitor()
        target = UptimeTarget("Test", "https://example.com")
        monitor.add_target(target)
        assert target in monitor._targets

    def test_get_results_empty_initially(self):
        monitor = UptimeMonitor()
        assert monitor.get_results() == []

    @pytest.mark.asyncio
    async def test_get_results_after_check(self):
        monitor = UptimeMonitor()
        target = UptimeTarget("Test", "https://example.com")
        monitor.add_target(target)

        up_result = _make_result("https://example.com", True)
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=up_result),
        ):
            await monitor._check_all()

        results = monitor.get_results()
        assert len(results) == 1
        assert results[0].is_up is True

    @pytest.mark.asyncio
    async def test_alert_callback_called_on_status_change(self):
        monitor = UptimeMonitor()
        target = UptimeTarget("Test", "https://example.com")
        monitor.add_target(target)

        alerts: list = []
        monitor.set_alert_callback(lambda r: alerts.append(r))

        # First check: UP
        up_result = _make_result("https://example.com", True)
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=up_result),
        ):
            await monitor._check_all()

        assert len(alerts) == 0  # No previous state, no alert

        # Second check: DOWN — should trigger alert
        down_result = _make_result("https://example.com", False, error="timeout")
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=down_result),
        ):
            await monitor._check_all()

        assert len(alerts) == 1
        assert alerts[0].is_up is False

    @pytest.mark.asyncio
    async def test_alert_not_called_when_status_unchanged(self):
        monitor = UptimeMonitor()
        target = UptimeTarget("Test", "https://example.com")
        monitor.add_target(target)

        alerts: list = []
        monitor.set_alert_callback(lambda r: alerts.append(r))

        up_result = _make_result("https://example.com", True)
        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=up_result),
        ):
            await monitor._check_all()
            await monitor._check_all()

        assert len(alerts) == 0  # Still up, no alert

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        monitor = UptimeMonitor()

        with patch(
            "backend.tools.uptime_monitor._check_single",
            new=AsyncMock(return_value=_make_result("https://example.com", True)),
        ):
            await monitor.start(interval_seconds=1000)
            await asyncio.sleep(0.05)
            monitor.stop()
            await asyncio.sleep(0.1)
            assert monitor._task is not None
            assert monitor._task.done() or monitor._task.cancelled()

    def test_module_level_singleton_exists(self):
        assert isinstance(uptime_monitor, UptimeMonitor)
        assert isinstance(uptime_check_tool, UptimeCheckTool)
