"""
OAuth token management with OS keychain storage

This module handles OAuth token storage and retrieval using the OS keychain,
ensuring tokens are never stored in plaintext files or environment variables.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    keyring = None
    logging.warning("keyring library not available - OAuth tokens will be stored in memory only")

from backend.pipeline.models import OAuthToken

logger = logging.getLogger(__name__)


class OAuthManager:
    """
    Manages OAuth tokens in OS keychain.

    Supports macOS (via Keychain), Linux (via secretstorage/GNOME Keyring/KWallet),
    and Windows (via Credential Manager).

    Tokens are stored with keys like: "assistant_gmail_access_token"
    """

    def __init__(self):
        self._memory_cache: Dict[str, OAuthToken] = {}  # Fallback when keyring unavailable
        self._provider_prefix = "assistant_"

    def _get_key(self, provider: str, token_type: str = "access_token") -> str:
        """Generate key for keychain storage"""
        return f"{self._provider_prefix}{provider}_{token_type}"

    async def store_token(self, provider: str, token: OAuthToken) -> None:
        """
        Store OAuth token in OS keychain.

        Args:
            provider: Provider name (e.g., "gmail", "outlook")
            token: OAuthToken to store

        Raises:
            RuntimeError: If keyring is not available and memory fallback fails
        """
        key = self._get_key(provider)

        try:
            if KEYRING_AVAILABLE:
                # Store access token
                keyring.set_password(key, "access_token", token.access_token)

                # Store refresh token if present
                if token.refresh_token:
                    keyring.set_password(key, "refresh_token", token.refresh_token)

                # Store metadata as JSON
                import json
                metadata = {
                    "token_type": token.token_type,
                    "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                    "scopes": token.scopes,
                }
                keyring.set_password(key, "metadata", json.dumps(metadata))

                logger.info(f"Stored OAuth token for provider: {provider}")
            else:
                # Fallback to memory
                self._memory_cache[provider] = token
                logger.warning(f"OAuth token for '{provider}' stored in memory only (keyring unavailable)")

        except Exception as e:
            logger.error(f"Failed to store OAuth token for '{provider}': {e}")
            # Attempt memory fallback
            self._memory_cache[provider] = token
            raise RuntimeError(f"Failed to store OAuth token: {e}")

    async def get_token(self, provider: str) -> Optional[str]:
        """
        Retrieve access token from OS keychain.

        Args:
            provider: Provider name (e.g., "gmail", "outlook")

        Returns:
            Access token string, or None if not found

        Raises:
            RuntimeError: If token is expired and cannot be refreshed
        """
        key = self._get_key(provider)

        try:
            if KEYRING_AVAILABLE:
                # Try to get from keychain
                access_token = keyring.get_password(key, "access_token")

                if not access_token:
                    # Try memory fallback
                    if provider in self._memory_cache:
                        return self._memory_cache[provider].access_token
                    return None

                # Check if expired
                metadata_json = keyring.get_password(key, "metadata")
                if metadata_json:
                    import json
                    metadata = json.loads(metadata_json)

                    if metadata.get("expires_at"):
                        expires_at = datetime.fromisoformat(metadata["expires_at"])
                        if datetime.utcnow() >= expires_at:
                            # Token expired, try to refresh
                            logger.info(f"Token expired for '{provider}', attempting refresh")
                            return await self._refresh_token(provider)

                return access_token

            else:
                # Memory fallback
                if provider in self._memory_cache:
                    token = self._memory_cache[provider]

                    # Check expiry
                    if token.expires_at and datetime.utcnow() >= token.expires_at:
                        return await self._refresh_token(provider)

                    return token.access_token

                return None

        except Exception as e:
            logger.error(f"Failed to retrieve OAuth token for '{provider}': {e}")
            return None

    async def get_full_token(self, provider: str) -> Optional[OAuthToken]:
        """
        Retrieve full OAuthToken object from OS keychain.

        Args:
            provider: Provider name

        Returns:
            OAuthToken object or None
        """
        key = self._get_key(provider)

        try:
            if KEYRING_AVAILABLE:
                access_token = keyring.get_password(key, "access_token")
                refresh_token = keyring.get_password(key, "refresh_token")
                metadata_json = keyring.get_password(key, "metadata")

                if not access_token:
                    return None

                # Parse metadata
                import json
                metadata = json.loads(metadata_json) if metadata_json else {}

                return OAuthToken(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    token_type=metadata.get("token_type", "Bearer"),
                    expires_at=datetime.fromisoformat(metadata["expires_at"]) if metadata.get("expires_at") else None,
                    scopes=metadata.get("scopes", []),
                )

            else:
                return self._memory_cache.get(provider)

        except Exception as e:
            logger.error(f"Failed to retrieve full OAuth token for '{provider}': {e}")
            return None

    async def _refresh_token(self, provider: str) -> Optional[str]:
        """
        Refresh expired access token using refresh token.

        This is a placeholder - actual implementation requires provider-specific
        refresh endpoints.

        Args:
            provider: Provider name

        Returns:
            New access token, or None if refresh fails
        """
        logger.warning(f"Token refresh not implemented for provider: {provider}")

        # TODO: Implement provider-specific refresh logic
        # For example:
        # if provider == "gmail":
        #     return await self._refresh_gmail_token()
        # elif provider == "outlook":
        #     return await self._refresh_outlook_token()

        return None

    async def revoke_token(self, provider: str) -> None:
        """
        Revoke and remove OAuth token from keychain.

        Args:
            provider: Provider name
        """
        key = self._get_key(provider)

        try:
            if KEYRING_AVAILABLE:
                # Delete from keychain
                try:
                    keyring.delete_password(key, "access_token")
                except:
                    pass

                try:
                    keyring.delete_password(key, "refresh_token")
                except:
                    pass

                try:
                    keyring.delete_password(key, "metadata")
                except:
                    pass

                logger.info(f"Revoked OAuth token for provider: {provider}")

            # Also remove from memory cache
            if provider in self._memory_cache:
                del self._memory_cache[provider]

        except Exception as e:
            logger.error(f"Failed to revoke OAuth token for '{provider}': {e}")

    async def is_authenticated(self, provider: str) -> bool:
        """
        Check if provider has a valid stored token.

        Args:
            provider: Provider name

        Returns:
            True if valid token exists, False otherwise
        """
        token = await self.get_token(provider)
        return token is not None

    async def store_from_oauth_flow(
        self,
        provider: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_in: Optional[int] = None,
        token_type: str = "Bearer",
        scopes: Optional[list] = None,
    ) -> None:
        """
        Store token received from OAuth flow.

        Args:
            provider: Provider name
            access_token: Access token from OAuth callback
            refresh_token: Optional refresh token
            expires_in: Optional seconds until expiry
            token_type: Token type (default "Bearer")
            scopes: List of granted scopes
        """
        # Calculate expiry
        expires_at = None
        if expires_in:
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        token = OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            expires_at=expires_at,
            scopes=scopes or [],
        )

        await self.store_token(provider, token)


# Global singleton instance
oauth_manager = OAuthManager()
