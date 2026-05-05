"""
Structured logging — structlog with JSON line output.

Quality standard §5 (CLAUDE.md):
- 1 line = 1 event, JSON formatted
- request_id / user_id / team_id / task_id flow via contextvars and are
  attached to every log line automatically
- PII (passwords, tokens, API keys, emails) must pass through mask_pii()
  before being logged
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_EMAIL_RE = re.compile(r"([^@\s]{1,2})[^@\s]*(@[^\s]+)")


def configure_logging(level: str = "INFO") -> None:
    """
    Configure structlog + stdlib logging to emit one JSON event per line on stdout.

    Idempotent: safe to call from app lifespan and from worker bootstrap.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    return logger  # type: ignore[no-any-return]


def mask_pii(value: Any) -> str:
    """
    Mask values that may contain PII before logging.

    - Empty/None → empty string
    - Email-like strings → keep first two characters of the local part + domain
    - Anything else → keep first two characters, replace the rest with ***
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if "@" in text:
        return _EMAIL_RE.sub(r"\1***\2", text, count=1)
    if len(text) <= 2:
        return "***"
    return f"{text[:2]}***"
