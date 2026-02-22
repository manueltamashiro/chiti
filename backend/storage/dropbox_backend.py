"""
Dropbox File Backend — async streaming I/O via Dropbox API v2

OAuth2 flow:
    1. get_authorization_url(config) → redirect user to Dropbox
    2. User approves → Dropbox redirects to redirect_uri with ?code=...
    3. exchange_code(config, code) → stores tokens in OS keychain via oauth_manager

Registered as the 'dropbox://' backend on filesystem_router at import time
(once a DropboxConfig is set via configure_dropbox()).
"""

import io
import json
import logging
import urllib.parse
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from backend.storage.abstract import FileInfo, FileSystem, filesystem_router
from backend.storage.oauth import oauth_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------

DROPBOX_AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
_API_BASE = "https://api.dropboxapi.com/2/"
_CONTENT_BASE = "https://content.dropboxapi.com/2/"

_CHUNK_SIZE = 65_536  # 64 KB


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DropboxConfig:
    """OAuth2 application credentials for Dropbox."""

    app_key: str
    app_secret: str
    redirect_uri: str = "http://localhost:8080/callback"


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_dropbox_config: Optional[DropboxConfig] = None


def configure_dropbox(config: DropboxConfig) -> None:
    """Set the global Dropbox configuration and register the backend."""
    global _dropbox_config
    _dropbox_config = config


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------


def get_authorization_url(config: DropboxConfig) -> str:
    """
    Return the Dropbox OAuth2 authorization URL.

    The user must visit this URL and grant access. After consent they are
    redirected to config.redirect_uri with a ``code`` query parameter.

    Args:
        config: Application OAuth credentials.

    Returns:
        Authorization URL string to redirect the user to.
    """
    params = {
        "client_id": config.app_key,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "token_access_type": "offline",
    }
    return f"{DROPBOX_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(config: DropboxConfig, code: str) -> None:
    """
    Exchange an authorization code for access + refresh tokens.

    POSTs to Dropbox's token endpoint and stores the result via oauth_manager
    so subsequent API calls can retrieve valid credentials from the keychain.

    Args:
        config: Application OAuth credentials.
        code:   The authorization code from the OAuth callback.

    Raises:
        ImportError:  If httpx is not installed.
        RuntimeError: If the token exchange fails.
    """
    if not HAS_HTTPX:
        raise ImportError("httpx required for Dropbox backend: pip install httpx")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            DROPBOX_TOKEN_URL,
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": config.redirect_uri,
            },
            auth=(config.app_key, config.app_secret),
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Dropbox token exchange failed ({resp.status_code}): {resp.text}"
        )

    data = resp.json()
    await oauth_manager.store_from_oauth_flow(
        provider="dropbox",
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        token_type=data.get("token_type", "Bearer"),
        scopes=data.get("scope", "").split() if data.get("scope") else None,
    )
    logger.info("Dropbox OAuth token stored successfully.")


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class DropboxFileSystem(FileSystem):
    """
    Dropbox filesystem backend using the Dropbox API v2.
    Requires httpx for async HTTP.

    Registered as the ``dropbox://`` backend on the module-level
    :data:`filesystem_router` at import time.

    Path notes:
    - Dropbox root is represented as ``""`` (empty string), NOT ``"/"``.
    - All other paths start with ``/`` (e.g. ``/Documents/file.txt``).
    """

    @property
    def scheme(self) -> str:
        return "dropbox"

    async def _get_headers(self) -> Dict[str, str]:
        """
        Return Authorization headers for a Dropbox API request.

        Raises:
            RuntimeError: If the user has not completed the OAuth flow.
        """
        token = await oauth_manager.get_token("dropbox")
        if not token:
            raise RuntimeError(
                "Dropbox not authenticated. Run OAuth flow first."
            )
        return {"Authorization": f"Bearer {token}"}

    async def _parse_metadata(self, entry: dict) -> FileInfo:
        """
        Parse a Dropbox API metadata entry into a FileInfo.

        Args:
            entry: A Dropbox API file or folder metadata dict.

        Returns:
            FileInfo populated from the entry.
        """
        is_dir = entry.get(".tag") == "folder"
        name = entry.get("name", "")
        path_display = entry.get("path_display", f"/{name}")

        # Parse modified time — Dropbox uses "2021-01-01T12:00:00Z"
        modified_raw = entry.get("server_modified") or entry.get("client_modified")
        if modified_raw:
            # Replace trailing Z with +00:00 for fromisoformat compatibility
            modified_at = datetime.fromisoformat(modified_raw.replace("Z", "+00:00"))
        else:
            modified_at = datetime.now(timezone.utc)

        size = int(entry.get("size", 0)) if not is_dir else 0

        return FileInfo(
            name=name,
            path=f"dropbox://{path_display}",
            size=size,
            is_dir=is_dir,
            modified_at=modified_at,
            created_at=None,
            mime_type=None,
            readable=True,
            writable=True,
        )

    async def list(self, path: str) -> List[FileInfo]:
        """
        List the contents of a Dropbox folder.

        Results are sorted: directories first, then files, both alphabetically.

        Args:
            path: Path relative to Dropbox root. Use ``""`` for root.

        Returns:
            List of FileInfo entries.

        Raises:
            RuntimeError:  Not authenticated.
            ImportError:   httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required for Dropbox backend: pip install httpx")

        headers = await self._get_headers()
        headers["Content-Type"] = "application/json"

        # Dropbox uses "" for root, not "/"
        dropbox_path = path if path != "/" else ""

        entries: List[dict] = []

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_API_BASE}files/list_folder",
                headers=headers,
                content=json.dumps({"path": dropbox_path}),
            )
            resp.raise_for_status()
            data = resp.json()
            entries.extend(data.get("entries", []))

            # Handle pagination
            while data.get("has_more"):
                cursor = data["cursor"]
                resp = await client.post(
                    f"{_API_BASE}files/list_folder/continue",
                    headers=headers,
                    content=json.dumps({"cursor": cursor}),
                )
                resp.raise_for_status()
                data = resp.json()
                entries.extend(data.get("entries", []))

        file_infos = [await self._parse_metadata(e) for e in entries]
        # Sort: directories first, then files, alphabetically within each group
        file_infos.sort(key=lambda fi: (not fi.is_dir, fi.name.lower()))
        return file_infos

    async def stat(self, path: str) -> FileInfo:
        """
        Return metadata for a Dropbox file or folder.

        Args:
            path: Path relative to Dropbox root.

        Returns:
            FileInfo with Dropbox metadata.

        Raises:
            FileNotFoundError: Path does not exist.
            RuntimeError:      Not authenticated.
            ImportError:       httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required for Dropbox backend: pip install httpx")

        headers = await self._get_headers()
        headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_API_BASE}files/get_metadata",
                headers=headers,
                content=json.dumps({"path": path}),
            )

        if resp.status_code == 409:
            error_summary = resp.json().get("error_summary", "")
            if "path/not_found" in error_summary:
                raise FileNotFoundError(f"Dropbox path not found: {path!r}")
            raise RuntimeError(
                f"Dropbox API error (409): {resp.text}"
            )

        resp.raise_for_status()
        return await self._parse_metadata(resp.json())

    async def read(self, path: str) -> AsyncIterator[bytes]:
        """
        Stream file contents from Dropbox in 64 KB chunks.

        Args:
            path: Path relative to Dropbox root.

        Yields:
            bytes: Raw file chunks.

        Raises:
            FileNotFoundError: File does not exist.
            RuntimeError:      Not authenticated.
            ImportError:       httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required for Dropbox backend: pip install httpx")

        headers = await self._get_headers()
        # The Dropbox download endpoint uses a special header for the path arg
        headers["Dropbox-API-Arg"] = json.dumps({"path": path})

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{_CONTENT_BASE}files/download",
                headers=headers,
            ) as resp:
                if resp.status_code == 409:
                    body = await resp.aread()
                    error_summary = json.loads(body).get("error_summary", "")
                    if "path/not_found" in error_summary:
                        raise FileNotFoundError(
                            f"Dropbox path not found: {path!r}"
                        )
                    raise RuntimeError(
                        f"Dropbox API error (409): {body.decode()}"
                    )
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    yield chunk

    @asynccontextmanager
    async def write(self, path: str):
        """
        Write a file to Dropbox via an async context manager.

        Collects bytes into an in-memory buffer, then on context exit
        uploads the buffer to Dropbox using the ``files/upload`` endpoint
        with ``mode=overwrite``.

        Args:
            path: Path relative to Dropbox root.

        Yields:
            io.BytesIO: In-memory buffer to write data into.

        Raises:
            RuntimeError: Not authenticated.
            ImportError:  httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required for Dropbox backend: pip install httpx")

        buf = io.BytesIO()
        yield buf  # caller writes into buf

        content = buf.getvalue()

        headers = await self._get_headers()
        headers["Content-Type"] = "application/octet-stream"
        headers["Dropbox-API-Arg"] = json.dumps({
            "path": path,
            "mode": "overwrite",
        })

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_CONTENT_BASE}files/upload",
                headers=headers,
                content=content,
            )
            resp.raise_for_status()

        logger.debug("Dropbox write complete: %s", path)

    async def delete(self, path: str) -> None:
        """
        Delete a file or folder from Dropbox.

        Args:
            path: Path relative to Dropbox root.

        Raises:
            FileNotFoundError: Path does not exist.
            RuntimeError:      Not authenticated.
            ImportError:       httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required for Dropbox backend: pip install httpx")

        headers = await self._get_headers()
        headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_API_BASE}files/delete_v2",
                headers=headers,
                content=json.dumps({"path": path}),
            )

        if resp.status_code == 409:
            error_summary = resp.json().get("error_summary", "")
            if "path/not_found" in error_summary or "path_lookup/not_found" in error_summary:
                raise FileNotFoundError(f"Dropbox path not found: {path!r}")
            raise RuntimeError(
                f"Dropbox API error (409): {resp.text}"
            )

        resp.raise_for_status()
        logger.debug("Deleted Dropbox file: %s", path)

    async def move(self, src: str, dst: str) -> None:
        """
        Move or rename a file within Dropbox.

        Args:
            src: Source path relative to Dropbox root.
            dst: Destination path relative to Dropbox root.

        Raises:
            FileNotFoundError: Source path does not exist.
            RuntimeError:      Not authenticated.
            ImportError:       httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required for Dropbox backend: pip install httpx")

        headers = await self._get_headers()
        headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_API_BASE}files/move_v2",
                headers=headers,
                content=json.dumps({"from_path": src, "to_path": dst}),
            )

        if resp.status_code == 409:
            error_summary = resp.json().get("error_summary", "")
            if "path/not_found" in error_summary or "from/not_found" in error_summary:
                raise FileNotFoundError(
                    f"Dropbox source path not found: {src!r}"
                )
            raise RuntimeError(
                f"Dropbox API error (409): {resp.text}"
            )

        resp.raise_for_status()
        logger.debug("Moved Dropbox file: %s → %s", src, dst)

    async def exists(self, path: str) -> bool:
        """
        Return True if the path exists in Dropbox.

        Args:
            path: Path relative to Dropbox root.
        """
        try:
            await self.stat(path)
            return True
        except (FileNotFoundError, RuntimeError) as exc:
            # stat() raises FileNotFoundError for 409 path/not_found
            if isinstance(exc, FileNotFoundError):
                return False
            # Re-raise unexpected runtime errors
            raise


# ---------------------------------------------------------------------------
# Module-level singleton + auto-registration
# ---------------------------------------------------------------------------

dropbox_fs = DropboxFileSystem()
filesystem_router.register(dropbox_fs)
