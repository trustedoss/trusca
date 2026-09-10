# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Read a finding's external ticket back on demand (#385).

User-triggered, not polled: a person looking at a finding clicks "refresh
ticket status", this makes ONE outbound call to the tracker named in
`ticket_url`, and the finding's `ticket_status` / `ticket_resolved` /
`ticket_checked_at` / `ticket_check_error` columns are updated with the
answer. There is no background sweep; see the module docstring on
`integrations.ticket_status` for why an unattended poller would need more
hardening (IP-pinning) than this on-demand path ships with.

Failure is data, not an HTTP error, past the caller-side checks (finding
missing, not this actor's team, nothing to check). Whatever went wrong
talking to the tracker (no credential configured, the URL failed the SSRF
guard, Jira rejected the token, the issue does not exist) lands in
`ticket_check_error` and the call still returns 200: the ATTEMPT succeeded
(the finding's ticket-check state is now current), even when the ANSWER is
"could not find out why". A previously-known status is left in place on
failure; `ticket_status` / `ticket_resolved` are never cleared just
because the most recent check failed, matching the changed-value-guarded
"verdict=None leaves the row untouched" idiom `eol_catalog.
stamp_component_version` already uses for the same reason: losing the
tracker for one check must not silently un-know an answer that was
already there.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from core.url_guard import TicketUrlValidationError, validate_http_url
from integrations.ticket_status import TicketStatusError, fetch_ticket_status
from models import Project, Scan, Team, VulnerabilityFinding
from services.ticket_credential_service import credential_for_host

log = structlog.get_logger("services.ticket_status")


class TicketStatusServiceError(Exception):
    """Base class for caller-side failures (never a tracker-side one).

    Carries an HTTP status + title, matching ``services.project_service.
    ProjectError`` / ``services.vulnerability_service.VulnerabilityError``'s
    shape, so the API layer maps every one of these the same small way.
    """

    status_code: int = 400
    title: str = "Ticket Status Error"


class FindingNotFound(TicketStatusServiceError):
    """No finding with this id, or it belongs to another team.

    One exception for both: existence-hiding, matching every other
    finding-scoped lookup in this codebase (a cross-team id 404s rather
    than 403ing).
    """

    status_code = 404
    title = "Vulnerability Finding Not Found"


class NoTicketConfigured(TicketStatusServiceError):
    """The finding has no `ticket_url` to check."""

    status_code = 422
    title = "No Ticket Configured"


@dataclass(frozen=True)
class TicketStatusRefreshResult:
    """The finding's ticket-check state after one refresh attempt."""

    finding_id: uuid.UUID
    ticket_status: str | None
    ticket_resolved: bool | None
    ticket_checked_at: datetime
    ticket_check_error: str | None


async def _load_finding_with_team(
    session: AsyncSession, finding_id: uuid.UUID
) -> tuple[VulnerabilityFinding, uuid.UUID, uuid.UUID] | None:
    """``(finding, team_id, organization_id)``, or ``None`` if not found."""
    stmt = (
        select(VulnerabilityFinding, Project.team_id, Team.organization_id)
        .join(Scan, Scan.id == VulnerabilityFinding.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(Team, Team.id == Project.team_id)
        .where(VulnerabilityFinding.id == finding_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1], row[2]


def _ticket_key_from(finding: VulnerabilityFinding, url_path: str) -> str | None:
    """Prefer the URL's last path segment; fall back to the stored `ticket_key`.

    The URL is what the SSRF guard just validated, and its final path
    segment is Jira's own convention for a browser link
    (``.../browse/PROJ-123``). `ticket_key` is free text the caller may or
    may not have filled in independently of that URL (neither field is
    validated against a tracker's key format at storage time), so it is
    used only when the URL carries no path segment to derive a key from.
    Trusting free text over the URL would let the two fields diverge and
    turn this into a lookup for an arbitrary key, unrelated to the ticket
    the URL actually names, against the org's shared tracker credential.
    """
    segment = url_path.rstrip("/").rsplit("/", 1)[-1]
    if segment:
        return segment
    if finding.ticket_key and finding.ticket_key.strip():
        return finding.ticket_key.strip()
    return None


async def refresh_ticket_status(
    session: AsyncSession,
    *,
    finding_id: uuid.UUID,
    actor: CurrentUser,
) -> TicketStatusRefreshResult:
    """Make one outbound call to the finding's ticket and record the answer.

    Raises :class:`FindingNotFound` (caller error → 404) and
    :class:`NoTicketConfigured` (caller error → 422, nothing to check).
    Every OTHER failure (no credential, SSRF-rejected URL, tracker auth
    rejected, issue not found, network error) is recorded on the finding
    and returned as data (see the module docstring).
    """
    loaded = await _load_finding_with_team(session, finding_id)
    if loaded is None:
        raise FindingNotFound(f"vulnerability finding {finding_id} not found")
    finding, team_id, organization_id = loaded

    await assert_team_access(
        session,
        actor,
        team_id,
        log=log,
        resource="vulnerability_finding_ticket_status",
        resource_id=str(finding_id),
        deny=lambda: FindingNotFound(f"vulnerability finding {finding_id} not found"),
    )

    ticket_url = (finding.ticket_url or "").strip()
    if not ticket_url:
        raise NoTicketConfigured(f"finding {finding_id} has no ticket_url set")

    now = datetime.now(tz=UTC)

    def _record_failure(reason: str) -> TicketStatusRefreshResult:
        finding.ticket_checked_at = now
        finding.ticket_check_error = reason
        return TicketStatusRefreshResult(
            finding_id=finding_id,
            ticket_status=finding.ticket_status,
            ticket_resolved=finding.ticket_resolved,
            ticket_checked_at=now,
            ticket_check_error=reason,
        )

    try:
        normalized_url = validate_http_url(ticket_url)
    except TicketUrlValidationError as exc:
        log.warning("ticket_status_url_rejected", finding_id=str(finding_id), reason=str(exc))
        result = _record_failure("ticket_url is not reachable from this deployment")
        await session.commit()
        return result

    parts = urlsplit(normalized_url)
    host = (parts.hostname or "").lower()
    ticket_key = _ticket_key_from(finding, parts.path)
    if not ticket_key:
        result = _record_failure("could not determine a ticket key from ticket_url")
        await session.commit()
        return result

    credential = await credential_for_host(session, organization_id=organization_id, host=host)
    if credential is None:
        result = _record_failure(f"no ticket-tracker credential configured for {host!r}")
        await session.commit()
        return result
    auth_scheme, username, api_token = credential

    try:
        status_result = fetch_ticket_status(
            auth_scheme=auth_scheme,
            host=host,
            ticket_key=ticket_key,
            username=username,
            api_token=api_token,
        )
    except TicketStatusError as exc:
        result = _record_failure(str(exc))
        await session.commit()
        return result

    finding.ticket_status = status_result.status_name
    finding.ticket_resolved = status_result.resolved
    finding.ticket_checked_at = now
    finding.ticket_check_error = None
    await session.commit()

    log.info(
        "ticket_status_refreshed",
        finding_id=str(finding_id),
        host=host,
        resolved=status_result.resolved,
    )
    return TicketStatusRefreshResult(
        finding_id=finding_id,
        ticket_status=finding.ticket_status,
        ticket_resolved=finding.ticket_resolved,
        ticket_checked_at=now,
        ticket_check_error=None,
    )


__all__ = [
    "FindingNotFound",
    "NoTicketConfigured",
    "TicketStatusRefreshResult",
    "TicketStatusServiceError",
    "refresh_ticket_status",
]
