"""
storage/router.py — P4-01/P4-02

URI scheme router with ``never_read`` blocklist enforcement.

Wraps :class:`~backend.storage.abstract.FileSystemRouter` and adds
config-driven security:

* ``never_read`` — glob patterns (from ``config.yml``) that must never be
  read, written to, or moved.  Matched against the *full resolved path* of
  the underlying file.  Raises :class:`PermissionError` on violation.

Backends are registered at module import time so callers only need::

    from backend.storage.router import file_router
    async for chunk in file_router.read("local://~/Documents/report.pdf"):
        ...

If a backend's optional dependency is missing (e.g. ``gdrive`` requires the
google-auth libraries), it is silently skipped with a WARNING log.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncContextManager, AsyncIterator, BinaryIO, List

from backend.config.loader import cfg
from backend.storage.abstract import FileInfo, FileSystem, FileSystemRouter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# never_read enforcement
# ---------------------------------------------------------------------------


def _check_never_read(raw_path: str, never_read_patterns: List[str]) -> None:
    """
    Raise PermissionError if *raw_path* (or any ancestor) matches a ``never_read`` glob.

    Uses :meth:`pathlib.PurePath.match` which supports ``**`` as a recursive
    wildcard.  Each candidate path is tested:

    * The resolved absolute path itself
    * Each parent directory  — so ``**/.aws`` blocks ``/home/u/.aws/creds``
    * The filename only      — so ``**/.env`` blocks bare ``.env``

    All matching is case-sensitive (Unix paths).
    """
    p = Path(raw_path).expanduser().resolve()
    # Collect the path itself plus every ancestor (up to filesystem root)
    candidates = [p] + list(p.parents)

    for pattern in never_read_patterns:
        for candidate in candidates:
            if candidate.match(pattern):
                raise PermissionError(
                    f"Access denied: '{raw_path}' matches never_read pattern '{pattern}'"
                )


# ---------------------------------------------------------------------------
# Secured router
# ---------------------------------------------------------------------------


class SecuredFileSystemRouter(FileSystemRouter):
    """
    :class:`FileSystemRouter` that enforces the ``never_read`` blocklist.

    All read-family operations (``read``, ``stat``, ``list``, ``exists``)
    check the path against ``cfg.filesystem.never_read`` before delegating
    to the backend.  Write-destructive operations (``delete``, ``move``)
    are also checked so sensitive files cannot be deleted through the
    assistant.
    """

    def __init__(self, never_read_patterns: List[str] | None = None) -> None:
        super().__init__()
        self._never_read_patterns: List[str] = (
            never_read_patterns
            if never_read_patterns is not None
            else list(cfg.filesystem.never_read)
        )

    # ------------------------------------------------------------------
    # Overrides with security checks
    # ------------------------------------------------------------------

    async def read(self, uri: str) -> AsyncIterator[bytes]:
        backend, path = self.resolve(uri)
        _check_never_read(path, self._never_read_patterns)
        return backend.read(path)

    async def write(self, uri: str) -> AsyncContextManager[BinaryIO]:
        # Writes to never_read files are also blocked (can't overwrite .env)
        backend, path = self.resolve(uri)
        _check_never_read(path, self._never_read_patterns)
        return backend.write(path)

    async def delete(self, uri: str) -> None:
        backend, path = self.resolve(uri)
        _check_never_read(path, self._never_read_patterns)
        await backend.delete(path)

    async def move(self, src_uri: str, dst_uri: str) -> None:
        src_backend, src_path = self.resolve(src_uri)
        dst_backend, dst_path = self.resolve(dst_uri)
        _check_never_read(src_path, self._never_read_patterns)
        _check_never_read(dst_path, self._never_read_patterns)
        if src_backend.scheme != dst_backend.scheme:
            raise ValueError(
                f"Cross-backend move not supported: "
                f"'{src_backend.scheme}' → '{dst_backend.scheme}'"
            )
        await src_backend.move(src_path, dst_path)

    async def exists(self, uri: str) -> bool:
        backend, path = self.resolve(uri)
        _check_never_read(path, self._never_read_patterns)
        return await backend.exists(path)

    async def stat(self, uri: str) -> FileInfo:
        backend, path = self.resolve(uri)
        _check_never_read(path, self._never_read_patterns)
        return await backend.stat(path)

    async def list(self, uri: str) -> List[FileInfo]:
        # List directory is allowed; individual files will be blocked on read
        backend, path = self.resolve(uri)
        return await backend.list(path)


# ---------------------------------------------------------------------------
# Backend registration
# ---------------------------------------------------------------------------


def _build_router() -> SecuredFileSystemRouter:
    router = SecuredFileSystemRouter()

    # Local filesystem (always available)
    try:
        from backend.storage.local import LocalFileSystem

        router.register(LocalFileSystem())
        logger.debug("Registered local filesystem backend")
    except Exception as exc:
        logger.error(f"Failed to register local filesystem backend: {exc}")

    # Google Drive (optional — requires google-auth)
    try:
        from backend.storage.gdrive import GoogleDriveFileSystem

        router.register(GoogleDriveFileSystem())
        logger.debug("Registered Google Drive filesystem backend")
    except ImportError:
        logger.info("Google Drive backend not available (missing google-auth libraries)")
    except Exception as exc:
        logger.warning(f"Google Drive backend init failed: {exc}")

    # Dropbox (optional)
    try:
        from backend.storage.dropbox_backend import DropboxFileSystem

        router.register(DropboxFileSystem())
        logger.debug("Registered Dropbox filesystem backend")
    except ImportError:
        logger.info("Dropbox backend not available (missing dropbox library)")
    except Exception as exc:
        logger.warning(f"Dropbox backend init failed: {exc}")

    # S3 (optional — requires aiobotocore)
    try:
        from backend.storage.s3 import S3FileSystem

        router.register(S3FileSystem())
        logger.debug("Registered S3 filesystem backend")
    except ImportError:
        logger.info("S3 backend not available (missing aiobotocore)")
    except Exception as exc:
        logger.warning(f"S3 backend init failed: {exc}")

    # SFTP (optional — requires asyncssh)
    try:
        from backend.storage.sftp import SFTPFileSystem

        router.register(SFTPFileSystem())
        logger.debug("Registered SFTP filesystem backend")
    except ImportError:
        logger.info("SFTP backend not available (missing asyncssh)")
    except Exception as exc:
        logger.warning(f"SFTP backend init failed: {exc}")

    return router


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: The application-wide file router.  Import this in tools and other modules.
file_router: SecuredFileSystemRouter = _build_router()
