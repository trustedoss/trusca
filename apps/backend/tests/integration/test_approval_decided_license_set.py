"""
A decided approval is asked again only when the component's licenses changed (#566).

An approval covers a component in a project, not one version of it, so the
scan pipeline cannot simply skip every component that was already decided: a
later version under a different license would then never reach a reviewer.
Instead a decision records the license set it was made against, and a re-scan
skips only when the current set is the same.

The sequences here run the real decision route (``PATCH /v1/approvals/{id}/transition``)
and the real scan stage (``tasks.scan_source._auto_create_conditional_approvals``)
against Postgres, so a decision that forgot to record its set, or a stage that
forgot to compare it, fails here rather than in somebody's queue.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token
from models import (
    Component,
    ComponentApproval,
    ComponentVersion,
    License,
    LicenseFinding,
    Project,
    Scan,
    User,
)
from models.component_approval import ApprovalStatus
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    unique_suffix,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _bearer_for(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def _seed(client: AsyncClient) -> tuple[User, Project]:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="group_admin")
        project = await make_project(session, team=team)
        return admin, project


async def _license(client: AsyncClient, *, spdx_id: str | None, name: str) -> License:
    factory = await _factory(client)
    async with factory() as session:
        lic = License(spdx_id=spdx_id, name=name, category="conditional")
        session.add(lic)
        await session.commit()
        await session.refresh(lic)
        return lic


def _conditional_id() -> str:
    # Namespaced so the shared ``licenses`` table never collides on spdx_id.
    return f"LicenseRef-566-{unique_suffix()}"


async def _component(client: AsyncClient) -> Component:
    factory = await _factory(client)
    async with factory() as session:
        purl = f"pkg:npm/approval-566-{unique_suffix()}"
        comp = Component(purl=purl, package_type="npm", name=purl.rsplit("/", 1)[-1])
        session.add(comp)
        await session.commit()
        await session.refresh(comp)
        return comp


async def _scan(
    client: AsyncClient,
    *,
    project: Project,
    component: Component,
    findings: list[tuple[str, License]],
) -> uuid.UUID:
    """One succeeded scan in which *component* carries the given findings.

    ``findings`` is ``(version, license)`` pairs, so a scan can carry the
    component at several versions at once the way a real dependency tree can.
    """
    factory = await _factory(client)
    async with factory() as session:
        scan = Scan(
            project_id=project.id,
            kind="source",
            status="succeeded",
            progress_percent=100,
            scan_metadata={},
            completed_at=datetime.now(UTC),
        )
        session.add(scan)
        await session.flush()
        for version, lic in findings:
            purl_with_version = f"{component.purl}@{version}"
            cv = (
                await session.execute(
                    select(ComponentVersion).where(
                        ComponentVersion.purl_with_version == purl_with_version
                    )
                )
            ).scalar_one_or_none()
            if cv is None:
                cv = ComponentVersion(
                    component_id=component.id,
                    version=version,
                    purl_with_version=purl_with_version,
                )
                session.add(cv)
                await session.flush()
            session.add(
                LicenseFinding(
                    scan_id=scan.id,
                    component_version_id=cv.id,
                    license_id=lic.id,
                    kind="declared",
                    raw_data={"source": "test"},
                )
            )
        await session.commit()
        return scan.id


def _run_stage(*, scan_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """The scan pipeline's own approval stage, on its own sync session.

    The stage swallows its own errors so a scan never fails on approvals. That
    would make every "nothing new was opened" assertion here pass when the
    stage crashed, so the swallowed error is caught and turned into a failure.
    """
    import tasks.scan_source as scan_source

    real_log = scan_source.log
    swallowed: list[dict[str, object]] = []

    class _Spy:
        def __getattr__(self, name: str) -> object:
            return getattr(real_log, name)

        def warning(self, event: str, **kw: object) -> object:
            if event == "approval.auto_create_stage_error":
                swallowed.append(kw)
            return real_log.warning(event, **kw)

    with patch.object(scan_source, "log", _Spy()):
        scan_source._auto_create_conditional_approvals(
            scan_uuid=scan_id, project_id=project_id
        )
    assert swallowed == [], swallowed


async def _approvals(
    client: AsyncClient, *, project_id: uuid.UUID, component_id: uuid.UUID
) -> list[ComponentApproval]:
    factory = await _factory(client)
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(ComponentApproval)
                    .where(
                        ComponentApproval.project_id == project_id,
                        ComponentApproval.component_id == component_id,
                    )
                    .order_by(ComponentApproval.created_at, ComponentApproval.id)
                )
            )
            .scalars()
            .all()
        )


def _open(rows: list[ComponentApproval]) -> list[ComponentApproval]:
    return [
        r
        for r in rows
        if r.status in (ApprovalStatus.pending.value, ApprovalStatus.under_review.value)
    ]


async def _decide(
    client: AsyncClient, *, admin: User, approval: ComponentApproval, verdict: str
) -> dict:
    """Drive the decision through the HTTP route a reviewer uses."""
    headers = _bearer_for(admin)
    version = approval.version
    if verdict == "approved":
        reviewing = await client.patch(
            f"/v1/approvals/{approval.id}/transition",
            headers={**headers, "If-Match": f'"{version}"'},
            json={"action": "under_review"},
        )
        assert reviewing.status_code == 200, reviewing.text
        version = reviewing.json()["version"]
    decided = await client.patch(
        f"/v1/approvals/{approval.id}/transition",
        headers={**headers, "If-Match": f'"{version}"'},
        json={"action": verdict, "decision_note": "reviewed"},
    )
    assert decided.status_code == 200, decided.text
    return dict(decided.json())


# ---------------------------------------------------------------------------
# The lifecycle the issue is about
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["approved", "rejected"])
async def test_decided_component_is_asked_again_only_when_its_licenses_change(
    client, verdict: str
) -> None:
    """decide -> re-scan with the same licenses -> nothing new -> re-scan with
    a changed license -> a new Pending approval.

    Both verdicts are decisions: a rejection re-raised by every scan would put
    the same refused package back in the queue each time it is scanned.
    """
    admin, project = await _seed(client)
    comp = await _component(client)
    first = await _license(client, spdx_id=_conditional_id(), name="first")
    second = await _license(client, spdx_id=_conditional_id(), name="second")

    scan1 = await _scan(client, project=project, component=comp, findings=[("1.0.0", first)])
    _run_stage(scan_id=scan1, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert [r.status for r in rows] == ["pending"]

    await _decide(client, admin=admin, approval=rows[0], verdict=verdict)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert rows[0].status == verdict
    assert rows[0].decided_license_ids == [first.spdx_id]

    # A newer version under the same license: the decision still covers it.
    scan2 = await _scan(client, project=project, component=comp, findings=[("1.1.0", first)])
    _run_stage(scan_id=scan2, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert len(rows) == 1, [r.status for r in rows]

    # A later version under another license: somebody has to look again.
    scan3 = await _scan(client, project=project, component=comp, findings=[("2.0.0", second)])
    _run_stage(scan_id=scan3, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert [r.status for r in rows] == [verdict, "pending"]


async def test_an_added_license_counts_as_a_change(client) -> None:
    """The set is compared as a whole. A version that keeps the reviewed
    license and adds another is not the thing that was reviewed."""
    admin, project = await _seed(client)
    comp = await _component(client)
    first = await _license(client, spdx_id=_conditional_id(), name="first")
    extra = await _license(client, spdx_id=_conditional_id(), name="extra")

    scan1 = await _scan(client, project=project, component=comp, findings=[("1.0.0", first)])
    _run_stage(scan_id=scan1, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    await _decide(client, admin=admin, approval=rows[0], verdict="approved")

    scan2 = await _scan(
        client,
        project=project,
        component=comp,
        findings=[("1.1.0", first), ("1.1.0", extra)],
    )
    _run_stage(scan_id=scan2, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert [r.status for r in rows] == ["approved", "pending"]


async def test_a_decision_without_a_recorded_set_re_opens(client) -> None:
    """Rows decided before the set was recorded carry NULL, and the licenses
    they were decided against cannot be recovered, so they re-open once."""
    _admin, project = await _seed(client)
    comp = await _component(client)
    first = await _license(client, spdx_id=_conditional_id(), name="first")
    factory = await _factory(client)
    async with factory() as session:
        session.add(
            ComponentApproval(
                component_id=comp.id,
                project_id=project.id,
                team_id=project.team_id,
                status=ApprovalStatus.approved.value,
                decided_at=datetime.now(UTC),
                decided_license_ids=None,
                version=3,
            )
        )
        await session.commit()

    scan1 = await _scan(client, project=project, component=comp, findings=[("1.0.0", first)])
    _run_stage(scan_id=scan1, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert [r.status for r in rows] == ["approved", "pending"]


async def test_only_the_most_recent_decision_is_compared(client) -> None:
    """Approved under one license, later rejected under another, then back to
    the first. The project's answer is the rejection, so the queue must ask again
    rather than lean on the older approval whose set happens to match."""
    _admin, project = await _seed(client)
    comp = await _component(client)
    first = await _license(client, spdx_id=_conditional_id(), name="first")
    second = await _license(client, spdx_id=_conditional_id(), name="second")
    now = datetime.now(UTC)
    factory = await _factory(client)
    async with factory() as session:
        session.add_all(
            [
                ComponentApproval(
                    component_id=comp.id,
                    project_id=project.id,
                    team_id=project.team_id,
                    status=ApprovalStatus.approved.value,
                    decided_at=now - timedelta(days=2),
                    decided_license_ids=[first.spdx_id],
                    version=3,
                ),
                ComponentApproval(
                    component_id=comp.id,
                    project_id=project.id,
                    team_id=project.team_id,
                    status=ApprovalStatus.rejected.value,
                    decided_at=now - timedelta(days=1),
                    decided_license_ids=[second.spdx_id],
                    version=3,
                ),
            ]
        )
        await session.commit()

    scan1 = await _scan(client, project=project, component=comp, findings=[("1.0.0", first)])
    _run_stage(scan_id=scan1, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert len(_open(rows)) == 1, [r.status for r in rows]


async def test_the_decision_records_the_current_succeeded_scan(client) -> None:
    """The set a decision records is the one the reviewer was shown: the
    project's current succeeded scan, not an older scan and not one still
    running."""
    admin, project = await _seed(client)
    comp = await _component(client)
    old = await _license(client, spdx_id=_conditional_id(), name="old")
    current = await _license(client, spdx_id=_conditional_id(), name="current")

    scan1 = await _scan(client, project=project, component=comp, findings=[("1.0.0", old)])
    _run_stage(scan_id=scan1, project_id=project.id)
    await _scan(client, project=project, component=comp, findings=[("1.1.0", current)])
    factory = await _factory(client)
    async with factory() as session:
        # A newer scan that has not finished must not be what gets recorded.
        running = Scan(
            project_id=project.id,
            kind="source",
            status="running",
            progress_percent=40,
            scan_metadata={},
        )
        session.add(running)
        await session.flush()
        cv = ComponentVersion(
            component_id=comp.id,
            version="9.9.9",
            purl_with_version=f"{comp.purl}@9.9.9",
        )
        session.add(cv)
        await session.flush()
        session.add(
            LicenseFinding(
                scan_id=running.id,
                component_version_id=cv.id,
                license_id=old.id,
                kind="declared",
                raw_data={"source": "test"},
            )
        )
        await session.commit()

    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    body = await _decide(client, admin=admin, approval=rows[0], verdict="rejected")
    assert body["status"] == "rejected"
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert rows[0].decided_license_ids == [current.spdx_id]


async def test_a_project_with_no_succeeded_scan_records_an_empty_set(client) -> None:
    """Empty is a recorded fact, distinct from NULL. A later scan that finds a
    conditional license carries a different set and re-opens the approval."""
    admin, project = await _seed(client)
    comp = await _component(client)
    factory = await _factory(client)
    async with factory() as session:
        approval = ComponentApproval(
            component_id=comp.id,
            project_id=project.id,
            team_id=project.team_id,
            status=ApprovalStatus.pending.value,
            version=1,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)

    await _decide(client, admin=admin, approval=approval, verdict="rejected")
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert rows[0].decided_license_ids == []


# ---------------------------------------------------------------------------
# The other decision path: an answer carried from an intake request
# ---------------------------------------------------------------------------


async def test_a_carried_intake_answer_records_the_set_and_spares_older_rows(
    client,
) -> None:
    """The scan stage carries an intake answer onto the approval it opens.
    That is a decision, so it records the scan's set and the next scan with
    the same set opens nothing. It also must not rewrite the component's
    older decided row, which a re-opened approval leaves behind."""
    from models.component_intake import ComponentIntakeRequest

    _admin, project = await _seed(client)
    comp = await _component(client)
    first = await _license(client, spdx_id=_conditional_id(), name="first")
    factory = await _factory(client)
    old_decided_at = datetime.now(UTC) - timedelta(days=30)
    async with factory() as session:
        session.add(
            ComponentApproval(
                component_id=comp.id,
                project_id=project.id,
                team_id=project.team_id,
                status=ApprovalStatus.rejected.value,
                decided_at=old_decided_at,
                decision_note="the original decision",
                decided_license_ids=None,
                version=2,
            )
        )
        session.add(
            ComponentIntakeRequest(
                project_id=project.id,
                team_id=project.team_id,
                purl=comp.purl,
                justification="needed for the date picker",
                status="approved",
                decided_at=datetime.now(UTC),
            )
        )
        await session.commit()

    scan1 = await _scan(client, project=project, component=comp, findings=[("1.0.0", first)])
    _run_stage(scan_id=scan1, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert [r.status for r in rows] == ["rejected", "approved"]
    assert rows[0].decision_note == "the original decision"
    assert rows[0].decided_at == old_decided_at
    assert rows[0].decided_license_ids is None
    assert rows[1].decided_license_ids == [first.spdx_id]

    scan2 = await _scan(client, project=project, component=comp, findings=[("1.1.0", first)])
    _run_stage(scan_id=scan2, project_id=project.id)
    rows = await _approvals(client, project_id=project.id, component_id=comp.id)
    assert len(rows) == 2, [r.status for r in rows]


# ---------------------------------------------------------------------------
# What counts as the same set
# ---------------------------------------------------------------------------


async def test_license_set_is_sorted_deduplicated_and_trimmed(client) -> None:
    """Several versions carrying the same license count once, a custom license
    with no SPDX id is named by its name, and whitespace is not a change."""
    from core.db import sync_session_scope
    from services.component_approval_service import license_sets_for_scan

    _admin, project = await _seed(client)
    comp = await _component(client)
    other = await _component(client)
    suffix = unique_suffix()
    zed = await _license(client, spdx_id=f"Zed-{suffix}", name="zed")
    alpha = await _license(client, spdx_id=f"  Alpha-{suffix}  ", name="alpha")
    custom = await _license(client, spdx_id=None, name=f" Custom {suffix} ")

    scan_id = await _scan(
        client,
        project=project,
        component=comp,
        findings=[("1.0.0", zed), ("2.0.0", zed), ("2.0.0", alpha), ("2.0.0", custom)],
    )

    with sync_session_scope() as session:
        sets = license_sets_for_scan(
            session, scan_id=scan_id, component_ids=[comp.id, other.id, comp.id]
        )

    assert sets == {
        comp.id: [f"Alpha-{suffix}", f"Custom {suffix}", f"Zed-{suffix}"],
        other.id: [],
    }
