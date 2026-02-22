"""
Tests for SFTPFileSystem backend.

asyncssh is mocked at the module level throughout TestSFTPFileSystem because
the native asyncssh package may not be importable in all environments. The
autouse fixture in TestSFTPFileSystem patches HAS_ASYNCSSH=True, injects a
stub asyncssh module, and pre-connects the SFTPFileSystem instance so every
test starts with a working (mocked) SFTP session.
"""

import stat as stat_module
import sys
import types
from datetime import datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.storage.sftp import SFTPConfig, SFTPFileSystem, create_sftp_backend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_attrs(size=100, mtime=None, is_dir=False):
    """Create a mock SFTPAttrs object."""
    attrs = MagicMock()
    attrs.size = size
    attrs.mtime = mtime or int(datetime(2024, 1, 1).timestamp())
    if is_dir:
        attrs.permissions = stat_module.S_IFDIR | 0o755
    else:
        attrs.permissions = stat_module.S_IFREG | 0o644
    return attrs


def _make_sftp_error_class():
    """Build a minimal SFTPError exception class for mocking."""
    class SFTPError(Exception):
        def __init__(self, code, reason=""):
            self.code = code
            self.reason = reason
            super().__init__(reason)
    return SFTPError


def _make_asyncssh_stub():
    """
    Return a stub module that provides the subset of asyncssh used by sftp.py
    and the tests (SFTPError, FX_NO_SUCH_FILE, connect).
    """
    stub = types.ModuleType("asyncssh")
    stub.SFTPError = _make_sftp_error_class()
    stub.FX_NO_SUCH_FILE = 2  # SFTP status code for "no such file"
    stub.connect = AsyncMock()
    return stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return SFTPConfig(host="myserver.com", username="user", password="pass")


@pytest.fixture
def fs(config):
    return SFTPFileSystem(config)


# ---------------------------------------------------------------------------
# SFTPConfig
# ---------------------------------------------------------------------------


class TestSFTPConfig:
    def test_default_port(self):
        c = SFTPConfig(host="server.com", username="user")
        assert c.port == 22

    def test_default_base_path(self):
        c = SFTPConfig(host="server.com", username="user")
        assert c.base_path == "/"

    def test_optional_password_defaults_none(self):
        c = SFTPConfig(host="server.com", username="user")
        assert c.password is None

    def test_optional_key_file_defaults_none(self):
        c = SFTPConfig(host="server.com", username="user")
        assert c.key_file is None

    def test_custom_port(self):
        c = SFTPConfig(host="server.com", username="user", port=2222)
        assert c.port == 2222


# ---------------------------------------------------------------------------
# SFTPFileSystem (pre-connected via autouse fixture)
# ---------------------------------------------------------------------------


class TestSFTPFileSystem:
    @pytest.fixture(autouse=True)
    def mock_asyncssh(self, fs):
        """
        Patch asyncssh at the backend module level with a stub, set
        HAS_ASYNCSSH=True, and pre-connect the fs instance so _ensure_connected
        is a no-op (self._sftp is not None).
        """
        self._asyncssh_stub = _make_asyncssh_stub()

        mock_sftp = AsyncMock()
        # exit() is a regular synchronous call in asyncssh's SFTPClient
        mock_sftp.exit = MagicMock()
        mock_conn = MagicMock()

        fs._sftp = mock_sftp
        fs._conn = mock_conn
        self.mock_sftp = mock_sftp
        self.mock_conn = mock_conn

        with patch("backend.storage.sftp.HAS_ASYNCSSH", True), \
             patch("backend.storage.sftp.asyncssh", self._asyncssh_stub):
            yield

    # ------------------------------------------------------------------
    # scheme
    # ------------------------------------------------------------------

    def test_scheme(self, fs):
        assert fs.scheme == "sftp"

    # ------------------------------------------------------------------
    # _full_path
    # ------------------------------------------------------------------

    async def test_full_path_combines_base_and_relative(self, fs):
        assert fs._full_path("docs/file.txt") == "/docs/file.txt"

    async def test_full_path_strips_leading_slash_from_rel(self, fs):
        assert fs._full_path("/docs/file.txt") == "/docs/file.txt"

    async def test_full_path_base_only_when_empty_rel(self, fs):
        assert fs._full_path("") == "/"

    async def test_full_path_custom_base(self, config):
        config.base_path = "/srv/data"
        custom_fs = SFTPFileSystem(config)
        assert custom_fs._full_path("report.pdf") == "/srv/data/report.pdf"

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def test_list_returns_entries(self, fs):
        entry1 = MagicMock()
        entry1.filename = "file.txt"
        entry1.attrs = make_attrs(size=200)

        entry2 = MagicMock()
        entry2.filename = "subdir"
        entry2.attrs = make_attrs(is_dir=True)

        dot = MagicMock()
        dot.filename = "."
        dotdot = MagicMock()
        dotdot.filename = ".."

        self.mock_sftp.readdir.return_value = [dot, dotdot, entry1, entry2]
        result = await fs.list("/")
        names = [e.name for e in result]
        assert "file.txt" in names
        assert "subdir" in names
        assert "." not in names
        assert ".." not in names

    async def test_list_dirs_come_first(self, fs):
        dir_entry = MagicMock()
        dir_entry.filename = "mydir"
        dir_entry.attrs = make_attrs(is_dir=True)

        file_entry = MagicMock()
        file_entry.filename = "file.py"
        file_entry.attrs = make_attrs()

        self.mock_sftp.readdir.return_value = [file_entry, dir_entry]
        result = await fs.list("/")
        assert result[0].is_dir
        assert not result[1].is_dir

    async def test_list_is_dir_flag(self, fs):
        dir_entry = MagicMock()
        dir_entry.filename = "mydir"
        dir_entry.attrs = make_attrs(is_dir=True)
        self.mock_sftp.readdir.return_value = [dir_entry]
        result = await fs.list("/")
        assert result[0].is_dir is True

    async def test_list_file_size(self, fs):
        file_entry = MagicMock()
        file_entry.filename = "data.bin"
        file_entry.attrs = make_attrs(size=12345)
        self.mock_sftp.readdir.return_value = [file_entry]
        result = await fs.list("/")
        assert result[0].size == 12345

    async def test_list_path_uses_sftp_scheme(self, fs):
        file_entry = MagicMock()
        file_entry.filename = "readme.md"
        file_entry.attrs = make_attrs()
        self.mock_sftp.readdir.return_value = [file_entry]
        result = await fs.list("/")
        assert result[0].path.startswith("sftp://")

    # ------------------------------------------------------------------
    # stat
    # ------------------------------------------------------------------

    async def test_stat_returns_file_info(self, fs):
        self.mock_sftp.stat.return_value = make_attrs(size=500)
        info = await fs.stat("/file.txt")
        assert info.size == 500

    async def test_stat_not_found(self, fs):
        self.mock_sftp.stat.side_effect = self._asyncssh_stub.SFTPError(
            self._asyncssh_stub.FX_NO_SUCH_FILE, "not found"
        )
        with pytest.raises(FileNotFoundError):
            await fs.stat("/ghost.txt")

    async def test_stat_returns_correct_name(self, fs):
        self.mock_sftp.stat.return_value = make_attrs(size=100)
        info = await fs.stat("/some/path/file.txt")
        assert info.name == "file.txt"

    async def test_stat_path_uses_sftp_scheme(self, fs):
        self.mock_sftp.stat.return_value = make_attrs(size=1)
        info = await fs.stat("/file.txt")
        assert info.path.startswith("sftp://")

    # ------------------------------------------------------------------
    # exists
    # ------------------------------------------------------------------

    async def test_exists_true(self, fs):
        self.mock_sftp.stat.return_value = make_attrs()
        assert await fs.exists("/file.txt") is True

    async def test_exists_false(self, fs):
        self.mock_sftp.stat.side_effect = self._asyncssh_stub.SFTPError(
            self._asyncssh_stub.FX_NO_SUCH_FILE, "not found"
        )
        assert await fs.exists("/ghost.txt") is False

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    async def test_read_yields_chunks(self, fs):
        mock_file = AsyncMock()
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=False)
        mock_file.read.side_effect = [b"data", b""]
        self.mock_sftp.open.return_value = mock_file

        chunks = []
        async for chunk in fs.read("/file.txt"):
            chunks.append(chunk)
        assert b"data" in chunks

    async def test_read_stops_on_empty_chunk(self, fs):
        mock_file = AsyncMock()
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=False)
        mock_file.read.side_effect = [b"chunk1", b"chunk2", b""]
        self.mock_sftp.open.return_value = mock_file

        chunks = []
        async for chunk in fs.read("/file.txt"):
            chunks.append(chunk)
        assert chunks == [b"chunk1", b"chunk2"]

    async def test_read_opens_file_in_rb_mode(self, fs):
        mock_file = AsyncMock()
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=False)
        mock_file.read.side_effect = [b""]
        self.mock_sftp.open.return_value = mock_file

        async for _ in fs.read("/file.txt"):
            pass
        self.mock_sftp.open.assert_called_once()
        # Verify "rb" appears in positional args
        call_args = self.mock_sftp.open.call_args
        assert "rb" in call_args[0]

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    async def test_delete_calls_remove(self, fs):
        self.mock_sftp.remove.return_value = None
        await fs.delete("/file.txt")
        self.mock_sftp.remove.assert_called_once()

    async def test_delete_falls_back_to_rmdir(self, fs):
        self.mock_sftp.remove.side_effect = self._asyncssh_stub.SFTPError(
            self._asyncssh_stub.FX_NO_SUCH_FILE, "not a file"
        )
        self.mock_sftp.rmdir.return_value = None
        await fs.delete("/mydir")
        self.mock_sftp.rmdir.assert_called_once()

    async def test_delete_raises_file_not_found_when_both_fail(self, fs):
        self.mock_sftp.remove.side_effect = self._asyncssh_stub.SFTPError(
            self._asyncssh_stub.FX_NO_SUCH_FILE, "not found"
        )
        self.mock_sftp.rmdir.side_effect = self._asyncssh_stub.SFTPError(
            self._asyncssh_stub.FX_NO_SUCH_FILE, "not found"
        )
        with pytest.raises(FileNotFoundError):
            await fs.delete("/ghost")

    # ------------------------------------------------------------------
    # move
    # ------------------------------------------------------------------

    async def test_move_calls_rename(self, fs):
        self.mock_sftp.rename.return_value = None
        await fs.move("/src.txt", "/dst.txt")
        self.mock_sftp.rename.assert_called_once()

    async def test_move_passes_correct_full_paths(self, fs):
        self.mock_sftp.rename.return_value = None
        await fs.move("/src.txt", "/dst.txt")
        call_args = self.mock_sftp.rename.call_args[0]
        assert call_args[0] == "/src.txt"
        assert call_args[1] == "/dst.txt"

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------

    async def test_close_clears_sftp_and_conn(self, fs):
        await fs.close()
        assert fs._sftp is None
        assert fs._conn is None

    async def test_close_calls_sftp_exit(self, fs):
        sftp_mock = self.mock_sftp
        await fs.close()
        sftp_mock.exit.assert_called_once()

    # ------------------------------------------------------------------
    # _get_lock (lazily created)
    # ------------------------------------------------------------------

    def test_get_lock_creates_lock(self, fs):
        import asyncio
        # _lock starts as None (before first call)
        fs._lock = None
        lock = fs._get_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_get_lock_returns_same_instance(self, fs):
        lock1 = fs._get_lock()
        lock2 = fs._get_lock()
        assert lock1 is lock2


# ---------------------------------------------------------------------------
# ImportError when asyncssh is not installed
# ---------------------------------------------------------------------------


class TestSFTPImportError:
    async def test_raises_import_error_if_no_asyncssh(self):
        with patch("backend.storage.sftp.HAS_ASYNCSSH", False):
            with patch("backend.storage.sftp.asyncssh", None):
                fs = SFTPFileSystem(SFTPConfig(host="h", username="u"))
                with pytest.raises(ImportError, match="asyncssh"):
                    await fs._ensure_connected()


# ---------------------------------------------------------------------------
# create_sftp_backend
# ---------------------------------------------------------------------------


class TestCreateSFTPBackend:
    def test_create_registers_backend(self):
        with patch("backend.storage.abstract.filesystem_router") as mock_router:
            backend = create_sftp_backend(SFTPConfig(host="h", username="u"))
            mock_router.register.assert_called_once_with(backend)

    def test_create_returns_sftp_filesystem_instance(self):
        with patch("backend.storage.abstract.filesystem_router"):
            backend = create_sftp_backend(SFTPConfig(host="h", username="u"))
            assert isinstance(backend, SFTPFileSystem)

    def test_create_sets_module_singleton(self):
        import backend.storage.sftp as sftp_module
        with patch("backend.storage.abstract.filesystem_router"):
            backend = create_sftp_backend(SFTPConfig(host="h", username="u"))
            assert sftp_module._sftp_instance is backend

    def test_create_uses_provided_config(self):
        with patch("backend.storage.abstract.filesystem_router"):
            cfg = SFTPConfig(host="example.com", username="alice", port=2222)
            backend = create_sftp_backend(cfg)
            assert backend._config.host == "example.com"
            assert backend._config.port == 2222
