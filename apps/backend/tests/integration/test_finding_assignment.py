# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Owning a finding: who, by when, tracked where (ER28a).

The deadline rule is stated in ``services.due_date`` and unit-tested there.
These drive the same cases through real SQL, because the rule has two
implementations that must not disagree: the list computes it in Postgres so it
can filter and sort a LIMIT/OFFSET page, and the drawer computes it in Python.
Postgres ``LEAST`` ignores NULLs and Python ``min()`` raises on them, so the
four NULL combinations are where a twin drifts.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping finding assignment tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture
async def app():  # noqa: ANN201
    import main as m

    return m.app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _bearer(user: User, role: str = "developer") -> dict[str, str]:
    actual = "super_admin" if user.is_superuser else role
    return {
        "Authorization": f"Bearer {create_access_token(subject=str(user.id), role=actual)}"
    }


async def _factory(client: AsyncClient):  # noqa: ANN202
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_finding(client: AsyncClient, *, severity: str = "high"):  # noqa: ANN202
    """One finding, its project, and a developer who can edit it."""
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )
    from tests._helpers import unique_suffix

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        dev = await make_user(session)
        await make_membership(session, user=dev, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")

        suffix = unique_suffix()
        purl = f"pkg:npm/pkg-{suffix}"
        component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
        session.add(component)
        await session.commit()
        await session.refresh(component)
        cv = ComponentVersion(
            component_id=component.id,
            version="1.0.0",
            purl_with_version=f"{purl}@1.0.0",
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)
        session.add(
            ScanComponent(
                scan_id=scan.id, component_version_id=cv.id, direct=True, raw_data={}
            )
        )
        vuln = Vulnerability(
            external_id=f"CVE-2026-{suffix}",
            source="NVD",
            severity=severity,
            summary="assignment fixture",
        )
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)
        finding = VulnerabilityFinding(
            scan_id=scan.id, component_version_id=cv.id, vulnerability_id=vuln.id
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        return finding, project, team, dev


def _url(finding_id: uuid.UUID) -> str:
    return f"/v1/vulnerability_findings/{finding_id}/assignment"


# --- the write path ---------------------------------------------------------


async def test_a_finding_can_be_assigned_and_unassigned(client) -> None:  # noqa: ANN001
    """Unassigning is why absent and null have to mean different things: if the
    body could not say "null", there would be no way back to unassigned."""
    finding, _project, _team, dev = await _seed_finding(client)

    assigned = await client.patch(
        _url(finding.id),
        headers=_bearer(dev),
        json={"assignee_user_id": str(dev.id)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee_user_id"] == str(dev.id)

    cleared = await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["assignee_user_id"] is None


async def test_an_absent_field_is_left_alone(client) -> None:  # noqa: ANN001
    """A PATCH that only sets a ticket must not unassign the finding."""
    finding, _project, _team, dev = await _seed_finding(client)
    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": str(dev.id)}
    )

    ticketed = await client.patch(
        _url(finding.id),
        headers=_bearer(dev),
        json={"ticket_url": "https://tracker.example.com/SEC-1"},
    )
    assert ticketed.status_code == 200, ticketed.text
    assert ticketed.json()["assignee_user_id"] == str(dev.id)
    assert ticketed.json()["ticket_url"] == "https://tracker.example.com/SEC-1"


async def test_an_empty_body_is_refused(client) -> None:  # noqa: ANN001
    """Rather than a silent no-op that reads as success."""
    finding, _project, _team, dev = await _seed_finding(client)
    response = await client.patch(_url(finding.id), headers=_bearer(dev), json={})
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# --- ticket_url is rendered as a link ---------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        # Case matters: a browser reads the scheme case-insensitively, so a
        # comparison that forgot .lower() would let this one through.
        "JAVASCRIPT:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "/relative/path",
        "SEC-1234",
    ],
)
async def test_a_ticket_url_that_is_not_a_link_is_refused(client, hostile) -> None:  # noqa: ANN001
    """Blocked at storage, not at rendering.

    The value is written once and read by the list, the drawer, and anything
    later built on them. A guard per surface is a guard one surface will be
    missing, and a stored `javascript:` URL reaches everyone who opens the
    finding.
    """
    finding, _project, _team, dev = await _seed_finding(client)
    response = await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"ticket_url": hostile}
    )
    assert response.status_code == 422, response.text

    # And nothing was stored: a refused write must not half-apply.
    factory = await _factory(client)
    async with factory() as session:
        stored = (
            await session.execute(
                text(
                    "SELECT ticket_url FROM vulnerability_findings WHERE id = :i"
                ),
                {"i": str(finding.id)},
            )
        ).scalar_one()
    assert stored is None


async def test_an_http_ticket_url_is_kept(client) -> None:  # noqa: ANN001
    finding, _project, _team, dev = await _seed_finding(client)
    for good in ("https://jira.example.com/browse/SEC-1", "http://tracker.local/2"):
        response = await client.patch(
            _url(finding.id), headers=_bearer(dev), json={"ticket_url": good}
        )
        assert response.status_code == 200, response.text
        assert response.json()["ticket_url"] == good


# --- who may write ----------------------------------------------------------


async def test_a_finding_in_another_team_is_hidden(client) -> None:  # noqa: ANN001
    """404 rather than 403, the same policy as the status PATCH: a caller must
    not learn that a finding id exists somewhere they cannot see."""
    finding, _project, _team, _dev = await _seed_finding(client)
    factory = await _factory(client)
    async with factory() as session:
        outsider = await make_user(session)

    response = await client.patch(
        _url(finding.id),
        headers=_bearer(outsider),
        json={"due_on": "2026-09-30"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_the_assignee_must_be_on_the_team(client) -> None:  # noqa: ANN001
    """Naming somebody outside the team records a row that looks owned while
    nobody has been asked."""
    finding, _project, _team, dev = await _seed_finding(client)
    factory = await _factory(client)
    async with factory() as session:
        stranger = await make_user(session)

    response = await client.patch(
        _url(finding.id),
        headers=_bearer(dev),
        json={"assignee_user_id": str(stranger.id)},
    )
    assert response.status_code == 422, response.text


async def test_a_stale_if_match_conflicts(client) -> None:  # noqa: ANN001
    finding, _project, _team, dev = await _seed_finding(client)
    stale = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    response = await client.patch(
        _url(finding.id),
        headers=_bearer(dev),
        json={"due_on": "2026-09-30", "if_match": stale},
    )
    assert response.status_code == 409, response.text


# --- the two implementations of the deadline rule ---------------------------


async def _principal(user: User, team_id: uuid.UUID):  # noqa: ANN202
    from tests._helpers import principal_for

    return principal_for(user, team_ids=[team_id], role="developer")


@pytest.mark.parametrize(
    ("severity", "written_date"),
    [
        # Both deadlines exist.
        ("high", date(2020, 1, 2)),
        # Only the policy's: no date written down.
        ("high", None),
        # Only a written one: info carries no SLA window, so this row can only
        # have a deadline because somebody set one.
        ("info", date(2020, 1, 2)),
        # Neither.
        ("info", None),
    ],
)
async def test_sql_and_python_agree_on_the_effective_deadline(
    client, severity, written_date  # noqa: ANN001
) -> None:
    """The four NULL combinations, through the real list query and the real
    drawer path.

    Postgres ``LEAST`` ignores NULLs; Python ``min()`` raises on them. That is
    the shape of the divergence, so each combination is driven through both
    rather than through the shared helper only.
    """
    from services.due_date import effective_due
    from services.vulnerability_service import (
        get_vulnerability_detail,
        list_project_vulnerabilities,
    )

    finding, project, team, dev = await _seed_finding(client, severity=severity)
    if written_date is not None:
        await client.patch(
            _url(finding.id),
            headers=_bearer(dev),
            json={"due_on": written_date.isoformat()},
        )

    factory = await _factory(client)
    async with factory() as session:
        actor = await _principal(dev, team.id)
        items, _total, _distribution = await list_project_vulnerabilities(
            session, project_id=project.id, actor=actor
        )
        row = next(r for r in items if r["id"] == finding.id)
        detail = await get_vulnerability_detail(
            session, finding_id=finding.id, actor=actor
        )

    # The SQL row and the Python drawer must agree with each other...
    assert row["effective_due_date"] == detail["effective_due_date"]
    assert row["due_source"] == detail["due_source"]
    # ...and both with the rule they are supposed to implement.
    expected, expected_source = effective_due(
        sla_due=detail["sla_due_date"], due_on=written_date
    )
    assert row["effective_due_date"] == expected
    assert row["due_source"] == expected_source


async def test_a_written_date_gives_an_info_finding_a_deadline(client) -> None:  # noqa: ANN001
    """`sla_status` used to be null for info severities no matter what, because
    they carry no SLA window. A written deadline is a deadline, so the status
    is no longer null and the `?sla=` filter can see the row."""
    from services.vulnerability_service import get_vulnerability_detail

    finding, project, team, dev = await _seed_finding(client, severity="info")
    factory = await _factory(client)
    async with factory() as session:
        actor = await _principal(dev, team.id)
        before = await get_vulnerability_detail(
            session, finding_id=finding.id, actor=actor
        )
    assert before["sla_status"] is None

    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"due_on": "2020-01-02"}
    )
    async with factory() as session:
        actor = await _principal(dev, team.id)
        after = await get_vulnerability_detail(
            session, finding_id=finding.id, actor=actor
        )
    assert after["sla_status"] == "overdue"
    assert after["due_source"] == "manual"


async def test_a_later_date_is_stored_and_reported_as_not_governing(client) -> None:  # noqa: ANN001
    """Condition for ER28a: the author is told at the moment they save it, not
    left to discover it from a list nobody re-reads."""
    finding, _project, _team, dev = await _seed_finding(client, severity="critical")
    far_future = (datetime.now(tz=UTC) + timedelta(days=3650)).date()

    response = await client.patch(
        _url(finding.id),
        headers=_bearer(dev),
        json={"due_on": far_future.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Stored, so the intent is not lost...
    assert body["due_on"] == far_future.isoformat()
    # ...but the policy still governs, and the response says so.
    assert body["due_source"] == "sla"
    assert body["manual_due_ignored"] is True


# --- what happens to an assignment when the person changes ------------------


async def test_deactivating_the_assignee_keeps_the_assignment_and_shows_it(
    client,  # noqa: ANN001
) -> None:
    """Current behaviour, pinned deliberately.

    Eligibility is checked when the assignment is written and never again, so
    deactivating somebody leaves their name on the finding. That is the state
    `services.assignee` calls worse than unassigned: it looks owned while
    nobody can act. Nothing here changes that; `assignee_is_active` is how a
    reader can see it.
    """
    from sqlalchemy import update

    from models import User as UserModel

    finding, _project, _team, dev = await _seed_finding(client)
    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": str(dev.id)}
    )

    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            update(UserModel).where(UserModel.id == dev.id).values(is_active=False)
        )
        await session.commit()

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT assignee_user_id FROM vulnerability_findings WHERE id=:i"
                ),
                {"i": str(finding.id)},
            )
        ).scalar_one()
    assert row == dev.id, "the assignment survives deactivation"


async def test_removing_the_assignee_from_the_team_keeps_the_assignment(
    client,  # noqa: ANN001
) -> None:
    """Same shape as deactivation: a write-time check does not re-run."""
    from sqlalchemy import delete

    from models import Membership

    finding, _project, team, dev = await _seed_finding(client)
    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": str(dev.id)}
    )

    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            delete(Membership).where(
                Membership.user_id == dev.id, Membership.team_id == team.id
            )
        )
        await session.commit()
        row = (
            await session.execute(
                text(
                    "SELECT assignee_user_id FROM vulnerability_findings WHERE id=:i"
                ),
                {"i": str(finding.id)},
            )
        ).scalar_one()
    assert row == dev.id, "the assignment survives leaving the team"


async def test_deleting_the_assignee_unassigns_rather_than_blocking(client) -> None:  # noqa: ANN001
    """`ON DELETE SET NULL`: a person has to be removable, so their
    assignments become unassigned. This is the path where the work silently
    stops being anybody's, which is why the list has to show unassigned rows.

    Obligations behave identically by construction (same column definition,
    same ondelete), which is what keeps user-lifecycle work from having to
    treat the two tables differently.
    """
    from sqlalchemy import delete

    from models import Membership
    from models import User as UserModel

    finding, _project, team, dev = await _seed_finding(client)
    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": str(dev.id)}
    )

    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            delete(Membership).where(Membership.user_id == dev.id)
        )
        await session.execute(delete(UserModel).where(UserModel.id == dev.id))
        await session.commit()
        row = (
            await session.execute(
                text(
                    "SELECT assignee_user_id FROM vulnerability_findings WHERE id=:i"
                ),
                {"i": str(finding.id)},
            )
        ).scalar_one()
    assert row is None


# --- assignee_is_active has three values, not two ---------------------------


async def test_an_unassigned_finding_reports_null_not_false(client) -> None:  # noqa: ANN001
    """`false` would read as "assigned to somebody who cannot act", which is
    the confusion this field exists to remove."""
    from services.vulnerability_service import get_vulnerability_detail

    finding, _project, team, dev = await _seed_finding(client)
    factory = await _factory(client)
    async with factory() as session:
        detail = await get_vulnerability_detail(
            session, finding_id=finding.id, actor=await _principal(dev, team.id)
        )
    assert detail["assignee_user_id"] is None
    assert detail["assignee_is_active"] is None


async def test_an_active_assignee_reports_true(client) -> None:  # noqa: ANN001
    from services.vulnerability_service import get_vulnerability_detail

    finding, _project, team, dev = await _seed_finding(client)
    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": str(dev.id)}
    )
    factory = await _factory(client)
    async with factory() as session:
        detail = await get_vulnerability_detail(
            session, finding_id=finding.id, actor=await _principal(dev, team.id)
        )
    assert detail["assignee_is_active"] is True


async def test_a_deactivated_assignee_reports_false(client) -> None:  # noqa: ANN001
    from sqlalchemy import update

    from models import User as UserModel
    from services.vulnerability_service import get_vulnerability_detail

    finding, _project, team, dev = await _seed_finding(client)
    await client.patch(
        _url(finding.id), headers=_bearer(dev), json={"assignee_user_id": str(dev.id)}
    )
    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            update(UserModel).where(UserModel.id == dev.id).values(is_active=False)
        )
        await session.commit()
    async with factory() as session:
        detail = await get_vulnerability_detail(
            session, finding_id=finding.id, actor=await _principal(dev, team.id)
        )
    assert detail["assignee_is_active"] is False
