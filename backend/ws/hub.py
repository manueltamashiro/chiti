"""
ws/hub.py — WebSocket connection hub.

Single WebSocket connection per authenticated client. All server-to-client events
flow through this hub so callers never touch raw WebSocket objects directly.

Event envelope (TypeScript interface match):
    { event: "chat.token";        data: OutputBlock }
    { event: "notification.new";  data: Notification }
    { event: "job.started";       data: JobStatus }
    { event: "job.completed";     data: JobResult }
    { event: "dashboard.update";  data: DashboardPatch }
    { event: "file.changed";      data: FileWatchEvent }
    { event: "heartbeat.alert";   data: HeartbeatResult }
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from backend.config.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """
    Manages all active WebSocket connections.

    Clients authenticate with a signed session token (HMAC-SHA256) on connect.
    After validation the client is assigned a stable client_id for the session.
    """

    def __init__(self) -> None:
        # client_id → WebSocket
        self._connections: Dict[str, WebSocket] = {}

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    @staticmethod
    def create_token(secret: str, ttl_minutes: int = 15) -> str:
        """Create a signed WS session token valid for `ttl_minutes`."""
        expires = int(time.time()) + ttl_minutes * 60
        payload = f"{expires}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{sig}"

    @staticmethod
    def validate_token(token: str, secret: str) -> bool:
        """Return True if the token is valid and not expired."""
        try:
            parts = token.split(".")
            if len(parts) != 2:
                return False
            expires_str, sig = parts
            expires = int(expires_str)
            if time.time() > expires:
                return False
            expected = hmac.new(
                secret.encode(), expires_str.encode(), hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, sig)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(
        self,
        websocket: WebSocket,
        token: str,
        secret: str,
        client_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Accept and register a WebSocket connection after token validation.

        Returns the client_id if successful, None if the token is invalid.
        """
        if not self.validate_token(token, secret):
            await websocket.close(code=4001, reason="invalid or expired token")
            logger.warning("WS connect rejected: invalid token")
            return None

        cid = client_id or str(uuid.uuid4())
        await websocket.accept()
        self._connections[cid] = websocket
        logger.info("WS client connected", extra={"client_id": cid, "total": len(self._connections)})
        return cid

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        logger.info("WS client disconnected", extra={"client_id": client_id})

    # ------------------------------------------------------------------
    # Sending events
    # ------------------------------------------------------------------

    async def send_to_client(self, client_id: str, event: str, data: Any) -> None:
        """Send a typed event to a single client."""
        ws = self._connections.get(client_id)
        if not ws:
            return
        try:
            await ws.send_text(json.dumps({"event": event, "data": data}))
        except Exception as exc:
            logger.warning(
                "WS send failed",
                extra={"client_id": client_id, "event": event, "error": str(exc)},
            )
            self.disconnect(client_id)

    async def broadcast(self, event: str, data: Any) -> None:
        """Send a typed event to all connected clients."""
        dead: list[str] = []
        payload = json.dumps({"event": event, "data": data})
        for cid, ws in list(self._connections.items()):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self.disconnect(cid)

    @property
    def client_count(self) -> int:
        return len(self._connections)


# Singleton hub
ws_hub = ConnectionManager()
