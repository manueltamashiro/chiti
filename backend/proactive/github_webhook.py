"""
GitHub Webhook Handler — P6-06

Parses GitHub webhook payloads for pull request, issue, and CI events, and
produces structured ``WebhookEvent`` / ``OutputBlock`` objects for the
notification pipeline.

---------------------------------------------------------------------------
Wiring up GitHub Webhooks
---------------------------------------------------------------------------

**Step 1 — Create a webhook in GitHub**

Go to your repository (or organisation) → *Settings* → *Webhooks* →
*Add webhook*.

* **Payload URL**: ``https://your-host/webhooks/github``
* **Content type**: ``application/json``
* **Secret**: a random string — copy it
* **Events**: choose *Let me select individual events* and tick:
  - Pull requests
  - Issues
  - Check runs  (for CI status)
  - Workflow runs  (for Actions CI)

**Step 2 — Register the handler**

    import os
    from backend.proactive.webhooks import webhook_receiver
    from backend.proactive.github_webhook import register_with_receiver

    register_with_receiver(webhook_receiver, secret=os.environ["GITHUB_WEBHOOK_SECRET"])

**Step 3 — Route HTTP traffic**

In your HTTP layer::

    @app.post("/webhooks/github")
    async def github_webhook(request: Request):
        result = webhook_receiver.receive(
            source="github",
            headers=dict(request.headers),
            body=await request.body(),
        )
        return result

That is all.  The handler emits ``OutputBlock`` notifications that can be
forwarded to the frontend notification bus or stored.

---------------------------------------------------------------------------
Supported events
---------------------------------------------------------------------------

+--------------------+-------------------------------+
| GitHub event       | Actions handled               |
+====================+===============================+
| ``pull_request``   | opened, closed (merged/not)   |
| ``issues``         | opened, closed                |
| ``check_run``      | completed (success/failure)   |
| ``workflow_run``   | completed (success/failure)   |
+--------------------+-------------------------------+

---------------------------------------------------------------------------
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from backend.pipeline.models import OutputBlock
from backend.proactive.webhooks import WebhookEvent, WebhookReceiver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal event representation
# ---------------------------------------------------------------------------


@dataclass
class GitHubEvent:
    """Parsed, normalised GitHub event.

    Attributes:
        event_type:  Raw GitHub event type (``pull_request``, ``issues``, …).
        action:      Action within the event (``opened``, ``closed``, …).
        repo:        Full repository name (``owner/name``).
        title:       Human-readable title for the event (PR title, issue
                     title, workflow name, …).
        url:         HTML URL to the object on GitHub.
        actor:       GitHub login of the user who triggered the event.
        number:      PR or issue number (``None`` for CI events).
        merged:      ``True`` when a PR was merged on close.
        ci_status:   ``"success"``, ``"failure"``, ``"cancelled"``, … for CI.
        branch:      Head branch for PR / CI events.
        verified:    Whether the originating ``WebhookEvent`` was HMAC-verified.
        timestamp:   UTC reception time.
        raw:         Complete original payload for custom downstream use.
    """

    event_type: str
    action: str
    repo: str
    title: str
    url: str
    actor: str
    number: Optional[int] = None
    merged: bool = False
    ci_status: Optional[str] = None
    branch: Optional[str] = None
    verified: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Handler class
# ---------------------------------------------------------------------------


class GitHubWebhookHandler:
    """
    Handler for GitHub webhook payloads.

    Register with a :class:`~backend.proactive.webhooks.WebhookReceiver`
    via :func:`register_with_receiver` or call :meth:`handle` directly.

    Usage (low-level)::

        handler = GitHubWebhookHandler()
        result = handler.handle(event_type="pull_request", payload={...})

    The returned dict contains a ``"github_event"`` key holding the parsed
    :class:`GitHubEvent` and an ``"output_block"`` key with the matching
    :class:`~backend.pipeline.models.OutputBlock`.
    """

    # Events we actively parse; others are accepted but return minimal output.
    SUPPORTED_EVENTS = frozenset(
        {"pull_request", "issues", "check_run", "workflow_run"}
    )

    def handle(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Entry point for :class:`~backend.proactive.webhooks.WebhookReceiver`.

        Args:
            event_type: Value of the ``X-GitHub-Event`` header.
            payload:    Decoded JSON payload dict.

        Returns:
            Dict with keys ``"github_event"`` and ``"output_block"``.
        """
        if event_type == "pull_request":
            github_event = self._parse_pull_request(payload)
            block = format_pr_notification(payload)
        elif event_type == "issues":
            github_event = self._parse_issue(payload)
            block = format_issue_notification(payload)
        elif event_type in ("check_run", "workflow_run"):
            github_event = self._parse_ci(event_type, payload)
            block = format_ci_notification(payload)
        else:
            logger.debug("Unhandled GitHub event type: %r", event_type)
            github_event = self._parse_generic(event_type, payload)
            block = OutputBlock(
                type="notification",
                content=f"GitHub event received: {event_type}",
                metadata={"source": "github", "event_type": event_type},
            )

        return {"github_event": github_event, "output_block": block}

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _repo_name(payload: Dict[str, Any]) -> str:
        repo = payload.get("repository") or {}
        return repo.get("full_name") or repo.get("name") or "unknown/repo"

    @staticmethod
    def _actor(payload: Dict[str, Any]) -> str:
        sender = payload.get("sender") or {}
        return sender.get("login", "unknown")

    def _parse_pull_request(self, payload: Dict[str, Any]) -> GitHubEvent:
        pr = payload.get("pull_request") or {}
        action = payload.get("action", "unknown")
        merged = bool(pr.get("merged")) if action == "closed" else False
        head = pr.get("head") or {}
        return GitHubEvent(
            event_type="pull_request",
            action=action,
            repo=self._repo_name(payload),
            title=pr.get("title", ""),
            url=pr.get("html_url", ""),
            actor=self._actor(payload),
            number=pr.get("number"),
            merged=merged,
            branch=head.get("ref"),
            raw=payload,
        )

    def _parse_issue(self, payload: Dict[str, Any]) -> GitHubEvent:
        issue = payload.get("issue") or {}
        return GitHubEvent(
            event_type="issues",
            action=payload.get("action", "unknown"),
            repo=self._repo_name(payload),
            title=issue.get("title", ""),
            url=issue.get("html_url", ""),
            actor=self._actor(payload),
            number=issue.get("number"),
            raw=payload,
        )

    def _parse_ci(self, event_type: str, payload: Dict[str, Any]) -> GitHubEvent:
        if event_type == "check_run":
            obj = payload.get("check_run") or {}
            title = obj.get("name", "CI check")
            url = obj.get("html_url", "")
            branch = (obj.get("check_suite") or {}).get("head_branch")
            status = obj.get("conclusion") or obj.get("status", "unknown")
        else:  # workflow_run
            obj = payload.get("workflow_run") or {}
            title = obj.get("name", "Workflow")
            url = obj.get("html_url", "")
            branch = obj.get("head_branch")
            status = obj.get("conclusion") or obj.get("status", "unknown")

        return GitHubEvent(
            event_type=event_type,
            action=payload.get("action", "unknown"),
            repo=self._repo_name(payload),
            title=title,
            url=url,
            actor=self._actor(payload),
            ci_status=status,
            branch=branch,
            raw=payload,
        )

    def _parse_generic(self, event_type: str, payload: Dict[str, Any]) -> GitHubEvent:
        return GitHubEvent(
            event_type=event_type,
            action=payload.get("action", "unknown"),
            repo=self._repo_name(payload),
            title=event_type,
            url="",
            actor=self._actor(payload),
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Notification formatters
# ---------------------------------------------------------------------------


def format_pr_notification(payload: Dict[str, Any]) -> OutputBlock:
    """Build a notification ``OutputBlock`` for a pull request event.

    Args:
        payload: Raw GitHub ``pull_request`` webhook payload.

    Returns:
        An ``OutputBlock`` with ``type="notification"`` containing a
        human-readable summary and structured metadata.

    Example output content::

        "[opened] PR #42: Add new feature — octocat → main
         https://github.com/org/repo/pull/42"
    """
    action = payload.get("action", "unknown")
    pr = payload.get("pull_request") or {}
    number = pr.get("number", "?")
    title = pr.get("title", "")
    url = pr.get("html_url", "")
    actor = (payload.get("sender") or {}).get("login", "unknown")
    head_ref = (pr.get("head") or {}).get("ref", "")
    base_ref = (pr.get("base") or {}).get("ref", "")
    merged = bool(pr.get("merged"))

    if action == "closed" and merged:
        display_action = "merged"
    else:
        display_action = action

    content = (
        f"[{display_action}] PR #{number}: {title} — {actor}"
        + (f" → {base_ref}" if base_ref else "")
        + (f"\n{url}" if url else "")
    )

    return OutputBlock(
        type="notification",
        content=content,
        metadata={
            "source": "github",
            "event_type": "pull_request",
            "action": display_action,
            "pr_number": number,
            "pr_title": title,
            "actor": actor,
            "head_branch": head_ref,
            "base_branch": base_ref,
            "url": url,
            "merged": merged,
        },
    )


def format_issue_notification(payload: Dict[str, Any]) -> OutputBlock:
    """Build a notification ``OutputBlock`` for an issue event.

    Args:
        payload: Raw GitHub ``issues`` webhook payload.

    Returns:
        An ``OutputBlock`` with ``type="notification"``.

    Example output content::

        "[opened] Issue #7: Bug in login flow — octocat
         https://github.com/org/repo/issues/7"
    """
    action = payload.get("action", "unknown")
    issue = payload.get("issue") or {}
    number = issue.get("number", "?")
    title = issue.get("title", "")
    url = issue.get("html_url", "")
    actor = (payload.get("sender") or {}).get("login", "unknown")

    content = (
        f"[{action}] Issue #{number}: {title} — {actor}"
        + (f"\n{url}" if url else "")
    )

    return OutputBlock(
        type="notification",
        content=content,
        metadata={
            "source": "github",
            "event_type": "issues",
            "action": action,
            "issue_number": number,
            "issue_title": title,
            "actor": actor,
            "url": url,
        },
    )


def format_ci_notification(payload: Dict[str, Any]) -> OutputBlock:
    """Build a notification ``OutputBlock`` for a CI status event.

    Handles both ``check_run`` and ``workflow_run`` payloads.

    Args:
        payload: Raw GitHub ``check_run`` or ``workflow_run`` payload.

    Returns:
        An ``OutputBlock`` with ``type="notification"``.

    Example output content::

        "[CI] Tests: success (main)
         https://github.com/org/repo/runs/12345"
    """
    # Detect whether this is a check_run or workflow_run payload
    if "check_run" in payload:
        obj = payload["check_run"]
        label = "Check"
    elif "workflow_run" in payload:
        obj = payload["workflow_run"]
        label = "Workflow"
    else:
        obj = {}
        label = "CI"

    name = obj.get("name", "CI")
    status = obj.get("conclusion") or obj.get("status", "unknown")
    url = obj.get("html_url", "")
    branch = (
        (obj.get("check_suite") or {}).get("head_branch")
        or obj.get("head_branch")
        or ""
    )

    content = (
        f"[CI] {label}: {name} — {status}"
        + (f" ({branch})" if branch else "")
        + (f"\n{url}" if url else "")
    )

    return OutputBlock(
        type="notification",
        content=content,
        metadata={
            "source": "github",
            "event_type": "check_run" if "check_run" in payload else "workflow_run",
            "ci_name": name,
            "ci_status": status,
            "branch": branch,
            "url": url,
        },
    )


# ---------------------------------------------------------------------------
# Convenience registration function
# ---------------------------------------------------------------------------


def register_with_receiver(receiver: WebhookReceiver, secret: str) -> None:
    """Register the GitHub handler with *receiver* under the ``"github"`` source.

    This is the recommended one-liner for wiring GitHub webhooks::

        from backend.proactive.webhooks import webhook_receiver
        from backend.proactive.github_webhook import register_with_receiver
        import os

        register_with_receiver(webhook_receiver, secret=os.environ["GITHUB_WEBHOOK_SECRET"])

    Args:
        receiver: A :class:`~backend.proactive.webhooks.WebhookReceiver` instance
                  (typically the module-level singleton).
        secret:   The HMAC secret configured in the GitHub webhook settings.
    """
    handler = GitHubWebhookHandler()
    receiver.register(source="github", secret=secret, handler=handler.handle)
    logger.info("GitHub webhook handler registered")
