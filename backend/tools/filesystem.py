"""
Filesystem Tool Suite

Tier 1 (no confirmation):
  read_file, list_directory, find_files, disk_usage

Tier 2 (soft confirmation):
  write_file, append_file, create_directory

Tier 3 (explicit confirmation):
  delete_file, move_file

All tools validate paths against hard-blocked patterns via the local
filesystem backend before performing any I/O.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)
from backend.storage.local import local_fs
from backend.tools.base import ToolBase

logger = logging.getLogger(__name__)

# Maximum characters returned from read_file to keep context window sane
_MAX_READ_CHARS = 100_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _path_output(path: str) -> str:
    """Expand ~ and resolve to absolute, as a string."""
    return str(Path(path).expanduser().resolve())


def _error_block(title: str, message: str) -> OutputBlock:
    return OutputBlock(
        type="notification",
        content={"level": "error", "title": title, "message": message},
    )


# ---------------------------------------------------------------------------
# Tier 1 — Read-only tools
# ---------------------------------------------------------------------------


class ReadFileTool(ToolBase):
    """Read a file's contents and return it as a code or text block."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="read_file",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description=(
                "Read the contents of a local file. "
                "Returns text for known text types, raw bytes info for binaries. "
                "Blocked paths (.env, private keys, etc.) are always rejected."
            ),
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params["path"]
        max_chars: int = params.get("max_chars", _MAX_READ_CHARS)

        try:
            content = await local_fs.read_text(path)
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except FileNotFoundError as e:
            return _error_block("File Not Found", str(e))
        except IsADirectoryError:
            return _error_block("Path Is a Directory", f"Use list_directory instead: {path}")
        except UnicodeDecodeError:
            return OutputBlock(
                type="text",
                content=f"[Binary file — cannot display as text: {_path_output(path)}]",
                metadata={"path": path, "binary": True},
            )
        except Exception as e:
            logger.error(f"read_file failed for {path}: {e}")
            return _error_block("Read Error", str(e))

        truncated = len(content) > max_chars
        display = content[:max_chars]

        # Detect language from extension for syntax highlighting
        ext = Path(path).suffix.lstrip(".")
        lang_map = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "sh": "bash", "yml": "yaml", "yaml": "yaml", "json": "json",
            "md": "markdown", "toml": "toml", "rs": "rust", "go": "go",
            "java": "java", "cpp": "cpp", "c": "c", "h": "c",
            "html": "html", "css": "css", "sql": "sql",
        }
        lang = lang_map.get(ext, "text")

        return OutputBlock(
            type="code",
            content=display,
            metadata={
                "path": _path_output(path),
                "language": lang,
                "truncated": truncated,
                "total_chars": len(content),
                "shown_chars": len(display),
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read (supports ~/... notation)",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Maximum characters to return (default {_MAX_READ_CHARS})",
                    "default": _MAX_READ_CHARS,
                },
            },
            "required": ["path"],
        }


class ListDirectoryTool(ToolBase):
    """List the contents of a directory as a table."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="list_directory",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="List directory contents. Returns a table of files and subdirectories.",
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params["path"]

        try:
            entries = await local_fs.list(path)
        except FileNotFoundError as e:
            return _error_block("Directory Not Found", str(e))
        except NotADirectoryError as e:
            return _error_block("Not a Directory", str(e))
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except Exception as e:
            logger.error(f"list_directory failed for {path}: {e}")
            return _error_block("List Error", str(e))

        if not entries:
            return OutputBlock(
                type="text",
                content=f"Directory is empty: {_path_output(path)}",
                metadata={"path": path},
            )

        columns = ["Name", "Type", "Size", "Modified"]
        rows = []
        for e in entries:
            kind = "dir" if e.is_dir else (e.mime_type or "file")
            size_str = _format_size(e.size) if not e.is_dir else "—"
            rows.append([
                e.name + ("/" if e.is_dir else ""),
                kind,
                size_str,
                e.modified_at.strftime("%Y-%m-%d %H:%M"),
            ])

        return OutputBlock(
            type="table",
            content={
                "columns": columns,
                "rows": rows,
                "metadata": {
                    "path": _path_output(path),
                    "entry_count": len(entries),
                },
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list",
                },
            },
            "required": ["path"],
        }


class FindFilesTool(ToolBase):
    """Search a directory tree for files matching a glob pattern."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="find_files",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description=(
                "Recursively find files matching a glob pattern. "
                "Pattern applies to file names only (e.g. '*.py', 'README*')."
            ),
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        root = params["root"]
        pattern = params.get("pattern", "*")
        max_results: int = params.get("max_results", 100)
        max_depth: int = params.get("max_depth", 10)

        try:
            entries = await local_fs.find_files(root, pattern, max_depth, max_results)
        except FileNotFoundError as e:
            return _error_block("Directory Not Found", str(e))
        except Exception as e:
            logger.error(f"find_files failed: {e}")
            return _error_block("Search Error", str(e))

        if not entries:
            return OutputBlock(
                type="text",
                content=f"No files matching '{pattern}' found under {_path_output(root)}",
                metadata={"root": root, "pattern": pattern},
            )

        columns = ["Path", "Size", "Modified"]
        rows = [
            [e.path, _format_size(e.size), e.modified_at.strftime("%Y-%m-%d %H:%M")]
            for e in entries
        ]

        return OutputBlock(
            type="table",
            content={
                "columns": columns,
                "rows": rows,
                "metadata": {
                    "root": _path_output(root),
                    "pattern": pattern,
                    "result_count": len(entries),
                    "capped": len(entries) >= max_results,
                },
            },
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Root directory to search"},
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern for file names (default '*')",
                    "default": "*",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default 100)",
                    "default": 100,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum directory depth (default 10)",
                    "default": 10,
                },
            },
            "required": ["root"],
        }


class DiskUsageTool(ToolBase):
    """Report disk usage for a path."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="disk_usage",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,
            description="Show disk usage statistics for a path or the overall filesystem.",
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params.get("path", "~")
        resolved = Path(path).expanduser().resolve()

        if not resolved.exists():
            return _error_block("Path Not Found", f"Does not exist: {resolved}")

        # Total/free/used for the filesystem
        usage = shutil.disk_usage(resolved)

        # Directory size (best-effort)
        dir_size: int = 0
        if resolved.is_dir():
            for dirpath, _dirs, files in os.walk(resolved, followlinks=False):
                for fname in files:
                    try:
                        dir_size += os.path.getsize(os.path.join(dirpath, fname))
                    except OSError:
                        pass
        else:
            dir_size = resolved.stat().st_size

        return OutputBlock(
            type="table",
            content={
                "columns": ["Metric", "Value"],
                "rows": [
                    ["Path", str(resolved)],
                    ["Path size", _format_size(dir_size)],
                    ["Filesystem total", _format_size(usage.total)],
                    ["Filesystem used", _format_size(usage.used)],
                    ["Filesystem free", _format_size(usage.free)],
                    ["Usage %", f"{usage.used / usage.total * 100:.1f}%"],
                ],
            },
            metadata={"path": str(resolved)},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to inspect (default: home directory)",
                    "default": "~",
                },
            },
            "required": [],
        }


# ---------------------------------------------------------------------------
# Tier 2 — Soft confirmation
# ---------------------------------------------------------------------------


class WriteFileTool(ToolBase):
    """Write content to a file (creates or overwrites)."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="write_file",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description=(
                "Write text content to a file. Creates the file if it does not exist; "
                "overwrites if it does. Parent directories are created automatically."
            ),
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params["path"]
        content = params["content"]
        encoding = params.get("encoding", "utf-8")

        try:
            await local_fs.write_text(path, content, encoding)
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except Exception as e:
            logger.error(f"write_file failed for {path}: {e}")
            return _error_block("Write Error", str(e))

        resolved = _path_output(path)
        size = len(content.encode(encoding))

        return OutputBlock(
            type="notification",
            content={
                "level": "success",
                "title": "File Written",
                "message": f"Wrote {_format_size(size)} to {resolved}",
            },
            metadata={"path": resolved, "bytes_written": size},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination file path"},
                "content": {"type": "string", "description": "Text content to write"},
                "encoding": {
                    "type": "string",
                    "description": "Text encoding (default utf-8)",
                    "default": "utf-8",
                },
            },
            "required": ["path", "content"],
        }


class AppendFileTool(ToolBase):
    """Append text to the end of an existing file (or create it)."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="append_file",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Append text to the end of a file. Creates the file if it does not exist.",
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params["path"]
        content = params["content"]
        encoding = params.get("encoding", "utf-8")

        try:
            await local_fs.append_text(path, content, encoding)
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except Exception as e:
            logger.error(f"append_file failed for {path}: {e}")
            return _error_block("Append Error", str(e))

        return OutputBlock(
            type="notification",
            content={
                "level": "success",
                "title": "Content Appended",
                "message": f"Appended {len(content.encode(encoding))} bytes to {_path_output(path)}",
            },
            metadata={"path": _path_output(path)},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Text to append"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path", "content"],
        }


class CreateDirectoryTool(ToolBase):
    """Create a directory (and any missing parents)."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="create_directory",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,
            description="Create a directory and all required parent directories.",
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params["path"]
        resolved = Path(path).expanduser().resolve()

        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except Exception as e:
            logger.error(f"create_directory failed for {path}: {e}")
            return _error_block("Create Error", str(e))

        return OutputBlock(
            type="notification",
            content={
                "level": "success",
                "title": "Directory Created",
                "message": str(resolved),
            },
            metadata={"path": str(resolved)},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to create"},
            },
            "required": ["path"],
        }


# ---------------------------------------------------------------------------
# Tier 3 — Explicit confirmation
# ---------------------------------------------------------------------------


class DeleteFileTool(ToolBase):
    """Delete a file (irreversible)."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="delete_file",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Permanently delete a file. This operation is irreversible. "
                "Use move_file to a trash directory if you want recovery."
            ),
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        path = params["path"]

        try:
            resolved_str = _path_output(path)
            await local_fs.delete(path)
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except FileNotFoundError as e:
            return _error_block("File Not Found", str(e))
        except Exception as e:
            logger.error(f"delete_file failed for {path}: {e}")
            return _error_block("Delete Error", str(e))

        return OutputBlock(
            type="notification",
            content={
                "level": "warning",
                "title": "File Deleted",
                "message": f"Permanently deleted: {resolved_str}",
            },
            metadata={"path": resolved_str},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to delete"},
            },
            "required": ["path"],
        }


class MoveFileTool(ToolBase):
    """Move or rename a file."""

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="move_file",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_3,
            description=(
                "Move or rename a file. If the destination already exists it will be "
                "overwritten. Parent directories for the destination are created automatically."
            ),
            allowed_paths=["local://~/**"],
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        src = params["src"]
        dst = params["dst"]

        try:
            src_r = _path_output(src)
            dst_r = _path_output(dst)
            await local_fs.move(src, dst)
        except PermissionError as e:
            return _error_block("Access Denied", str(e))
        except FileNotFoundError as e:
            return _error_block("File Not Found", str(e))
        except Exception as e:
            logger.error(f"move_file failed {src} → {dst}: {e}")
            return _error_block("Move Error", str(e))

        return OutputBlock(
            type="notification",
            content={
                "level": "success",
                "title": "File Moved",
                "message": f"{src_r} → {dst_r}",
            },
            metadata={"src": src_r, "dst": dst_r},
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Source file path"},
                "dst": {"type": "string", "description": "Destination file path"},
            },
            "required": ["src", "dst"],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size(size: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ---------------------------------------------------------------------------
# Exported instances
# ---------------------------------------------------------------------------

read_file_tool = ReadFileTool()
list_directory_tool = ListDirectoryTool()
find_files_tool = FindFilesTool()
disk_usage_tool = DiskUsageTool()
write_file_tool = WriteFileTool()
append_file_tool = AppendFileTool()
create_directory_tool = CreateDirectoryTool()
delete_file_tool = DeleteFileTool()
move_file_tool = MoveFileTool()

ALL_FILESYSTEM_TOOLS: List[ToolBase] = [
    read_file_tool,
    list_directory_tool,
    find_files_tool,
    disk_usage_tool,
    write_file_tool,
    append_file_tool,
    create_directory_tool,
    delete_file_tool,
    move_file_tool,
]
