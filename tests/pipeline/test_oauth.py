"""
Tests for OAuth Manager
"""

import pytest
from datetime import datetime, timedelta

from backend.storage.oauth import OAuthManager
from backend.pipeline.models import OAuthToken


class TestOAuthManager:
    """Test OAuth token management"""

    def setup_method(self):
        """Create fresh manager for each test"""
        self.manager = OAuthManager()

    @pytest.mark.asyncio
    async def test_store_and_retrieve_token(self):
        """Test storing and retrieving a token"""
        token = OAuthToken(
            access_token="test_access_token_123",
            refresh_token="test_refresh_token_456",
            token_type="Bearer",
            scopes=["gmail.readonly"],
        )

        await self.manager.store_token("gmail", token)

        # Retrieve
        retrieved = await self.manager.get_token("gmail")

        assert retrieved == "test_access_token_123"

    @pytest.mark.asyncio
    async def test_retrieve_full_token(self):
        """Test retrieving full token object"""
        token = OAuthToken(
            access_token="test_access_token",
            refresh_token="test_refresh_token",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
            scopes=["gmail.readonly"],
        )

        await self.manager.store_token("gmail", token)

        retrieved = await self.manager.get_full_token("gmail")

        assert retrieved is not None
        assert retrieved.access_token == "test_access_token"
        assert retrieved.refresh_token == "test_refresh_token"
        assert retrieved.token_type == "Bearer"
        assert retrieved.scopes == ["gmail.readonly"]

    @pytest.mark.asyncio
    async def test_expired_token(self):
        """Test handling of expired tokens"""
        # Create expired token
        token = OAuthToken(
            access_token="expired_token",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )

        await self.manager.store_token("test_provider", token)

        # Should attempt refresh (returns None for now, as refresh not implemented)
        retrieved = await self.manager.get_token("test_provider")

        # Will return None since refresh not implemented
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_revoke_token(self):
        """Test revoking a token"""
        token = OAuthToken(access_token="to_be_revoked")

        await self.manager.store_token("test", token)
        assert await self.manager.get_token("test") is not None

        await self.manager.revoke_token("test")
        assert await self.manager.get_token("test") is None

    @pytest.mark.asyncio
    async def test_is_authenticated(self):
        """Test authentication status check"""
        # Not authenticated initially
        assert not await self.manager.is_authenticated("new_provider")

        # After storing token
        token = OAuthToken(access_token="authenticated")
        await self.manager.store_token("new_provider", token)

        assert await self.manager.is_authenticated("new_provider")

    @pytest.mark.asyncio
    async def test_store_from_oauth_flow(self):
        """Test storing token from OAuth callback"""
        await self.manager.store_from_oauth_flow(
            provider="gmail",
            access_token="oauth_flow_token",
            refresh_token="oauth_flow_refresh",
            expires_in=3600,  # 1 hour
            scopes=["gmail.readonly", "gmail.labels"],
        )

        retrieved = await self.manager.get_full_token("gmail")

        assert retrieved.access_token == "oauth_flow_token"
        assert retrieved.refresh_token == "oauth_flow_refresh"
        assert retrieved.expires_at is not None
        assert retrieved.scopes == ["gmail.readonly", "gmail.labels"]

    @pytest.mark.asyncio
    async def test_memory_fallback_when_keyring_unavailable(self):
        """Test memory fallback when keyring is not available"""
        # This tests the fallback path - would need to mock keyring being unavailable
        # For now, just test that it works with memory
        token = OAuthToken(access_token="memory_token")

        await self.manager.store_token("memory_test", token)

        # Even without keyring, should store in memory
        assert "memory_test" in self.manager._memory_cache
        assert await self.manager.get_token("memory_test") == "memory_token"
