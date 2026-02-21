"""
Tests for Filesystem Tool Suite (P4-03 / P4-04 / P4-05)
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.tools.filesystem import (
    AppendFileTool,
    CreateDirectoryTool,
    DeleteFileTool,
    DiskUsageTool,
    FindFilesTool,
    ListDirectoryTool,
    MoveFileTool,
    ReadFileTool,
    WriteFileTool,
    _format_size,
    ALL_FILESYSTEM_TOOLS,
)
from backend.pipeline.models import ActionTier, CapabilityType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello, World!", encoding="utf-8")
    return f


@pytest.fixture
def tmp_tree(tmp_path):
    (tmp_path / "a.py").write_text("print('a')")
    (tmp_path / "b.md").write_text("# B")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "c.json").write_text("{}")
    return tmp_path


# ---------------------------------------------------------------------------
# Metadata tests (all tools)
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_all_tools_have_names(self):
        for tool in ALL_FILESYSTEM_TOOLS:
            assert tool.metadata.name, f"Tool has no name: {tool}"

    def test_all_tools_have_descriptions(self):
        for tool in ALL_FILESYSTEM_TOOLS:
            assert tool.metadata.description, f"Tool has no description: {tool.metadata.name}"

    def test_all_tools_have_valid_tier(self):
        for tool in ALL_FILESYSTEM_TOOLS:
            assert isinstance(tool.metadata.tier, ActionTier)

    def test_all_tools_have_schemas(self):
        for tool in ALL_FILESYSTEM_TOOLS:
            schema = tool.get_parameter_schema()
            assert isinstance(schema, dict)
            assert "type" in schema

    def test_tier_assignments(self):
        tiers = {t.metadata.name: t.metadata.tier for t in ALL_FILESYSTEM_TOOLS}
        # Tier 1 — read-only
        assert tiers["read_file"] == ActionTier.TIER_1
        assert tiers["list_directory"] == ActionTier.TIER_1
        assert tiers["find_files"] == ActionTier.TIER_1
        assert tiers["disk_usage"] == ActionTier.TIER_1
        # Tier 2 — soft confirmation
        assert tiers["write_file"] == ActionTier.TIER_2
        assert tiers["append_file"] == ActionTier.TIER_2
        assert tiers["create_directory"] == ActionTier.TIER_2
        # Tier 3 — explicit confirmation
        assert tiers["delete_file"] == ActionTier.TIER_3
        assert tiers["move_file"] == ActionTier.TIER_3

    def test_capability_type_is_tool(self):
        for tool in ALL_FILESYSTEM_TOOLS:
            assert tool.metadata.type == CapabilityType.TOOL


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


class TestReadFileTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = ReadFileTool()

    async def test_reads_text_file(self, tmp_file):
        result = await self.tool.execute({"path": str(tmp_file)})
        assert result.type == "code"
        assert "Hello, World!" in result.content

    async def test_language_detected_from_extension(self, tmp_path):
        py_file = tmp_path / "script.py"
        py_file.write_text("print('hello')")
        result = await self.tool.execute({"path": str(py_file)})
        assert result.metadata["language"] == "python"

    async def test_unknown_extension_is_text(self, tmp_path):
        f = tmp_path / "data.xyz"
        f.write_text("raw data")
        result = await self.tool.execute({"path": str(f)})
        assert result.metadata["language"] == "text"

    async def test_file_not_found_returns_error_block(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path / "ghost.txt")})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_blocked_path_returns_error(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("SECRET=abc")
        result = await self.tool.execute({"path": str(env)})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_directory_returns_error(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path)})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_truncation_at_max_chars(self, tmp_path):
        big = tmp_path / "big.txt"
        big.write_text("x" * 200_000)
        result = await self.tool.execute({"path": str(big), "max_chars": 1000})
        assert result.metadata["truncated"] is True
        assert len(result.content) <= 1000

    async def test_no_truncation_for_small_file(self, tmp_file):
        result = await self.tool.execute({"path": str(tmp_file)})
        assert result.metadata["truncated"] is False

    async def test_schema_has_required_path(self):
        schema = self.tool.get_parameter_schema()
        assert "path" in schema["required"]


# ---------------------------------------------------------------------------
# ListDirectoryTool
# ---------------------------------------------------------------------------


class TestListDirectoryTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = ListDirectoryTool()

    async def test_lists_directory(self, tmp_tree):
        result = await self.tool.execute({"path": str(tmp_tree)})
        assert result.type == "table"
        names = [row[0] for row in result.content["rows"]]
        assert any("a.py" in n for n in names)
        assert any("b.md" in n for n in names)

    async def test_directories_appear_first(self, tmp_tree):
        result = await self.tool.execute({"path": str(tmp_tree)})
        rows = result.content["rows"]
        dir_rows = [r for r in rows if r[0].endswith("/")]
        file_rows = [r for r in rows if not r[0].endswith("/")]
        if dir_rows and file_rows:
            assert rows.index(dir_rows[-1]) < rows.index(file_rows[0])

    async def test_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = await self.tool.execute({"path": str(empty)})
        assert result.type == "text"
        assert "empty" in result.content.lower()

    async def test_not_found(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path / "nope")})
        assert result.content["level"] == "error"

    async def test_file_path_returns_error(self, tmp_file):
        result = await self.tool.execute({"path": str(tmp_file)})
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# FindFilesTool
# ---------------------------------------------------------------------------


class TestFindFilesTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = FindFilesTool()

    async def test_finds_all_files(self, tmp_tree):
        result = await self.tool.execute({"root": str(tmp_tree)})
        assert result.type == "table"

    async def test_pattern_filters(self, tmp_tree):
        result = await self.tool.execute({"root": str(tmp_tree), "pattern": "*.py"})
        assert result.type == "table"
        paths = [row[0] for row in result.content["rows"]]
        assert all(".py" in p for p in paths)

    async def test_no_match_returns_text(self, tmp_tree):
        result = await self.tool.execute({"root": str(tmp_tree), "pattern": "*.nonexistent"})
        assert result.type == "text"

    async def test_max_results_cap(self, tmp_tree):
        result = await self.tool.execute({"root": str(tmp_tree), "max_results": 1})
        assert len(result.content["rows"]) <= 1

    async def test_root_not_found(self, tmp_path):
        result = await self.tool.execute({"root": str(tmp_path / "nope")})
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# DiskUsageTool
# ---------------------------------------------------------------------------


class TestDiskUsageTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = DiskUsageTool()

    async def test_returns_table(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path)})
        assert result.type == "table"

    async def test_table_has_expected_rows(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path)})
        metric_names = [row[0] for row in result.content["rows"]]
        assert "Filesystem total" in metric_names
        assert "Filesystem free" in metric_names

    async def test_not_found_returns_error(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path / "ghost")})
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


class TestWriteFileTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = WriteFileTool()

    async def test_creates_file(self, tmp_path):
        dest = tmp_path / "output.txt"
        result = await self.tool.execute({"path": str(dest), "content": "written!"})
        assert result.content["level"] == "success"
        assert dest.read_text() == "written!"

    async def test_overwrites_existing(self, tmp_file):
        result = await self.tool.execute({"path": str(tmp_file), "content": "new"})
        assert result.content["level"] == "success"
        assert tmp_file.read_text() == "new"

    async def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "a" / "b" / "c.txt"
        result = await self.tool.execute({"path": str(dest), "content": "deep"})
        assert result.content["level"] == "success"
        assert dest.read_text() == "deep"

    async def test_blocked_path_returns_error(self, tmp_path):
        result = await self.tool.execute(
            {"path": str(tmp_path / "id_rsa"), "content": "key"}
        )
        assert result.content["level"] == "error"

    async def test_schema_has_required_fields(self):
        schema = self.tool.get_parameter_schema()
        assert "path" in schema["required"]
        assert "content" in schema["required"]


# ---------------------------------------------------------------------------
# AppendFileTool
# ---------------------------------------------------------------------------


class TestAppendFileTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = AppendFileTool()

    async def test_appends_to_existing(self, tmp_file):
        result = await self.tool.execute({"path": str(tmp_file), "content": "\nExtra"})
        assert result.content["level"] == "success"
        assert "Extra" in tmp_file.read_text()

    async def test_creates_new_file(self, tmp_path):
        new_file = tmp_path / "new.log"
        result = await self.tool.execute({"path": str(new_file), "content": "log line"})
        assert result.content["level"] == "success"
        assert new_file.read_text() == "log line"

    async def test_blocked_path(self, tmp_path):
        result = await self.tool.execute(
            {"path": str(tmp_path / "secrets.key"), "content": "x"}
        )
        assert result.content["level"] == "error"


# ---------------------------------------------------------------------------
# CreateDirectoryTool
# ---------------------------------------------------------------------------


class TestCreateDirectoryTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CreateDirectoryTool()

    async def test_creates_directory(self, tmp_path):
        target = tmp_path / "new_dir"
        result = await self.tool.execute({"path": str(target)})
        assert result.content["level"] == "success"
        assert target.is_dir()

    async def test_creates_nested_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        result = await self.tool.execute({"path": str(target)})
        assert result.content["level"] == "success"
        assert target.is_dir()

    async def test_existing_dir_is_ok(self, tmp_path):
        # mkdir with exist_ok=True, should succeed silently
        result = await self.tool.execute({"path": str(tmp_path)})
        assert result.content["level"] == "success"


# ---------------------------------------------------------------------------
# DeleteFileTool
# ---------------------------------------------------------------------------


class TestDeleteFileTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = DeleteFileTool()

    async def test_deletes_file(self, tmp_file):
        result = await self.tool.execute({"path": str(tmp_file)})
        assert result.content["level"] == "warning"  # delete uses warning level
        assert not tmp_file.exists()

    async def test_not_found_returns_error(self, tmp_path):
        result = await self.tool.execute({"path": str(tmp_path / "ghost.txt")})
        assert result.content["level"] == "error"

    async def test_blocked_path_returns_error(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("SECRET=x")
        result = await self.tool.execute({"path": str(env)})
        assert result.content["level"] == "error"
        assert env.exists()  # file was NOT deleted


# ---------------------------------------------------------------------------
# MoveFileTool
# ---------------------------------------------------------------------------


class TestMoveFileTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = MoveFileTool()

    async def test_moves_file(self, tmp_file):
        dst = tmp_file.parent / "moved.txt"
        result = await self.tool.execute({"src": str(tmp_file), "dst": str(dst)})
        assert result.content["level"] == "success"
        assert dst.exists()
        assert not tmp_file.exists()

    async def test_src_not_found(self, tmp_path):
        result = await self.tool.execute(
            {"src": str(tmp_path / "ghost.txt"), "dst": str(tmp_path / "dst.txt")}
        )
        assert result.content["level"] == "error"

    async def test_schema_requires_src_and_dst(self):
        schema = self.tool.get_parameter_schema()
        assert "src" in schema["required"]
        assert "dst" in schema["required"]


# ---------------------------------------------------------------------------
# _format_size helper
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_bytes(self):
        assert "B" in _format_size(500)

    def test_kilobytes(self):
        assert "KB" in _format_size(2048)

    def test_megabytes(self):
        assert "MB" in _format_size(5 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in _format_size(3 * 1024 ** 3)
