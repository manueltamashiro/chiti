"""
Tests for the generic webhook receiver (backend/proactive/webhooks.py).

Covers:
 - HMAC signature validation (valid, wrong, missing, no-secret)
 - Handler dispatch (registered, unknown source)
 - WebhookEvent fields populated correctly
 - Event listener notification
 - Payload decoding edge cases
"""

import hashlib
import hmac
import json
import pytest
from datetime import datetime

from backend.proactive.webhooks import WebhookConfig, WebhookEvent, WebhookReceiver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SECRET = "test-secret-abc123"


def _make_body(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


def _sign(body: bytes, secret: str = SECRET) -> str:
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _receiver_with_handler():
    """Return (receiver, calls_list) where calls_list records handler invocations."""
    receiver = WebhookReceiver()
    calls = []

    def handler(event_type, payload):
        calls.append({"event_type": event_type, "payload": payload})
        return {"custom_key": "custom_value"}

    receiver.register("mysvc", secret=SECRET, handler=handler)
    return receiver, calls


# ---------------------------------------------------------------------------
# 1. Valid HMAC signature is accepted
# ---------------------------------------------------------------------------


def test_valid_hmac_signature_accepted():
    receiver, calls = _receiver_with_handler()
    body = _make_body({"action": "ping"})
    headers = {"X-Hub-Signature-256": _sign(body)}
    result = receiver.receive("mysvc", headers, body)
    assert result["verified"] is True
    assert result["handled"] is True


# ---------------------------------------------------------------------------
# 2. Wrong HMAC signature raises ValueError
# ---------------------------------------------------------------------------


def test_wrong_hmac_signature_raises():
    receiver, _ = _receiver_with_handler()
    body = _make_body({"action": "ping"})
    headers = {"X-Hub-Signature-256": "sha256=deadbeef"}
    with pytest.raises(ValueError, match="signature mismatch"):
        receiver.receive("mysvc", headers, body)


# ---------------------------------------------------------------------------
# 3. Missing signature header raises ValueError when secret is configured
# ---------------------------------------------------------------------------


def test_missing_signature_raises_when_secret_configured():
    receiver, _ = _receiver_with_handler()
    body = _make_body({"action": "ping"})
    with pytest.raises(ValueError, match="Missing webhook signature header"):
        receiver.receive("mysvc", {}, body)


# ---------------------------------------------------------------------------
# 4. No secret configured → accept without verification (verified=False)
# ---------------------------------------------------------------------------


def test_no_secret_accepts_without_verification():
    receiver = WebhookReceiver()
    receiver.register("unsecured", secret="", handler=lambda et, p: {})
    body = _make_body({"event": "something"})
    result = receiver.receive("unsecured", {}, body)
    assert result["verified"] is False
    assert result["handled"] is True


# ---------------------------------------------------------------------------
# 5. Unknown source returns handled=False
# ---------------------------------------------------------------------------


def test_unknown_source_returns_not_handled():
    receiver = WebhookReceiver()
    body = _make_body({})
    result = receiver.receive("not-registered", {}, body)
    assert result["handled"] is False
    assert result["source"] == "not-registered"


# ---------------------------------------------------------------------------
# 6. Registered handler is called with correct arguments
# ---------------------------------------------------------------------------


def test_handler_called_with_correct_arguments():
    receiver, calls = _receiver_with_handler()
    payload = {"action": "push", "ref": "refs/heads/main"}
    body = _make_body(payload)
    headers = {"X-Hub-Signature-256": _sign(body)}
    receiver.receive("mysvc", headers, body)
    assert len(calls) == 1
    assert calls[0]["event_type"] == "push"
    assert calls[0]["payload"]["ref"] == "refs/heads/main"


# ---------------------------------------------------------------------------
# 7. Handler return value is merged into result dict
# ---------------------------------------------------------------------------


def test_handler_return_merged_into_result():
    receiver, _ = _receiver_with_handler()
    body = _make_body({"action": "test"})
    headers = {"X-Hub-Signature-256": _sign(body)}
    result = receiver.receive("mysvc", headers, body)
    assert result.get("custom_key") == "custom_value"


# ---------------------------------------------------------------------------
# 8. WebhookEvent fields are populated correctly
# ---------------------------------------------------------------------------


def test_webhook_event_fields_populated():
    receiver = WebhookReceiver()
    captured_events = []
    receiver.add_event_listener(captured_events.append)

    payload = {"type": "payment.succeeded", "amount": 100}
    body = _make_body(payload)

    receiver.register("stripe", secret="", handler=lambda et, p: {})
    receiver.receive("stripe", {}, body)

    assert len(captured_events) == 1
    ev = captured_events[0]
    assert isinstance(ev, WebhookEvent)
    assert ev.source == "stripe"
    assert ev.payload == payload
    assert isinstance(ev.timestamp, datetime)


# ---------------------------------------------------------------------------
# 9. X-Webhook-Signature header is also accepted
# ---------------------------------------------------------------------------


def test_x_webhook_signature_header_accepted():
    receiver = WebhookReceiver()
    receiver.register("svc2", secret=SECRET, handler=lambda et, p: {})
    body = _make_body({"event": "update"})
    sig = _sign(body)
    headers = {"X-Webhook-Signature": sig}
    result = receiver.receive("svc2", headers, body)
    assert result["verified"] is True


# ---------------------------------------------------------------------------
# 10. Header names are treated case-insensitively
# ---------------------------------------------------------------------------


def test_header_names_case_insensitive():
    receiver, _ = _receiver_with_handler()
    body = _make_body({"action": "ping"})
    headers = {"x-hub-signature-256": _sign(body)}  # lowercase
    result = receiver.receive("mysvc", headers, body)
    assert result["verified"] is True


# ---------------------------------------------------------------------------
# 11. event_type extracted from X-GitHub-Event header
# ---------------------------------------------------------------------------


def test_event_type_from_github_header():
    receiver, calls = _receiver_with_handler()
    body = _make_body({"action": "opened"})
    headers = {
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Event": "pull_request",
    }
    result = receiver.receive("mysvc", headers, body)
    assert result["event_type"] == "pull_request"
    assert calls[0]["event_type"] == "pull_request"


# ---------------------------------------------------------------------------
# 12. event_type falls back to payload field when no header present
# ---------------------------------------------------------------------------


def test_event_type_from_payload_fallback():
    receiver, calls = _receiver_with_handler()
    body = _make_body({"event": "checkout.completed"})
    headers = {"X-Hub-Signature-256": _sign(body)}
    result = receiver.receive("mysvc", headers, body)
    assert result["event_type"] == "checkout.completed"


# ---------------------------------------------------------------------------
# 13. Empty body is handled gracefully
# ---------------------------------------------------------------------------


def test_empty_body_handled_gracefully():
    receiver = WebhookReceiver()
    receiver.register("svc3", secret="", handler=lambda et, p: {})
    result = receiver.receive("svc3", {}, b"")
    assert result["handled"] is True
    assert result["event_type"] == "unknown"


# ---------------------------------------------------------------------------
# 14. Event listener is notified after dispatch
# ---------------------------------------------------------------------------


def test_event_listener_notified():
    receiver, _ = _receiver_with_handler()
    listener_events = []
    receiver.add_event_listener(listener_events.append)

    body = _make_body({"action": "ping"})
    headers = {"X-Hub-Signature-256": _sign(body)}
    receiver.receive("mysvc", headers, body)

    assert len(listener_events) == 1
    assert listener_events[0].source == "mysvc"
    assert listener_events[0].verified is True


# ---------------------------------------------------------------------------
# 15. Result timestamp is a valid ISO 8601 string
# ---------------------------------------------------------------------------


def test_result_timestamp_is_iso8601():
    receiver, _ = _receiver_with_handler()
    body = _make_body({"action": "ping"})
    headers = {"X-Hub-Signature-256": _sign(body)}
    result = receiver.receive("mysvc", headers, body)
    # Should not raise
    ts = datetime.fromisoformat(result["timestamp"])
    assert isinstance(ts, datetime)
