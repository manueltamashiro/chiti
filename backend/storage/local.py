"""
Local Filesystem Backend — async streaming I/O via aiofiles

Security:
- Hard-blocked path patterns (private keys, .env, etc.) are always denied
  regardless of any configuration.
- All paths are fully resolved (expanduser + resolve) to prevent symlink
  traversal.
- aiofiles is used for non-blocking I/O; falls back to synchronous I/O
  if the library is not installed.
"""

import fnmatch
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, List, Optional, Set

try:
    import aiofiles
    import aiofiles.os

    HAS_AIOFILES = True
except ImportError:
    HAS_AIOFILES = False

from backend.storage.abstract import FileInfo, FileSystem, filesystem_router

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 65_536  # 64 KB

# Patterns that can NEVER be read, regardless of any allowlist configuration.
# Matched against the resolved absolute path string using fnmatch.
_HARD_BLOCKED_PATTERNS: Set[str] = {
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa",
    "**/id_rsa.*",
    "**/id_ed25519",
    "**/id_ed25519.*",
    "**/.aws/**",
    "**/.ssh/**",
    "**/*.pfx",
    "**/*.p12",
    "**/*.pkcs12",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    """Expand ``~`` and symlinks, return absolute Path."""
    return Path(path).expanduser().resolve()


def _is_blocked(resolved: Path) -> bool:
    """Return True if the resolved path matches any hard-blocked pattern."""
    path_str = str(resolved)
    name = resolved.name
    for pattern in _HARD_BLOCKED_PATTERNS:
        bare = pattern.lstrip("*").lstrip("/")
        # Simple name-only match (e.g. ".env", "*.pem")
        if fnmatch.fnmatch(name, bare):
            return True
        # Full-path match
        if fnmatch.fnmatch(path_str, pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class LocalFileSystem(FileSystem):
    """
    Local filesystem backend with async streaming I/O.

    Registered as the ``local://`` backend on the module-level
    :data:`filesystem_router` at import time.
    """

    @property
    def scheme(self) -> str:
        return "local"

    # ------------------------------------------------------------------
    # Core abstract methods
    # ------------------------------------------------------------------

    async def read(self, path: str) -> AsyncIterator[bytes]:
        """Stream file in ``_CHUNK_SIZE`` byte chunks."""
        resolved = _resolve(path)

        if _is_blocked(resolved):
            raise PermissionError(f"Read access denied (blocked path): {resolved}")
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")
        if resolved.is_dir():
            raise IsADirectoryError(f"Path is a directory: {resolved}")

        if HAS_AIOFILES:
            async with aiofiles.open(resolved, "rb") as fh:
                while True:
                    chunk = await fh.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
        else:
            with open(resolved, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

    @asynccontextmanager
    async def write(self, path: str):
        """Open file for writing. Creates parent directories as needed."""
        resolved = _resolve(path)

        if _is_blocked(resolved):
            raise PermissionError(f"Write access denied (blocked path): {resolved}")

        resolved.parent.mkdir(parents=True, exist_ok=True)

        if HAS_AIOFILES:
            async with aiofiles.open(resolved, "wb") as fh:
                yield fh
        else:
            with open(resolved, "wb") as fh:
                yield fh

    async def delete(self, path: str) -> None:
        """Delete a file or empty directory."""
        resolved = _resolve(path)

        if _is_blocked(resolved):
            raise PermissionError(f"Delete access denied (blocked path): {resolved}")
        if not resolved.exists():
            raise FileNotFoundError(f"Path not found: {resolved}")

        if resolved.is_dir():
            if HAS_AIOFILES:
                await aiofiles.os.rmdir(resolved)
            else:
                os.rmdir(resolved)
        else:
            if HAS_AIOFILES:
                await aiofiles.os.remove(resolved)
            else:
                os.remove(resolved)

    async def list(self, path: str) -> List[FileInfo]:
        """List directory contents sorted: dirs first, then files, alphabetically."""
        resolved = _resolve(path)

        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {resolved}")

        entries: List[FileInfo] = []
        for entry in sorted(resolved.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                st = entry.stat()
                mime_type: Optional[str] = None
                if entry.is_file():
                    mime_type, _ = mimetypes.guess_type(entry.name)
                entries.append(
                    FileInfo(
                        name=entry.name,
                        path=f"local://{entry}",
                        size=st.st_size if entry.is_file() else 0,
                        is_dir=entry.is_dir(),
                        modified_at=datetime.fromtimestamp(st.st_mtime),
                        created_at=datetime.fromtimestamp(st.st_ctime),
                        mime_type=mime_type,
                    )
                )
            except (PermissionError, OSError):
                continue

        return entries

    async def move(self, src: str, dst: str) -> None:
        """Move or rename a file within the local filesystem."""
        src_r = _resolve(src)
        dst_r = _resolve(dst)

        if _is_blocked(src_r) or _is_blocked(dst_r):
            raise PermissionError("Move access denied (blocked path)")
        if not src_r.exists():
            raise FileNotFoundError(f"Source not found: {src_r}")

        dst_r.parent.mkdir(parents=True, exist_ok=True)

        if HAS_AIOFILES:
            await aiofiles.os.rename(src_r, dst_r)
        else:
            src_r.rename(dst_r)

    async def exists(self, path: str) -> bool:
        return _resolve(path).exists()

    async def stat(self, path: str) -> FileInfo:
        resolved = _resolve(path)

        if not resolved.exists():
            raise FileNotFoundError(f"Path not found: {resolved}")

        st = resolved.stat()
        mime_type: Optional[str] = None
        if resolved.is_file():
            mime_type, _ = mimetypes.guess_type(resolved.name)

        return FileInfo(
            name=resolved.name,
            path=f"local://{resolved}",
            size=st.st_size if resolved.is_file() else 0,
            is_dir=resolved.is_dir(),
            modified_at=datetime.fromtimestamp(st.st_mtime),
            created_at=datetime.fromtimestamp(st.st_ctime),
            mime_type=mime_type,
        )

    # ------------------------------------------------------------------
    # Convenience helpers (not part of abstract interface)
    # ------------------------------------------------------------------

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read entire file as a string (for small files only)."""
        chunks: List[bytes] = []
        async for chunk in self.read(path):
            chunks.append(chunk)
        return b"".join(chunks).decode(encoding, errors="replace")

    async def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write a string to a file."""
        async with self.write(path) as fh:
            data = content.encode(encoding)
            if HAS_AIOFILES:
                await fh.write(data)
            else:
                fh.write(data)

    async def append_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Append text to a file (creates it if it does not exist)."""
        resolved = _resolve(path)

        if _is_blocked(resolved):
            raise PermissionError(f"Write access denied (blocked path): {resolved}")

        resolved.parent.mkdir(parents=True, exist_ok=True)

        if HAS_AIOFILES:
            async with aiofiles.open(resolved, "a", encoding=encoding) as fh:
                await fh.write(content)
        else:
            with open(resolved, "a", encoding=encoding) as fh:
                fh.write(content)

    async def find_files(
        self,
        root: str,
        pattern: str = "*",
        max_depth: int = 10,
        max_results: int = 200,
    ) -> List[FileInfo]:
        """
        Recursively find files matching a glob pattern.

        Args:
            root:        Root directory to search.
            pattern:     Glob pattern applied to file names (e.g. "*.py").
            max_depth:   Maximum directory recursion depth.
            max_results: Maximum number of results returned.

        Returns:
            Matching FileInfo entries, capped at max_results.
        """
        resolved_root = _resolve(root)

        if not resolved_root.exists():
            raise FileNotFoundError(f"Directory not found: {resolved_root}")

        results: List[FileInfo] = []
        self._find_recursive(resolved_root, pattern, 0, max_depth, max_results, results)
        return results

    def _find_recursive(
        self,
        directory: Path,
        pattern: str,
        depth: int,
        max_depth: int,
        max_results: int,
        results: List[FileInfo],
    ) -> None:
        if depth > max_depth or len(results) >= max_results:
            return

        try:
            for entry in directory.iterdir():
                if len(results) >= max_results:
                    return
                if entry.is_file() and fnmatch.fnmatch(entry.name, pattern):
                    try:
                        st = entry.stat()
                        mime_type, _ = mimetypes.guess_type(entry.name)
                        results.append(
                            FileInfo(
                                name=entry.name,
                                path=f"local://{entry}",
                                size=st.st_size,
                                is_dir=False,
                                modified_at=datetime.fromtimestamp(st.st_mtime),
                                created_at=datetime.fromtimestamp(st.st_ctime),
                                mime_type=mime_type,
                            )
                        )
                    except (PermissionError, OSError):
                        continue
                elif entry.is_dir() and not entry.name.startswith("."):
                    self._find_recursive(entry, pattern, depth + 1, max_depth, max_results, results)
        except (PermissionError, OSError):
            pass


# ---------------------------------------------------------------------------
# Module-level singleton + auto-registration
# ---------------------------------------------------------------------------

local_fs = LocalFileSystem()
filesystem_router.register(local_fs)
