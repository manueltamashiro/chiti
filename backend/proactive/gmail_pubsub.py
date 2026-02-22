"""
Gmail push notifications via Google Pub/Sub

Implements P6-02: Gmail push notification receiver that replaces polling.

The Gmail API sends push notifications to a configured Pub/Sub topic when
new messages arrive. This module handles:
- Parsing the base64-encoded Pub/Sub message body
- Setting up the Gmail watch subscription
- Dispatching notifications to registered callbacks
"""

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class GmailPubSubConfig:
    """Configuration for Gmail Pub/Sub integration."""

    project_id: str
    topic_name: str
    subscription_name: str
    email_address: str


@dataclass
class GmailPushNotification:
    """Parsed Gmail push notification from Pub/Sub."""

    email_address: str
    history_id: str
    received_at: datetime = field(default_factory=datetime.utcnow)


class GmailPubSubReceiver:
    """
    Receives and parses Gmail push notifications delivered via Google Pub/Sub.

    Gmail sends push notifications in the standard Pub/Sub push delivery format:

        {
          "message": {
            "data": "<base64-encoded JSON>",
            "messageId": "...",
            "publishTime": "..."
          },
          "subscription": "projects/my-project/subscriptions/my-sub"
        }

    The base64-decoded ``data`` field contains a JSON object with:

        {
          "emailAddress": "user@example.com",
          "historyId": "12345"
        }
    """

    def parse_push_notification(self, body: bytes) -> GmailPushNotification:
        """
        Parse a raw Pub/Sub push notification body into a GmailPushNotification.

        Args:
            body: Raw HTTP request body bytes (JSON-encoded Pub/Sub envelope).

        Returns:
            GmailPushNotification with the parsed email address and history ID.

        Raises:
            ValueError: If the body is malformed or missing required fields.
        """
        if not body:
            raise ValueError("Empty notification body")

        try:
            envelope = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid JSON in notification body: {exc}") from exc

        if not isinstance(envelope, dict):
            raise ValueError("Notification body must be a JSON object")

        message = envelope.get("message")
        if not message or not isinstance(message, dict):
            raise ValueError("Missing or invalid 'message' field in Pub/Sub envelope")

        encoded_data = message.get("data")
        if not encoded_data:
            raise ValueError("Missing 'data' field in Pub/Sub message")

        try:
            # Pub/Sub uses standard base64 with padding; add padding if needed.
            padded = encoded_data + "=" * (4 - len(encoded_data) % 4) if len(encoded_data) % 4 else encoded_data
            decoded_bytes = base64.b64decode(padded)
            data = json.loads(decoded_bytes)
        except Exception as exc:
            raise ValueError(f"Failed to decode Pub/Sub message data: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError("Decoded Pub/Sub data must be a JSON object")

        email_address = data.get("emailAddress")
        history_id = data.get("historyId")

        if not email_address:
            raise ValueError("Missing 'emailAddress' in Gmail push notification data")
        if not history_id:
            raise ValueError("Missing 'historyId' in Gmail push notification data")

        return GmailPushNotification(
            email_address=str(email_address),
            history_id=str(history_id),
        )

    def handle_notification(
        self, notification: GmailPushNotification, callback: Callable
    ) -> None:
        """
        Invoke ``callback`` with the parsed notification.

        Args:
            notification: Parsed GmailPushNotification.
            callback:     Callable that accepts a single GmailPushNotification argument.
        """
        try:
            callback(notification)
        except Exception as exc:
            logger.error(
                "Callback raised an exception while handling Gmail push notification "
                "for %s (history_id=%s): %s",
                notification.email_address,
                notification.history_id,
                exc,
                exc_info=True,
            )
            raise


def setup_pubsub_watch(
    credentials_dict: dict,
    user_id: str,
    topic_name: str,
) -> dict:
    """
    Register a Gmail push-notification watch via the Gmail API ``users.watch()`` call.

    Args:
        credentials_dict: Google OAuth2 credentials as a dictionary
                          (e.g. ``{"token": "...", "refresh_token": "...", ...}``).
        user_id:          Gmail user ID (e.g. ``"me"`` or a full email address).
        topic_name:       Pub/Sub topic resource name, e.g.
                          ``"projects/my-project/topics/gmail-notifications"``.

    Returns:
        The raw response dictionary from ``users.watch()``, containing
        ``historyId`` and ``expiration`` fields.

    Raises:
        ImportError: If ``google-api-python-client`` or ``google-auth`` are not installed.
        RuntimeError: If the API call fails.
    """
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ImportError(
            "google-api-python-client and google-auth are required for Pub/Sub watch setup. "
            "Install them with: pip install google-api-python-client google-auth"
        ) from exc

    try:
        creds = Credentials(
            token=credentials_dict.get("token"),
            refresh_token=credentials_dict.get("refresh_token"),
            token_uri=credentials_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=credentials_dict.get("client_id"),
            client_secret=credentials_dict.get("client_secret"),
            scopes=credentials_dict.get("scopes"),
        )

        service = build("gmail", "v1", credentials=creds)

        request_body = {
            "labelIds": ["INBOX"],
            "topicName": topic_name,
        }

        response = (
            service.users()
            .watch(userId=user_id, body=request_body)
            .execute()
        )

        logger.info(
            "Gmail watch registered for user=%s, topic=%s, historyId=%s, expiration=%s",
            user_id,
            topic_name,
            response.get("historyId"),
            response.get("expiration"),
        )
        return response

    except Exception as exc:
        raise RuntimeError(f"Failed to set up Gmail Pub/Sub watch: {exc}") from exc


# Module-level singleton receiver
gmail_pubsub_receiver = GmailPubSubReceiver()
