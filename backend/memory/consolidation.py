"""
memory/consolidation.py — Nightly memory consolidation job.

Scheduled via APScheduler at 3am (cfg.memory.consolidation_hour).

Steps:
  1. Load all facts where confidence < 0.5
  2. Ask Claude: "Are these facts still accurate / should they be merged?"
  3. Merge duplicates, delete stale facts, update confidences
  4. Deduplicate Qdrant embeddings (cosine similarity > 0.98)
  5. Log consolidation report to structured log
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)

_CONSOLIDATION_PROMPT = """\
You are a memory consolidation assistant. Review these low-confidence facts and return a JSON object:
{
  "keep": [{"id": "...", "new_confidence": 0.0-1.0}],
  "delete": ["id1", "id2"],
  "merge": [{"keep_id": "...", "delete_ids": ["..."], "merged_object": "..."}]
}

Rules:
- Keep facts that seem plausible, raise confidence to 0.7
- Delete facts that seem stale, wrong, or contradicted
- Merge facts with the same subject+predicate but different objects
- Respond ONLY with the JSON, no markdown.
"""


async def run_consolidation() -> Dict[str, Any]:
    """
    Run the nightly consolidation job.
    Returns a report dict with stats.
    """
    start = datetime.now(timezone.utc)
    report: Dict[str, Any] = {
        "started_at": start.isoformat(),
        "facts_reviewed": 0,
        "facts_deleted": 0,
        "facts_merged": 0,
        "episodic_deduplicated": 0,
        "errors": [],
    }

    try:
        from backend.memory import structured, episodic
        import anthropic

        # Step 1: load low-confidence facts
        facts = await structured.get_low_confidence_facts(threshold=0.5)
        report["facts_reviewed"] = len(facts)

        if facts:
            # Step 2: ask Claude
            facts_json = json.dumps(
                [{"id": f["id"], "subject": f["subject"], "predicate": f["predicate"],
                  "object": f["object"], "confidence": f["confidence"]} for f in facts],
                indent=2,
            )

            client = anthropic.AsyncAnthropic()
            response = await client.messages.create(
                model=cfg.llm.model,
                max_tokens=1024,
                system=_CONSOLIDATION_PROMPT,
                messages=[{"role": "user", "content": f"Facts to review:\n{facts_json}"}],
            )
            raw = response.content[0].text.strip()

            try:
                decisions: Dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Consolidation: failed to parse LLM response", extra={"raw": raw[:200]})
                decisions = {}

            # Step 3: apply decisions
            for item in decisions.get("keep", []):
                fid = item.get("id", "")
                new_conf = float(item.get("new_confidence", 0.7))
                async with __import__("aiosqlite").connect(str(__import__("pathlib").Path(cfg.database.path).expanduser())) as db:
                    await db.execute(
                        "UPDATE facts SET confidence = ? WHERE id = ?", (new_conf, fid)
                    )
                    await db.commit()

            for fid in decisions.get("delete", []):
                try:
                    await structured.delete_fact(fid)
                    report["facts_deleted"] += 1
                except Exception as exc:
                    report["errors"].append(f"delete {fid}: {exc}")

            for merge in decisions.get("merge", []):
                keep_id = merge.get("keep_id")
                merged_obj = merge.get("merged_object", "")
                for del_id in merge.get("delete_ids", []):
                    try:
                        await structured.delete_fact(del_id)
                        report["facts_merged"] += 1
                    except Exception:
                        pass
                if keep_id and merged_obj:
                    async with __import__("aiosqlite").connect(str(__import__("pathlib").Path(cfg.database.path).expanduser())) as db:
                        await db.execute(
                            "UPDATE facts SET object = ?, confidence = 0.8 WHERE id = ?",
                            (merged_obj, keep_id),
                        )
                        await db.commit()

        # Step 4: deduplicate Qdrant
        try:
            deleted = await episodic.deduplicate(similarity_threshold=0.98)
            report["episodic_deduplicated"] = deleted
        except Exception as exc:
            report["errors"].append(f"episodic dedup: {exc}")

    except Exception as exc:
        report["errors"].append(str(exc))
        logger.error("Consolidation failed", extra={"error": str(exc)})

    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("Memory consolidation complete", extra=report)
    return report
