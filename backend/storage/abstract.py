"""
File Abstraction Layer — Abstract base classes and URI routing

All file operations use URI scheme routing:

    local://~/Documents/report.pdf
    gdrive://My Drive/Projects/
    dropbox://Personal/
    s3://my-bucket/backups/
    sftp://myserver.com/var/www/

All I/O is async streaming — files are never loaded fully into memory.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncContextManager, AsyncIterator, BinaryIO, Dict, List, Optional, Tuple


@dataclass
class FileInfo:
    """Metadata for a file or directory entry."""

    name: str
    path: str          # Full URI e.g. local:///home/user/file.txt
    size: int          # Bytes; 0 for directories
    is_dir: bool
    modified_at: datetime
    created_at: Optional[datetime] = None
    mime_type: Optional[str] = None
    readable: bool = True
    writable: bool = False


class FileSystem(ABC):
    """
    Abstract filesystem backend.

    Concrete subclasses implement scheme-specific I/O. All paths are
    scheme-stripped before they reach these methods — the router handles
    the URI prefix.
    """

    @property
    @abstractmethod
    def scheme(self) -> str:
        """URI scheme handled by this backend (e.g. 'local', 'gdrive')."""

    @abstractmethod
    async def read(self, path: str) -> AsyncIterator[bytes]:
        """
        Stream file contents in chunks.

        Args:
            path: Backend-relative path (scheme already stripped by router).

        Yields:
            bytes: Raw file chunks (≤ 64 KB each).

        Raises:
            FileNotFoundError: File does not exist.
            IsADirectoryError: Path is a directory.
            PermissionError: Path is blocked by security policy.
        """

    @abstractmethod
    async def write(self, path: str) -> AsyncContextManager[BinaryIO]:
        """
        Open a file for writing via an async context manager.

        Args:
            path: Backend-relative path.

        Returns:
            Async context manager that yields a writable binary file object.
        """

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete a file or empty directory."""

    @abstractmethod
    async def list(self, path: str) -> List[FileInfo]:
        """List directory contents, sorted: directories first, then files."""

    @abstractmethod
    async def move(self, src: str, dst: str) -> None:
        """Move or rename src to dst (same backend only)."""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Return True if path exists."""

    @abstractmethod
    async def stat(self, path: str) -> FileInfo:
        """Return metadata for a file or directory."""


class FileSystemRouter:
    """
    Routes URI-schemed paths to the correct FileSystem backend.

    Usage::

        router = FileSystemRouter()
        router.register(LocalFileSystem())

        async for chunk in router.read("local://~/Documents/report.pdf"):
            process(chunk)

    Bare paths without a scheme are treated as ``local://``.
    """

    def __init__(self) -> None:
        self._backends: Dict[str, FileSystem] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, backend: FileSystem) -> None:
        """Register a backend for its declared scheme."""
        self._backends[backend.scheme] = backend

    def get_backend(self, scheme: str) -> Optional[FileSystem]:
        """Return backend for a scheme, or None if unregistered."""
        return self._backends.get(scheme)

    def registered_schemes(self) -> List[str]:
        """Return list of registered URI schemes."""
        return list(self._backends.keys())

    # ------------------------------------------------------------------
    # URI resolution
    # ------------------------------------------------------------------

    def resolve(self, uri: str) -> Tuple[FileSystem, str]:
        """
        Parse a URI and return ``(backend, path)``.

        ``local://~/Documents/file.txt`` → ``(LocalFS, "~/Documents/file.txt")``
        ``/absolute/path``               → ``(LocalFS, "/absolute/path")``
        ``relative/path``                → ``(LocalFS, "relative/path")``
        """
        if "://" in uri:
            scheme, path = uri.split("://", 1)
        else:
            scheme, path = "local", uri

        backend = self._backends.get(scheme)
        if backend is None:
            raise ValueError(
                f"No backend registered for scheme '{scheme}'. "
                f"Registered: {self.registered_schemes()}"
            )
        return backend, path

    # ------------------------------------------------------------------
    # Forwarding methods
    # ------------------------------------------------------------------

    async def read(self, uri: str) -> AsyncIterator[bytes]:
        """Stream file contents."""
        backend, path = self.resolve(uri)
        return backend.read(path)

    async def write(self, uri: str) -> AsyncContextManager[BinaryIO]:
        """Open file for writing."""
        backend, path = self.resolve(uri)
        return backend.write(path)

    async def delete(self, uri: str) -> None:
        """Delete file or empty directory."""
        backend, path = self.resolve(uri)
        await backend.delete(path)

    async def list(self, uri: str) -> List[FileInfo]:
        """List directory contents."""
        backend, path = self.resolve(uri)
        return await backend.list(path)

    async def move(self, src_uri: str, dst_uri: str) -> None:
        """
        Move file.  Cross-backend moves are rejected — both URIs must share
        the same scheme.
        """
        src_backend, src_path = self.resolve(src_uri)
        dst_backend, dst_path = self.resolve(dst_uri)
        if src_backend.scheme != dst_backend.scheme:
            raise ValueError(
                f"Cross-backend move not supported: "
                f"'{src_backend.scheme}' → '{dst_backend.scheme}'"
            )
        await src_backend.move(src_path, dst_path)

    async def exists(self, uri: str) -> bool:
        """Check if path exists."""
        backend, path = self.resolve(uri)
        return await backend.exists(path)

    async def stat(self, uri: str) -> FileInfo:
        """Return file metadata."""
        backend, path = self.resolve(uri)
        return await backend.stat(path)


# ---------------------------------------------------------------------------
# Module-level singleton — populated by storage/local.py at import time
# ---------------------------------------------------------------------------
filesystem_router = FileSystemRouter()
