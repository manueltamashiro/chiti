"""Tests for the assistant backup job — P8-07."""

import hashlib
import json
import io
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.backup.job import (
    AssistantBackupJob,
    BackupItem,
    BackupManifest,
    backup_job,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage(captured: dict):
    """
    Return a minimal mock FileSystemRouter that captures what was written.

    The mock's `resolve` method returns a fake backend whose `write` is an
    async context manager that writes to a BytesIO buffer.
    """
    buf = io.BytesIO()

    @asynccontextmanager
    async def _write_cm(_path: str):
        yield buf

    fake_backend = MagicMock()
    fake_backend.write = AsyncMock(side_effect=lambda p: _write_cm(p))

    fake_router = MagicMock()
    fake_router.resolve.return_value = (fake_backend, "/tmp/backup.json")

    captured["buf"] = buf
    captured["backend"] = fake_backend
    return fake_router


# ---------------------------------------------------------------------------
# BackupItem
# ---------------------------------------------------------------------------


class TestBackupItem:
    def test_item_has_name(self):
        item = BackupItem(name="conversations", content_type="application/json", data=[])
        assert item.name == "conversations"

    def test_item_has_content_type(self):
        item = BackupItem(name="memories", content_type="application/json", data={})
        assert item.content_type == "application/json"

    def test_item_stores_data(self):
        data = {"key": "value"}
        item = BackupItem(name="config", content_type="application/json", data=data)
        assert item.data == data


# ---------------------------------------------------------------------------
# AssistantBackupJob — item management
# ---------------------------------------------------------------------------


class TestItemManagement:
    def test_add_item_increases_count(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("a", "application/json", {}))
        assert job.item_count == 1

    def test_add_multiple_items(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("a", "application/json", {}))
        job.add_item(BackupItem("b", "application/json", {}))
        assert job.item_count == 2

    def test_clear_items_resets_count(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("a", "application/json", {}))
        job.clear_items()
        assert job.item_count == 0

    def test_initial_item_count_is_zero(self):
        job = AssistantBackupJob()
        assert job.item_count == 0


# ---------------------------------------------------------------------------
# AssistantBackupJob — serialisation
# ---------------------------------------------------------------------------


class TestSerialisation:
    def test_serialize_returns_bytes(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("x", "application/json", {"n": 1}))
        raw = job._serialize()
        assert isinstance(raw, bytes)

    def test_serialize_is_valid_json(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("x", "application/json", {"n": 1}))
        raw = job._serialize()
        data = json.loads(raw)
        assert "items" in data

    def test_serialize_includes_item_name(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("conversations", "application/json", []))
        raw = job._serialize()
        data = json.loads(raw)
        assert "conversations" in data["items"]

    def test_serialize_includes_created_at(self):
        job = AssistantBackupJob()
        raw = job._serialize()
        data = json.loads(raw)
        assert "created_at" in data


# ---------------------------------------------------------------------------
# AssistantBackupJob — run()
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_run_returns_manifest(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("audit_log", "application/json", []))
        captured: dict = {}
        storage = _make_storage(captured)
        manifest = await job.run("local:///tmp/backup.json", storage=storage)
        assert isinstance(manifest, BackupManifest)

    @pytest.mark.asyncio
    async def test_manifest_has_destination_uri(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("a", "application/json", {}))
        captured: dict = {}
        manifest = await job.run("local:///tmp/backup.json", storage=_make_storage(captured))
        assert manifest.destination_uri == "local:///tmp/backup.json"

    @pytest.mark.asyncio
    async def test_manifest_lists_item_names(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("conversations", "application/json", []))
        job.add_item(BackupItem("memories", "application/json", {}))
        captured: dict = {}
        manifest = await job.run("local:///tmp/backup.json", storage=_make_storage(captured))
        assert "conversations" in manifest.items
        assert "memories" in manifest.items

    @pytest.mark.asyncio
    async def test_manifest_has_created_at(self):
        job = AssistantBackupJob()
        captured: dict = {}
        manifest = await job.run("local:///tmp/backup.json", storage=_make_storage(captured))
        assert isinstance(manifest.created_at, datetime)

    @pytest.mark.asyncio
    async def test_manifest_has_checksum(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("x", "application/json", 42))
        captured: dict = {}
        manifest = await job.run("local:///tmp/backup.json", storage=_make_storage(captured))
        assert len(manifest.checksum) == 64  # SHA-256 hex digest

    @pytest.mark.asyncio
    async def test_manifest_total_bytes_positive(self):
        job = AssistantBackupJob()
        job.add_item(BackupItem("x", "application/json", {"big": "data"}))
        captured: dict = {}
        manifest = await job.run("local:///tmp/backup.json", storage=_make_storage(captured))
        assert manifest.total_bytes > 0

    @pytest.mark.asyncio
    async def test_checksum_is_64_char_hex(self):
        """Checksum is a valid SHA-256 hex digest (64 hex chars)."""
        job = AssistantBackupJob()
        job.add_item(BackupItem("audit_log", "application/json", [1, 2, 3]))
        captured: dict = {}
        manifest = await job.run("local:///tmp/backup.json", storage=_make_storage(captured))
        assert len(manifest.checksum) == 64
        assert all(c in "0123456789abcdef" for c in manifest.checksum)


# ---------------------------------------------------------------------------
# manifest_from_payload (restore helper)
# ---------------------------------------------------------------------------


class TestManifestFromPayload:
    def _make_payload(self) -> str:
        job = AssistantBackupJob()
        job.add_item(BackupItem("conversations", "application/json", [{"role": "user"}]))
        job.add_item(BackupItem("memories", "application/json", {"count": 5}))
        return job._serialize().decode("utf-8")

    def test_parses_item_names(self):
        raw = self._make_payload()
        manifest = AssistantBackupJob.manifest_from_payload(raw)
        assert "conversations" in manifest.items
        assert "memories" in manifest.items

    def test_parses_created_at(self):
        raw = self._make_payload()
        manifest = AssistantBackupJob.manifest_from_payload(raw)
        assert isinstance(manifest.created_at, datetime)

    def test_checksum_matches_raw_bytes(self):
        raw = self._make_payload()
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        manifest = AssistantBackupJob.manifest_from_payload(raw)
        assert manifest.checksum == expected

    def test_destination_uri_defaults_empty(self):
        raw = self._make_payload()
        manifest = AssistantBackupJob.manifest_from_payload(raw)
        assert manifest.destination_uri == ""

    def test_destination_uri_can_be_set(self):
        raw = self._make_payload()
        manifest = AssistantBackupJob.manifest_from_payload(raw, destination_uri="s3://bucket/key")
        assert manifest.destination_uri == "s3://bucket/key"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_singleton_exists(self):
        assert backup_job is not None

    def test_singleton_is_assistant_backup_job(self):
        assert isinstance(backup_job, AssistantBackupJob)
