"""
Google Drive Filesystem Backend — async streaming I/O via httpx

Authentication:
- Uses the OAuth2 flow managed by backend.storage.oauth.oauth_manager.
- Tokens are stored in the OS keychain (or memory fallback).
- Call get_authorization_url() → redirect user → exchange_code() to set up.
- Access tokens are refreshed automatically when expired.

All Drive API calls use the REST v3 API over httpx (not google-api-python-client),
so no heavy SDK dependency is required.

Path format:  gdrive://<path relative to My Drive>
              e.g.  gdrive://Projects/report.pdf
              e.g.  gdrive://   (root of My Drive)
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

_API_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
GDRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"
_CHUNK_SIZE = 65_536  # 64 KB

# Drive API fields requested on every file metadata fetch
_FILE_FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,parents"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GDriveConfig:
    """OAuth2 application credentials for Google Drive."""

    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8000/webhooks/oauth/gdrive"


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------


def get_authorization_url(config: GDriveConfig) -> str:
    """
    Build the Google OAuth2 authorization URL.

    The user must visit this URL and grant access. After consent they are
    redirected to config.redirect_uri with a ``code`` query parameter.

    Args:
        config: Application OAuth credentials.

    Returns:
        Authorization URL string to redirect the user to.
    """
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": GDRIVE_SCOPE,
        "access_type": "offline",   # request refresh token
        "prompt": "consent",        # force refresh token issuance even if previously granted
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(config: GDriveConfig, code: str) -> None:
    """
    Exchange an authorization code for access + refresh tokens.

    POSTs to Google's token endpoint and stores the result via oauth_manager
    so subsequent API calls can retrieve valid credentials from the keychain.

    Args:
        config: Application OAuth credentials.
        code:   The authorization code from the OAuth callback.

    Raises:
        ImportError:  If httpx is not installed.
        RuntimeError: If the token exchange fails.
    """
    if not HAS_HTTPX:
        raise ImportError("httpx required: pip install httpx")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "redirect_uri": config.redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed ({resp.status_code}): {resp.text}"
        )

    data = resp.json()
    await oauth_manager.store_from_oauth_flow(
        provider="gdrive",
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        scopes=data.get("scope", "").split(),
    )
    logger.info("Google Drive OAuth token stored successfully.")


async def refresh_access_token(config: GDriveConfig) -> str:
    """
    Use the stored refresh token to obtain a new access token.

    Updates the keychain with the refreshed credentials and returns the
    new access token string.

    Args:
        config: Application OAuth credentials.

    Returns:
        New access token string.

    Raises:
        ImportError:    If httpx is not installed.
        PermissionError: If no refresh token is stored.
        RuntimeError:   If the refresh request fails.
    """
    if not HAS_HTTPX:
        raise ImportError("httpx required: pip install httpx")

    full_token = await oauth_manager.get_full_token("gdrive")
    if not full_token or not full_token.refresh_token:
        raise PermissionError(
            "No refresh token stored for Google Drive. "
            "Complete the OAuth flow first."
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": full_token.refresh_token,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed ({resp.status_code}): {resp.text}"
        )

    data = resp.json()
    await oauth_manager.store_from_oauth_flow(
        provider="gdrive",
        access_token=data["access_token"],
        refresh_token=full_token.refresh_token,  # Google may not re-issue refresh token
        expires_in=data.get("expires_in"),
        scopes=data.get("scope", "").split() or full_token.scopes,
    )
    logger.info("Google Drive access token refreshed.")
    return data["access_token"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string from the Drive API into a datetime."""
    if not value:
        return None
    # Drive returns RFC 3339 strings like "2024-01-15T10:30:00.000Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _item_to_fileinfo(item: Dict, parent_path: str) -> FileInfo:
    """Convert a Drive API files resource dict to a FileInfo."""
    name = item["name"]
    is_dir = item.get("mimeType") == FOLDER_MIME
    # Build full URI path
    joined = f"{parent_path.rstrip('/')}/{name}" if parent_path else name
    uri = f"gdrive://{joined}"

    return FileInfo(
        name=name,
        path=uri,
        size=int(item.get("size", 0)) if not is_dir else 0,
        is_dir=is_dir,
        modified_at=_parse_datetime(item.get("modifiedTime")) or datetime.now(timezone.utc),
        created_at=_parse_datetime(item.get("createdTime")),
        mime_type=item.get("mimeType"),
        readable=True,
        writable=True,
    )


def _raise_for_status(resp: "httpx.Response") -> None:
    """Raise a descriptive exception for non-2xx Drive API responses."""
    if resp.status_code == 401:
        raise PermissionError(
            "Google Drive access token rejected (401). Re-authenticate."
        )
    if resp.status_code == 403:
        raise PermissionError(
            f"Google Drive permission denied (403): {resp.text}"
        )
    if resp.status_code == 404:
        raise FileNotFoundError(
            f"Resource not found in Google Drive (404): {resp.text}"
        )
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(
            f"Google Drive API error ({resp.status_code}): {resp.text}"
        )


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class GoogleDriveFileSystem(FileSystem):
    """
    Google Drive filesystem backend.

    Registered as the ``gdrive://`` backend on the module-level
    :data:`filesystem_router` at import time (only when httpx is available).

    All paths are relative to the root of the authenticated user's "My Drive".
    """

    @property
    def scheme(self) -> str:
        return "gdrive"

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    async def _get_headers(self) -> Dict[str, str]:
        """
        Return Authorization headers for a Drive API request.

        Raises:
            PermissionError: If the user has not completed the OAuth flow.
        """
        token = await oauth_manager.get_token("gdrive")
        if not token:
            raise PermissionError(
                "Not authenticated with Google Drive. Complete OAuth flow first."
            )
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Path → Drive file-ID resolution
    # ------------------------------------------------------------------

    async def _resolve_path_to_id(self, path: str) -> str:
        """
        Walk the Drive folder hierarchy to resolve a path to a file ID.

        Empty or root paths resolve to ``"root"``.  Each path component is
        looked up via a Drive API query for a child with that name inside
        the previously resolved folder.

        Args:
            path: Path relative to My Drive root, e.g. "Projects/report.pdf".

        Returns:
            Drive file ID string (or ``"root"`` for the root folder).

        Raises:
            FileNotFoundError: If any component of the path does not exist.
        """
        # Strip leading/trailing slashes and handle root
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            return "root"

        headers = await self._get_headers()
        current_id = "root"

        async with httpx.AsyncClient() as client:
            for part in parts:
                # Escape single quotes in name for Drive query syntax
                escaped = part.replace("'", "\\'")
                query = (
                    f"'{current_id}' in parents "
                    f"and name = '{escaped}' "
                    f"and trashed = false"
                )
                resp = await client.get(
                    f"{_API_BASE}/files",
                    headers=headers,
                    params={"q": query, "fields": f"files({_FILE_FIELDS})", "pageSize": 1},
                )
                _raise_for_status(resp)
                files = resp.json().get("files", [])
                if not files:
                    raise FileNotFoundError(
                        f"Not found in Google Drive: {path!r} "
                        f"(component '{part}' missing)"
                    )
                current_id = files[0]["id"]

        return current_id

    async def _list_folder(self, folder_id: str) -> List[Dict]:
        """
        Return all children of a Drive folder, handling pagination.

        Args:
            folder_id: Drive file ID of the folder (or ``"root"``).

        Returns:
            List of Drive API files resource dicts.
        """
        headers = await self._get_headers()
        items: List[Dict] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient() as client:
            while True:
                params: Dict = {
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": f"nextPageToken,files({_FILE_FIELDS})",
                    "pageSize": 1000,
                }
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    f"{_API_BASE}/files",
                    headers=headers,
                    params=params,
                )
                _raise_for_status(resp)
                data = resp.json()
                items.extend(data.get("files", []))

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return items

    async def _ensure_parent_folders(self, path: str) -> str:
        """
        Create any missing parent folders for the given path and return
        the Drive file ID of the immediate parent folder.

        Args:
            path: Full path, e.g. "Projects/2024/report.pdf".

        Returns:
            Drive file ID of the parent directory.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        # The file itself is the last part; we only need its parent folders.
        folder_parts = parts[:-1]
        if not folder_parts:
            return "root"

        headers = await self._get_headers()
        current_id = "root"

        async with httpx.AsyncClient() as client:
            for folder_name in folder_parts:
                escaped = folder_name.replace("'", "\\'")
                query = (
                    f"'{current_id}' in parents "
                    f"and name = '{escaped}' "
                    f"and mimeType = '{FOLDER_MIME}' "
                    f"and trashed = false"
                )
                resp = await client.get(
                    f"{_API_BASE}/files",
                    headers=headers,
                    params={"q": query, "fields": "files(id,name)", "pageSize": 1},
                )
                _raise_for_status(resp)
                files = resp.json().get("files", [])

                if files:
                    current_id = files[0]["id"]
                else:
                    # Create the folder
                    create_resp = await client.post(
                        f"{_API_BASE}/files",
                        headers={**headers, "Content-Type": "application/json"},
                        content=json.dumps({
                            "name": folder_name,
                            "mimeType": FOLDER_MIME,
                            "parents": [current_id],
                        }),
                    )
                    _raise_for_status(create_resp)
                    current_id = create_resp.json()["id"]
                    logger.debug("Created Drive folder: %s (%s)", folder_name, current_id)

        return current_id

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def read(self, path: str) -> AsyncIterator[bytes]:
        """
        Stream file contents from Google Drive in 64 KB chunks.

        Args:
            path: Path relative to My Drive root.

        Yields:
            bytes: Raw file chunks.

        Raises:
            FileNotFoundError: File does not exist.
            PermissionError:   Not authenticated.
            ImportError:       httpx not installed.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        file_id = await self._resolve_path_to_id(path)
        headers = await self._get_headers()

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                f"{_API_BASE}/files/{file_id}",
                headers=headers,
                params={"alt": "media"},
            ) as resp:
                _raise_for_status(resp)
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    yield chunk

    @asynccontextmanager
    async def write(self, path: str):
        """
        Write a file to Google Drive via an async context manager.

        On context exit the buffered content is uploaded:
        - If the file already exists, it is updated via PATCH (multipart).
        - If the file is new, it is created via POST (multipart).
        Parent folders are created automatically.

        Args:
            path: Path relative to My Drive root.

        Yields:
            io.BytesIO: In-memory buffer to write data into.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        buf = io.BytesIO()
        yield buf  # caller writes into buf

        content = buf.getvalue()
        headers = await self._get_headers()

        # Check whether the file already exists
        existing_id: Optional[str] = None
        try:
            existing_id = await self._resolve_path_to_id(path)
        except FileNotFoundError:
            pass

        file_name = path.strip("/").split("/")[-1]

        # Construct the multipart body (metadata + media)
        boundary = "===gdrive_upload_boundary==="
        metadata_bytes = json.dumps({"name": file_name}).encode()
        multipart_body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode() + metadata_bytes + (
            f"\r\n--{boundary}\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--".encode()

        upload_headers = {
            **headers,
            "Content-Type": f"multipart/related; boundary={boundary}",
        }

        async with httpx.AsyncClient() as client:
            if existing_id:
                # Update existing file contents
                resp = await client.patch(
                    f"{_UPLOAD_BASE}/files/{existing_id}",
                    headers=upload_headers,
                    params={"uploadType": "multipart", "fields": "id"},
                    content=multipart_body,
                )
            else:
                # Create new file — ensure parent folder hierarchy exists first
                parent_id = await self._ensure_parent_folders(path)
                meta = {"name": file_name, "parents": [parent_id]}
                meta_bytes = json.dumps(meta).encode()
                multipart_body = (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                ).encode() + meta_bytes + (
                    f"\r\n--{boundary}\r\n"
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode() + content + f"\r\n--{boundary}--".encode()

                resp = await client.post(
                    f"{_UPLOAD_BASE}/files",
                    headers=upload_headers,
                    params={"uploadType": "multipart", "fields": "id"},
                    content=multipart_body,
                )

            _raise_for_status(resp)
            logger.debug("Drive write complete: %s → id=%s", path, resp.json().get("id"))

    async def delete(self, path: str) -> None:
        """
        Permanently delete a file or folder from Google Drive.

        Args:
            path: Path relative to My Drive root.

        Raises:
            FileNotFoundError: File does not exist.
            PermissionError:   Not authenticated or access denied.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        file_id = await self._resolve_path_to_id(path)
        headers = await self._get_headers()

        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{_API_BASE}/files/{file_id}",
                headers=headers,
            )
            _raise_for_status(resp)
        logger.debug("Deleted Drive file: %s", path)

    async def list(self, path: str) -> List[FileInfo]:
        """
        List the contents of a Google Drive folder.

        Results are sorted: directories first, then files, both alphabetically.

        Args:
            path: Path relative to My Drive root (empty string for root).

        Returns:
            List of FileInfo entries.

        Raises:
            FileNotFoundError: Folder does not exist.
            PermissionError:   Not authenticated.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        folder_id = await self._resolve_path_to_id(path)
        items = await self._list_folder(folder_id)

        entries = [_item_to_fileinfo(item, path) for item in items]
        # Sort: directories first, then files, alphabetically within each group
        entries.sort(key=lambda fi: (not fi.is_dir, fi.name.lower()))
        return entries

    async def move(self, src: str, dst: str) -> None:
        """
        Move or rename a file within Google Drive.

        Updates both the file's parent folder and its name in a single PATCH.

        Args:
            src: Source path relative to My Drive root.
            dst: Destination path relative to My Drive root.

        Raises:
            FileNotFoundError: Source file does not exist.
            PermissionError:   Not authenticated.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        file_id = await self._resolve_path_to_id(src)

        # Determine destination parent ID (create folders if necessary)
        dst_parent_id = await self._ensure_parent_folders(dst)
        dst_name = dst.strip("/").split("/")[-1]

        # Retrieve current parent(s) so we can remove them
        headers = await self._get_headers()
        async with httpx.AsyncClient() as client:
            meta_resp = await client.get(
                f"{_API_BASE}/files/{file_id}",
                headers=headers,
                params={"fields": "parents"},
            )
            _raise_for_status(meta_resp)
            old_parents = ",".join(meta_resp.json().get("parents", []))

            resp = await client.patch(
                f"{_API_BASE}/files/{file_id}",
                headers={**headers, "Content-Type": "application/json"},
                params={
                    "addParents": dst_parent_id,
                    "removeParents": old_parents,
                    "fields": "id,parents",
                },
                content=json.dumps({"name": dst_name}),
            )
            _raise_for_status(resp)
        logger.debug("Moved Drive file: %s → %s", src, dst)

    async def exists(self, path: str) -> bool:
        """
        Return True if the path exists in Google Drive.

        Args:
            path: Path relative to My Drive root.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        try:
            await self._resolve_path_to_id(path)
            return True
        except FileNotFoundError:
            return False

    async def stat(self, path: str) -> FileInfo:
        """
        Return metadata for a Google Drive file or folder.

        Args:
            path: Path relative to My Drive root.

        Returns:
            FileInfo with Drive metadata.

        Raises:
            FileNotFoundError: Path does not exist.
            PermissionError:   Not authenticated.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx required: pip install httpx")

        file_id = await self._resolve_path_to_id(path)
        headers = await self._get_headers()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_API_BASE}/files/{file_id}",
                headers=headers,
                params={"fields": _FILE_FIELDS},
            )
            _raise_for_status(resp)

        item = resp.json()
        # Determine display name and parent path for URI construction
        parent_path = "/".join(path.strip("/").split("/")[:-1])
        return _item_to_fileinfo(item, parent_path)


# ---------------------------------------------------------------------------
# Module-level singleton + auto-registration
# ---------------------------------------------------------------------------

gdrive_fs = GoogleDriveFileSystem()

if HAS_HTTPX:
    filesystem_router.register(gdrive_fs)
else:
    logger.warning(
        "Google Drive backend not registered: httpx is not installed. "
        "Install it with: pip install httpx"
    )
