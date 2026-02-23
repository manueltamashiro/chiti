"""
memory/summarizer.py — Session summarization at conversation close.

Calls Claude to produce a structured summary of the conversation, then
stores it in Qdrant (episodic memory) and extracts entities into SQLite
(structured memory).

Triggered by: conversation close event (called from /api/chat endpoint
when a conversation ends, or by the worker on idle timeout).
"""

from __future__ import annotations

from typing import Any, Dict, List

from backend.config.logging import get_logger
from backend.memory import episodic, structured

logger = get_logger(__name__)

_SUMMARY_PROMPT = """\
You are a memory assistant. Summarise this conversation for future recall.

Produce a JSON object with these exact keys:
{
  "summary": "<2-4 sentence narrative summary of what was discussed and decided>",
  "facts": [
    {"subject": "...", "predicate": "...", "object": "...", "confidence": 0.0-1.0}
  ],
  "preferences": [
    {"key": "...", "value": "..."}
  ],
  "people": [
    {"name": "...", "relationship": "...", "notes": "..."}
  ]
}

Only include facts/preferences/people that were explicitly mentioned.
Respond with ONLY the JSON object, no markdown fences.
"""


async def summarize_conversation(
    conversation_id: str,
    messages: List[Dict[str, str]],
) -> str:
    """
    Summarize a conversation and store the result in episodic + structured memory.
    Returns the plain-text summary string.
    """
    if not messages:
        return ""

    try:
        import anthropic
        import json

        from backend.config.loader import cfg

        # Build transcript
        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in messages
        )

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=cfg.llm.model,
            max_tokens=1024,
            system=_SUMMARY_PROMPT,
            messages=[{"role": "user", "content": f"Conversation transcript:\n\n{transcript}"}],
        )

        raw = response.content[0].text.strip()

        # Parse JSON
        data: Dict[str, Any] = {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: treat entire response as plain summary
            data = {"summary": raw, "facts": [], "preferences": [], "people": []}

        summary_text: str = data.get("summary", raw)

        # Store in Qdrant
        await episodic.store_summary(
            conversation_id=conversation_id,
            summary=summary_text,
            metadata={"message_count": len(messages)},
        )

        # Store structured entities
        await _store_entities(conversation_id, data)

        logger.info(
            "Conversation summarized",
            extra={"conversation_id": conversation_id, "facts": len(data.get("facts", []))},
        )
        return summary_text

    except Exception as exc:
        logger.error("Failed to summarize conversation", extra={"error": str(exc)})
        return ""


async def _store_entities(conversation_id: str, data: Dict[str, Any]) -> None:
    """Write extracted entities to structured memory."""
    for fact in data.get("facts", []):
        try:
            await structured.upsert_fact(
                subject=fact.get("subject", ""),
                predicate=fact.get("predicate", ""),
                obj=fact.get("object", ""),
                confidence=float(fact.get("confidence", 0.8)),
                source_conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("Failed to store fact", extra={"error": str(exc)})

    for pref in data.get("preferences", []):
        try:
            await structured.upsert_preference(
                key=pref.get("key", ""),
                value=pref.get("value", ""),
            )
        except Exception as exc:
            logger.warning("Failed to store preference", extra={"error": str(exc)})

    for person in data.get("people", []):
        try:
            await structured.upsert_person(
                name=person.get("name", ""),
                relationship=person.get("relationship", ""),
                notes=person.get("notes", ""),
            )
        except Exception as exc:
            logger.warning("Failed to store person", extra={"error": str(exc)})
