"""
config/logging.py — Structured logging setup with correlation IDs.

Uses structlog for JSON output. Correlation IDs propagate through the entire
request lifecycle via Python's contextvars.

Usage:
    from backend.config.logging import setup_logging, get_logger, correlation_id_var
    setup_logging()
    logger = get_logger(__name__)
    logger.info("event happened", extra_field="value")

    # Set correlation ID for a request:
    token = correlation_id_var.set("req-abc-123")
    try:
        ...
    finally:
        correlation_id_var.reset(token)
"""

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Optional

import structlog

# ContextVar carrying the current request's correlation ID
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def _add_correlation_id(
    logger: logging.Logger,
    method: str,
    event_dict: dict,
) -> dict:
    """Structlog processor: inject current correlation ID into every log record."""
    cid = correlation_id_var.get("")
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """
    Configure structlog + stdlib logging for structured output.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR)
        fmt:   "json" for machine-readable logs, "console" for human-readable dev output
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_id,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet noisy libraries
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for the given module name."""
    return structlog.get_logger(name)


def new_correlation_id() -> str:
    """Generate a fresh correlation ID (UUID4)."""
    return str(uuid.uuid4())
