"""
worker.py — assistant-worker process entry point.

Responsibilities:
  - Poll SQLite job queue (500ms interval) and execute jobs
  - Run APScheduler for cron/interval jobs
  - Heartbeat monitor (every N minutes)
  - Daily memory consolidation (3am)
  - Daily reflection (11pm)

Run with: python -m backend.worker
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, timezone

from backend.config.loader import cfg
from backend.config.logging import get_logger, setup_logging

setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
logger = get_logger(__name__)

_stop_event = asyncio.Event()


# ---------------------------------------------------------------------------
# Job executor
# ---------------------------------------------------------------------------

async def _execute_job(job: dict) -> str:
    """Execute a job from the queue. Returns result string."""
    job_type = job.get("type", "")
    payload = job.get("payload", {})

    if job_type == "summarize_conversation":
        from backend.memory.summarizer import summarize_conversation
        conv_id = payload.get("conversation_id", "")
        messages = payload.get("messages", [])
        summary = await summarize_conversation(conv_id, messages)
        return summary or "No summary produced"

    elif job_type == "llm_task":
        # Run a task_prompt with the LLM (for scheduled jobs)
        from backend.scheduler.jobs import record_run
        import anthropic
        prompt = payload.get("task_prompt", "")
        job_id = payload.get("job_id", "")
        if not prompt:
            return "No task_prompt provided"
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=cfg.llm.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip()
        if job_id:
            await record_run(job_id, result)
        return result

    elif job_type == "heartbeat":
        from backend.proactive.heartbeat import run_heartbeat
        report = await run_heartbeat()
        return f"Heartbeat: {report.get('anomalies', [])} anomalies"

    elif job_type == "memory_consolidation":
        from backend.memory.consolidation import run_consolidation
        report = await run_consolidation()
        return f"Consolidation: {report}"

    elif job_type == "daily_reflection":
        from backend.proactive.reflection import run_daily_reflection
        report = await run_daily_reflection()
        return report.get("reflection", "No reflection produced")

    else:
        return f"Unknown job type: {job_type}"


# ---------------------------------------------------------------------------
# Queue poll loop
# ---------------------------------------------------------------------------

async def _queue_poll_loop() -> None:
    from backend.scheduler.queue import claim_next, mark_done, mark_failed

    logger.info("Job queue poll loop started")
    while not _stop_event.is_set():
        try:
            job = await claim_next()
            if job:
                logger.info("Processing job", extra={"job_id": job["id"], "type": job["type"]})
                try:
                    result = await _execute_job(job)
                    await mark_done(job["id"], result)
                    logger.info("Job done", extra={"job_id": job["id"]})
                except Exception as exc:
                    await mark_failed(job["id"], str(exc))
                    logger.error("Job failed", extra={"job_id": job["id"], "error": str(exc)})
        except Exception as exc:
            logger.error("Queue poll error", extra={"error": str(exc)})

        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Scheduled jobs registration
# ---------------------------------------------------------------------------

async def _run_heartbeat_job() -> None:
    try:
        from backend.proactive.heartbeat import run_heartbeat
        await run_heartbeat()
    except Exception as exc:
        logger.error("Scheduled heartbeat failed", extra={"error": str(exc)})


async def _run_consolidation_job() -> None:
    try:
        from backend.memory.consolidation import run_consolidation
        await run_consolidation()
    except Exception as exc:
        logger.error("Scheduled consolidation failed", extra={"error": str(exc)})


async def _run_reflection_job() -> None:
    try:
        from backend.proactive.reflection import run_daily_reflection
        await run_daily_reflection()
    except Exception as exc:
        logger.error("Scheduled reflection failed", extra={"error": str(exc)})


async def _run_audit_purge_job() -> None:
    try:
        from backend.memory.audit import purge_old_entries
        await purge_old_entries()
    except Exception as exc:
        logger.error("Scheduled audit purge failed", extra={"error": str(exc)})


async def _register_scheduled_jobs_from_db() -> None:
    """Re-register LLM-managed scheduled jobs from the DB after restart."""
    from backend.scheduler.jobs import list_jobs
    from backend.scheduler.engine import add_cron_job, add_interval_job
    from backend.scheduler.queue import enqueue

    jobs = await list_jobs()
    for job in jobs:
        if not job["enabled"]:
            continue
        try:
            job_id = job["id"]
            cron = job["cron"]
            task_prompt = job["task_prompt"]

            async def _make_llm_task(jid: str, prompt: str):
                async def _run():
                    await enqueue("llm_task", {"job_id": jid, "task_prompt": prompt})
                return _run

            func = await _make_llm_task(job_id, task_prompt)
            add_cron_job(func, job_id=f"user_job_{job_id}", cron_expression=cron)
        except Exception as exc:
            logger.warning("Failed to re-register job", extra={"job_id": job["id"], "error": str(exc)})


def _register_builtin_scheduled_jobs() -> None:
    from backend.scheduler.engine import add_cron_job, add_interval_job

    # Heartbeat every N minutes
    interval = cfg.proactive.heartbeat_interval_minutes
    add_interval_job(_run_heartbeat_job, job_id="heartbeat", minutes=interval)

    # Memory consolidation at 3am
    consolidation_hour = cfg.memory.consolidation_hour
    add_cron_job(
        _run_consolidation_job,
        job_id="memory_consolidation",
        cron_expression=f"0 {consolidation_hour} * * *",
    )

    # Daily reflection at configured hour
    if cfg.proactive.reflection_enabled:
        reflection_hour = cfg.proactive.reflection_hour
        add_cron_job(
            _run_reflection_job,
            job_id="daily_reflection",
            cron_expression=f"0 {reflection_hour} * * *",
        )

    # Audit log purge at 2am
    add_cron_job(_run_audit_purge_job, job_id="audit_purge", cron_expression="0 2 * * *")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("assistant-worker starting")

    # Init DBs
    from backend.memory.conversation import init_db
    from backend.memory.structured import init_structured_db
    from backend.memory.audit import init_audit_db
    from backend.scheduler.queue import init_queue_db
    from backend.scheduler.jobs import init_jobs_db
    from backend.proactive.notifications import init_notifications_db
    from backend.proactive.push import init_push_db

    await init_db()
    await init_structured_db()
    await init_audit_db()
    await init_queue_db()
    await init_jobs_db()
    await init_notifications_db()
    await init_push_db()

    # Start APScheduler
    from backend.scheduler.engine import start_scheduler, stop_scheduler
    _register_builtin_scheduled_jobs()
    await _register_scheduled_jobs_from_db()
    start_scheduler()

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_event_loop()

    def _handle_signal():
        logger.info("Shutdown signal received")
        _stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    # Run queue poll loop until stopped
    try:
        await _queue_poll_loop()
    finally:
        stop_scheduler()
        logger.info("assistant-worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
