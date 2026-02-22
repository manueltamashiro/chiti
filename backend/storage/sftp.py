"""
SFTP File Backend — async I/O via asyncssh

Connection lifecycle: a single SSH connection is maintained per SFTPFileSystem
instance and reconnected automatically on disconnect.

Registered as the 'sftp://' backend when create_sftp_backend(config) is called.
"""

# Optional import — catch any exception because some broken installations
# raise non-ImportError exceptions (e.g. native-library crashes) at import time.
try:
    import asyncssh
    HAS_ASYNCSSH = True
except Exception:  # noqa: BLE001
    HAS_ASYNCSSH = False
    asyncssh = None  # type: ignore[assignment]

import asyncio
import stat as stat_module
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator, List, Optional

from backend.storage.abstract import FileInfo, FileSystem


@dataclass
class SFTPConfig:
    host: str
    port: int = 22
    username: str = ""
    password: Optional[str] = None
    key_file: Optional[str] = None       # Path to private key file
    known_hosts: Optional[str] = None    # Path to known_hosts, or None to disable checking
    base_path: str = "/"                 # Remote base directory


class SFTPFileSystem(FileSystem):
    """
    SFTP filesystem backend using asyncssh.

    Maintains a persistent SSH/SFTP connection that reconnects on drop.
    """

    def __init__(self, config: SFTPConfig) -> None:
        self._config = config
        self._conn = None   # asyncssh.SSHClientConnection
        self._sftp = None   # asyncssh.SFTPClient
        self._lock = None   # asyncio.Lock, initialized lazily

    @property
    def scheme(self) -> str:
        return "sftp"

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create asyncio.Lock (can't create at __init__ time)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_connected(self) -> None:
        """Connect or reconnect the SSH/SFTP session."""
        if self._sftp is not None:
            return  # Already connected

        if not HAS_ASYNCSSH:
            raise ImportError("asyncssh required for SFTP backend: pip install asyncssh")

        # Build connect kwargs
        connect_kwargs = {
            "host": self._config.host,
            "port": self._config.port,
            "username": self._config.username,
        }
        if self._config.password:
            connect_kwargs["password"] = self._config.password
        if self._config.key_file:
            connect_kwargs["client_keys"] = [self._config.key_file]
        if self._config.known_hosts is None:
            connect_kwargs["known_hosts"] = None  # Disable host key checking
        else:
            connect_kwargs["known_hosts"] = self._config.known_hosts

        self._conn = await asyncssh.connect(**connect_kwargs)
        self._sftp = await self._conn.start_sftp_client()

    def _full_path(self, path: str) -> str:
        """Combine base_path and relative path into a full remote path."""
        base = self._config.base_path.rstrip("/")
        rel = path.lstrip("/")
        if rel:
            return f"{base}/{rel}"
        # rel is empty — return base, defaulting to "/" if base was "/"
        return base if base else "/"

    def _parse_attrs(self, path: str, name: str, attrs) -> FileInfo:
        """Convert asyncssh SFTPAttrs to FileInfo."""
        is_dir = stat_module.S_ISDIR(attrs.permissions or 0) if attrs.permissions else False
        mtime = datetime.fromtimestamp(attrs.mtime) if attrs.mtime else datetime.utcnow()
        return FileInfo(
            name=name,
            path=f"sftp://{self._config.host}/{path.lstrip('/')}",
            size=attrs.size or 0,
            is_dir=is_dir,
            modified_at=mtime,
        )

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def list(self, path: str) -> List[FileInfo]:
        async with self._get_lock():
            await self._ensure_connected()
        full = self._full_path(path)
        entries = await self._sftp.readdir(full)
        # Each entry is an SFTPName object with .filename and .attrs attributes
        # Skip "." and ".."
        result = []
        for entry in entries:
            name = entry.filename  # asyncssh uses .filename
            if name in (".", ".."):
                continue
            attrs = entry.attrs
            result.append(self._parse_attrs(f"{path.rstrip('/')}/{name}", name, attrs))
        # Sort: dirs first, then by name (case-insensitive)
        result.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return result

    async def stat(self, path: str) -> FileInfo:
        async with self._get_lock():
            await self._ensure_connected()
        full = self._full_path(path)
        try:
            attrs = await self._sftp.stat(full)
        except Exception as exc:
            # Catch asyncssh.SFTPError by class name to avoid a hard import
            if type(exc).__name__ == "SFTPError":
                raise FileNotFoundError(f"Not found: {path}") from exc
            raise
        name = Path(full).name
        return self._parse_attrs(path, name, attrs)

    async def read(self, path: str) -> AsyncIterator[bytes]:
        async with self._get_lock():
            await self._ensure_connected()
        full = self._full_path(path)
        async with self._sftp.open(full, "rb") as f:
            while True:
                chunk = await f.read(65536)
                if not chunk:
                    break
                yield chunk

    @asynccontextmanager
    async def write(self, path: str):
        async with self._get_lock():
            await self._ensure_connected()
        buf = BytesIO()
        yield buf
        full = self._full_path(path)
        buf.seek(0)
        async with self._sftp.open(full, "wb") as f:
            await f.write(buf.read())

    async def delete(self, path: str) -> None:
        async with self._get_lock():
            await self._ensure_connected()
        full = self._full_path(path)
        try:
            await self._sftp.remove(full)
        except Exception as exc:
            if type(exc).__name__ != "SFTPError":
                raise
            # remove failed — try rmdir
            try:
                await self._sftp.rmdir(full)
            except Exception as exc2:
                if type(exc2).__name__ == "SFTPError":
                    raise FileNotFoundError(f"Not found: {path}") from exc2
                raise

    async def move(self, src: str, dst: str) -> None:
        async with self._get_lock():
            await self._ensure_connected()
        await self._sftp.rename(self._full_path(src), self._full_path(dst))

    async def exists(self, path: str) -> bool:
        try:
            await self.stat(path)
            return True
        except FileNotFoundError:
            return False

    async def close(self) -> None:
        """Close the SSH connection."""
        if self._sftp:
            self._sftp.exit()
            self._sftp = None
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Factory / registration
# ---------------------------------------------------------------------------

# Module-level singleton (set by create_sftp_backend)
_sftp_instance: Optional[SFTPFileSystem] = None


def create_sftp_backend(config: SFTPConfig) -> SFTPFileSystem:
    """Create an SFTPFileSystem, register it with filesystem_router, return it."""
    global _sftp_instance
    from backend.storage.abstract import filesystem_router
    backend = SFTPFileSystem(config)
    filesystem_router.register(backend)
    _sftp_instance = backend
    return backend
