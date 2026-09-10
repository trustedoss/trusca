# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Ticket-status adapter dispatch (#385).

Public surface
--------------
* :func:`fetch_ticket_status`: given an ``auth_scheme`` (from the
  organization's stored :class:`models.TicketCredential`) plus the tracker
  host, ticket key and credential material, routes to the matching adapter
  and returns a :class:`TicketStatusResult`.
* :data:`AUTH_SCHEME_TO_ADAPTER`: scheme to adapter factory, re-exported for
  tests that want to plug a stub adapter.

Threading / concurrency
------------------------
Called from a synchronous request-handling path triggered by one user
clicking one button; see ``services.ticket_status_service``. Each call
opens (and closes) its own short-lived ``httpx.Client``; there is no
per-host throttle here the way the license fetcher has one, because this
is a single on-demand read, not a scan walking hundreds of components.
"""

from __future__ import annotations

from collections.abc import Callable

from models import DEFAULT_AUTH_SCHEME

from .base import (
    DEFAULT_TIMEOUT_SECONDS,
    TicketStatusAdapter,
    TicketStatusError,
    TicketStatusResult,
)
from .jira import JiraTicketStatusAdapter

#: A factory map (rather than instances), matching
#: ``license_fetcher.PURL_PREFIX_TO_FETCHER``'s shape; each dispatch call
#: gets its own adapter, avoiding any shared mutable state across calls.
AUTH_SCHEME_TO_ADAPTER: dict[str, Callable[[], TicketStatusAdapter]] = {
    DEFAULT_AUTH_SCHEME: JiraTicketStatusAdapter,
}


def fetch_ticket_status(
    *,
    auth_scheme: str,
    host: str,
    ticket_key: str,
    username: str | None,
    api_token: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> TicketStatusResult:
    """Dispatch to the adapter for ``auth_scheme`` and read the ticket back.

    Raises :class:`TicketStatusError` for both "no adapter for this scheme"
    (should not happen, since ``ticket_credential_service`` refuses to
    store a scheme with no adapter, but a stored row could predate a scheme
    being removed) and every adapter-level failure.
    """
    factory = AUTH_SCHEME_TO_ADAPTER.get(auth_scheme)
    if factory is None:
        raise TicketStatusError(f"no ticket-status adapter for auth_scheme {auth_scheme!r}")
    adapter = factory()
    return adapter.fetch(
        host=host,
        ticket_key=ticket_key,
        username=username,
        api_token=api_token,
        timeout=timeout,
    )


__all__ = [
    "AUTH_SCHEME_TO_ADAPTER",
    "TicketStatusAdapter",
    "TicketStatusError",
    "TicketStatusResult",
    "fetch_ticket_status",
]
