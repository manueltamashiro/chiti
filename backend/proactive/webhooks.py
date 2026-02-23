"""
Webhook Receiver — P6-01 / P6-07

Generic HMAC-validated webhook receiver with pluggable per-source handlers.
Emits structured ``WebhookEvent`` objects that can be forwarded to a
notification bus, stored, or acted on by downstream consumers.

---------------------------------------------------------------------------
How to wire up any service
---------------------------------------------------------------------------

**Step 1 — Get a shared secret from the service**

Every webhook service lets you configure a "secret" that it uses to sign
outgoing requests.  Copy that secret; you will pass it to ``register()``.

**Step 2 — Write a handler function**

A handler is any callable that accepts ``(event_type: str, payload: dict)``
and returns a ``dict``.  The return value is merged into the final result
that ``receive()`` sends back to the HTTP layer.

Example::

    def my_handler(event_type: str, payload: dict) -> dict:
        print(f"Got {event_type!r} from my-service: {payload}")
        return {"processed": True}

**Step 3 — Register the handler before the first request arrives**

    from backend.proactive.webhooks import webhook_receiver

    webhook_receiver.register(
        source="my-service",
        secret="s3cr3t-from-dashboard",
        handler=my_handler,
    )

**Step 4 — Route HTTP requests to ``receive()``**

In your HTTP layer (FastAPI, Flask, aiohttp, …) extract the raw body
*before* any JSON parsing, then call::

    result = webhook_receiver.receive(
        source="my-service",
        headers=dict(request.headers),   # plain dict of str→str
        body=await request.body(),       # raw bytes
    )

``receive()`` returns a dict like::

    {
        "handled": True,
        "source": "my-service",
        "event_type": "...",
        "verified": True,
        "timestamp": "2024-01-01T00:00:00",
    }

**Signature headers understood out of the box**

+-----------------------------+--------------------------------+
| Header                      | Format                         |
+=============================+================================+
| ``X-Hub-Signature-256``     | ``sha256=<hex>`` (GitHub)      |
| ``X-Webhook-Signature``     | ``sha256=<hex>`` or ``<hex>``  |
+-----------------------------+--------------------------------+

Any other header can be supported by subclassing ``WebhookReceiver`` and
overriding ``_extract_signature()``.

**Accepting unsigned webhooks**

Register the source with ``secret=""`` (empty string).  The event will still
be dispatched but ``WebhookEvent.verified`` will be ``False``.

**Full example — Stripe**

    def stripe_handler(event_type: str, payload: dict) -> dict:
        if event_type == "payment_intent.succeeded":
            amount = payload["data"]["object"]["amount"]
            print(f"Payment of {amount} succeeded")
        return {}

    webhook_receiver.register("stripe", secret=os.environ["STRIPE_WEBHOOK_SECRET"],
                              handler=stripe_handler)

    # In your FastAPI route:
    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        return webhook_receiver.receive(
            source="stripe",
            headers=dict(request.headers),
            body=await request.body(),
        )

    # Stripe uses "Stripe-Signature" with a different format — override
    # _extract_signature() or pre-validate with the stripe SDK before calling
    # receive().

---------------------------------------------------------------------------
"""

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WebhookConfig:
    """Configuration for a registered webhook source.

    Attributes:
        source:  Logical name for the source (e.g. ``"github"``, ``"stripe"``).
        secret:  Shared secret used to verify HMAC signatures.  Pass an empty
                 string to accept unsigned payloads (``verified=False``).
        handler: Callable invoked with ``(event_type, payload)`` when a
                 request is received for this source.
    """

    source: str
    secret: str
    handler: Callable[[str, Dict[str, Any]], Dict[str, Any]]


@dataclass
class WebhookEvent:
    """A fully-parsed and (optionally) verified webhook event.

    Attributes:
        source:     Logical name of the originating service.
        event_type: Service-specific event identifier (e.g. ``"push"``,
                    ``"payment.succeeded"``).
        payload:    Decoded JSON payload as a Python dict.
        verified:   ``True`` if the HMAC signature was valid; ``False`` if
                    the source has no secret configured or signature mismatch
                    was tolerated.
        timestamp:  UTC time at which the event was received.
        raw_headers: Original HTTP headers for debugging / downstream use.
    """

    source: str
    event_type: str
    payload: Dict[str, Any]
    verified: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)
    raw_headers: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------


class WebhookReceiver:
    """
    Central webhook receiver that validates signatures and dispatches events.

    Typical usage::

        receiver = WebhookReceiver()

        def my_handler(event_type, payload):
            ...
            return {}

        receiver.register("github", secret="abc123", handler=my_handler)

        result = receiver.receive(
            source="github",
            headers=request.headers,
            body=await request.body(),
        )
    """

    def __init__(self) -> None:
        self._configs: Dict[str, WebhookConfig] = {}
        self._event_listeners: List[Callable[[WebhookEvent], None]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        source: str,
        secret: str,
        handler: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        """Register a handler for *source*.

        Args:
            source:  Logical name (must match the ``source`` argument passed
                     to :meth:`receive`).
            secret:  HMAC-SHA256 signing secret.  Use ``""`` to accept
                     unsigned payloads.
            handler: Callable ``(event_type: str, payload: dict) -> dict``.
        """
        self._configs[source] = WebhookConfig(source=source, secret=secret, handler=handler)
        logger.info("Registered webhook handler for source=%r", source)

    def add_event_listener(self, listener: Callable[[WebhookEvent], None]) -> None:
        """Subscribe to all ``WebhookEvent`` objects emitted by this receiver.

        Args:
            listener: Callable that receives a :class:`WebhookEvent`.  May be
                      sync or async (async listeners are called without
                      ``await`` — wrap in a task if needed).
        """
        self._event_listeners.append(listener)

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    def receive(
        self,
        source: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        """Validate signature, dispatch to handler, emit event.

        Args:
            source:  Logical name of the incoming source.
            headers: HTTP headers as a plain ``str → str`` dict.  Header
                     names are treated case-insensitively.
            body:    Raw request body bytes (do **not** pass decoded JSON).

        Returns:
            A dict with at minimum::

                {
                    "handled":    bool,
                    "source":     str,
                    "event_type": str,
                    "verified":   bool,
                    "timestamp":  str,   # ISO 8601
                }

            Additional keys may be present if the handler returns extra data.

        Raises:
            ValueError: If signature validation fails and a secret is
                        configured for the source.
        """
        # Normalize header keys to lowercase for consistent lookup
        norm_headers: Dict[str, str] = {k.lower(): v for k, v in headers.items()}

        config = self._configs.get(source)
        if config is None:
            logger.warning("Received webhook for unregistered source=%r", source)
            return {
                "handled": False,
                "source": source,
                "event_type": "unknown",
                "verified": False,
                "timestamp": datetime.utcnow().isoformat(),
            }

        # --- Signature verification ---
        verified = self._verify_signature(config.secret, norm_headers, body)

        # --- Decode payload ---
        payload = self._decode_payload(body)

        # --- Determine event type ---
        event_type = self._extract_event_type(source, norm_headers, payload)

        # --- Build event ---
        event = WebhookEvent(
            source=source,
            event_type=event_type,
            payload=payload,
            verified=verified,
            raw_headers=norm_headers,
        )

        # --- Dispatch to handler ---
        handler_result: Dict[str, Any] = {}
        try:
            handler_result = config.handler(event_type, payload) or {}
        except Exception as exc:
            logger.error("Handler error for source=%r event_type=%r: %s", source, event_type, exc)
            handler_result = {"handler_error": str(exc)}

        # --- Emit to listeners ---
        self._emit(event)

        result: Dict[str, Any] = {
            "handled": True,
            "source": source,
            "event_type": event_type,
            "verified": verified,
            "timestamp": event.timestamp.isoformat(),
        }
        result.update(handler_result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _verify_signature(
        self,
        secret: str,
        norm_headers: Dict[str, str],
        body: bytes,
    ) -> bool:
        """Return True if the payload signature is valid (or no secret is set)."""
        if not secret:
            # No secret configured — accept but mark unverified
            return False

        raw_sig = self._extract_signature(norm_headers)
        if raw_sig is None:
            logger.warning("No signature header found; rejecting payload")
            raise ValueError("Missing webhook signature header")

        # Strip "sha256=" prefix if present
        if raw_sig.startswith("sha256="):
            provided_hex = raw_sig[len("sha256="):]
        else:
            provided_hex = raw_sig

        # Compute expected HMAC-SHA256
        expected_hex = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_hex, provided_hex):
            raise ValueError("Webhook signature mismatch")

        return True

    @staticmethod
    def _extract_signature(norm_headers: Dict[str, str]) -> Optional[str]:
        """Return the raw signature string from known headers, or None."""
        # GitHub-style
        if "x-hub-signature-256" in norm_headers:
            return norm_headers["x-hub-signature-256"]
        # Generic fallback
        if "x-webhook-signature" in norm_headers:
            return norm_headers["x-webhook-signature"]
        return None

    @staticmethod
    def _decode_payload(body: bytes) -> Dict[str, Any]:
        """Decode JSON body, returning empty dict on failure."""
        if not body:
            return {}
        try:
            decoded = json.loads(body.decode("utf-8"))
            if isinstance(decoded, dict):
                return decoded
            return {"data": decoded}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Failed to decode webhook body: %s", exc)
            return {}

    @staticmethod
    def _extract_event_type(
        source: str,
        norm_headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> str:
        """Best-effort extraction of an event type string.

        Checks common headers first, then falls back to payload fields.
        """
        # GitHub
        if "x-github-event" in norm_headers:
            return norm_headers["x-github-event"]
        # Stripe / generic
        if "x-event-type" in norm_headers:
            return norm_headers["x-event-type"]
        # Payload-level
        for key in ("event", "type", "event_type", "action"):
            if key in payload and isinstance(payload[key], str):
                return payload[key]
        return "unknown"

    def _emit(self, event: WebhookEvent) -> None:
        """Notify all registered event listeners."""
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as exc:
                logger.error("Event listener error: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

webhook_receiver = WebhookReceiver()
"""
Module-level singleton ``WebhookReceiver``.

Import and use this directly in your application so all handlers share the
same registry::

    from backend.proactive.webhooks import webhook_receiver

    webhook_receiver.register("my-service", secret="...", handler=my_fn)
"""
