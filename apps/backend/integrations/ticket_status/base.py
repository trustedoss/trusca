# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Shared types for the ticket-status adapters (#385).

One adapter exists today (:mod:`.jira`). The shape here is written for more
than one anyway: a future GitHub Issues / GitLab adapter is a new module
and a new ``AUTH_SCHEME_TO_ADAPTER`` entry in :mod:`__init__`, not a change
to the orchestrating service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: Same egress identification as the license fetcher, so an operator
#: reading access logs sees one product identity across every outbound
#: read this deployment makes, not a second unexplained user agent.
USER_AGENT = (
    "TrustedOSS-Portal/0.1 "
    "(+https://github.com/trustedoss/trusca; ticket-status)"
)

DEFAULT_TIMEOUT_SECONDS = 15.0


class TicketStatusError(Exception):
    """A ticket's status could not be read.

    The message is written to be shown to the caller verbatim (it ends up
    in ``VulnerabilityFinding.ticket_check_error``), so it must never carry
    the token or any header value; adapters raise this with a short,
    human-readable reason only ("issue not found", "authentication
    rejected", a timeout), never the underlying response body.
    """


@dataclass(frozen=True)
class TicketStatusResult:
    """One tracker's answer for one ticket, at the moment it was asked.

    ``status_name`` is the tracker's own display string for the ticket's
    current status (Jira's workflow status name), free text, shown to the
    caller verbatim. ``resolved`` is the adapter's closed-vocabulary
    reading of that status (Jira: ``statusCategory.key == "done"``),
    independent of whatever a customer renamed the status to.
    """

    status_name: str
    resolved: bool


class TicketStatusAdapter(Protocol):
    """One tracker's read-back implementation."""

    def fetch(
        self,
        *,
        host: str,
        ticket_key: str,
        username: str | None,
        api_token: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> TicketStatusResult:
        """Return the ticket's current status, or raise :class:`TicketStatusError`."""


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "USER_AGENT",
    "TicketStatusAdapter",
    "TicketStatusError",
    "TicketStatusResult",
]
