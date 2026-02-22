"""
Tests for DropboxFileSystem backend
"""

import json
import pytest
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

from backend.storage.dropbox_backend import (
    DropboxConfig,
    DropboxFileSystem,
    get_authorization_url,
    DROPBOX_AUTH_URL,
)
from backend.storage.abstract import FileInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return DropboxConfig(
        app_key="test_app_key",
        app_secret="test_app_secret",
        redirect_uri="http://localhost:8080/callback",
    )


@pytest.fixture
def fs():
    return DropboxFileSystem()


def _make_mock_response(status_code: int, json_data: dict) -> MagicMock:
    """Build a mock httpx.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.text = json.dumps(json_data)
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = Exception(
            f"HTTP {status_code}"
        )
    return mock_resp


def _make_file_entry(
    name: str = "report.pdf",
    path_display: str = "/Documents/report.pdf",
    size: int = 1024,
    modified: str = "2024-01-15T10:30:00Z",
) -> dict:
    return {
        ".tag": "file",
        "name": name,
        "path_display": path_display,
        "size": size,
        "server_modified": modified,
        "client_modified": modified,
    }


def _make_folder_entry(
    name: str = "Documents",
    path_display: str = "/Documents",
) -> dict:
    return {
        ".tag": "folder",
        "name": name,
        "path_display": path_display,
    }


# ---------------------------------------------------------------------------
# TestDropboxAuth
# ---------------------------------------------------------------------------


class TestDropboxAuth:
    def test_get_authorization_url_contains_app_key(self, config):
        url = get_authorization_url(config)
        assert config.app_key in url

    def test_get_authorization_url_starts_with_dropbox_auth_url(self, config):
        url = get_authorization_url(config)
        assert url.startswith(DROPBOX_AUTH_URL)

    def test_get_authorization_url_contains_response_type_code(self, config):
        url = get_authorization_url(config)
        assert "response_type=code" in url

    def test_get_authorization_url_contains_redirect_uri(self, config):
        url = get_authorization_url(config)
        assert "redirect_uri" in url

    def test_get_authorization_url_contains_offline_access(self, config):
        url = get_authorization_url(config)
        assert "offline" in url


# ---------------------------------------------------------------------------
# TestDropboxFileSystem
# ---------------------------------------------------------------------------


class TestDropboxFileSystem:
    # -----------------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------------

    async def test_raises_if_not_authenticated(self, fs):
        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="not authenticated"):
                await fs._get_headers()

    async def test_get_headers_returns_bearer_token(self, fs):
        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ):
            headers = await fs._get_headers()
            assert headers["Authorization"] == "Bearer fake_token"

    # -----------------------------------------------------------------------
    # list
    # -----------------------------------------------------------------------

    async def test_list_parses_response(self, fs):
        list_response = {
            "entries": [
                _make_file_entry("readme.txt", "/readme.txt", 512),
            ],
            "cursor": "cursor123",
            "has_more": False,
        }

        mock_response = _make_mock_response(200, list_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            entries = await fs.list("")

        assert len(entries) == 1
        assert entries[0].name == "readme.txt"
        assert isinstance(entries[0], FileInfo)

    async def test_list_returns_dirs_and_files(self, fs):
        list_response = {
            "entries": [
                _make_file_entry("file.txt", "/file.txt", 100),
                _make_folder_entry("MyFolder", "/MyFolder"),
            ],
            "cursor": "cursor456",
            "has_more": False,
        }

        mock_response = _make_mock_response(200, list_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            entries = await fs.list("")

        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        assert len(dirs) == 1
        assert dirs[0].name == "MyFolder"
        assert len(files) == 1
        assert files[0].name == "file.txt"

    async def test_list_dirs_sorted_before_files(self, fs):
        list_response = {
            "entries": [
                _make_file_entry("alpha.txt", "/alpha.txt", 50),
                _make_folder_entry("Zeta", "/Zeta"),
                _make_file_entry("beta.txt", "/beta.txt", 60),
                _make_folder_entry("Alpha", "/Alpha"),
            ],
            "cursor": "cur",
            "has_more": False,
        }

        mock_response = _make_mock_response(200, list_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            entries = await fs.list("")

        dir_entries = [e for e in entries if e.is_dir]
        file_entries = [e for e in entries if not e.is_dir]
        if dir_entries and file_entries:
            last_dir_idx = max(entries.index(e) for e in dir_entries)
            first_file_idx = min(entries.index(e) for e in file_entries)
            assert last_dir_idx < first_file_idx

    async def test_list_paginates(self, fs):
        first_response = {
            "entries": [_make_file_entry("a.txt", "/a.txt")],
            "cursor": "cursor_page1",
            "has_more": True,
        }
        second_response = {
            "entries": [_make_file_entry("b.txt", "/b.txt")],
            "cursor": "cursor_page2",
            "has_more": False,
        }

        first_mock = _make_mock_response(200, first_response)
        second_mock = _make_mock_response(200, second_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=[first_mock, second_mock])
            mock_httpx.AsyncClient.return_value = mock_client

            entries = await fs.list("")

        assert len(entries) == 2
        names = {e.name for e in entries}
        assert "a.txt" in names
        assert "b.txt" in names

    # -----------------------------------------------------------------------
    # stat
    # -----------------------------------------------------------------------

    async def test_stat_returns_file_info(self, fs):
        metadata_response = _make_file_entry(
            "report.pdf", "/Documents/report.pdf", 2048
        )

        mock_response = _make_mock_response(200, metadata_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            info = await fs.stat("/Documents/report.pdf")

        assert isinstance(info, FileInfo)
        assert info.name == "report.pdf"
        assert info.size == 2048
        assert not info.is_dir
        assert info.path == "dropbox:///Documents/report.pdf"

    async def test_stat_folder_returns_is_dir(self, fs):
        metadata_response = _make_folder_entry("Documents", "/Documents")

        mock_response = _make_mock_response(200, metadata_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            info = await fs.stat("/Documents")

        assert info.is_dir
        assert info.size == 0

    async def test_stat_raises_file_not_found_on_409(self, fs):
        error_response = {
            "error_summary": "path/not_found/...",
            "error": {".tag": "path", "path": {".tag": "not_found"}},
        }
        mock_response = _make_mock_response(409, error_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            with pytest.raises(FileNotFoundError):
                await fs.stat("/nonexistent/file.txt")

    # -----------------------------------------------------------------------
    # read
    # -----------------------------------------------------------------------

    async def test_read_streams_content(self, fs):
        expected_chunks = [b"hello ", b"world"]

        async def fake_aiter_bytes(chunk_size=65536):
            for chunk in expected_chunks:
                yield chunk

        mock_stream_resp = MagicMock()
        mock_stream_resp.status_code = 200
        mock_stream_resp.aiter_bytes = fake_aiter_bytes
        mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_stream_resp.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream.return_value = mock_stream_resp
            mock_httpx.AsyncClient.return_value = mock_client

            collected = []
            async for chunk in fs.read("/Documents/report.pdf"):
                collected.append(chunk)

        assert b"".join(collected) == b"hello world"

    # -----------------------------------------------------------------------
    # write
    # -----------------------------------------------------------------------

    async def test_write_uploads_content(self, fs):
        mock_upload_resp = _make_mock_response(200, {
            ".tag": "file",
            "name": "out.txt",
            "path_display": "/out.txt",
            "id": "id:abc123",
        })

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_upload_resp)
            mock_httpx.AsyncClient.return_value = mock_client

            async with fs.write("/out.txt") as buf:
                buf.write(b"test content")

        # Verify post was called with the upload endpoint
        call_args = mock_client.post.call_args
        assert "files/upload" in call_args[0][0]

    async def test_write_sends_overwrite_mode(self, fs):
        mock_upload_resp = _make_mock_response(200, {"name": "file.bin"})

        captured_headers = {}

        async def capture_post(url, *, headers=None, content=None, **kwargs):
            captured_headers.update(headers or {})
            return mock_upload_resp

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = capture_post
            mock_httpx.AsyncClient.return_value = mock_client

            async with fs.write("/file.bin") as buf:
                buf.write(b"data")

        api_arg = json.loads(captured_headers.get("Dropbox-API-Arg", "{}"))
        assert api_arg.get("mode") == "overwrite"

    # -----------------------------------------------------------------------
    # exists
    # -----------------------------------------------------------------------

    async def test_exists_true_when_stat_succeeds(self, fs):
        metadata_response = _make_file_entry("hello.txt", "/hello.txt", 10)
        mock_response = _make_mock_response(200, metadata_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            result = await fs.exists("/hello.txt")

        assert result is True

    async def test_exists_false_on_path_not_found(self, fs):
        error_response = {
            "error_summary": "path/not_found/...",
            "error": {".tag": "path", "path": {".tag": "not_found"}},
        }
        mock_response = _make_mock_response(409, error_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            result = await fs.exists("/no/such/file.txt")

        assert result is False

    # -----------------------------------------------------------------------
    # delete
    # -----------------------------------------------------------------------

    async def test_delete_calls_correct_endpoint(self, fs):
        mock_response = _make_mock_response(200, {
            "metadata": _make_file_entry("gone.txt", "/gone.txt"),
        })

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            await fs.delete("/gone.txt")

        call_args = mock_client.post.call_args
        assert "files/delete_v2" in call_args[0][0]
        body = json.loads(call_args[1]["content"])
        assert body["path"] == "/gone.txt"

    async def test_delete_raises_file_not_found_on_409(self, fs):
        error_response = {
            "error_summary": "path_lookup/not_found/...",
            "error": {".tag": "path_lookup", "path_lookup": {".tag": "not_found"}},
        }
        mock_response = _make_mock_response(409, error_response)

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            with pytest.raises(FileNotFoundError):
                await fs.delete("/nonexistent.txt")

    # -----------------------------------------------------------------------
    # move
    # -----------------------------------------------------------------------

    async def test_move_calls_correct_endpoint(self, fs):
        mock_response = _make_mock_response(200, {
            "metadata": _make_file_entry("new.txt", "/dst/new.txt"),
        })

        with patch(
            "backend.storage.dropbox_backend.oauth_manager.get_token",
            new=AsyncMock(return_value="fake_token"),
        ), patch("backend.storage.dropbox_backend.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            await fs.move("/src/old.txt", "/dst/new.txt")

        call_args = mock_client.post.call_args
        assert "files/move_v2" in call_args[0][0]
        body = json.loads(call_args[1]["content"])
        assert body["from_path"] == "/src/old.txt"
        assert body["to_path"] == "/dst/new.txt"

    # -----------------------------------------------------------------------
    # _parse_metadata
    # -----------------------------------------------------------------------

    async def test_parse_metadata_file(self, fs):
        entry = _make_file_entry("doc.pdf", "/Docs/doc.pdf", 4096, "2024-06-01T08:00:00Z")
        info = await fs._parse_metadata(entry)

        assert info.name == "doc.pdf"
        assert info.path == "dropbox:///Docs/doc.pdf"
        assert info.size == 4096
        assert not info.is_dir
        assert info.modified_at.year == 2024
        assert info.modified_at.month == 6

    async def test_parse_metadata_folder(self, fs):
        entry = _make_folder_entry("Projects", "/Projects")
        info = await fs._parse_metadata(entry)

        assert info.name == "Projects"
        assert info.is_dir
        assert info.size == 0
        assert info.path == "dropbox:///Projects"

    async def test_parse_metadata_datetime_utc(self, fs):
        entry = _make_file_entry(modified="2021-01-01T12:00:00Z")
        info = await fs._parse_metadata(entry)

        assert info.modified_at == datetime(2021, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # -----------------------------------------------------------------------
    # Router registration
    # -----------------------------------------------------------------------

    def test_dropbox_scheme_registered(self):
        from backend.storage.abstract import filesystem_router
        assert "dropbox" in filesystem_router.registered_schemes()

    def test_dropbox_backend_resolves(self):
        from backend.storage.abstract import filesystem_router
        backend, path = filesystem_router.resolve("dropbox:///Documents/file.txt")
        assert backend.scheme == "dropbox"
        assert path == "/Documents/file.txt"
