"""
scheduler/engine.py — APScheduler setup for the Worker process.

Uses AsyncIOScheduler with SQLAlchemyJobStore so jobs survive restarts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)

_scheduler = None


def _scheduler_db_path() -> str:
    p = Path(cfg.database.path).expanduser().parent / "scheduler.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p}"


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        jobstores = {
            "default": SQLAlchemyJobStore(url=_scheduler_db_path()),
        }
        _scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
        logger.info("APScheduler initialized", extra={"db": _scheduler_db_path()})
    return _scheduler


def add_cron_job(
    func: Callable,
    job_id: str,
    cron_expression: str,
    *,
    replace_existing: bool = True,
    **kwargs: Any,
) -> None:
    """
    Add a cron job. cron_expression: "minute hour day month day_of_week"
    e.g. "0 3 * * *" for 3am daily.
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = cron_expression.strip().split()
    if len(parts) == 5:
        minute, hour, day, month, day_of_week = parts
        trigger = CronTrigger(
            minute=minute, hour=hour, day=day,
            month=month, day_of_week=day_of_week,
        )
    else:
        raise ValueError(f"Invalid cron expression: {cron_expression!r}")

    scheduler = get_scheduler()
    scheduler.add_job(
        func,
        trigger=trigger,
        id=job_id,
        replace_existing=replace_existing,
        **kwargs,
    )
    logger.info("Scheduled cron job", extra={"job_id": job_id, "cron": cron_expression})


def add_interval_job(
    func: Callable,
    job_id: str,
    minutes: int,
    *,
    replace_existing: bool = True,
    **kwargs: Any,
) -> None:
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = get_scheduler()
    scheduler.add_job(
        func,
        IntervalTrigger(minutes=minutes),
        id=job_id,
        replace_existing=replace_existing,
        **kwargs,
    )
    logger.info("Scheduled interval job", extra={"job_id": job_id, "minutes": minutes})


def remove_job(job_id: str) -> bool:
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        logger.info("Removed scheduled job", extra={"job_id": job_id})
        return True
    except Exception:
        return False


def start_scheduler() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started")


def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
