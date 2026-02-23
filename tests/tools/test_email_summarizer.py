"""
Tests for backend/tools/email/email_summarizer.py

Covers:
- EmailSummarizerTool metadata (tier, oauth, type)
- EmailSummarizerTool.get_parameter_schema() structure and defaults
- EmailSummaryJob.run() returns a notification OutputBlock
- Summary stats computed correctly from injected email data
- Sender filtering (include / exclude)
- max_emails cap
- Empty email list edge case
- EmailSummaryConfig defaults
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.tools.email.email_summarizer import (
    EmailSummaryConfig,
    EmailSummaryJob,
    EmailSummarizerTool,
)
from backend.pipeline.models import (
    ActionTier,
    CapabilityType,
    OutputBlock,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_emails(n: int, sender_prefix: str = "sender") -> list:
    """Create n mock email dicts."""
    return [
        {
            "id": f"id_{i}",
            "subject": f"Subject {i}",
            "sender": f"{sender_prefix}{i}@example.com",
            "date": "2026-02-22T08:00:00",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# EmailSummarizerTool – metadata tests
# ---------------------------------------------------------------------------


class TestEmailSummarizerToolMetadata:
    def setup_method(self):
        self.tool = EmailSummarizerTool()

    def test_tier_is_tier_1_read_only(self):
        assert self.tool.metadata.tier == ActionTier.TIER_1

    def test_requires_oauth_gmail(self):
        assert self.tool.metadata.requires_oauth == "gmail"

    def test_capability_type_is_tool(self):
        assert self.tool.metadata.type == CapabilityType.TOOL

    def test_requires_network_true(self):
        assert self.tool.metadata.requires_network is True

    def test_name_is_email_summarizer(self):
        assert self.tool.metadata.name == "email_summarizer"

    def test_description_is_non_empty_string(self):
        assert isinstance(self.tool.metadata.description, str)
        assert len(self.tool.metadata.description) > 0


# ---------------------------------------------------------------------------
# EmailSummarizerTool – parameter schema tests
# ---------------------------------------------------------------------------


class TestEmailSummarizerToolSchema:
    def setup_method(self):
        self.tool = EmailSummarizerTool()
        self.schema = self.tool.get_parameter_schema()

    def test_schema_is_dict(self):
        assert isinstance(self.schema, dict)

    def test_schema_has_max_emails_property(self):
        assert "max_emails" in self.schema["properties"]

    def test_schema_has_since_hours_property(self):
        assert "since_hours" in self.schema["properties"]

    def test_max_emails_default_is_20(self):
        assert self.schema["properties"]["max_emails"]["default"] == 20

    def test_since_hours_default_is_24(self):
        assert self.schema["properties"]["since_hours"]["default"] == 24

    def test_max_emails_type_is_integer(self):
        assert self.schema["properties"]["max_emails"]["type"] == "integer"

    def test_since_hours_type_is_integer(self):
        assert self.schema["properties"]["since_hours"]["type"] == "integer"


# ---------------------------------------------------------------------------
# EmailSummaryJob – run() return type tests
# ---------------------------------------------------------------------------


class TestEmailSummaryJobOutputBlock:
    def setup_method(self):
        self.job = EmailSummaryJob()
        self.config = EmailSummaryConfig()

    def test_run_returns_output_block(self):
        result = self.job.run(self.config, make_emails(5))
        assert isinstance(result, OutputBlock)

    def test_run_returns_notification_type(self):
        result = self.job.run(self.config, make_emails(5))
        assert result.type == "notification"

    def test_run_content_has_stats_key(self):
        result = self.job.run(self.config, make_emails(3))
        assert "stats" in result.content

    def test_run_content_has_title(self):
        result = self.job.run(self.config, make_emails(2))
        assert "title" in result.content

    def test_run_content_has_message(self):
        result = self.job.run(self.config, make_emails(2))
        assert "message" in result.content


# ---------------------------------------------------------------------------
# EmailSummaryJob – stats correctness tests
# ---------------------------------------------------------------------------


class TestEmailSummaryJobStats:
    def setup_method(self):
        self.job = EmailSummaryJob()

    def test_total_emails_count_matches_input(self):
        emails = make_emails(7)
        result = self.job.run(EmailSummaryConfig(), emails)
        assert result.content["stats"]["total_emails"] == 7

    def test_unique_senders_computed_correctly(self):
        emails = [
            {"subject": "A", "sender": "a@x.com", "date": ""},
            {"subject": "B", "sender": "a@x.com", "date": ""},
            {"subject": "C", "sender": "b@x.com", "date": ""},
        ]
        result = self.job.run(EmailSummaryConfig(), emails)
        assert result.content["stats"]["unique_senders"] == 2

    def test_top_senders_list_present(self):
        emails = make_emails(4)
        result = self.job.run(EmailSummaryConfig(), emails)
        assert "top_senders" in result.content["stats"]

    def test_top_sender_is_most_frequent(self):
        emails = [
            {"subject": "1", "sender": "popular@x.com", "date": ""},
            {"subject": "2", "sender": "popular@x.com", "date": ""},
            {"subject": "3", "sender": "popular@x.com", "date": ""},
            {"subject": "4", "sender": "other@x.com", "date": ""},
        ]
        result = self.job.run(EmailSummaryConfig(), emails)
        top = result.content["stats"]["top_senders"][0]
        assert top["sender"] == "popular@x.com"
        assert top["count"] == 3

    def test_max_emails_cap_applied(self):
        config = EmailSummaryConfig(max_emails=3)
        emails = make_emails(10)
        result = self.job.run(config, emails)
        assert result.content["stats"]["total_emails"] == 3

    def test_empty_email_list_returns_notification(self):
        result = self.job.run(EmailSummaryConfig(), [])
        assert result.type == "notification"
        assert result.content["stats"]["total_emails"] == 0

    def test_exclude_senders_filter(self):
        config = EmailSummaryConfig(exclude_senders=["spam@x.com"])
        emails = [
            {"subject": "ok", "sender": "ok@x.com", "date": ""},
            {"subject": "spam", "sender": "spam@x.com", "date": ""},
        ]
        result = self.job.run(config, emails)
        assert result.content["stats"]["total_emails"] == 1

    def test_include_senders_filter(self):
        config = EmailSummaryConfig(include_senders=["vip@x.com"])
        emails = [
            {"subject": "vip", "sender": "vip@x.com", "date": ""},
            {"subject": "other", "sender": "other@x.com", "date": ""},
        ]
        result = self.job.run(config, emails)
        assert result.content["stats"]["total_emails"] == 1

    def test_metadata_contains_generated_at(self):
        result = self.job.run(EmailSummaryConfig(), make_emails(1))
        assert "generated_at" in result.metadata


# ---------------------------------------------------------------------------
# EmailSummaryConfig – defaults test
# ---------------------------------------------------------------------------


def test_email_summary_config_defaults():
    config = EmailSummaryConfig()
    assert config.max_emails == 20
    assert 0 <= config.summary_hour <= 23
    assert isinstance(config.include_senders, list)
    assert isinstance(config.exclude_senders, list)
