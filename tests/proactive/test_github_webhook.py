"""
Tests for the GitHub webhook handler (backend/proactive/github_webhook.py).

Covers:
 - PR opened / closed (merged & not-merged) events
 - Issue opened / closed events
 - CI check_run success / failure events
 - CI workflow_run events
 - OutputBlock type and content shape
 - register_with_receiver wires up correctly
"""

import json
import pytest

from backend.pipeline.models import OutputBlock
from backend.proactive.github_webhook import (
    GitHubWebhookHandler,
    format_ci_notification,
    format_issue_notification,
    format_pr_notification,
    register_with_receiver,
)
from backend.proactive.webhooks import WebhookReceiver


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _pr_payload(action: str = "opened", merged: bool = False) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 42,
            "title": "Add cool feature",
            "html_url": "https://github.com/org/repo/pull/42",
            "merged": merged,
            "head": {"ref": "feature-branch"},
            "base": {"ref": "main"},
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "octocat"},
    }


def _issue_payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "issue": {
            "number": 7,
            "title": "Bug in login flow",
            "html_url": "https://github.com/org/repo/issues/7",
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "octocat"},
    }


def _check_run_payload(conclusion: str = "success") -> dict:
    return {
        "action": "completed",
        "check_run": {
            "name": "Tests",
            "html_url": "https://github.com/org/repo/runs/12345",
            "conclusion": conclusion,
            "status": "completed",
            "check_suite": {"head_branch": "main"},
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "octocat"},
    }


def _workflow_run_payload(conclusion: str = "success") -> dict:
    return {
        "action": "completed",
        "workflow_run": {
            "name": "CI Pipeline",
            "html_url": "https://github.com/org/repo/actions/runs/99",
            "conclusion": conclusion,
            "status": "completed",
            "head_branch": "main",
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "octocat"},
    }


handler = GitHubWebhookHandler()


# ---------------------------------------------------------------------------
# 1. PR opened — parsed correctly
# ---------------------------------------------------------------------------


def test_pr_opened_parsed():
    result = handler.handle("pull_request", _pr_payload("opened"))
    ev = result["github_event"]
    assert ev.event_type == "pull_request"
    assert ev.action == "opened"
    assert ev.number == 42
    assert ev.title == "Add cool feature"
    assert ev.actor == "octocat"
    assert ev.merged is False


# ---------------------------------------------------------------------------
# 2. PR closed (not merged)
# ---------------------------------------------------------------------------


def test_pr_closed_not_merged():
    result = handler.handle("pull_request", _pr_payload("closed", merged=False))
    ev = result["github_event"]
    assert ev.action == "closed"
    assert ev.merged is False


# ---------------------------------------------------------------------------
# 3. PR closed and merged
# ---------------------------------------------------------------------------


def test_pr_closed_merged():
    result = handler.handle("pull_request", _pr_payload("closed", merged=True))
    ev = result["github_event"]
    assert ev.merged is True


# ---------------------------------------------------------------------------
# 4. Issue opened — parsed correctly
# ---------------------------------------------------------------------------


def test_issue_opened_parsed():
    result = handler.handle("issues", _issue_payload("opened"))
    ev = result["github_event"]
    assert ev.event_type == "issues"
    assert ev.action == "opened"
    assert ev.number == 7
    assert ev.title == "Bug in login flow"


# ---------------------------------------------------------------------------
# 5. Issue closed — parsed correctly
# ---------------------------------------------------------------------------


def test_issue_closed_parsed():
    result = handler.handle("issues", _issue_payload("closed"))
    ev = result["github_event"]
    assert ev.action == "closed"


# ---------------------------------------------------------------------------
# 6. check_run success — CI status extracted
# ---------------------------------------------------------------------------


def test_check_run_success_parsed():
    result = handler.handle("check_run", _check_run_payload("success"))
    ev = result["github_event"]
    assert ev.event_type == "check_run"
    assert ev.ci_status == "success"
    assert ev.title == "Tests"
    assert ev.branch == "main"


# ---------------------------------------------------------------------------
# 7. check_run failure — CI status is failure
# ---------------------------------------------------------------------------


def test_check_run_failure_parsed():
    result = handler.handle("check_run", _check_run_payload("failure"))
    ev = result["github_event"]
    assert ev.ci_status == "failure"


# ---------------------------------------------------------------------------
# 8. workflow_run success
# ---------------------------------------------------------------------------


def test_workflow_run_success_parsed():
    result = handler.handle("workflow_run", _workflow_run_payload("success"))
    ev = result["github_event"]
    assert ev.event_type == "workflow_run"
    assert ev.ci_status == "success"
    assert ev.title == "CI Pipeline"
    assert ev.branch == "main"


# ---------------------------------------------------------------------------
# 9. format_pr_notification returns correct OutputBlock type and content
# ---------------------------------------------------------------------------


def test_format_pr_notification_output_block():
    block = format_pr_notification(_pr_payload("opened"))
    assert isinstance(block, OutputBlock)
    assert block.type == "notification"
    assert "PR #42" in block.content
    assert "opened" in block.content
    assert block.metadata["pr_number"] == 42
    assert block.metadata["source"] == "github"


# ---------------------------------------------------------------------------
# 10. format_pr_notification marks merged PRs correctly
# ---------------------------------------------------------------------------


def test_format_pr_notification_merged():
    block = format_pr_notification(_pr_payload("closed", merged=True))
    assert "merged" in block.content
    assert block.metadata["merged"] is True


# ---------------------------------------------------------------------------
# 11. format_issue_notification returns correct OutputBlock
# ---------------------------------------------------------------------------


def test_format_issue_notification_output_block():
    block = format_issue_notification(_issue_payload("opened"))
    assert isinstance(block, OutputBlock)
    assert block.type == "notification"
    assert "Issue #7" in block.content
    assert "Bug in login flow" in block.content
    assert block.metadata["issue_number"] == 7


# ---------------------------------------------------------------------------
# 12. format_ci_notification returns correct OutputBlock for check_run
# ---------------------------------------------------------------------------


def test_format_ci_notification_check_run():
    block = format_ci_notification(_check_run_payload("success"))
    assert isinstance(block, OutputBlock)
    assert block.type == "notification"
    assert "Tests" in block.content
    assert "success" in block.content
    assert block.metadata["ci_status"] == "success"


# ---------------------------------------------------------------------------
# 13. format_ci_notification returns correct OutputBlock for workflow_run
# ---------------------------------------------------------------------------


def test_format_ci_notification_workflow_run():
    block = format_ci_notification(_workflow_run_payload("failure"))
    assert block.type == "notification"
    assert "CI Pipeline" in block.content
    assert "failure" in block.content
    assert block.metadata["ci_status"] == "failure"


# ---------------------------------------------------------------------------
# 14. register_with_receiver wires the handler under "github" source
# ---------------------------------------------------------------------------


def test_register_with_receiver_wires_handler():
    receiver = WebhookReceiver()
    register_with_receiver(receiver, secret="")
    # Should have "github" registered
    assert "github" in receiver._configs
    assert receiver._configs["github"].source == "github"


# ---------------------------------------------------------------------------
# 15. Unknown event type handled without raising
# ---------------------------------------------------------------------------


def test_unknown_event_type_does_not_raise():
    result = handler.handle("deployment", {"action": "created"})
    block = result["output_block"]
    assert isinstance(block, OutputBlock)
    assert block.type == "notification"
