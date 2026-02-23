"""
memory/injector.py — Context injection into LLM system prompts.

Called before every LLM request to inject:
  1. Current datetime (ISO 8601)
  2. Upcoming calendar events (next 24h)
  3. Top-5 episodic memories semantically similar to the current query
  4. Key structured facts (preferences, recent people/events)

Token budget: capped at 2000 tokens (cfg.memory.context_token_budget).
Rough estimate: 1 token ≈ 4 characters.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _truncate(text: str, remaining_chars: int) -> str:
    if len(text) <= remaining_chars:
        return text
    return text[:remaining_chars] + "…"


async def build_context(query: str) -> str:
    """
    Build the memory/context injection string for the LLM system prompt.
    Returns a markdown-formatted block to prepend to the system prompt.
    """
    budget_chars = cfg.memory.context_token_budget * _CHARS_PER_TOKEN
    parts: List[str] = []

    # 1. Current datetime
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"**Current time:** {now_str}")
    budget_chars -= len(parts[-1])

    # 2. Episodic memories
    if cfg.memory.episodic_enabled and budget_chars > 200:
        try:
            from backend.memory import episodic
            memories = await episodic.search(query, top_k=5)
            if memories:
                mem_lines = [f"- [{m['timestamp'][:10]}] {m['summary']}" for m in memories]
                mem_block = "**Relevant past memories:**\n" + "\n".join(mem_lines)
                mem_block = _truncate(mem_block, budget_chars - 100)
                parts.append(mem_block)
                budget_chars -= len(mem_block)
        except Exception as exc:
            logger.warning("Failed to retrieve episodic memories", extra={"error": str(exc)})

    # 3. Structured facts
    if budget_chars > 200:
        try:
            from backend.memory import structured
            facts = await structured.list_facts(limit=10)
            prefs = await structured.list_preferences()
            people = await structured.list_people()

            fact_lines = [f"- {f['subject']} {f['predicate']} {f['object']}" for f in facts[:8]]
            pref_lines = [f"- {p['key']}: {p['value']}" for p in prefs[:8]]
            people_lines = [f"- {p['name']} ({p['relationship']})" for p in people[:5]]

            structured_parts: List[str] = []
            if fact_lines:
                structured_parts.append("**Known facts:**\n" + "\n".join(fact_lines))
            if pref_lines:
                structured_parts.append("**Preferences:**\n" + "\n".join(pref_lines))
            if people_lines:
                structured_parts.append("**People:**\n" + "\n".join(people_lines))

            if structured_parts:
                struct_block = "\n\n".join(structured_parts)
                struct_block = _truncate(struct_block, budget_chars - 50)
                parts.append(struct_block)
                budget_chars -= len(struct_block)
        except Exception as exc:
            logger.warning("Failed to retrieve structured memory", extra={"error": str(exc)})

    # 4. Upcoming events
    if budget_chars > 100:
        try:
            from backend.memory import structured
            events = await structured.list_events(upcoming_only=True)
            if events:
                ev_lines = [f"- {e['date'][:16]}: {e['title']}" for e in events[:5]]
                ev_block = "**Upcoming events:**\n" + "\n".join(ev_lines)
                ev_block = _truncate(ev_block, budget_chars)
                parts.append(ev_block)
        except Exception as exc:
            logger.warning("Failed to retrieve events", extra={"error": str(exc)})

    if not parts:
        return ""

    header = "## Assistant Memory Context\n\n"
    return header + "\n\n".join(parts) + "\n\n---\n\n"
