"""
Assistant Backup Job — P8-07

Backs up assistant data (conversations, memories, audit log) to any
registered storage backend (local, S3, GDrive, Dropbox, SFTP, …).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class BackupItem:
    """A single data item to include in a backup archive."""
    name: str            # e.g. "conversations", "audit_log", "memories"
    content_type: str    # e.g. "application/json", "text/plain"
    data: Any            # Must be JSON-serialisable


@dataclass
class BackupManifest:
    """Metadata describing a completed backup."""
    destination_uri: str
    items: List[str]     # Names of backed-up items (same order as written)
    created_at: datetime
    checksum: str        # SHA-256 hex digest of the serialised payload
    total_bytes: int = 0


class AssistantBackupJob:
    """
    Collects assistant data items and writes them to a storage destination.

    Usage::

        job = AssistantBackupJob()
        job.add_item(BackupItem("conversations", "application/json", conv_data))
        job.add_item(BackupItem("memories",      "application/json", mem_data))
        manifest = await job.run("s3://my-bucket/backups/2025-01-15.json")
    """

    def __init__(self) -> None:
        self._items: List[BackupItem] = []

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def add_item(self, item: BackupItem) -> None:
        """Register a data item to include in the next backup run."""
        self._items.append(item)

    def clear_items(self) -> None:
        """Remove all pending backup items (useful between runs)."""
        self._items.clear()

    @property
    def item_count(self) -> int:
        """Number of items currently registered."""
        return len(self._items)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _serialize(self) -> bytes:
        """Return the full backup payload as UTF-8-encoded JSON."""
        payload: Dict[str, Any] = {
            "created_at": datetime.utcnow().isoformat(),
            "items": {
                item.name: {
                    "content_type": item.content_type,
                    "data": item.data,
                }
                for item in self._items
            },
        }
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(
        self,
        destination_uri: str,
        storage=None,
    ) -> BackupManifest:
        """
        Serialise all registered items and write them to *destination_uri*.

        Args:
            destination_uri: Target URI understood by the FileSystemRouter,
                             e.g. ``"local:///tmp/backup.json"`` or
                             ``"s3://bucket/prefix/backup.json"``.
            storage:         Optional :class:`~backend.storage.abstract.FileSystemRouter`
                             for dependency injection.  Defaults to the
                             module-level singleton.

        Returns:
            :class:`BackupManifest` describing the completed backup.
        """
        payload = self._serialize()
        checksum = hashlib.sha256(payload).hexdigest()

        if storage is None:
            from backend.storage.abstract import filesystem_router as _router
            storage = _router

        backend, path = storage.resolve(destination_uri)
        async with await backend.write(path) as fh:
            fh.write(payload)

        return BackupManifest(
            destination_uri=destination_uri,
            items=[item.name for item in self._items],
            created_at=datetime.utcnow(),
            checksum=checksum,
            total_bytes=len(payload),
        )

    # ------------------------------------------------------------------
    # Restore helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_payload(raw: str) -> Dict[str, Any]:
        """
        Parse a raw backup JSON string.

        Returns a dict with keys ``created_at`` (str) and ``items`` (dict).
        """
        return json.loads(raw)

    @staticmethod
    def manifest_from_payload(raw: str, destination_uri: str = "") -> BackupManifest:
        """
        Reconstruct a :class:`BackupManifest` from a serialised backup payload.

        Useful for validating a downloaded backup or listing its contents.
        """
        data = json.loads(raw)
        items = list(data.get("items", {}).keys())
        created_at = datetime.fromisoformat(data["created_at"])
        payload_bytes = raw.encode("utf-8")
        checksum = hashlib.sha256(payload_bytes).hexdigest()
        return BackupManifest(
            destination_uri=destination_uri,
            items=items,
            created_at=created_at,
            checksum=checksum,
            total_bytes=len(payload_bytes),
        )


# Module-level singleton — pre-configured with no items
backup_job = AssistantBackupJob()
