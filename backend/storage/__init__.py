"""
Storage module - External service credential management and file backends
"""

from .oauth import oauth_manager, OAuthManager
from .database import DatabaseManager, DatabaseConfig
from .abstract import FileSystem, FileInfo, FileSystemRouter, filesystem_router
from .local import LocalFileSystem, local_fs

# Cloud backends — imported for side-effect of registering with filesystem_router
try:
    from .gdrive import GoogleDriveFileSystem, gdrive_fs, GDriveConfig, get_authorization_url as gdrive_get_auth_url, exchange_code as gdrive_exchange_code
    _gdrive_available = True
except ImportError:
    _gdrive_available = False

try:
    from .dropbox_backend import DropboxFileSystem, dropbox_fs, DropboxConfig, get_authorization_url as dropbox_get_auth_url, exchange_code as dropbox_exchange_code
    _dropbox_available = True
except ImportError:
    _dropbox_available = False

try:
    from .s3 import S3FileSystem, S3Config, create_s3_backend
    _s3_available = True
except ImportError:
    _s3_available = False

try:
    from .sftp import SFTPFileSystem, SFTPConfig, create_sftp_backend
    _sftp_available = True
except ImportError:
    _sftp_available = False

__all__ = [
    # OAuth
    "oauth_manager", "OAuthManager",
    # Database
    "DatabaseManager", "DatabaseConfig",
    # File abstraction layer
    "FileSystem", "FileInfo", "FileSystemRouter", "filesystem_router",
    "LocalFileSystem", "local_fs",
    # Cloud backends (conditionally available)
    "GoogleDriveFileSystem", "gdrive_fs", "GDriveConfig",
    "gdrive_get_auth_url", "gdrive_exchange_code",
    "DropboxFileSystem", "dropbox_fs", "DropboxConfig",
    "dropbox_get_auth_url", "dropbox_exchange_code",
    "S3FileSystem", "S3Config", "create_s3_backend",
    "SFTPFileSystem", "SFTPConfig", "create_sftp_backend",
]
