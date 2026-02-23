"""
S3 File Backend — async-compatible I/O via boto3 + asyncio executor

Supports AWS S3 and S3-compatible services (MinIO, Backblaze B2, etc.)
via the endpoint_url option in S3Config.

Registered as the 's3://' backend when create_s3_backend(config) is called.
"""

import asyncio
import mimetypes
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import AsyncIterator, List, Optional

# Optional import with clear error message
try:
    import boto3
    from botocore.exceptions import ClientError

    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    ClientError = Exception  # type: ignore[assignment,misc]

from backend.storage.abstract import FileInfo, FileSystem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 65_536  # 64 KB


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class S3Config:
    bucket: str
    prefix: str = ""                            # Optional key prefix inside bucket
    endpoint_url: Optional[str] = None          # For MinIO/custom S3, e.g. "http://localhost:9000"
    region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None     # None = use env vars / IAM role
    aws_secret_access_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class S3FileSystem(FileSystem):
    """
    AWS S3 / S3-compatible filesystem backend.

    Uses boto3 with asyncio.get_event_loop().run_in_executor() for async operation.
    All operations run in the default thread pool executor.
    """

    def __init__(self, config: S3Config) -> None:
        self._config = config
        self._client = None  # lazy init

    @property
    def scheme(self) -> str:
        return "s3"

    def _get_client(self):
        """Lazily create and cache boto3 S3 client."""
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")
        if self._client is None:
            kwargs = {
                "region_name": self._config.region,
            }
            if self._config.endpoint_url is not None:
                kwargs["endpoint_url"] = self._config.endpoint_url
            if self._config.aws_access_key_id is not None:
                kwargs["aws_access_key_id"] = self._config.aws_access_key_id
            if self._config.aws_secret_access_key is not None:
                kwargs["aws_secret_access_key"] = self._config.aws_secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _full_key(self, path: str) -> str:
        """Build full S3 key from config prefix + path."""
        # Strip leading slashes from path
        clean_path = path.lstrip("/")
        prefix = self._config.prefix
        if not prefix:
            return clean_path
        # Combine: config.prefix.rstrip("/") + "/" + path.lstrip("/")
        base = prefix.rstrip("/")
        if not clean_path:
            return base + "/"
        return base + "/" + clean_path

    async def _run(self, func, *args, **kwargs):
        """Run a boto3 call in the thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def list(self, path: str) -> List[FileInfo]:
        """
        List directory contents using S3 list_objects_v2 with delimiter.

        CommonPrefixes become is_dir=True entries; Contents become files.
        Handles pagination via ContinuationToken.
        Sorted: directories first, then files.
        """
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket

        # Build the prefix for listing — must end with "/" for directory semantics
        prefix_key = self._full_key(path)
        if prefix_key and not prefix_key.endswith("/"):
            prefix_key = prefix_key + "/"

        dirs: List[FileInfo] = []
        files: List[FileInfo] = []

        paginate_kwargs: dict = {
            "Bucket": bucket,
            "Prefix": prefix_key,
            "Delimiter": "/",
        }

        while True:
            response = await self._run(client.list_objects_v2, **paginate_kwargs)

            # Process common prefixes (virtual directories)
            for cp in response.get("CommonPrefixes", []):
                info = await self._parse_prefix_entry(cp["Prefix"])
                dirs.append(info)

            # Process file contents (exclude the directory placeholder itself)
            for entry in response.get("Contents", []):
                key = entry["Key"]
                # Skip the directory placeholder (key == prefix_key or ends with "/")
                if key == prefix_key or key.endswith("/"):
                    continue
                info = await self._parse_content_entry(entry)
                files.append(info)

            # Handle pagination
            if response.get("IsTruncated"):
                paginate_kwargs["ContinuationToken"] = response["NextContinuationToken"]
            else:
                break

        return dirs + files

    async def stat(self, path: str) -> FileInfo:
        """
        Return metadata for a file or directory.

        Tries head_object first (files); falls back to list_objects_v2
        to detect virtual directories.
        """
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket
        key = self._full_key(path)

        # Try as a file first
        try:
            response = await self._run(client.head_object, Bucket=bucket, Key=key)
            content_length = response.get("ContentLength", 0)
            last_modified = response.get("LastModified", datetime.utcnow())
            if isinstance(last_modified, datetime):
                last_modified = last_modified.replace(tzinfo=None)
            content_type = response.get("ContentType")
            name = key.rstrip("/").rsplit("/", 1)[-1] if "/" in key else key
            return FileInfo(
                name=name,
                path=f"s3://{bucket}/{key}",
                size=content_length,
                is_dir=False,
                modified_at=last_modified,
                mime_type=content_type,
                readable=True,
                writable=False,
            )
        except ClientError as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code not in ("404", "NoSuchKey"):
                raise

        # Try as a virtual directory
        dir_key = key if key.endswith("/") else key + "/"
        response = await self._run(
            client.list_objects_v2,
            Bucket=bucket,
            Prefix=dir_key,
            Delimiter="/",
            MaxKeys=1,
        )
        if response.get("Contents") or response.get("CommonPrefixes"):
            name = dir_key.rstrip("/").rsplit("/", 1)[-1] if "/" in dir_key.rstrip("/") else dir_key.rstrip("/")
            return FileInfo(
                name=name,
                path=f"s3://{bucket}/{dir_key}",
                size=0,
                is_dir=True,
                modified_at=datetime.utcnow(),
                readable=True,
                writable=False,
            )

        raise FileNotFoundError(f"S3 path not found: s3://{bucket}/{key}")

    async def read(self, path: str) -> AsyncIterator[bytes]:
        """Stream S3 object body in chunks of _CHUNK_SIZE bytes."""
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket
        key = self._full_key(path)

        response = await self._run(client.get_object, Bucket=bucket, Key=key)
        body = response["Body"]

        while True:
            chunk = await self._run(body.read, _CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    @asynccontextmanager
    async def write(self, path: str):
        """
        Yield a BytesIO buffer for writing.

        On context exit, uploads the buffer contents to S3 via put_object.
        """
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket
        key = self._full_key(path)

        buffer = BytesIO()
        yield buffer

        # Upload after yield — buffer contains written data
        buffer.seek(0)
        content_type, _ = mimetypes.guess_type(path)
        put_kwargs: dict = {
            "Bucket": bucket,
            "Key": key,
            "Body": buffer.getvalue(),
        }
        if content_type:
            put_kwargs["ContentType"] = content_type
        await self._run(client.put_object, **put_kwargs)

    async def delete(self, path: str) -> None:
        """Delete an S3 object."""
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket
        key = self._full_key(path)

        await self._run(client.delete_object, Bucket=bucket, Key=key)

    async def move(self, src: str, dst: str) -> None:
        """
        Move an S3 object by copying then deleting the source.

        Uses copy_object + delete_object (S3 has no native move).
        """
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket
        src_key = self._full_key(src)
        dst_key = self._full_key(dst)

        await self._run(
            client.copy_object,
            Bucket=bucket,
            Key=dst_key,
            CopySource={"Bucket": bucket, "Key": src_key},
        )
        await self._run(client.delete_object, Bucket=bucket, Key=src_key)

    async def exists(self, path: str) -> bool:
        """Return True if the path exists as a file or virtual directory."""
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3 backend: pip install boto3")

        client = self._get_client()
        bucket = self._config.bucket
        key = self._full_key(path)

        # Try as a file first
        try:
            await self._run(client.head_object, Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code not in ("404", "NoSuchKey"):
                raise

        # Try as a virtual directory
        dir_key = key if key.endswith("/") else key + "/"
        response = await self._run(
            client.list_objects_v2,
            Bucket=bucket,
            Prefix=dir_key,
            Delimiter="/",
            MaxKeys=1,
        )
        return bool(response.get("Contents") or response.get("CommonPrefixes"))

    # ------------------------------------------------------------------
    # Entry parsers
    # ------------------------------------------------------------------

    async def _parse_content_entry(self, entry: dict) -> FileInfo:
        """Parse an S3 Contents entry into a FileInfo."""
        bucket = self._config.bucket
        key = entry["Key"]
        size = entry.get("Size", 0)
        last_modified = entry.get("LastModified", datetime.utcnow())
        if isinstance(last_modified, datetime):
            last_modified = last_modified.replace(tzinfo=None)

        name = key.rstrip("/").rsplit("/", 1)[-1] if "/" in key else key
        mime_type, _ = mimetypes.guess_type(name)

        return FileInfo(
            name=name,
            path=f"s3://{bucket}/{key}",
            size=size,
            is_dir=False,
            modified_at=last_modified,
            mime_type=mime_type,
            readable=True,
            writable=False,
        )

    async def _parse_prefix_entry(self, prefix: str) -> FileInfo:
        """Parse an S3 CommonPrefixes entry (virtual directory) into a FileInfo."""
        bucket = self._config.bucket
        # prefix looks like "some/path/subdir/"
        name = prefix.rstrip("/").rsplit("/", 1)[-1] if "/" in prefix.rstrip("/") else prefix.rstrip("/")

        return FileInfo(
            name=name,
            path=f"s3://{bucket}/{prefix}",
            size=0,
            is_dir=True,
            modified_at=datetime.utcnow(),
            readable=True,
            writable=False,
        )


# ---------------------------------------------------------------------------
# Module-level state and factory
# ---------------------------------------------------------------------------

_s3_instance: Optional[S3FileSystem] = None


def create_s3_backend(config: S3Config) -> S3FileSystem:
    """Create an S3FileSystem, register it with filesystem_router, and return it."""
    global _s3_instance
    from backend.storage.abstract import filesystem_router

    backend = S3FileSystem(config)
    filesystem_router.register(backend)
    _s3_instance = backend
    return backend
