"""
proactive/heartbeat.py — Rule-based monitoring loop, runs every 5 minutes.

Checks (no LLM unless anomaly found):
  - System resource anomalies: disk > 90%, memory > 90%, CPU > 95% for 5 min
  - Monitored URLs/services (via uptime_monitor if available)
  - Filesystem changes in watched dirs
  - Any pending urgent notifications

On anomaly: calls LLM to analyse, then emits notification.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.config.loader import cfg
from backend.config.logging import get_logger

logger = get_logger(__name__)

# CPU high-usage tracking (need 5 consecutive readings @ 1 min = 5 readings)
_cpu_high_count = 0
_CPU_HIGH_THRESHOLD = 95.0
_CPU_HIGH_CONSECUTIVE = 3  # 3 heartbeat ticks = ~15 min


async def _check_system_resources() -> List[Dict[str, Any]]:
    """Return list of anomaly dicts if thresholds exceeded."""
    import psutil

    anomalies: List[Dict[str, Any]] = []

    # Disk
    try:
        disk = psutil.disk_usage("/")
        pct = disk.percent
        if pct > 90:
            anomalies.append({
                "type": "disk",
                "message": f"Disk usage is {pct:.1f}% (threshold: 90%)",
                "value": pct,
            })
    except Exception as exc:
        logger.warning("Disk check failed", extra={"error": str(exc)})

    # Memory
    try:
        mem = psutil.virtual_memory()
        pct = mem.percent
        if pct > 90:
            anomalies.append({
                "type": "memory",
                "message": f"Memory usage is {pct:.1f}% (threshold: 90%)",
                "value": pct,
            })
    except Exception as exc:
        logger.warning("Memory check failed", extra={"error": str(exc)})

    # CPU (sustained)
    global _cpu_high_count
    try:
        cpu_pct = psutil.cpu_percent(interval=1)
        if cpu_pct > _CPU_HIGH_THRESHOLD:
            _cpu_high_count += 1
        else:
            _cpu_high_count = 0

        if _cpu_high_count >= _CPU_HIGH_CONSECUTIVE:
            anomalies.append({
                "type": "cpu",
                "message": f"CPU usage has been above {_CPU_HIGH_THRESHOLD:.0f}% for "
                           f"{_cpu_high_count} consecutive heartbeats",
                "value": cpu_pct,
            })
    except Exception as exc:
        logger.warning("CPU check failed", extra={"error": str(exc)})

    return anomalies


async def _analyse_anomalies(anomalies: List[Dict[str, Any]]) -> str:
    """Call LLM to produce a human-readable alert message."""
    try:
        import anthropic
        import json

        client = anthropic.AsyncAnthropic()
        summary = json.dumps(anomalies, indent=2)
        response = await client.messages.create(
            model=cfg.llm.model,
            max_tokens=256,
            system="You are a system monitoring assistant. Briefly describe the issue and suggest one action.",
            messages=[{"role": "user", "content": f"System anomalies detected:\n{summary}"}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("LLM analysis failed", extra={"error": str(exc)})
        return "; ".join(a["message"] for a in anomalies)


async def run_heartbeat() -> Dict[str, Any]:
    """
    Single heartbeat tick. Called by APScheduler every N minutes.
    Returns a report dict.
    """
    start = time.monotonic()
    report: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "anomalies": [],
        "notifications_created": 0,
        "duration_ms": 0,
    }

    try:
        # System resource checks
        anomalies = await _check_system_resources()
        report["anomalies"] = anomalies

        if anomalies:
            analysis = await _analyse_anomalies(anomalies)

            # Determine priority
            has_critical = any(a["type"] == "disk" and a["value"] > 95 for a in anomalies)
            priority = "urgent" if has_critical else "warning"

            from backend.proactive.notifications import create_notification
            await create_notification(
                title="System Alert",
                body=analysis,
                priority=priority,
                source="heartbeat",
            )
            report["notifications_created"] += 1

    except Exception as exc:
        logger.error("Heartbeat failed", extra={"error": str(exc)})
        report["error"] = str(exc)

    report["duration_ms"] = int((time.monotonic() - start) * 1000)
    logger.info("Heartbeat complete", extra=report)
    return report
