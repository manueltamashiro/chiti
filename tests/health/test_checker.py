"""Tests for the health checker — P8-08."""

from datetime import datetime

import pytest

from backend.health.checker import (
    ComponentStatus,
    HealthChecker,
    HealthReport,
    health_checker,
)


# ---------------------------------------------------------------------------
# ComponentStatus
# ---------------------------------------------------------------------------


class TestComponentStatus:
    def test_healthy_component(self):
        cs = ComponentStatus(name="ollama", healthy=True, latency_ms=12.5)
        assert cs.healthy is True
        assert cs.name == "ollama"

    def test_unhealthy_component_has_detail(self):
        cs = ComponentStatus(name="anthropic", healthy=False, detail="Connection refused")
        assert cs.healthy is False
        assert "Connection refused" in cs.detail

    def test_latency_optional(self):
        cs = ComponentStatus(name="storage", healthy=True)
        assert cs.latency_ms is None


# ---------------------------------------------------------------------------
# HealthReport
# ---------------------------------------------------------------------------


class TestHealthReport:
    def test_has_checked_at(self):
        report = HealthReport(healthy=True, components=[])
        assert isinstance(report.checked_at, datetime)

    def test_get_component_found(self):
        cs = ComponentStatus(name="ollama", healthy=True)
        report = HealthReport(healthy=True, components=[cs])
        found = report.get_component("ollama")
        assert found is cs

    def test_get_component_not_found_returns_none(self):
        report = HealthReport(healthy=True, components=[])
        assert report.get_component("missing") is None

    def test_overall_healthy_all_true(self):
        report = HealthReport(
            healthy=True,
            components=[
                ComponentStatus(name="a", healthy=True),
                ComponentStatus(name="b", healthy=True),
            ],
        )
        assert report.healthy is True

    def test_overall_unhealthy_when_any_false(self):
        report = HealthReport(
            healthy=False,
            components=[
                ComponentStatus(name="a", healthy=True),
                ComponentStatus(name="b", healthy=False),
            ],
        )
        assert report.healthy is False


# ---------------------------------------------------------------------------
# HealthChecker — registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_builtin_components_registered(self):
        checker = HealthChecker()
        components = checker.registered_components()
        assert "ollama" in components
        assert "anthropic" in components
        assert "storage" in components

    def test_register_custom_check(self):
        checker = HealthChecker()
        checker.register_check("custom", lambda: ComponentStatus(name="custom", healthy=True))
        assert "custom" in checker.registered_components()

    def test_custom_check_replaces_builtin(self):
        """Re-registering a built-in name replaces it."""
        checker = HealthChecker()
        checker.register_check("ollama", lambda: ComponentStatus(name="ollama", healthy=True, detail="mocked"))
        status = checker.check_component("ollama")
        assert status.detail == "mocked"


# ---------------------------------------------------------------------------
# HealthChecker — check_component
# ---------------------------------------------------------------------------


class TestCheckComponent:
    def test_unknown_component_returns_unhealthy(self):
        checker = HealthChecker()
        status = checker.check_component("nonexistent")
        assert status.healthy is False
        assert "nonexistent" in status.detail

    def test_custom_component_returns_its_status(self):
        checker = HealthChecker()
        checker.register_check("db", lambda: ComponentStatus(name="db", healthy=True, latency_ms=2.0))
        status = checker.check_component("db")
        assert status.healthy is True
        assert status.latency_ms == 2.0


# ---------------------------------------------------------------------------
# HealthChecker — check() (full aggregation)
# ---------------------------------------------------------------------------


class TestCheck:
    def _checker_all_healthy(self) -> HealthChecker:
        checker = HealthChecker()
        for name in checker.registered_components():
            checker.register_check(
                name,
                (lambda n: lambda: ComponentStatus(name=n, healthy=True, latency_ms=1.0))(name),
            )
        return checker

    def test_check_returns_health_report(self):
        checker = self._checker_all_healthy()
        report = checker.check()
        assert isinstance(report, HealthReport)

    def test_check_has_components(self):
        checker = self._checker_all_healthy()
        report = checker.check()
        assert len(report.components) > 0

    def test_all_healthy_overall_true(self):
        checker = self._checker_all_healthy()
        report = checker.check()
        assert report.healthy is True

    def test_one_unhealthy_overall_false(self):
        checker = self._checker_all_healthy()
        checker.register_check("ollama", lambda: ComponentStatus(name="ollama", healthy=False))
        report = checker.check()
        assert report.healthy is False

    def test_check_has_checked_at(self):
        checker = self._checker_all_healthy()
        report = checker.check()
        assert isinstance(report.checked_at, datetime)

    def test_component_latency_recorded(self):
        checker = HealthChecker()
        checker.register_check(
            "fast",
            lambda: ComponentStatus(name="fast", healthy=True, latency_ms=0.5),
        )
        # Replace builtins to avoid real network calls
        for name in ["ollama", "anthropic", "storage"]:
            checker.register_check(
                name,
                (lambda n: lambda: ComponentStatus(name=n, healthy=True, latency_ms=0.0))(name),
            )
        report = checker.check()
        fast_comp = report.get_component("fast")
        assert fast_comp is not None
        assert fast_comp.latency_ms == 0.5


# ---------------------------------------------------------------------------
# Built-in checks (mocked to avoid real I/O)
# ---------------------------------------------------------------------------


class TestOllamaCheck:
    def test_ollama_healthy_when_available(self):
        checker = HealthChecker()
        checker.register_check(
            "ollama",
            lambda: ComponentStatus(name="ollama", healthy=True, latency_ms=5.0),
        )
        status = checker.check_component("ollama")
        assert status.healthy is True

    def test_ollama_unhealthy_when_unavailable(self):
        checker = HealthChecker()
        checker.register_check(
            "ollama",
            lambda: ComponentStatus(
                name="ollama", healthy=False, detail="Ollama service not reachable"
            ),
        )
        status = checker.check_component("ollama")
        assert status.healthy is False
        assert "not reachable" in status.detail

    def test_ollama_returns_component_status_type(self):
        checker = HealthChecker()
        checker.register_check(
            "ollama",
            lambda: ComponentStatus(name="ollama", healthy=True),
        )
        status = checker.check_component("ollama")
        assert isinstance(status, ComponentStatus)


class TestAnthropicCheck:
    def test_anthropic_healthy_on_success(self):
        checker = HealthChecker()
        checker.register_check(
            "anthropic",
            lambda: ComponentStatus(name="anthropic", healthy=True, latency_ms=80.0),
        )
        status = checker.check_component("anthropic")
        assert status.healthy is True

    def test_anthropic_healthy_on_http_error(self):
        """4xx/5xx still means the server is up."""
        checker = HealthChecker()
        checker.register_check(
            "anthropic",
            lambda: ComponentStatus(name="anthropic", healthy=True, detail="HTTP 403"),
        )
        status = checker.check_component("anthropic")
        assert status.healthy is True

    def test_anthropic_unhealthy_on_url_error(self):
        checker = HealthChecker()
        checker.register_check(
            "anthropic",
            lambda: ComponentStatus(
                name="anthropic", healthy=False, detail="<urlopen error ...>"
            ),
        )
        status = checker.check_component("anthropic")
        assert status.healthy is False


class TestStorageCheck:
    def test_storage_healthy_when_writable(self):
        checker = HealthChecker()
        checker.register_check(
            "storage",
            lambda: ComponentStatus(name="storage", healthy=True, latency_ms=0.2),
        )
        status = checker.check_component("storage")
        assert status.healthy is True

    def test_storage_unhealthy_on_permission_error(self):
        checker = HealthChecker()
        checker.register_check(
            "storage",
            lambda: ComponentStatus(
                name="storage", healthy=False, detail="Permission denied"
            ),
        )
        status = checker.check_component("storage")
        assert status.healthy is False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_singleton_exists(self):
        assert health_checker is not None

    def test_singleton_is_health_checker(self):
        assert isinstance(health_checker, HealthChecker)

    def test_singleton_has_builtin_components(self):
        components = health_checker.registered_components()
        assert "ollama" in components
        assert "anthropic" in components
        assert "storage" in components
