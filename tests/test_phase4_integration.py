"""
Phase 4 integration tests — P4-03/P4-04/P4-05/P4-11/P4-12

Verifies:
  - All expected tools are registered in the CapabilityRegistry
  - Tools are registered under the correct tier
  - Filesystem observer starts and calls its callback on changes
  - _start_filesystem_observer wires observer to ws_hub
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.pipeline.capability_gateway import capability_registry
from backend.pipeline.models import ActionTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tier_name(tier: ActionTier) -> str:
    return tier.value if hasattr(tier, "value") else str(tier)


# ---------------------------------------------------------------------------
# Tool registration — P4-03 (Tier 1)
# ---------------------------------------------------------------------------


EXPECTED_TIER1 = [
    "read_file",
    "list_directory",
    "find_files",
    "disk_usage",
    "system_stats",
    "ping",
    "dns_lookup",
    "check_url",
    "git_status",
    "git_log",
    "git_diff",
    "docker_ps",
    "docker_stats",
    "docker_logs",
    "log_tail",
    "memory_search",
    "job_list",
]


EXPECTED_TIER2 = [
    "write_file",
    "append_file",
    "create_directory",
    "run_python",
    "run_node",
    "git_add",
    "git_commit",
    "memory_write",
    "job_create",
    "job_update",
]


EXPECTED_TIER3 = [
    "delete_file",
    "move_file",
    "git_push",
    "git_create_branch",
    "git_checkout",     # modifies working tree — Tier 3 by design
    "docker_restart",   # high-impact: can restart production services
    "docker_start",
    "docker_stop",
    "memory_delete",
    "job_delete",
]


class TestToolRegistration:
    """All Phase 4 tools must be present in the CapabilityRegistry."""

    @pytest.fixture(autouse=True)
    def _register(self):
        """Ensure tools are registered (idempotent)."""
        from backend.main import _register_tools
        _register_tools()

    def _registered_names(self):
        # list_capabilities() returns a list of name strings
        return set(capability_registry.list_capabilities())

    def _tier_of(self, name: str) -> ActionTier:
        meta = capability_registry.get_metadata(name)
        assert meta is not None, f"No metadata for '{name}'"
        return meta.tier

    def test_tier1_tools_registered(self):
        registered = self._registered_names()
        missing = [name for name in EXPECTED_TIER1 if name not in registered]
        assert not missing, f"Missing Tier 1 tools: {missing}"

    def test_tier2_tools_registered(self):
        registered = self._registered_names()
        missing = [name for name in EXPECTED_TIER2 if name not in registered]
        assert not missing, f"Missing Tier 2 tools: {missing}"

    def test_tier3_tools_registered(self):
        registered = self._registered_names()
        missing = [name for name in EXPECTED_TIER3 if name not in registered]
        assert not missing, f"Missing Tier 3 tools: {missing}"

    def test_tier1_tools_have_correct_tier(self):
        registered = self._registered_names()
        for name in EXPECTED_TIER1:
            if name not in registered:
                continue  # already reported by test_tier1_tools_registered
            tier = self._tier_of(name)
            assert tier == ActionTier.TIER_1, f"{name} should be TIER_1, got {tier}"

    def test_tier2_tools_have_correct_tier(self):
        registered = self._registered_names()
        for name in EXPECTED_TIER2:
            if name not in registered:
                continue
            tier = self._tier_of(name)
            assert tier == ActionTier.TIER_2, f"{name} should be TIER_2, got {tier}"

    def test_tier3_tools_have_correct_tier(self):
        registered = self._registered_names()
        for name in EXPECTED_TIER3:
            if name not in registered:
                continue
            tier = self._tier_of(name)
            assert tier == ActionTier.TIER_3, f"{name} should be TIER_3, got {tier}"

    def test_total_tool_count(self):
        """Registry should have a meaningful number of tools registered."""
        count = len(capability_registry.list_capabilities())
        assert count >= 30, f"Expected ≥30 tools, got {count}"


# ---------------------------------------------------------------------------
# Filesystem observer — P4-11/P4-12
# ---------------------------------------------------------------------------


class TestFilesystemObserver:
    """Unit tests for the FilesystemObserver (watchdog + polling paths)."""

    def test_observer_singleton_importable(self):
        from backend.proactive.observers import filesystem_observer
        assert filesystem_observer is not None

    def test_observer_starts_and_stops(self, tmp_path):
        from backend.proactive.observers import FilesystemObserver

        received = []

        def on_change(event):
            received.append(event)

        obs = FilesystemObserver(loop=asyncio.new_event_loop())
        obs.watch(str(tmp_path), on_change)
        obs.start()
        assert obs.is_running

        obs.stop()
        assert not obs.is_running

    def test_observer_reports_watched_paths(self, tmp_path):
        from backend.proactive.observers import FilesystemObserver

        obs = FilesystemObserver(loop=asyncio.new_event_loop())
        obs.watch(str(tmp_path), lambda e: None)

        assert str(tmp_path.resolve()) in obs.watched_paths

    def test_observer_no_paths_is_no_op(self):
        from backend.proactive.observers import FilesystemObserver

        obs = FilesystemObserver(loop=asyncio.new_event_loop())
        obs.start()  # Should not raise even with no paths
        assert not obs.is_running  # stays False when no paths

    def test_polling_observer_detects_created_file(self, tmp_path):
        """Polling backend detects a newly created file."""
        from backend.proactive.observers import _PollingObserver, FileChangeEvent

        events = []

        # The polling observer calls loop.call_soon_threadsafe(cb, event) for
        # sync callbacks.  Provide a mock loop that calls the callback directly
        # so the test doesn't need a running asyncio event loop.
        class _DirectLoop:
            def call_soon_threadsafe(self, fn, *args):
                fn(*args)

        def callback(event: FileChangeEvent):
            events.append(event)

        poll = _PollingObserver(
            paths=[str(tmp_path)],
            callback=callback,
            loop=_DirectLoop(),
            interval=0.1,
        )
        poll.start()

        # Wait for the first snapshot, then create a file
        time.sleep(0.2)
        new_file = tmp_path / "test_file.txt"
        new_file.write_text("hello")
        # Wait for at least two poll cycles to detect the new file
        time.sleep(0.4)

        poll.stop()

        created = [e for e in events if e.event_type == "created"]
        assert any(str(new_file) in e.path for e in created), (
            f"Expected created event for {new_file}; got events: {events}"
        )

    def test_file_change_event_dataclass(self):
        from backend.proactive.observers import FileChangeEvent

        evt = FileChangeEvent(path="/tmp/foo.txt", event_type="created", is_directory=False)
        assert evt.path == "/tmp/foo.txt"
        assert evt.event_type == "created"
        assert not evt.is_directory
        assert evt.timestamp is not None


class TestStartFilesystemObserver:
    """Tests for _start_filesystem_observer helper in main.py (P4-12)."""

    def test_no_watched_dirs_no_start(self):
        """If watched_dirs is empty, the observer should not be started."""
        from backend.proactive.observers import FilesystemObserver

        obs = FilesystemObserver.__new__(FilesystemObserver)
        obs._running = False
        obs._watched_paths = []
        obs._callbacks = []
        obs._backend = None

        with (
            patch("backend.main.cfg") as mock_cfg,
            patch("backend.proactive.observers.filesystem_observer", obs),
        ):
            mock_cfg.filesystem.watched_dirs = []
            from backend.main import _start_filesystem_observer
            _start_filesystem_observer()

        assert not obs.is_running

    def test_watched_dir_starts_observer(self, tmp_path):
        """If watched_dirs has entries, observer.start() is called."""
        mock_observer = MagicMock()
        mock_observer.is_running = False

        with (
            patch("backend.main.cfg") as mock_cfg,
            patch("backend.proactive.observers.filesystem_observer", mock_observer),
            patch("backend.main.ws_hub"),
        ):
            mock_cfg.filesystem.watched_dirs = [str(tmp_path)]
            from backend.main import _start_filesystem_observer
            _start_filesystem_observer()

        mock_observer.watch.assert_called_once()
        mock_observer.start.assert_called_once()
