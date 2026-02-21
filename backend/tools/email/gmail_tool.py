"""
Gmail tool - Read and manage Gmail messages via OAuth API

This is an example tool demonstrating OAuth integration.
Tokens are stored in OS keychain via oauth_manager.
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

from backend.tools.base import ToolBase
from backend.pipeline.models import (
    CapabilityMetadata,
    CapabilityType,
    ActionTier,
    OutputBlock,
)

logger = logging.getLogger(__name__)


class GmailTool(ToolBase):
    """
    Read and manage Gmail messages.

    This tool requires OAuth authentication. Users must complete the OAuth flow
    before using this tool. Tokens are automatically stored in the OS keychain.
    """

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="gmail",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,  # Read-only
            description="Read Gmail messages from your inbox",
            requires_network=True,
            requires_oauth="gmail",
            allowed_scopes=["gmail.readonly"],
            max_runtime_seconds=30,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        """
        Execute Gmail operation.

        Supported operations:
        - list: List messages matching query
        - get: Get a specific message by ID
        - search: Search for messages

        Args:
            params: {
                "operation": "list" | "get" | "search",
                "query": Gmail search query (for list/search),
                "message_id": Message ID (for get),
                "limit": Max results (default 10),
            }
        """
        from backend.storage.oauth import oauth_manager

        # Get OAuth token
        access_token = await oauth_manager.get_token("gmail")

        if not access_token:
            return OutputBlock(
                type="notification",
                content={
                    "level": "warning",
                    "title": "Gmail Not Connected",
                    "message": "Please complete Gmail OAuth flow first",
                    "action_url": "/auth/oauth/gmail/start"
                },
                metadata={"requires_auth": True}
            )

        operation = params.get("operation", "list")

        try:
            if operation == "list":
                return await self._list_messages(access_token, params)
            elif operation == "get":
                return await self._get_message(access_token, params)
            elif operation == "search":
                return await self._search_messages(access_token, params)
            else:
                raise ValueError(f"Unknown operation: {operation}")

        except Exception as e:
            logger.error(f"Gmail operation failed: {e}")
            return OutputBlock(
                type="notification",
                content={
                    "level": "error",
                    "title": "Gmail Error",
                    "message": str(e),
                },
                metadata={"error": str(e)}
            )

    async def _list_messages(self, access_token: str, params: Dict[str, Any]) -> OutputBlock:
        """List Gmail messages"""
        # This is a mock implementation
        # Real implementation would use Google API Client
        query = params.get("query", "is:unread")
        limit = params.get("limit", 10)

        # TODO: Implement actual Gmail API call
        # import googleapiclient.discovery
        # service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)
        # results = service.users().messages().list(userId='me', q=query, maxResults=limit).execute()

        # Mock response for now
        messages = [
            {
                "id": "12345",
                "threadId": "67890",
                "snippet": "This is a mock email message...",
                "from": "sender@example.com",
                "subject": "Test Subject",
                "date": datetime.utcnow().isoformat(),
            }
        ]

        return OutputBlock(
            type="table",
            content={
                "columns": ["ID", "From", "Subject", "Date"],
                "rows": [
                    [msg["id"], msg["from"], msg["subject"], msg["date"]]
                    for msg in messages
                ],
                "metadata": {
                    "query": query,
                    "total": len(messages),
                }
            }
        )

    async def _get_message(self, access_token: str, params: Dict[str, Any]) -> OutputBlock:
        """Get a specific Gmail message"""
        message_id = params.get("message_id")

        if not message_id:
            raise ValueError("message_id is required for 'get' operation")

        # TODO: Implement actual Gmail API call
        message = {
            "id": message_id,
            "threadId": "67890",
            "snippet": "Full email content would be here...",
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test Subject",
            "body": "This is the full email body...",
            "date": datetime.utcnow().isoformat(),
        }

        return OutputBlock(
            type="text",
            content=f"""
From: {message['from']}
To: {message['to']}
Subject: {message['subject']}
Date: {message['date']}

{message['body']}
            """.strip(),
            metadata={"message_id": message_id}
        )

    async def _search_messages(self, access_token: str, params: Dict[str, Any]) -> OutputBlock:
        """Search Gmail messages"""
        query = params.get("query", "")
        limit = params.get("limit", 10)

        # TODO: Implement actual Gmail API search
        results = [
            {
                "id": "12345",
                "snippet": f"Result for query: {query}",
            }
        ]

        return OutputBlock(
            type="table",
            content={
                "columns": ["ID", "Snippet"],
                "rows": [[r["id"], r["snippet"]] for r in results],
                "metadata": {"query": query, "total": len(results)},
            }
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Return JSON schema for parameters"""
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list", "get", "search"],
                    "description": "Operation to perform"
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query (e.g., 'is:unread', 'from:boss@example.com')"
                },
                "message_id": {
                    "type": "string",
                    "description": "Message ID (required for 'get' operation)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 10)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                }
            },
            "required": ["operation"]
        }
