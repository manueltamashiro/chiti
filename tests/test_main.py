"""
Tests for backend/main.py — health endpoint, WS token, history API.

Uses HTTPX async test client (FastAPI's TestClient is sync; we prefer async).
"""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app


@pytest.fixture
async def client():
    # Ensure all required DB tables exist before making API requests.
    # The lifespan may not trigger via ASGITransport in all httpx versions,
    # so we initialize explicitly here.
    from backend.memory.conversation import init_db
    await init_db()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestHealth:
    async def test_health_ok(self, client: AsyncClient):
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    async def test_security_headers_present(self, client: AsyncClient):
        res = await client.get("/health")
        assert "x-frame-options" in res.headers
        assert res.headers["x-frame-options"] == "DENY"
        assert "x-content-type-options" in res.headers


class TestWsToken:
    async def test_token_returned(self, client: AsyncClient):
        res = await client.get("/api/ws/token")
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert "." in data["token"]  # format: {expires}.{sig}

    async def test_token_is_valid(self, client: AsyncClient):
        from backend.ws.hub import ConnectionManager
        from backend.config.loader import cfg

        res = await client.get("/api/ws/token")
        token = res.json()["token"]
        assert ConnectionManager.validate_token(token, cfg.security.ws_secret)


class TestHistoryEndpoints:
    async def test_history_list_empty(self, client: AsyncClient):
        res = await client.get("/api/history")
        assert res.status_code == 200
        data = res.json()
        assert "conversations" in data
        assert isinstance(data["conversations"], list)

    async def test_history_detail_not_found(self, client: AsyncClient):
        res = await client.get("/api/history/nonexistent-id-12345")
        assert res.status_code == 404

    async def test_tool_approve_not_found(self, client: AsyncClient):
        res = await client.post("/api/tool/approve/no-such-id")
        assert res.status_code == 404

    async def test_tool_deny_not_found(self, client: AsyncClient):
        res = await client.post("/api/tool/deny/no-such-id")
        assert res.status_code == 404
