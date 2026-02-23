"""
Email summarization scheduled job and tool

Implements P6-03: Daily email summarization.

Two components are provided:

1.  ``EmailSummarizerTool`` – a ToolBase that fetches email headers and
    returns them as a structured ``table`` OutputBlock for display.

2.  ``EmailSummaryJob`` – a plain (non-ToolBase) scheduler job that accepts
    injectable email data and returns a ``notification`` OutputBlock with
    summary statistics.  This separation keeps the job testable without a
    real Gmail connection.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.tools.base import ToolBase
from backend.pipeline.models import (
    ActionTier,
    CapabilityMetadata,
    CapabilityType,
    OutputBlock,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class EmailSummaryConfig:
    """Configuration for the daily email summarization job."""

    max_emails: int = 20
    summary_hour: int = 8          # 0–23, hour at which the daily job runs
    include_senders: List[str] = field(default_factory=list)
    exclude_senders: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# EmailSummarizerTool – ToolBase (for on-demand use)
# ---------------------------------------------------------------------------


class EmailSummarizerTool(ToolBase):
    """
    Fetch recent email headers and return them as a structured table.

    This tool does NOT send email content to an LLM – it only retrieves
    and structures the metadata (subject, sender, date) for display.
    """

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="email_summarizer",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_1,       # read-only
            description="Fetch recent email headers and return a structured table summary",
            requires_network=True,
            requires_oauth="gmail",
            allowed_scopes=["gmail.readonly"],
            max_runtime_seconds=30,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        """
        Fetch email headers for the most recent ``since_hours`` hours.

        Args:
            params: {
                "max_emails":  int  – maximum number of emails to return (default 20),
                "since_hours": int  – look-back window in hours (default 24),
            }

        Returns:
            OutputBlock(type="table") listing recent emails.
        """
        max_emails = int(params.get("max_emails", 20))
        since_hours = int(params.get("since_hours", 24))

        try:
            from backend.storage.oauth import oauth_manager

            access_token = await oauth_manager.get_token("gmail")
        except Exception as exc:
            logger.warning("Could not retrieve Gmail OAuth token: %s", exc)
            access_token = None

        if not access_token:
            return OutputBlock(
                type="notification",
                content={
                    "level": "warning",
                    "title": "Gmail Not Connected",
                    "message": "Please complete Gmail OAuth flow first",
                    "action_url": "/auth/oauth/gmail/start",
                },
                metadata={"requires_auth": True},
            )

        try:
            emails = await self._fetch_email_headers(
                access_token, max_emails=max_emails, since_hours=since_hours
            )
        except Exception as exc:
            logger.error("Failed to fetch email headers: %s", exc)
            return OutputBlock(
                type="notification",
                content={
                    "level": "error",
                    "title": "Email Fetch Error",
                    "message": str(exc),
                },
                metadata={"error": str(exc)},
            )

        rows = [
            [email.get("subject", ""), email.get("sender", ""), email.get("date", "")]
            for email in emails
        ]

        return OutputBlock(
            type="table",
            content={
                "columns": ["Subject", "From", "Date"],
                "rows": rows,
                "metadata": {
                    "total": len(emails),
                    "since_hours": since_hours,
                    "max_emails": max_emails,
                },
            },
            metadata={"since_hours": since_hours, "total": len(emails)},
        )

    async def _fetch_email_headers(
        self,
        access_token: str,
        max_emails: int = 20,
        since_hours: int = 24,
    ) -> List[Dict[str, Any]]:
        """
        Fetch email headers from Gmail.

        The actual implementation uses the Gmail API; for now this returns
        mock data that matches the expected schema.
        """
        # TODO: Replace with a real Gmail API call.
        # Example:
        #   from googleapiclient.discovery import build
        #   from google.oauth2.credentials import Credentials
        #   creds = Credentials(token=access_token)
        #   service = build('gmail', 'v1', credentials=creds)
        #   cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        #   query = f"after:{int(cutoff.timestamp())}"
        #   results = service.users().messages().list(
        #       userId='me', q=query, maxResults=max_emails
        #   ).execute()
        #   ... fetch headers for each message ...

        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        mock_emails: List[Dict[str, Any]] = [
            {
                "id": "mock_001",
                "subject": "Weekly report",
                "sender": "boss@example.com",
                "date": cutoff.isoformat(),
            },
            {
                "id": "mock_002",
                "subject": "Meeting tomorrow",
                "sender": "colleague@example.com",
                "date": datetime.utcnow().isoformat(),
            },
        ]
        return mock_emails[:max_emails]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_emails": {
                    "type": "integer",
                    "description": "Maximum number of emails to include in the summary",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200,
                },
                "since_hours": {
                    "type": "integer",
                    "description": "Look-back window in hours (e.g. 24 = last 24 hours)",
                    "default": 24,
                    "minimum": 1,
                    "maximum": 168,
                },
            },
            "required": [],
        }


# ---------------------------------------------------------------------------
# EmailSummaryJob – scheduler job (NOT a ToolBase)
# ---------------------------------------------------------------------------


class EmailSummaryJob:
    """
    Scheduler job that produces a daily email summary notification.

    This class is intentionally decoupled from any OAuth / network layer so
    that it can be tested with injected email data without hitting real APIs.

    Usage (production)::

        job = EmailSummaryJob()
        emails = await fetch_emails_somehow()
        result = job.run(config, emails)

    Usage (tests)::

        job = EmailSummaryJob()
        emails = [{"subject": "Hi", "sender": "a@b.com", "date": "..."}]
        result = job.run(EmailSummaryConfig(), emails)
    """

    def run(
        self,
        config: EmailSummaryConfig,
        emails: List[Dict[str, Any]],
    ) -> OutputBlock:
        """
        Compute summary statistics from injected email data.

        Args:
            config: EmailSummaryConfig controlling limits and filters.
            emails: List of email dicts, each with keys:
                    ``subject``, ``sender``, ``date`` (all strings).

        Returns:
            OutputBlock(type="notification") with summary statistics.
        """
        # Apply sender filters
        filtered = self._apply_filters(emails, config)

        # Enforce max_emails cap
        limited = filtered[: config.max_emails]

        # Compute per-sender counts
        sender_counts: Dict[str, int] = {}
        for email in limited:
            sender = email.get("sender", "unknown")
            sender_counts[sender] = sender_counts.get(sender, 0) + 1

        # Top senders (up to 5), sorted by count descending
        top_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        summary_text = (
            f"{len(limited)} email(s) received. "
            + (
                "Top senders: " + ", ".join(f"{s} ({n})" for s, n in top_senders)
                if top_senders
                else "No emails to summarise."
            )
        )

        return OutputBlock(
            type="notification",
            content={
                "level": "info",
                "title": "Daily Email Summary",
                "message": summary_text,
                "stats": {
                    "total_emails": len(limited),
                    "unique_senders": len(sender_counts),
                    "top_senders": [
                        {"sender": s, "count": n} for s, n in top_senders
                    ],
                },
            },
            metadata={
                "generated_at": datetime.utcnow().isoformat(),
                "max_emails": config.max_emails,
                "summary_hour": config.summary_hour,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_filters(
        self,
        emails: List[Dict[str, Any]],
        config: EmailSummaryConfig,
    ) -> List[Dict[str, Any]]:
        """Apply include/exclude sender filters."""
        result = emails

        if config.include_senders:
            include_lower = [s.lower() for s in config.include_senders]
            result = [
                e for e in result
                if e.get("sender", "").lower() in include_lower
            ]

        if config.exclude_senders:
            exclude_lower = [s.lower() for s in config.exclude_senders]
            result = [
                e for e in result
                if e.get("sender", "").lower() not in exclude_lower
            ]

        return result
