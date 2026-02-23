"""
Tests for backend/storage/router.py — P4-01/P4-02

Covers:
  - SecuredFileSystemRouter: never_read blocklist enforcement
  - _check_never_read: pattern matching logic
  - _build_router: backend registration
  - file_router singleton: registered schemes
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.storage.router import (
    SecuredFileSystemRouter,
    _check_never_read,
    file_router,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(scheme: str):
    """Return a MagicMock that looks like a FileSystem backend."""
    backend = MagicMock()
    backend.scheme = scheme
    backend.read = AsyncMock(return_value=aiter([b"chunk"]))
    backend.write = AsyncMock()
    backend.delete = AsyncMock()
    backend.move = AsyncMock()
    backend.exists = AsyncMock(return_value=True)
    backend.stat = AsyncMock()
    backend.list = AsyncMock(return_value=[])
    return backend


async def aiter(items):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# _check_never_read
# ---------------------------------------------------------------------------


class TestCheckNeverRead:
    def test_env_file_blocked(self):
        with pytest.raises(PermissionError, match="never_read"):
            _check_never_read("/home/user/.env", ["**/.env"])

    def test_pem_blocked_by_extension(self):
        with pytest.raises(PermissionError):
            _check_never_read("/home/user/cert.pem", ["**/*.pem"])

    def test_key_file_blocked(self):
        with pytest.raises(PermissionError):
            _check_never_read("/home/user/server.key", ["**/*.key"])

    def test_id_rsa_blocked(self):
        with pytest.raises(PermissionError):
            _check_never_read("/home/user/.ssh/id_rsa", ["**/id_rsa*"])

    def test_aws_credentials_blocked(self):
        with pytest.raises(PermissionError):
            _check_never_read("/home/user/.aws/credentials", ["**/.aws"])

    def test_safe_file_not_blocked(self):
        # Should not raise
        _check_never_read("/home/user/Documents/report.txt", ["**/.env", "**/*.pem"])

    def test_python_file_not_blocked(self):
        _check_never_read("/home/user/projects/main.py", ["**/.env", "**/*.pem", "**/*.key"])

    def test_name_only_match(self):
        """Filename-only pattern match works even without full path."""
        with pytest.raises(PermissionError):
            _check_never_read(".env", ["**/.env"])

    def test_empty_patterns_allow_all(self):
        """No patterns → nothing is blocked."""
        _check_never_read("/home/user/.env", [])


# ---------------------------------------------------------------------------
# SecuredFileSystemRouter — blocklist enforcement
# ---------------------------------------------------------------------------


class TestSecuredFileSystemRouter:
    def setup_method(self):
        self.router = SecuredFileSystemRouter(never_read_patterns=["**/.env", "**/*.key"])
        self.backend = _make_backend("local")
        self.router.register(self.backend)

    @pytest.mark.asyncio
    async def test_read_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.read("local://.env")

    @pytest.mark.asyncio
    async def test_write_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.write("local://server.key")

    @pytest.mark.asyncio
    async def test_delete_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.delete("local://.env")

    @pytest.mark.asyncio
    async def test_move_src_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.move("local://.env", "local://backup.txt")

    @pytest.mark.asyncio
    async def test_move_dst_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.move("local://source.txt", "local://.env")

    @pytest.mark.asyncio
    async def test_exists_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.exists("local://.env")

    @pytest.mark.asyncio
    async def test_stat_blocked(self):
        with pytest.raises(PermissionError):
            await self.router.stat("local://server.key")

    @pytest.mark.asyncio
    async def test_list_not_blocked(self):
        """list() is allowed — individual file reads are blocked separately."""
        result = await self.router.list("local:///home/user/Documents")
        self.backend.list.assert_called_once()
        assert result == []

    @pytest.mark.asyncio
    async def test_safe_read_passes_through(self):
        self.backend.read = MagicMock(return_value=aiter([b"data"]))
        result = await self.router.read("local:///home/user/Documents/report.txt")
        self.backend.read.assert_called_once_with("/home/user/Documents/report.txt")

    def test_unregistered_scheme_raises(self):
        with pytest.raises(ValueError, match="No backend registered"):
            self.router.resolve("gdrive://My Drive/file.txt")

    @pytest.mark.asyncio
    async def test_cross_backend_move_rejected(self):
        gdrive = _make_backend("gdrive")
        self.router.register(gdrive)
        with pytest.raises(ValueError, match="Cross-backend"):
            await self.router.move("local:///a.txt", "gdrive://b.txt")


# ---------------------------------------------------------------------------
# file_router singleton
# ---------------------------------------------------------------------------


class TestFileRouterSingleton:
    def test_local_scheme_registered(self):
        assert "local" in file_router.registered_schemes()

    def test_never_read_patterns_populated(self):
        assert len(file_router._never_read_patterns) > 0

    @pytest.mark.asyncio
    async def test_env_blocked_by_singleton(self):
        with pytest.raises(PermissionError):
            await file_router.read("local://.env")

    @pytest.mark.asyncio
    async def test_pem_blocked_by_singleton(self):
        with pytest.raises(PermissionError):
            await file_router.read("local://cert.pem")
