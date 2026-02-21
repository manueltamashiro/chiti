"""
Filesystem Observer — P4-11

Watches configured directories for file changes and emits events to the
notification bus via the WebSocket event loop.

Uses the watchdog library when available. Falls back to polling if not installed.

Events emitted:
    file.changed  → { path, event_type, is_directory, timestamp }
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer as WatchdogObserver

    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logger.warning(
        "watchdog not installed — filesystem observer will use polling. "
        "Install with: pip install watchdog"
    )


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class FileChangeEvent:
    """A single filesystem change event."""

    path: str
    event_type: str       # "created", "modified", "deleted", "moved"
    is_directory: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)
    dest_path: Optional[str] = None  # Only set for "moved" events


# Callback type: async or sync function that receives a FileChangeEvent
EventCallback = Callable[[FileChangeEvent], None]


# ---------------------------------------------------------------------------
# Watchdog-based implementation
# ---------------------------------------------------------------------------


class _WatchdogHandler(FileSystemEventHandler):
    """Translate watchdog events into FileChangeEvent and call our callback."""

    def __init__(self, callback: EventCallback, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._callback = callback
        self._loop = loop

    def _dispatch(self, event: "FileSystemEvent", event_type: str) -> None:
        dest = getattr(event, "dest_path", None)
        change = FileChangeEvent(
            path=event.src_path,
            event_type=event_type,
            is_directory=event.is_directory,
            dest_path=dest if dest else None,
        )
        if asyncio.iscoroutinefunction(self._callback):
            asyncio.run_coroutine_threadsafe(self._callback(change), self._loop)
        else:
            self._loop.call_soon_threadsafe(self._callback, change)

    def on_created(self, event: "FileSystemEvent") -> None:
        self._dispatch(event, "created")

    def on_modified(self, event: "FileSystemEvent") -> None:
        self._dispatch(event, "modified")

    def on_deleted(self, event: "FileSystemEvent") -> None:
        self._dispatch(event, "deleted")

    def on_moved(self, event: "FileSystemEvent") -> None:
        self._dispatch(event, "moved")


# ---------------------------------------------------------------------------
# Polling-based fallback
# ---------------------------------------------------------------------------


class _PollingObserver(threading.Thread):
    """
    Minimal polling-based observer for environments without watchdog.

    Polls directory every ``interval`` seconds and emits events for files
    that appear, disappear, or change mtime.
    """

    def __init__(
        self,
        paths: List[str],
        callback: EventCallback,
        loop: asyncio.AbstractEventLoop,
        interval: float = 5.0,
    ) -> None:
        super().__init__(daemon=True, name="filesystem-observer-poll")
        self._paths = [Path(p).expanduser().resolve() for p in paths]
        self._callback = callback
        self._loop = loop
        self._interval = interval
        self._stop_event = threading.Event()
        self._snapshots: Dict[str, float] = {}

    def _snapshot(self) -> Dict[str, float]:
        """Return {path: mtime} for all files in watched dirs."""
        snap: Dict[str, float] = {}
        for root in self._paths:
            if not root.is_dir():
                continue
            for entry in root.rglob("*"):
                try:
                    snap[str(entry)] = entry.stat().st_mtime
                except OSError:
                    pass
        return snap

    def _emit(self, change: FileChangeEvent) -> None:
        if asyncio.iscoroutinefunction(self._callback):
            asyncio.run_coroutine_threadsafe(self._callback(change), self._loop)
        else:
            self._loop.call_soon_threadsafe(self._callback, change)

    def run(self) -> None:
        self._snapshots = self._snapshot()
        while not self._stop_event.wait(self._interval):
            new_snap = self._snapshot()

            old_keys = set(self._snapshots)
            new_keys = set(new_snap)

            for path in new_keys - old_keys:
                self._emit(FileChangeEvent(path=path, event_type="created", is_directory=False))

            for path in old_keys - new_keys:
                self._emit(FileChangeEvent(path=path, event_type="deleted", is_directory=False))

            for path in old_keys & new_keys:
                if new_snap[path] != self._snapshots[path]:
                    self._emit(
                        FileChangeEvent(path=path, event_type="modified", is_directory=False)
                    )

            self._snapshots = new_snap

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class FilesystemObserver:
    """
    Manages filesystem watching for one or more directories.

    Usage::

        observer = FilesystemObserver()
        observer.watch("~/Downloads", callback=on_change)
        observer.watch("~/projects", callback=on_change)
        observer.start()

        # later ...
        observer.stop()

    Callbacks receive a :class:`FileChangeEvent`.  They can be sync or async
    functions; async callbacks are scheduled on the provided event loop.
    """

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self._watched_paths: List[str] = []
        self._callbacks: List[EventCallback] = []
        self._running = False
        self._backend: Optional[object] = None

    def watch(self, path: str, callback: EventCallback) -> None:
        """
        Register a directory to watch and a callback to call on changes.

        Multiple calls add more directories / callbacks.  All callbacks are
        called for changes in any watched directory.
        """
        resolved = str(Path(path).expanduser().resolve())
        if resolved not in self._watched_paths:
            self._watched_paths.append(resolved)
            logger.info(f"Registered watch: {resolved}")
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def start(self) -> None:
        """Start the observer (non-blocking — runs in a background thread)."""
        if self._running:
            return
        if not self._watched_paths:
            logger.warning("FilesystemObserver.start() called with no paths registered")
            return

        def _combined_callback(event: FileChangeEvent) -> None:
            for cb in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.run_coroutine_threadsafe(cb(event), self._loop)
                    else:
                        self._loop.call_soon_threadsafe(cb, event)
                except Exception as e:
                    logger.error(f"Observer callback error: {e}")

        if HAS_WATCHDOG:
            observer = WatchdogObserver()
            handler = _WatchdogHandler(_combined_callback, self._loop)
            for path in self._watched_paths:
                observer.schedule(handler, path, recursive=True)
            observer.start()
            self._backend = observer
            logger.info(
                f"Filesystem observer started (watchdog) watching {len(self._watched_paths)} path(s)"
            )
        else:
            poll = _PollingObserver(self._watched_paths, _combined_callback, self._loop)
            poll.start()
            self._backend = poll
            logger.info(
                f"Filesystem observer started (polling) watching {len(self._watched_paths)} path(s)"
            )

        self._running = True

    def stop(self) -> None:
        """Stop the observer."""
        if not self._running or self._backend is None:
            return

        if HAS_WATCHDOG:
            self._backend.stop()  # type: ignore[attr-defined]
            self._backend.join()  # type: ignore[attr-defined]
        else:
            self._backend.stop()  # type: ignore[attr-defined]

        self._running = False
        logger.info("Filesystem observer stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def watched_paths(self) -> List[str]:
        return list(self._watched_paths)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

filesystem_observer = FilesystemObserver()
