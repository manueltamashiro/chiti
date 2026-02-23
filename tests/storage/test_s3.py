"""
Tests for S3FileSystem backend.

Uses unittest.mock to patch boto3 calls — no real AWS credentials or
network access required.
"""

from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from backend.storage.s3 import S3Config, S3FileSystem, create_s3_backend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return S3Config(bucket="test-bucket", prefix="")


@pytest.fixture
def fs(config):
    return S3FileSystem(config)


# ---------------------------------------------------------------------------
# S3Config tests
# ---------------------------------------------------------------------------


class TestS3Config:
    def test_default_region(self):
        c = S3Config(bucket="mybucket")
        assert c.region == "us-east-1"

    def test_custom_endpoint(self):
        c = S3Config(bucket="mybucket", endpoint_url="http://localhost:9000")
        assert c.endpoint_url == "http://localhost:9000"

    def test_default_prefix_is_empty(self):
        c = S3Config(bucket="mybucket")
        assert c.prefix == ""

    def test_default_credentials_are_none(self):
        c = S3Config(bucket="mybucket")
        assert c.aws_access_key_id is None
        assert c.aws_secret_access_key is None

    def test_custom_credentials(self):
        c = S3Config(
            bucket="mybucket",
            aws_access_key_id="AKID",
            aws_secret_access_key="secret",
        )
        assert c.aws_access_key_id == "AKID"
        assert c.aws_secret_access_key == "secret"


# ---------------------------------------------------------------------------
# S3FileSystem tests
# ---------------------------------------------------------------------------


class TestS3FileSystem:
    @pytest.fixture(autouse=True)
    def mock_boto3(self):
        """Patch boto3.client to return a controlled MagicMock for every test."""
        with patch("backend.storage.s3.boto3") as mock_b3:
            mock_client = MagicMock()
            mock_b3.client.return_value = mock_client
            self.mock_client = mock_client
            yield mock_b3

    # ------------------------------------------------------------------
    # scheme
    # ------------------------------------------------------------------

    def test_scheme_is_s3(self, fs):
        assert fs.scheme == "s3"

    # ------------------------------------------------------------------
    # _full_key
    # ------------------------------------------------------------------

    def test_full_key_no_prefix(self, fs):
        assert fs._full_key("some/file.txt") == "some/file.txt"

    def test_full_key_strips_leading_slash(self, fs):
        assert fs._full_key("/some/file.txt") == "some/file.txt"

    def test_full_key_with_prefix(self):
        cfg = S3Config(bucket="b", prefix="myprefix")
        fs2 = S3FileSystem(cfg)
        assert fs2._full_key("file.txt") == "myprefix/file.txt"

    def test_full_key_prefix_trailing_slash_normalised(self):
        cfg = S3Config(bucket="b", prefix="myprefix/")
        fs2 = S3FileSystem(cfg)
        assert fs2._full_key("file.txt") == "myprefix/file.txt"

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def test_list_returns_files_and_dirs(self, fs):
        self.mock_client.list_objects_v2.return_value = {
            "CommonPrefixes": [{"Prefix": "subdir/"}],
            "Contents": [
                {"Key": "file.txt", "Size": 100, "LastModified": datetime(2024, 1, 1)}
            ],
            "IsTruncated": False,
        }
        result = await fs.list("")
        names = [r.name for r in result]
        assert "file.txt" in names

    async def test_list_dirs_come_before_files(self, fs):
        self.mock_client.list_objects_v2.return_value = {
            "CommonPrefixes": [{"Prefix": "subdir/"}],
            "Contents": [
                {"Key": "alpha.txt", "Size": 50, "LastModified": datetime(2024, 1, 1)}
            ],
            "IsTruncated": False,
        }
        result = await fs.list("")
        dir_indices = [i for i, e in enumerate(result) if e.is_dir]
        file_indices = [i for i, e in enumerate(result) if not e.is_dir]
        if dir_indices and file_indices:
            assert max(dir_indices) < min(file_indices)

    async def test_list_directory_placeholder_excluded(self, fs):
        """The zero-byte key that matches the prefix itself should be excluded."""
        self.mock_client.list_objects_v2.return_value = {
            "CommonPrefixes": [],
            "Contents": [
                # This is the directory placeholder — should be skipped
                {"Key": "mydir/", "Size": 0, "LastModified": datetime(2024, 1, 1)},
                {"Key": "mydir/real.txt", "Size": 42, "LastModified": datetime(2024, 1, 1)},
            ],
            "IsTruncated": False,
        }
        result = await fs.list("mydir")
        names = [r.name for r in result]
        assert "real.txt" in names
        # The directory placeholder key ending in "/" should not appear as a file
        assert "" not in names

    async def test_list_handles_pagination(self, fs):
        """list() follows ContinuationToken until IsTruncated is False."""
        self.mock_client.list_objects_v2.side_effect = [
            {
                "CommonPrefixes": [],
                "Contents": [
                    {"Key": "page1.txt", "Size": 10, "LastModified": datetime(2024, 1, 1)}
                ],
                "IsTruncated": True,
                "NextContinuationToken": "token-abc",
            },
            {
                "CommonPrefixes": [],
                "Contents": [
                    {"Key": "page2.txt", "Size": 20, "LastModified": datetime(2024, 1, 1)}
                ],
                "IsTruncated": False,
            },
        ]
        result = await fs.list("")
        names = [r.name for r in result]
        assert "page1.txt" in names
        assert "page2.txt" in names
        assert self.mock_client.list_objects_v2.call_count == 2

    async def test_list_fileinfo_path_format(self, fs):
        self.mock_client.list_objects_v2.return_value = {
            "CommonPrefixes": [],
            "Contents": [
                {"Key": "notes.txt", "Size": 5, "LastModified": datetime(2024, 1, 1)}
            ],
            "IsTruncated": False,
        }
        result = await fs.list("")
        assert result[0].path.startswith("s3://test-bucket/")

    async def test_list_returns_empty_for_empty_prefix(self, fs):
        self.mock_client.list_objects_v2.return_value = {
            "CommonPrefixes": [],
            "Contents": [],
            "IsTruncated": False,
        }
        result = await fs.list("empty/")
        assert result == []

    # ------------------------------------------------------------------
    # stat
    # ------------------------------------------------------------------

    async def test_stat_file(self, fs):
        self.mock_client.head_object.return_value = {
            "ContentLength": 500,
            "LastModified": datetime(2024, 1, 1),
            "ContentType": "text/plain",
        }
        info = await fs.stat("file.txt")
        assert info.size == 500
        assert not info.is_dir

    async def test_stat_file_mime_type(self, fs):
        self.mock_client.head_object.return_value = {
            "ContentLength": 100,
            "LastModified": datetime(2024, 1, 1),
            "ContentType": "application/json",
        }
        info = await fs.stat("data.json")
        assert info.mime_type == "application/json"

    async def test_stat_virtual_directory(self, fs):
        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        self.mock_client.head_object.side_effect = error
        self.mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "mydir/file.txt", "Size": 10, "LastModified": datetime(2024, 1, 1)}
            ],
            "CommonPrefixes": [],
            "IsTruncated": False,
        }
        info = await fs.stat("mydir")
        assert info.is_dir

    async def test_stat_not_found_raises(self, fs):
        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        self.mock_client.head_object.side_effect = error
        self.mock_client.list_objects_v2.return_value = {
            "Contents": [],
            "CommonPrefixes": [],
            "IsTruncated": False,
        }
        with pytest.raises(FileNotFoundError):
            await fs.stat("ghost.txt")

    async def test_stat_strips_tzinfo(self, fs):
        """LastModified datetime should have no tzinfo."""
        from datetime import timezone

        aware_dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.mock_client.head_object.return_value = {
            "ContentLength": 10,
            "LastModified": aware_dt,
            "ContentType": "text/plain",
        }
        info = await fs.stat("file.txt")
        assert info.modified_at.tzinfo is None

    # ------------------------------------------------------------------
    # exists
    # ------------------------------------------------------------------

    async def test_exists_true(self, fs):
        self.mock_client.head_object.return_value = {
            "ContentLength": 10,
            "LastModified": datetime(2024, 1, 1),
        }
        assert await fs.exists("file.txt")

    async def test_exists_false(self, fs):
        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        self.mock_client.head_object.side_effect = error
        # Also mock list_objects_v2 for directory check
        self.mock_client.list_objects_v2.return_value = {
            "Contents": [],
            "CommonPrefixes": [],
            "IsTruncated": False,
        }
        assert not await fs.exists("ghost.txt")

    async def test_exists_true_for_virtual_directory(self, fs):
        """exists() returns True when a virtual directory prefix is found."""
        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        self.mock_client.head_object.side_effect = error
        self.mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "mydir/file.txt", "Size": 5, "LastModified": datetime(2024, 1, 1)}
            ],
            "CommonPrefixes": [],
            "IsTruncated": False,
        }
        assert await fs.exists("mydir")

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    async def test_delete_calls_delete_object(self, fs):
        self.mock_client.delete_object.return_value = {}
        await fs.delete("file.txt")
        self.mock_client.delete_object.assert_called_once()

    async def test_delete_passes_correct_key(self, fs):
        self.mock_client.delete_object.return_value = {}
        await fs.delete("path/to/file.txt")
        call_kwargs = self.mock_client.delete_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "path/to/file.txt"

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    async def test_read_streams_content(self, fs):
        mock_body = MagicMock()
        mock_body.read.side_effect = [b"chunk1", b"chunk2", b""]
        self.mock_client.get_object.return_value = {"Body": mock_body}
        chunks = []
        async for chunk in fs.read("file.txt"):
            chunks.append(chunk)
        assert b"chunk1" in chunks
        assert b"chunk2" in chunks

    async def test_read_stops_on_empty_chunk(self, fs):
        mock_body = MagicMock()
        mock_body.read.side_effect = [b"data", b""]
        self.mock_client.get_object.return_value = {"Body": mock_body}
        chunks = []
        async for chunk in fs.read("file.txt"):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0] == b"data"

    async def test_read_passes_correct_key(self, fs):
        mock_body = MagicMock()
        mock_body.read.side_effect = [b""]
        self.mock_client.get_object.return_value = {"Body": mock_body}
        async for _ in fs.read("dir/file.txt"):
            pass
        call_kwargs = self.mock_client.get_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "dir/file.txt"

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    async def test_write_calls_put_object(self, fs):
        self.mock_client.put_object.return_value = {}
        async with fs.write("out.txt") as fh:
            fh.write(b"hello")
        self.mock_client.put_object.assert_called_once()

    async def test_write_uploads_correct_data(self, fs):
        self.mock_client.put_object.return_value = {}
        async with fs.write("out.txt") as fh:
            fh.write(b"hello world")
        call_kwargs = self.mock_client.put_object.call_args[1]
        assert call_kwargs["Body"] == b"hello world"
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "out.txt"

    async def test_write_yields_bytesio(self, fs):
        self.mock_client.put_object.return_value = {}
        async with fs.write("out.bin") as fh:
            assert isinstance(fh, BytesIO)

    async def test_write_empty_file(self, fs):
        self.mock_client.put_object.return_value = {}
        async with fs.write("empty.txt") as fh:
            pass  # write nothing
        self.mock_client.put_object.assert_called_once()
        call_kwargs = self.mock_client.put_object.call_args[1]
        assert call_kwargs["Body"] == b""

    # ------------------------------------------------------------------
    # move
    # ------------------------------------------------------------------

    async def test_move_copies_then_deletes(self, fs):
        self.mock_client.copy_object.return_value = {}
        self.mock_client.delete_object.return_value = {}
        await fs.move("src.txt", "dst.txt")
        self.mock_client.copy_object.assert_called_once()
        self.mock_client.delete_object.assert_called_once()

    async def test_move_copy_source_is_correct(self, fs):
        self.mock_client.copy_object.return_value = {}
        self.mock_client.delete_object.return_value = {}
        await fs.move("src.txt", "dst.txt")
        copy_kwargs = self.mock_client.copy_object.call_args[1]
        assert copy_kwargs["CopySource"] == {"Bucket": "test-bucket", "Key": "src.txt"}
        assert copy_kwargs["Key"] == "dst.txt"
        assert copy_kwargs["Bucket"] == "test-bucket"

    async def test_move_deletes_source_key(self, fs):
        self.mock_client.copy_object.return_value = {}
        self.mock_client.delete_object.return_value = {}
        await fs.move("old/path.txt", "new/path.txt")
        delete_kwargs = self.mock_client.delete_object.call_args[1]
        assert delete_kwargs["Key"] == "old/path.txt"

    # ------------------------------------------------------------------
    # _parse_content_entry
    # ------------------------------------------------------------------

    async def test_parse_content_entry(self, fs):
        entry = {
            "Key": "folder/image.png",
            "Size": 2048,
            "LastModified": datetime(2024, 3, 15),
            "ETag": '"abc123"',
        }
        info = await fs._parse_content_entry(entry)
        assert info.name == "image.png"
        assert info.size == 2048
        assert not info.is_dir
        assert info.path == "s3://test-bucket/folder/image.png"

    async def test_parse_content_entry_strips_tzinfo(self, fs):
        from datetime import timezone

        aware_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        entry = {"Key": "file.txt", "Size": 10, "LastModified": aware_dt}
        info = await fs._parse_content_entry(entry)
        assert info.modified_at.tzinfo is None

    # ------------------------------------------------------------------
    # _parse_prefix_entry
    # ------------------------------------------------------------------

    async def test_parse_prefix_entry(self, fs):
        info = await fs._parse_prefix_entry("photos/2024/")
        assert info.name == "2024"
        assert info.is_dir
        assert info.size == 0
        assert info.path == "s3://test-bucket/photos/2024/"

    async def test_parse_prefix_entry_top_level(self, fs):
        info = await fs._parse_prefix_entry("topdir/")
        assert info.name == "topdir"
        assert info.is_dir


# ---------------------------------------------------------------------------
# create_s3_backend
# ---------------------------------------------------------------------------


class TestCreateS3Backend:
    def test_create_registers_backend(self):
        with patch("backend.storage.s3.boto3"):
            with patch("backend.storage.abstract.filesystem_router") as mock_router:
                backend = create_s3_backend(S3Config(bucket="b"))
                mock_router.register.assert_called_once_with(backend)

    def test_create_returns_s3_filesystem(self):
        with patch("backend.storage.s3.boto3"):
            with patch("backend.storage.abstract.filesystem_router"):
                backend = create_s3_backend(S3Config(bucket="mybucket"))
                assert isinstance(backend, S3FileSystem)

    def test_create_sets_module_level_instance(self):
        import backend.storage.s3 as s3_module

        with patch("backend.storage.s3.boto3"):
            with patch("backend.storage.abstract.filesystem_router"):
                backend = create_s3_backend(S3Config(bucket="singleton-bucket"))
                assert s3_module._s3_instance is backend
