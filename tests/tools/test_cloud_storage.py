"""
Tests for Cloud Storage Tool Suite (P5-08)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from backend.tools.cloud_storage import (
    CloudListTool,
    CloudReadTool,
    CloudUploadTool,
    CloudDownloadTool,
    CloudDeleteTool,
    CloudMoveTool,
    ALL_CLOUD_STORAGE_TOOLS,
)
from backend.pipeline.models import ActionTier, CapabilityType
from backend.storage.abstract import FileInfo


def make_file_info(name="file.txt", size=100, is_dir=False):
    return FileInfo(
        name=name,
        path=f"gdrive://{name}",
        size=size,
        is_dir=is_dir,
        modified_at=datetime(2024, 1, 1),
    )


# ---------------------------------------------------------------------------
# Metadata tests (all tools)
# ---------------------------------------------------------------------------


class TestCloudStorageToolMetadata:
    def test_all_tools_have_names(self):
        for tool in ALL_CLOUD_STORAGE_TOOLS:
            assert tool.metadata.name

    def test_all_tools_have_descriptions(self):
        for tool in ALL_CLOUD_STORAGE_TOOLS:
            assert tool.metadata.description

    def test_all_tools_have_valid_tiers(self):
        for tool in ALL_CLOUD_STORAGE_TOOLS:
            assert isinstance(tool.metadata.tier, ActionTier)

    def test_tier_assignments(self):
        tiers = {t.metadata.name: t.metadata.tier for t in ALL_CLOUD_STORAGE_TOOLS}
        assert tiers["cloud_list"] == ActionTier.TIER_1
        assert tiers["cloud_read"] == ActionTier.TIER_1
        assert tiers["cloud_upload"] == ActionTier.TIER_2
        assert tiers["cloud_download"] == ActionTier.TIER_2
        assert tiers["cloud_delete"] == ActionTier.TIER_3
        assert tiers["cloud_move"] == ActionTier.TIER_3

    def test_all_schemas_valid(self):
        for tool in ALL_CLOUD_STORAGE_TOOLS:
            schema = tool.get_parameter_schema()
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"

    def test_capability_type_is_tool(self):
        for tool in ALL_CLOUD_STORAGE_TOOLS:
            assert tool.metadata.type == CapabilityType.TOOL

    def test_network_tools_require_network(self):
        for tool in ALL_CLOUD_STORAGE_TOOLS:
            assert tool.metadata.requires_network is True


# ---------------------------------------------------------------------------
# CloudListTool
# ---------------------------------------------------------------------------


class TestCloudListTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CloudListTool()

    async def test_returns_table_of_entries(self):
        entries = [make_file_info("doc.pdf"), make_file_info("subdir", is_dir=True)]
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(return_value=entries)
            result = await self.tool.execute({"uri": "gdrive://My Drive/"})
        assert result.type == "table"
        assert len(result.content["rows"]) == 2

    async def test_empty_dir_returns_text(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(return_value=[])
            result = await self.tool.execute({"uri": "gdrive://empty/"})
        assert result.type == "text"

    async def test_unknown_scheme_returns_error(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(side_effect=ValueError("No backend registered for scheme 'unknown'"))
            result = await self.tool.execute({"uri": "unknown://path"})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_dirs_marked_with_slash(self):
        entries = [make_file_info("mydir", is_dir=True)]
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(return_value=entries)
            result = await self.tool.execute({"uri": "gdrive://"})
        assert any("mydir/" in row[0] for row in result.content["rows"])

    def test_uri_is_required(self):
        schema = self.tool.get_parameter_schema()
        assert "uri" in schema["required"]

    async def test_generic_exception_returns_error(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(side_effect=ConnectionError("Network unavailable"))
            result = await self.tool.execute({"uri": "gdrive://My Drive/"})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_metadata_contains_uri_and_count(self):
        entries = [make_file_info("a.txt"), make_file_info("b.txt")]
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(return_value=entries)
            result = await self.tool.execute({"uri": "gdrive://docs/"})
        assert result.metadata["uri"] == "gdrive://docs/"
        assert result.metadata["count"] == 2

    async def test_table_has_expected_headers(self):
        entries = [make_file_info("file.txt")]
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.list = AsyncMock(return_value=entries)
            result = await self.tool.execute({"uri": "gdrive://"})
        assert result.content["headers"] == ["Name", "Type", "Size", "Modified"]


# ---------------------------------------------------------------------------
# CloudReadTool
# ---------------------------------------------------------------------------


class TestCloudReadTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CloudReadTool()

    async def test_returns_code_block(self):
        async def fake_read(path):
            yield b"print('hello')"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "script.py")
            result = await self.tool.execute({"uri": "gdrive://script.py"})
        assert result.type == "code"

    async def test_python_language_detected(self):
        async def fake_read(path):
            yield b"code"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "script.py")
            result = await self.tool.execute({"uri": "gdrive://script.py"})
        assert result.metadata["language"] == "python"

    async def test_error_returns_notification(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.side_effect = ValueError("No backend")
            result = await self.tool.execute({"uri": "unknown://file.txt"})
        assert result.type == "notification"
        assert result.content["level"] == "error"

    async def test_truncation_at_max_chars(self):
        big_data = b"x" * 200_000

        async def fake_read(path):
            yield big_data

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "big.txt")
            result = await self.tool.execute({"uri": "gdrive://big.txt", "max_chars": 1000})
        assert result.metadata["truncated"] is True
        assert len(result.content) <= 1000

    async def test_no_truncation_for_small_file(self):
        async def fake_read(path):
            yield b"small content"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "small.txt")
            result = await self.tool.execute({"uri": "gdrive://small.txt"})
        assert result.metadata["truncated"] is False

    async def test_unknown_extension_defaults_to_text(self):
        async def fake_read(path):
            yield b"raw data"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "data.xyz")
            result = await self.tool.execute({"uri": "gdrive://data.xyz"})
        assert result.metadata["language"] == "text"

    async def test_javascript_language_detected(self):
        async def fake_read(path):
            yield b"console.log('hi')"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "app.js")
            result = await self.tool.execute({"uri": "gdrive://app.js"})
        assert result.metadata["language"] == "javascript"

    async def test_uri_stored_in_metadata(self):
        async def fake_read(path):
            yield b"data"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "notes.md")
            result = await self.tool.execute({"uri": "gdrive://notes.md"})
        assert result.metadata["uri"] == "gdrive://notes.md"

    def test_uri_is_required(self):
        schema = self.tool.get_parameter_schema()
        assert "uri" in schema["required"]

    def test_max_chars_is_optional(self):
        schema = self.tool.get_parameter_schema()
        assert "max_chars" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# CloudUploadTool
# ---------------------------------------------------------------------------


class TestCloudUploadTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CloudUploadTool()

    async def test_success_returns_notification(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_router.write = AsyncMock(return_value=mock_ctx)
            result = await self.tool.execute({"local_path": str(src), "cloud_uri": "gdrive://dst.txt"})
        assert result.content["level"] == "success"

    async def test_missing_local_file_returns_error(self):
        result = await self.tool.execute({"local_path": "/no/such/file.txt", "cloud_uri": "gdrive://dst.txt"})
        assert result.content["level"] == "error"

    async def test_success_message_contains_paths(self, tmp_path):
        src = tmp_path / "upload.bin"
        src.write_bytes(b"binary content")

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_router.write = AsyncMock(return_value=mock_ctx)
            result = await self.tool.execute({"local_path": str(src), "cloud_uri": "s3://bucket/upload.bin"})
        assert str(src) in result.content["message"]
        assert "s3://bucket/upload.bin" in result.content["message"]

    def test_schema_requires_local_path_and_cloud_uri(self):
        schema = self.tool.get_parameter_schema()
        assert "local_path" in schema["required"]
        assert "cloud_uri" in schema["required"]


# ---------------------------------------------------------------------------
# CloudDownloadTool
# ---------------------------------------------------------------------------


class TestCloudDownloadTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CloudDownloadTool()

    async def test_success_writes_file(self, tmp_path):
        dest = tmp_path / "downloaded.txt"

        async def fake_read(path):
            yield b"cloud content"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "file.txt")
            result = await self.tool.execute({
                "cloud_uri": "gdrive://file.txt",
                "local_path": str(dest),
            })
        assert result.content["level"] == "success"
        assert dest.read_bytes() == b"cloud content"

    async def test_invalid_scheme_returns_error(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.side_effect = ValueError("No backend")
            result = await self.tool.execute({
                "cloud_uri": "unknown://file.txt",
                "local_path": "/tmp/out.txt",
            })
        assert result.content["level"] == "error"

    async def test_creates_parent_directories(self, tmp_path):
        dest = tmp_path / "a" / "b" / "c" / "downloaded.txt"

        async def fake_read(path):
            yield b"data"

        mock_backend = MagicMock()
        mock_backend.read = fake_read

        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.resolve.return_value = (mock_backend, "remote.txt")
            result = await self.tool.execute({
                "cloud_uri": "gdrive://remote.txt",
                "local_path": str(dest),
            })
        assert result.content["level"] == "success"
        assert dest.exists()

    def test_schema_requires_cloud_uri_and_local_path(self):
        schema = self.tool.get_parameter_schema()
        assert "cloud_uri" in schema["required"]
        assert "local_path" in schema["required"]


# ---------------------------------------------------------------------------
# CloudDeleteTool
# ---------------------------------------------------------------------------


class TestCloudDeleteTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CloudDeleteTool()

    async def test_success_returns_warning(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.delete = AsyncMock()
            result = await self.tool.execute({"uri": "gdrive://file.txt"})
        assert result.content["level"] == "warning"

    async def test_error_returns_error(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.delete = AsyncMock(side_effect=FileNotFoundError("not found"))
            result = await self.tool.execute({"uri": "gdrive://ghost.txt"})
        assert result.content["level"] == "error"

    async def test_warning_message_contains_uri(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.delete = AsyncMock()
            result = await self.tool.execute({"uri": "s3://bucket/old-backup.zip"})
        assert "s3://bucket/old-backup.zip" in result.content["message"]

    def test_uri_is_required(self):
        schema = self.tool.get_parameter_schema()
        assert "uri" in schema["required"]


# ---------------------------------------------------------------------------
# CloudMoveTool
# ---------------------------------------------------------------------------


class TestCloudMoveTool:
    @pytest.fixture(autouse=True)
    def tool(self):
        self.tool = CloudMoveTool()

    async def test_success_returns_success(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.move = AsyncMock()
            result = await self.tool.execute({"src_uri": "gdrive://a.txt", "dst_uri": "gdrive://b.txt"})
        assert result.content["level"] == "success"

    async def test_cross_backend_returns_error(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.move = AsyncMock(side_effect=ValueError("Cross-backend move not supported"))
            result = await self.tool.execute({"src_uri": "gdrive://a.txt", "dst_uri": "dropbox://b.txt"})
        assert result.content["level"] == "error"

    async def test_success_message_contains_both_uris(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.move = AsyncMock()
            result = await self.tool.execute({
                "src_uri": "s3://bucket/old.csv",
                "dst_uri": "s3://bucket/archive/old.csv",
            })
        assert "s3://bucket/old.csv" in result.content["message"]
        assert "s3://bucket/archive/old.csv" in result.content["message"]

    async def test_generic_error_returns_error(self):
        with patch("backend.tools.cloud_storage.filesystem_router") as mock_router:
            mock_router.move = AsyncMock(side_effect=PermissionError("Not allowed"))
            result = await self.tool.execute({"src_uri": "gdrive://a.txt", "dst_uri": "gdrive://b.txt"})
        assert result.content["level"] == "error"

    def test_schema_requires_src_and_dst_uri(self):
        schema = self.tool.get_parameter_schema()
        assert "src_uri" in schema["required"]
        assert "dst_uri" in schema["required"]
