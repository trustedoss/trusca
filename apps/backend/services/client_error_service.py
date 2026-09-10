# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Record a frontend crash report as a structured log line (#423).

`ErrorBoundary.tsx` used to stop at `console.error`, visible only in a
browser console nobody is watching. This gives it somewhere to land that an
operator actually sees: the same structured-logging path (CLAUDE.md core
rule #5) every backend error already goes through, so `grep`-ing or
alerting on one surface catches both.

No database row, no queue: one log line is the whole feature. A render
crash is not an event anything needs to query, aggregate, or act on beyond
"an operator can see this happened and to whom", and a table would be
another thing to retain and prune for that.

PII: `message` / `stack` / `component_stack` are free text from a browser
about its own crash, not user-echoing content by construction (a JS stack
trace names files and functions, not form fields), logged as-is, capped by
the schema's field lengths rather than content-scanned, matching how this
codebase already treats other free-text-but-not-user-authored fields (scan
tool stderr, webhook delivery bodies). `url` IS attacker/user-influenced
(the caller controls `window.location.href`), so its query string is
stripped before logging: a query param can carry a token or a
password-reset code, and nothing about a crash report needs it.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

import structlog

from schemas.client_error import ClientErrorReportIn

log = structlog.get_logger("services.client_error")


def _strip_query(url: str) -> str:
    """`url` with any query string and fragment removed.

    Falls back to the raw value if it does not parse as a URL at all
    (`urlsplit` never raises, so this is purely for callers that manage to
    pass something too malformed to be worth trusting either way).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def record_client_error(
    report: ClientErrorReportIn,
    *,
    actor_user_id: uuid.UUID | None,
    user_agent: str | None,
) -> None:
    """Log one crash report. Never raises: a broken report must not turn a
    "the UI already crashed" request into a second failure."""
    log.warning(
        "client_error_reported",
        message=report.message,
        stack=report.stack,
        component_stack=report.component_stack,
        url=_strip_query(report.url),
        user_agent=user_agent,
        actor_user_id=str(actor_user_id) if actor_user_id else None,
    )


__all__ = ["record_client_error"]
