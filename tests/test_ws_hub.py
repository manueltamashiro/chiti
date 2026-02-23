"""
Tests for backend/ws/hub.py — token creation, validation, connection manager
"""

import time
import pytest
from backend.ws.hub import ConnectionManager

SECRET = "test-secret-key"


class TestTokenValidation:
    def test_valid_token_accepted(self):
        token = ConnectionManager.create_token(SECRET, ttl_minutes=15)
        assert ConnectionManager.validate_token(token, SECRET)

    def test_wrong_secret_rejected(self):
        token = ConnectionManager.create_token(SECRET, ttl_minutes=15)
        assert not ConnectionManager.validate_token(token, "wrong-secret")

    def test_malformed_token_rejected(self):
        assert not ConnectionManager.validate_token("notavalidtoken", SECRET)
        assert not ConnectionManager.validate_token("", SECRET)
        assert not ConnectionManager.validate_token("a.b.c", SECRET)

    def test_expired_token_rejected(self):
        # Token that expired 1 second ago
        expires = int(time.time()) - 1
        import hashlib, hmac
        payload = str(expires)
        sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = f"{expires}.{sig}"
        assert not ConnectionManager.validate_token(token, SECRET)

    def test_token_format(self):
        token = ConnectionManager.create_token(SECRET)
        parts = token.split(".")
        assert len(parts) == 2
        expires = int(parts[0])
        assert expires > int(time.time())


class TestConnectionManager:
    def test_client_count_starts_zero(self):
        mgr = ConnectionManager()
        assert mgr.client_count == 0

    def test_disconnect_nonexistent_is_safe(self):
        mgr = ConnectionManager()
        mgr.disconnect("ghost-id")  # should not raise
