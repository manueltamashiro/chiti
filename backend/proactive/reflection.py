"""
proactive/reflection.py — Daily LLM reflection job, scheduled at 11pm.

Inputs: last 24h of activity logs + heartbeat results.
Calls Claude → stores as notification (info) + fact in structured memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)

_REFLECTION_PROMPT = """\
You are a personal assistant reflecting on the day's activity.
Given the activity summary below, produce a brief daily reflection (3-5 sentences) that:
1. Summarises what was accomplished
2. Notes any patterns or recurring issues
3. Suggests one thing to improve or watch for tomorrow

Be warm and concise. Do NOT include any JSON — just plain prose.
"""


async def run_daily_reflection() -> Dict[str, Any]:
    """
    Run the daily reflection job. Called by APScheduler at configured hour.
    Returns a report dict.
    """
    report: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reflection": "",
        "error": None,
    }

    try:
        import anthropic
        from backend.memory import audit as audit_mod, structured

        # Gather last 24h audit log
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        recent_logs = await audit_mod.list_audit_logs(limit=100, date_from=yesterday)

        if not recent_logs:
            report["reflection"] = "No activity recorded today."
            return report

        # Build summary
        tool_counts: Dict[str, int] = {}
        for entry in recent_logs:
            tn = entry.get("tool_name", "unknown")
            tool_counts[tn] = tool_counts.get(tn, 0) + 1

        lines = [f"- {name}: {count} call(s)" for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])]
        summary = f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        summary += f"Total tool calls: {len(recent_logs)}\n"
        summary += "Tool usage:\n" + "\n".join(lines)

        # Call LLM
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=cfg.llm.model,
            max_tokens=512,
            system=_REFLECTION_PROMPT,
            messages=[{"role": "user", "content": f"Activity summary:\n\n{summary}"}],
        )
        reflection_text = response.content[0].text.strip()
        report["reflection"] = reflection_text

        # Store as notification
        from backend.proactive.notifications import create_notification
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await create_notification(
            title=f"Daily Reflection — {today_str}",
            body=reflection_text,
            priority="info",
            source="daily_reflection",
        )

        # Store as fact in structured memory
        await structured.upsert_fact(
            subject=today_str,
            predicate="daily_reflection",
            obj=reflection_text[:500],
            confidence=1.0,
            source_conversation_id=None,
        )

        logger.info("Daily reflection complete", extra={"date": today_str})

    except Exception as exc:
        report["error"] = str(exc)
        logger.error("Daily reflection failed", extra={"error": str(exc)})

    return report
