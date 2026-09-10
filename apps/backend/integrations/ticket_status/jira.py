# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Jira Cloud ticket-status adapter (#385).

Reads one issue's status via the Jira Cloud REST API v3
(``GET /rest/api/3/issue/{key}?fields=status``), authenticated with HTTP
Basic auth (the account email as username, an API token as password; Jira
Cloud's own documented scheme; this is not a TRUSCA convention).

Deliberately NOT built on ``integrations.license_fetcher.base.
request_with_retry``: that helper collapses every non-2xx / non-404
outcome to ``None`` (right for license lookup's "best-effort, missing is
fine" semantics, wrong here); a caller who clicked "refresh ticket
status" and gets back "unknown" with no reason has no way to tell "this
Jira project doesn't exist" from "the API token was revoked" from "Jira is
down". This adapter keeps that distinction and reports it in the raised
:class:`TicketStatusError`'s message, which is shown to the caller
verbatim as ``ticket_check_error``.

No retry, on purpose: a user is synchronously waiting on this one click.
Retrying a 5xx would just make them wait longer for the same likely
outcome; they can click the button again.
"""

from __future__ import annotations

import json
import re

import httpx
import structlog

from .base import DEFAULT_TIMEOUT_SECONDS, USER_AGENT, TicketStatusError, TicketStatusResult

log = structlog.get_logger("integrations.ticket_status.jira")

#: A Jira issue key: one or more uppercase letters/digits (the project key,
#: which may itself contain a digit but must start with a letter), a
#: literal hyphen, then digits. Anchored so a partial match cannot smuggle
#: extra path segments into the request URL.
_ISSUE_KEY_RE = re.compile(r"\A[A-Z][A-Z0-9]*-[0-9]+\Z")

#: A legitimate Jira issue-status JSON response is a few KB. Capped well
#: above that and far below anything that would matter for worker memory.
#: Enforced on the STREAMED response (mirroring ``license_fetcher.base``),
#: so a misbehaving proxy or an unexpectedly large body never sits fully
#: buffered in memory before this check runs.
_MAX_RESPONSE_BYTES = 1024 * 1024


def _statuscategory_is_done(fields: dict[str, object]) -> bool:
    status = fields.get("status")
    if not isinstance(status, dict):
        return False
    category = status.get("statusCategory")
    if not isinstance(category, dict):
        return False
    return category.get("key") == "done"


class JiraTicketStatusAdapter:
    """Reads one Jira Cloud issue's status.

    ``transport`` is a test seam only (``httpx.MockTransport``); every real
    caller leaves it ``None`` and gets a normal network-backed client. A
    fresh ``httpx.Client`` is built per :meth:`fetch` call rather than once
    per adapter instance, unlike ``MavenLicenseFetcher``'s reused client:
    the credential (and therefore the ``auth`` tuple) varies per call here,
    where Maven Central's fetcher never authenticates at all.
    """

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def fetch(
        self,
        *,
        host: str,
        ticket_key: str,
        username: str | None,
        api_token: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> TicketStatusResult:
        key = ticket_key.strip().upper()
        if not _ISSUE_KEY_RE.match(key):
            raise TicketStatusError(
                f"{ticket_key!r} does not look like a Jira issue key (e.g. 'PROJ-123')"
            )
        if not username:
            # Enforced earlier by ticket_credential_service for auth_scheme
            # `jira_basic`, but this adapter does not trust that a caller
            # upheld it; a missing username here would otherwise reach
            # httpx as `auth=(None, token)`, which raises a confusing
            # TypeError instead of the clear message below.
            raise TicketStatusError("no account email configured for this Jira credential")

        url = f"https://{host}/rest/api/3/issue/{key}?fields=status"
        try:
            with httpx.Client(
                auth=(username, api_token),
                timeout=timeout,
                # Security review: Jira's own API never redirects a caller
                # who used the correct host; an unexpected 3xx could only
                # be a misconfigured proxy or something worse, and the
                # `Location` host was never screened by url_guard.
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                transport=self._transport,
            ) as client, client.stream("GET", url) as response:
                status = response.status_code
                body_parts: list[bytes] = []
                size = 0
                too_large = False
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES:
                        too_large = True
                        break
                    body_parts.append(chunk)
                body = b"".join(body_parts)
        except httpx.TimeoutException as exc:
            raise TicketStatusError("the request to Jira timed out") from exc
        except httpx.HTTPError as exc:
            raise TicketStatusError(f"could not reach {host}: {exc.__class__.__name__}") from exc

        if too_large:
            raise TicketStatusError("Jira's response was unexpectedly large; refused to parse it")

        if status in (401, 403):
            log.warning("jira_ticket_status_auth_rejected", host=host, ticket_key=key)
            raise TicketStatusError("the stored Jira credential was rejected (401/403)")
        if status == 404:
            raise TicketStatusError(f"Jira has no issue {key!r} at {host}")
        if 300 <= status < 400:
            log.warning(
                "jira_ticket_status_unexpected_redirect",
                host=host,
                status=status,
                location=response.headers.get("Location", "")[:200],
            )
            raise TicketStatusError("Jira responded with an unexpected redirect")
        if status != 200:
            raise TicketStatusError(f"Jira returned an unexpected status ({status})")

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TicketStatusError("Jira's response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise TicketStatusError("Jira's response was not shaped like an issue")
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            raise TicketStatusError("Jira's response carried no status field")
        status_obj = fields.get("status")
        status_name = status_obj.get("name") if isinstance(status_obj, dict) else None
        if not isinstance(status_name, str) or not status_name:
            raise TicketStatusError("Jira's response carried no status name")

        return TicketStatusResult(
            status_name=status_name,
            resolved=_statuscategory_is_done(fields),
        )


__all__ = ["JiraTicketStatusAdapter"]
