"""
Cloud Storage Tool Suite

Provides a unified tool interface for browsing and managing cloud storage
across all registered backends (Google Drive, Dropbox, S3, SFTP).

All paths use URI scheme routing:
    gdrive://My Drive/Documents/
    dropbox://Personal/
    s3://my-bucket/backups/
    sftp://myserver.com/var/www/
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.storage.abstract import filesystem_router, FileInfo
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extension → language map for syntax highlighting
# ---------------------------------------------------------------------------

_EXT_LANGUAGE = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "jsx", ".tsx": "tsx", ".json": "json", ".yaml": "yaml",
    ".yml": "yaml", ".md": "markdown", ".html": "html", ".css": "css",
    ".sh": "bash", ".sql": "sql", ".rs": "rust", ".go": "go",
    ".java": "java", ".c": "c", ".cpp": "cpp", ".rb": "ruby",
}

# Maximum characters returned from cloud_read by default
_DEFAULT_MAX_CHARS = 50_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size(size: int) -> str:
    """Format byte size as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ---------------------------------------------------------------------------
# Tier 1 — Read-only cloud tools
# ---------------------------------------------------------------------------


class CloudListTool(ToolBase):
    """List contents of a cloud storage path."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="cloud_list",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="List the contents of a cloud storage path (gdrive://, dropbox://, s3://, sftp://)",
            requires_network=True,
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Cloud storage URI, e.g. 'gdrive://My Drive/' or 'dropbox://Personal/'",
                }
            },
            "required": ["uri"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        uri = params.get("uri", "")
        try:
            entries = await filesystem_router.list(uri)
        except ValueError as e:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": str(e)},
            )
        except Exception as e:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to list {uri}: {e}"},
            )

        if not entries:
            return OutputBlock(type="text", content=f"Directory is empty: {uri}")

        # Build table
        rows = []
        for entry in entries:
            type_str = "dir" if entry.is_dir else "file"
            size_str = _format_size(entry.size) if not entry.is_dir else ""
            modified_str = entry.modified_at.strftime("%Y-%m-%d %H:%M") if entry.modified_at else ""
            name_str = (entry.name + "/") if entry.is_dir else entry.name
            rows.append([name_str, type_str, size_str, modified_str])

        return OutputBlock(
            type="table",
            content={"headers": ["Name", "Type", "Size", "Modified"], "rows": rows},
            metadata={"uri": uri, "count": len(entries)},
        )


class CloudReadTool(ToolBase):
    """Read the contents of a cloud storage file."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="cloud_read",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Read the contents of a file from cloud storage (gdrive://, dropbox://, s3://, sftp://)",
            requires_network=True,
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Cloud storage URI of the file to read",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Maximum characters to return (default {_DEFAULT_MAX_CHARS})",
                    "default": _DEFAULT_MAX_CHARS,
                },
            },
            "required": ["uri"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        uri = params.get("uri", "")
        max_chars: int = params.get("max_chars", _DEFAULT_MAX_CHARS)

        try:
            backend, path = filesystem_router.resolve(uri)
            chunks = []
            total = 0
            async for chunk in backend.read(path):
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_chars:
                    break
        except ValueError as e:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": str(e)},
            )
        except Exception as e:
            logger.error(f"cloud_read failed for {uri}: {e}")
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to read {uri}: {e}"},
            )

        raw = b"".join(chunks)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("latin-1", errors="replace")

        truncated = len(content) > max_chars
        display = content[:max_chars]

        ext = Path(uri.split("://", 1)[-1]).suffix.lower()
        lang = _EXT_LANGUAGE.get(ext, "text")

        return OutputBlock(
            type="code",
            content=display,
            metadata={"language": lang, "truncated": truncated, "uri": uri},
        )


# ---------------------------------------------------------------------------
# Tier 2 — Cloud write tools (soft confirmation)
# ---------------------------------------------------------------------------


class CloudUploadTool(ToolBase):
    """Upload a local file to cloud storage."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="cloud_upload",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Upload a local file to a cloud storage URI (gdrive://, dropbox://, s3://, sftp://)",
            requires_network=True,
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "local_path": {
                    "type": "string",
                    "description": "Local filesystem path of the file to upload",
                },
                "cloud_uri": {
                    "type": "string",
                    "description": "Destination cloud storage URI",
                },
            },
            "required": ["local_path", "cloud_uri"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        local_path = params.get("local_path", "")
        cloud_uri = params.get("cloud_uri", "")

        try:
            with open(local_path, "rb") as local_fh:
                data = local_fh.read()
        except FileNotFoundError:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Local file not found: {local_path}"},
            )
        except Exception as e:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to read local file {local_path}: {e}"},
            )

        try:
            ctx = await filesystem_router.write(cloud_uri)
            async with ctx as fh:
                fh.write(data)
        except Exception as e:
            logger.error(f"cloud_upload failed {local_path} → {cloud_uri}: {e}")
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to upload to {cloud_uri}: {e}"},
            )

        return OutputBlock(
            type="notification",
            content={"level": "success", "message": f"Uploaded {local_path} to {cloud_uri}"},
        )


class CloudDownloadTool(ToolBase):
    """Download a file from cloud storage to a local path."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="cloud_download",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Download a file from cloud storage to a local path",
            requires_network=True,
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "cloud_uri": {
                    "type": "string",
                    "description": "Source cloud storage URI",
                },
                "local_path": {
                    "type": "string",
                    "description": "Destination local filesystem path",
                },
            },
            "required": ["cloud_uri", "local_path"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        cloud_uri = params.get("cloud_uri", "")
        local_path = params.get("local_path", "")

        try:
            backend, path = filesystem_router.resolve(cloud_uri)
            chunks = []
            async for chunk in backend.read(path):
                chunks.append(chunk)
            data = b"".join(chunks)
        except ValueError as e:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": str(e)},
            )
        except Exception as e:
            logger.error(f"cloud_download read failed for {cloud_uri}: {e}")
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to read {cloud_uri}: {e}"},
            )

        try:
            dest = Path(local_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        except Exception as e:
            logger.error(f"cloud_download write failed to {local_path}: {e}")
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to write to {local_path}: {e}"},
            )

        return OutputBlock(
            type="notification",
            content={"level": "success", "message": f"Downloaded {cloud_uri} to {local_path}"},
        )


# ---------------------------------------------------------------------------
# Tier 3 — Destructive cloud tools (explicit confirmation)
# ---------------------------------------------------------------------------


class CloudDeleteTool(ToolBase):
    """Delete a file or directory from cloud storage."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="cloud_delete",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Permanently delete a file or directory from cloud storage. "
                "This operation is irreversible."
            ),
            requires_network=True,
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Cloud storage URI of the file or directory to delete",
                },
            },
            "required": ["uri"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        uri = params.get("uri", "")
        try:
            await filesystem_router.delete(uri)
        except Exception as e:
            logger.error(f"cloud_delete failed for {uri}: {e}")
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to delete {uri}: {e}"},
            )

        return OutputBlock(
            type="notification",
            content={"level": "warning", "message": f"Deleted: {uri}"},
        )


class CloudMoveTool(ToolBase):
    """Move or rename a file within cloud storage."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="cloud_move",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Move or rename a file within the same cloud storage backend. "
                "Cross-backend moves are not supported."
            ),
            requires_network=True,
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "src_uri": {
                    "type": "string",
                    "description": "Source cloud storage URI",
                },
                "dst_uri": {
                    "type": "string",
                    "description": "Destination cloud storage URI",
                },
            },
            "required": ["src_uri", "dst_uri"],
        }

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        src_uri = params.get("src_uri", "")
        dst_uri = params.get("dst_uri", "")
        try:
            await filesystem_router.move(src_uri, dst_uri)
        except ValueError as e:
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": str(e)},
            )
        except Exception as e:
            logger.error(f"cloud_move failed {src_uri} → {dst_uri}: {e}")
            return OutputBlock(
                type="notification",
                content={"level": "error", "message": f"Failed to move {src_uri} to {dst_uri}: {e}"},
            )

        return OutputBlock(
            type="notification",
            content={"level": "success", "message": f"Moved {src_uri} to {dst_uri}"},
        )


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

ALL_CLOUD_STORAGE_TOOLS: List[ToolBase] = [
    CloudListTool(),
    CloudReadTool(),
    CloudUploadTool(),
    CloudDownloadTool(),
    CloudDeleteTool(),
    CloudMoveTool(),
]
