# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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
import os
import re
import sys
from typing import Any

import structlog

_EMAIL_RE = re.compile(r"([^@\s]{1,2})[^@\s]*(@[^\s]+)")

# The level this process is already configured for, or ``None`` before the
# first call. See :func:`configure_logging` for why re-configuring at the same
# level is not merely redundant but harmful.
_CONFIGURED_LEVEL: int | None = None


def _under_pytest() -> bool:
    """True while a pytest session is running.

    Read at call time, never cached at module level (CLAUDE.md rule 11).
    ``PYTEST_VERSION`` is set by pytest for the whole session, unlike
    ``PYTEST_CURRENT_TEST`` which only exists inside a running test — and this
    is called from app startup, which fixtures trigger between tests.
    """
    return "PYTEST_VERSION" in os.environ or "PYTEST_CURRENT_TEST" in os.environ


def configure_logging(level: str = "INFO") -> None:
    """
    Configure structlog + stdlib logging to emit one JSON event per line on stdout.

    Idempotent, and now actually so: a second call at the same level returns
    without touching structlog.

    Why that matters beyond saving work
    -----------------------------------
    ``cache_logger_on_first_use=True`` means a module-level
    ``log = structlog.get_logger(...)`` freezes its bound logger the first time
    it is used. ``structlog.configure()`` replaces the global config but cannot
    reach into a proxy that has already frozen — so after a re-configure, that
    logger is running a chain nothing else can see or replace. The visible
    symptom is that ``structlog.testing.capture_logs()`` silently captures
    nothing from any module that logged before the re-configure, which is how
    three cross-team authorization assertions started failing depending on
    which tests ran first.

    Re-configuring at a DIFFERENT level is still honoured — that is a real
    change of intent (a worker booting at DEBUG), and the caller accepts the
    same caveat for loggers already in use.
    """
    global _CONFIGURED_LEVEL

    log_level = getattr(logging, level.upper(), logging.INFO)
    if _CONFIGURED_LEVEL == log_level:
        return

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
        # Caching binds a module's ``log`` to a concrete logger the first time
        # it is used, which is the right trade in production — and wrong under
        # test. A frozen proxy ignores every later ``structlog.configure()``,
        # so ``structlog.testing.capture_logs()`` silently captures NOTHING
        # from any module that logged earlier in the session. The result is a
        # logging assertion whose outcome depends on which tests ran before it.
        # Under pytest we pay the small per-call cost instead.
        cache_logger_on_first_use=not _under_pytest(),
    )

    _CONFIGURED_LEVEL = log_level


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
