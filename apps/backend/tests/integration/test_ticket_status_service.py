# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Ticket-status refresh orchestration (#385).

The adapter itself is unit-tested with a MockTransport
(``tests/unit/integrations/ticket_status/test_jira.py``); this file drives
``services.ticket_status_service.refresh_ticket_status`` against a real
Postgres session with the adapter dispatch monkeypatched, proving the
service's OWN job: team-scoped access, the SSRF guard actually gets
called, a missing credential is reported rather than raised, and a
previously-known status survives a failed re-check.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import database_url
from core.security import CurrentUser
from integrations.ticket_status.base import TicketStatusError, TicketStatusResult
from models import (
    Component,
    ComponentVersion,
    Organization,
    ScanComponent,
    Team,
    Vulnerability,
    VulnerabilityFinding,
)
from services.ticket_credential_service import upsert_credential
from services.ticket_status_service import (
    FindingNotFound,
    NoTicketConfigured,
    refresh_ticket_status,
)
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _resolve_test_host_only(monkeypatch: pytest.MonkeyPatch, host: str, ip: str) -> None:
    """Fake DNS for exactly ``host``; every other lookup (including the test's
    own asyncpg connection to Postgres) goes to the real resolver.

    A blanket ``socket.getaddrinfo`` patch is process-global: ``socket`` is
    one shared module object, so patching ``core.url_guard.socket.
    getaddrinfo`` (the pattern the unit tests use) also breaks asyncpg's own
    DNS lookups in any test that ALSO holds a live DB session, which every
    test in this file does.
    """
    real = socket.getaddrinfo

    def _fake(name: str, port: object, *args: object, **kwargs: object) -> object:
        if name == host:
            return [(socket.AF_INET, 0, 0, "", (ip, 0))]
        return real(name, port, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("core.url_guard.socket.getaddrinfo", _fake)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


@dataclass
class _Fixture:
    finding_id: uuid.UUID
    org: Organization
    team: Team
    actor: CurrentUser


async def _seed_finding(session: AsyncSession, *, ticket_url: str | None) -> _Fixture:
    """One open finding, its team, and a developer actor who can see it."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")

    suffix = unique_suffix()
    purl = f"pkg:npm/ticket-status-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"ticket-status-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    version = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)

    session.add(
        ScanComponent(scan_id=scan.id, component_version_id=version.id, direct=True, raw_data={})
    )
    vulnerability = Vulnerability(
        external_id=f"CVE-2024-{suffix}", source="NVD", severity="high", summary="fixture"
    )
    session.add(vulnerability)
    await session.commit()
    await session.refresh(vulnerability)

    finding = VulnerabilityFinding(
        scan_id=scan.id,
        component_version_id=version.id,
        vulnerability_id=vulnerability.id,
        status="new",
        ticket_url=ticket_url,
        ticket_key="PROJ-1" if ticket_url else None,
    )
    session.add(finding)
    await session.commit()
    await session.refresh(finding)

    actor = CurrentUser(
        id=user.id,
        email=user.email,
        role="developer",
        team_ids=[team.id],
        team_roles={team.id: "developer"},
        is_active=True,
        is_superuser=False,
    )
    return _Fixture(finding_id=finding.id, org=org, team=team, actor=actor)


async def test_no_ticket_url_raises_no_ticket_configured(session: AsyncSession) -> None:
    fx = await _seed_finding(session, ticket_url=None)
    with pytest.raises(NoTicketConfigured):
        await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)


async def test_unknown_finding_raises_not_found(session: AsyncSession) -> None:
    fx = await _seed_finding(session, ticket_url="https://example.atlassian.net/browse/PROJ-1")
    with pytest.raises(FindingNotFound):
        await refresh_ticket_status(session, finding_id=uuid.uuid4(), actor=fx.actor)


async def test_a_finding_in_another_team_404s_not_403s(session: AsyncSession) -> None:
    """Existence-hiding: a cross-team id reads the same as a nonexistent one."""
    fx = await _seed_finding(session, ticket_url="https://example.atlassian.net/browse/PROJ-1")
    other = await _seed_finding(session, ticket_url=None)
    with pytest.raises(FindingNotFound):
        await refresh_ticket_status(session, finding_id=fx.finding_id, actor=other.actor)


async def test_ssrf_rejected_url_is_recorded_not_raised(session: AsyncSession) -> None:
    fx = await _seed_finding(session, ticket_url="http://169.254.169.254/browse/PROJ-1")
    result = await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)
    assert result.ticket_check_error is not None
    assert "not reachable" in result.ticket_check_error
    assert "169.254.169.254" not in result.ticket_check_error
    assert result.ticket_status is None
    assert result.ticket_resolved is None


async def test_no_credential_configured_is_recorded_not_raised(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resolve_test_host_only(monkeypatch, "example.atlassian.net", "8.8.8.8")
    fx = await _seed_finding(session, ticket_url="https://example.atlassian.net/browse/PROJ-1")
    result = await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)
    assert result.ticket_check_error is not None
    assert "no ticket-tracker credential" in result.ticket_check_error


async def test_successful_check_updates_the_finding(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resolve_test_host_only(monkeypatch, "example.atlassian.net", "8.8.8.8")
    fx = await _seed_finding(session, ticket_url="https://example.atlassian.net/browse/PROJ-1")
    await upsert_credential(
        session,
        organization_id=fx.org.id,
        host="example.atlassian.net",
        auth_scheme="jira_basic",
        username="bot@example.com",
        api_token="tok",  # noqa: S106
    )

    def _fake_fetch(**kwargs: object) -> TicketStatusResult:
        return TicketStatusResult(status_name="Done", resolved=True)

    monkeypatch.setattr("services.ticket_status_service.fetch_ticket_status", _fake_fetch)

    result = await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)
    assert result.ticket_status == "Done"
    assert result.ticket_resolved is True
    assert result.ticket_check_error is None


async def test_ticket_key_field_cannot_override_the_url_derived_key(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `ticket_key` that diverges from the URL's path is ignored.

    Otherwise a caller could set `ticket_url` to a host the org has a
    credential for and `ticket_key` to an unrelated issue, turning the
    shared credential into a lookup oracle for any key in the tracker.
    """
    _resolve_test_host_only(monkeypatch, "example.atlassian.net", "8.8.8.8")
    fx = await _seed_finding(session, ticket_url="https://example.atlassian.net/browse/PROJ-1")
    finding = await session.get(VulnerabilityFinding, fx.finding_id)
    assert finding is not None
    finding.ticket_key = "HR-4"
    await session.commit()

    await upsert_credential(
        session,
        organization_id=fx.org.id,
        host="example.atlassian.net",
        auth_scheme="jira_basic",
        username="bot@example.com",
        api_token="tok",  # noqa: S106
    )

    seen_keys: list[str] = []

    def _fake_fetch(*, ticket_key: str, **kwargs: object) -> TicketStatusResult:
        seen_keys.append(ticket_key)
        return TicketStatusResult(status_name="Done", resolved=True)

    monkeypatch.setattr("services.ticket_status_service.fetch_ticket_status", _fake_fetch)

    await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)
    assert seen_keys == ["PROJ-1"]


async def test_a_known_status_survives_a_failed_recheck(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previously-known answer is not cleared just because THIS check failed."""
    _resolve_test_host_only(monkeypatch, "example.atlassian.net", "8.8.8.8")
    fx = await _seed_finding(session, ticket_url="https://example.atlassian.net/browse/PROJ-1")
    await upsert_credential(
        session,
        organization_id=fx.org.id,
        host="example.atlassian.net",
        auth_scheme="jira_basic",
        username="bot@example.com",
        api_token="tok",  # noqa: S106
    )

    monkeypatch.setattr(
        "services.ticket_status_service.fetch_ticket_status",
        lambda **kwargs: TicketStatusResult(status_name="In Progress", resolved=False),
    )
    first = await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)
    assert first.ticket_status == "In Progress"

    def _raise(**kwargs: object) -> TicketStatusResult:
        raise TicketStatusError("Jira is down")

    monkeypatch.setattr("services.ticket_status_service.fetch_ticket_status", _raise)
    second = await refresh_ticket_status(session, finding_id=fx.finding_id, actor=fx.actor)
    assert second.ticket_check_error == "Jira is down"
    # The status from the FIRST (successful) check is still there.
    assert second.ticket_status == "In Progress"
    assert second.ticket_resolved is False
