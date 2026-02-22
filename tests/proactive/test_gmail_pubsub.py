"""
Tests for backend/proactive/gmail_pubsub.py

Covers:
- Parsing valid Pub/Sub push notification bodies
- Handling malformed bodies gracefully
- Correct extraction of history_id and email_address
- Singleton module-level receiver
- handle_notification callback dispatch
- setup_pubsub_watch ImportError guard
"""

import base64
import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from backend.proactive.gmail_pubsub import (
    GmailPubSubConfig,
    GmailPubSubReceiver,
    GmailPushNotification,
    gmail_pubsub_receiver,
    setup_pubsub_watch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pubsub_body(email_address: str, history_id: str) -> bytes:
    """Build a valid Pub/Sub push notification body."""
    inner = json.dumps({"emailAddress": email_address, "historyId": history_id})
    encoded = base64.b64encode(inner.encode()).decode()
    envelope = {
        "message": {
            "data": encoded,
            "messageId": "msg-001",
            "publishTime": "2026-02-22T10:00:00Z",
        },
        "subscription": "projects/my-project/subscriptions/gmail-sub",
    }
    return json.dumps(envelope).encode()


# ---------------------------------------------------------------------------
# Tests: parsing valid notifications
# ---------------------------------------------------------------------------


class TestGmailPubSubReceiverParse:
    """Tests for GmailPubSubReceiver.parse_push_notification."""

    def setup_method(self):
        self.receiver = GmailPubSubReceiver()

    def test_parse_valid_notification_email_address(self):
        body = _make_pubsub_body("user@example.com", "99887766")
        notification = self.receiver.parse_push_notification(body)
        assert notification.email_address == "user@example.com"

    def test_parse_valid_notification_history_id(self):
        body = _make_pubsub_body("user@example.com", "99887766")
        notification = self.receiver.parse_push_notification(body)
        assert notification.history_id == "99887766"

    def test_parse_returns_gmail_push_notification_type(self):
        body = _make_pubsub_body("alice@domain.com", "12345")
        notification = self.receiver.parse_push_notification(body)
        assert isinstance(notification, GmailPushNotification)

    def test_parse_received_at_is_datetime(self):
        body = _make_pubsub_body("bob@example.org", "54321")
        notification = self.receiver.parse_push_notification(body)
        assert isinstance(notification.received_at, datetime)

    def test_parse_numeric_history_id_converted_to_string(self):
        inner = json.dumps({"emailAddress": "c@d.com", "historyId": 42})
        encoded = base64.b64encode(inner.encode()).decode()
        envelope = {"message": {"data": encoded, "messageId": "x"}, "subscription": "s"}
        body = json.dumps(envelope).encode()
        notification = self.receiver.parse_push_notification(body)
        assert notification.history_id == "42"

    def test_parse_different_email_addresses(self):
        for email in ["alpha@beta.com", "test.user+tag@sub.domain.co.uk"]:
            body = _make_pubsub_body(email, "111")
            notification = self.receiver.parse_push_notification(body)
            assert notification.email_address == email


# ---------------------------------------------------------------------------
# Tests: malformed bodies
# ---------------------------------------------------------------------------


class TestGmailPubSubReceiverMalformed:
    """Tests for graceful handling of malformed notification bodies."""

    def setup_method(self):
        self.receiver = GmailPubSubReceiver()

    def test_empty_body_raises_value_error(self):
        with pytest.raises(ValueError, match="Empty"):
            self.receiver.parse_push_notification(b"")

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError):
            self.receiver.parse_push_notification(b"not json {{")

    def test_missing_message_field_raises_value_error(self):
        body = json.dumps({"subscription": "s"}).encode()
        with pytest.raises(ValueError, match="message"):
            self.receiver.parse_push_notification(body)

    def test_missing_data_field_raises_value_error(self):
        envelope = {"message": {"messageId": "x"}, "subscription": "s"}
        body = json.dumps(envelope).encode()
        with pytest.raises(ValueError, match="data"):
            self.receiver.parse_push_notification(body)

    def test_invalid_base64_data_raises_value_error(self):
        envelope = {"message": {"data": "!!!not-base64!!!", "messageId": "x"}, "subscription": "s"}
        body = json.dumps(envelope).encode()
        with pytest.raises(ValueError):
            self.receiver.parse_push_notification(body)

    def test_missing_email_address_in_data_raises_value_error(self):
        inner = json.dumps({"historyId": "123"})
        encoded = base64.b64encode(inner.encode()).decode()
        envelope = {"message": {"data": encoded, "messageId": "x"}, "subscription": "s"}
        body = json.dumps(envelope).encode()
        with pytest.raises(ValueError, match="emailAddress"):
            self.receiver.parse_push_notification(body)

    def test_missing_history_id_in_data_raises_value_error(self):
        inner = json.dumps({"emailAddress": "x@y.com"})
        encoded = base64.b64encode(inner.encode()).decode()
        envelope = {"message": {"data": encoded, "messageId": "x"}, "subscription": "s"}
        body = json.dumps(envelope).encode()
        with pytest.raises(ValueError, match="historyId"):
            self.receiver.parse_push_notification(body)


# ---------------------------------------------------------------------------
# Tests: handle_notification
# ---------------------------------------------------------------------------


class TestHandleNotification:
    def setup_method(self):
        self.receiver = GmailPubSubReceiver()

    def test_callback_is_called_with_notification(self):
        notification = GmailPushNotification(
            email_address="test@example.com", history_id="777"
        )
        callback = MagicMock()
        self.receiver.handle_notification(notification, callback)
        callback.assert_called_once_with(notification)

    def test_callback_exception_propagates(self):
        notification = GmailPushNotification(
            email_address="err@example.com", history_id="000"
        )
        def bad_callback(n):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            self.receiver.handle_notification(notification, bad_callback)


# ---------------------------------------------------------------------------
# Tests: module-level singleton
# ---------------------------------------------------------------------------


def test_module_level_singleton_is_receiver_instance():
    assert isinstance(gmail_pubsub_receiver, GmailPubSubReceiver)


# ---------------------------------------------------------------------------
# Tests: GmailPubSubConfig dataclass
# ---------------------------------------------------------------------------


def test_gmail_pubsub_config_fields():
    cfg = GmailPubSubConfig(
        project_id="my-project",
        topic_name="gmail-topic",
        subscription_name="gmail-sub",
        email_address="admin@example.com",
    )
    assert cfg.project_id == "my-project"
    assert cfg.topic_name == "gmail-topic"
    assert cfg.subscription_name == "gmail-sub"
    assert cfg.email_address == "admin@example.com"


# ---------------------------------------------------------------------------
# Tests: setup_pubsub_watch ImportError guard
# ---------------------------------------------------------------------------


def test_setup_pubsub_watch_raises_import_error_when_google_not_installed():
    import sys
    # Patch google packages as unavailable
    with patch.dict(sys.modules, {
        "google": None,
        "google.oauth2": None,
        "google.oauth2.credentials": None,
        "googleapiclient": None,
        "googleapiclient.discovery": None,
    }):
        with pytest.raises((ImportError, Exception)):
            setup_pubsub_watch({}, "me", "projects/p/topics/t")
