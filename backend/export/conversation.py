"""
Conversation Export — P8-06

Exports conversation history to Markdown or JSON format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class ExportFormat(Enum):
    MARKDOWN = "markdown"
    JSON = "json"


@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    role: str                         # "user" | "assistant" | "system"
    content: str
    timestamp: Optional[datetime] = None


@dataclass
class ConversationExport:
    """A full conversation ready for export."""
    messages: List[ConversationMessage]
    title: str = "Conversation"
    system_prompt: Optional[str] = None
    exported_at: datetime = field(default_factory=datetime.utcnow)


class ConversationExporter:
    """Exports conversation history to Markdown or JSON."""

    def export(
        self,
        messages: List[ConversationMessage],
        fmt: ExportFormat = ExportFormat.MARKDOWN,
        title: str = "Conversation",
        system_prompt: Optional[str] = None,
    ) -> str:
        """
        Export a list of messages to the requested format.

        Args:
            messages:      Ordered list of conversation messages.
            fmt:           Output format (MARKDOWN or JSON).
            title:         Human-readable title for the conversation.
            system_prompt: Optional system prompt to include in the export.

        Returns:
            Formatted string (Markdown or JSON).
        """
        conv = ConversationExport(
            messages=messages,
            title=title,
            system_prompt=system_prompt,
        )
        if fmt == ExportFormat.MARKDOWN:
            return self.to_markdown(conv)
        elif fmt == ExportFormat.JSON:
            return self.to_json(conv)
        else:
            raise ValueError(f"Unsupported format: {fmt!r}")

    def to_markdown(self, conv: ConversationExport) -> str:
        """Render a ConversationExport as a Markdown document."""
        lines: List[str] = [
            f"# {conv.title}",
            "",
            f"*Exported: {conv.exported_at.strftime('%Y-%m-%d %H:%M:%S UTC')}*",
            "",
        ]

        if conv.system_prompt:
            lines += [
                "## System Prompt",
                "",
                conv.system_prompt,
                "",
            ]

        if conv.messages:
            lines += ["## Conversation", ""]
            for msg in conv.messages:
                role_label = msg.role.capitalize()
                lines.append(f"**{role_label}:** {msg.content}")
                lines.append("")

        return "\n".join(lines)

    def to_json(self, conv: ConversationExport) -> str:
        """Render a ConversationExport as a JSON string."""
        data = {
            "title": conv.title,
            "exported_at": conv.exported_at.isoformat(),
            "system_prompt": conv.system_prompt,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                }
                for msg in conv.messages
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)


# Module-level singleton
conversation_exporter = ConversationExporter()
