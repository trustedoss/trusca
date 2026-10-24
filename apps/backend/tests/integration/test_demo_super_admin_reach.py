"""
A super admin's reach on the public read-only demo, surface by surface.

``core.authz.can_access_group`` and ``team_scope_filter`` stop a super admin at
the demo organisation while ``DEMO_READ_ONLY`` is on, because the demo
publishes super-admin accounts. Services that kept their own super-admin
shortcut answered before that boundary was consulted, so a published account
could still list another organisation's API keys (with issuer emails),
service accounts, policies, rulings, routing rules and queues. Every such
shortcut now goes through ``super_admin_reaches`` / ``super_admin_group_filter``
/ ``group_visibility_filter``.

Each case runs with the flag off and on. Off, the super admin sees the rows
outside the demo exactly as before (the non-demo behaviour is unchanged);
on, it does not, while rows in a demo group stay visible. Running both shows a
passing "hidden" assertion comes from the demo boundary and not from the
fixture (hardening rules 7 and 8).

Target-less rows (an org-scoped key, an organisation's default policy,
organisation-wide routing rules and rulings) are denied on the demo, the
documented default of ``super_admin_reaches(..., None)``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.security import CurrentUser, create_access_token
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

pytestmark = pytest.mark.integration

DEMO_STATES = [pytest.param(False, id="normal"), pytest.param(True, id="demo")]


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@dataclass
class _World:
    admin: CurrentUser
    admin_id: uuid.UUID
    demo_team_id: uuid.UUID
    demo_project_id: uuid.UUID
    other_org_id: uuid.UUID
    other_team_id: uuid.UUID
    other_project_id: uuid.UUID
    org_key_id: uuid.UUID
    other_team_key_id: uuid.UUID
    demo_team_key_id: uuid.UUID
    other_policy_id: uuid.UUID
    other_org_policy_id: uuid.UUID
    demo_policy_id: uuid.UUID
    other_approval_id: uuid.UUID
    demo_approval_id: uuid.UUID
    other_intake_id: uuid.UUID
    demo_intake_id: uuid.UUID
    other_credential_id: uuid.UUID
    demo_credential_id: uuid.UUID
    other_transition_id: uuid.UUID
    demo_transition_id: uuid.UUID


async def _component(session: AsyncSession) -> uuid.UUID:
    from models import Component

    suffix = unique_suffix()
    component = Component(
        purl=f"pkg:npm/reach-{suffix}", package_type="npm", name=f"reach-{suffix}"
    )
    session.add(component)
    await session.commit()
    return component.id


async def _finding(session: AsyncSession, project: Any) -> uuid.UUID:
    from models import ComponentVersion, Vulnerability, VulnerabilityFinding
    from tests._helpers import make_scan

    scan = await make_scan(session, project=project, status="succeeded")
    component_id = await _component(session)
    suffix = unique_suffix()
    version = ComponentVersion(
        component_id=component_id, version="1.0.0", purl_with_version=f"pkg:npm/r-{suffix}@1"
    )
    session.add(version)
    vulnerability = Vulnerability(
        external_id=f"CVE-2099-{suffix}", source="NVD", severity="high", summary="fixture"
    )
    session.add(vulnerability)
    await session.commit()
    finding = VulnerabilityFinding(
        scan_id=scan.id,
        component_version_id=version.id,
        vulnerability_id=vulnerability.id,
        status="new",
    )
    session.add(finding)
    await session.commit()
    return finding.id


@pytest.fixture
async def world(
    db_factory: async_sessionmaker[Any], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_World]:
    """A demo organisation and one outside it, with rows of every kind in each.

    The rows are written with the flag off, as a normal deployment's super
    admin would have written them, so the demo cases read pre-existing data.
    """
    from models import (
        ComponentApproval,
        ComponentIntakeRequest,
        GitHubAppCredential,
        Organization,
        TransitionApproval,
    )
    from schemas.license_policy import LicensePolicyUpsertIn
    from schemas.notification_routing import NotificationRoutingRuleIn
    from services import (
        api_key_service,
        license_policy_service,
        notification_routing_service,
        organization_verdict_service,
        service_account_service,
    )
    from services.demo_scope import DEMO_ORG_SLUG

    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")

    async with db_factory() as session:
        demo_org = (
            await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        if demo_org is None:
            demo_org = await make_organization(session, slug=DEMO_ORG_SLUG, name="Demo")
        demo_team = await make_team(session, organization=demo_org)
        demo_project = await make_project(session, team=demo_team)
        other_org = await make_organization(session)
        other_team = await make_team(session, organization=other_org)
        other_project = await make_project(session, team=other_team)

        # The rows are written by the operator's own administrator, and read
        # by a second super admin: an issuer always sees its own keys, which
        # would otherwise hide what the reach decides.
        operator = principal_for(await make_user(session, is_superuser=True), role="super_admin")
        user = await make_user(session, is_superuser=True)
        admin = principal_for(user, role="super_admin")

        keys = {}
        for label, scope, team_id in (
            ("org", "org", None),
            ("other", "team", other_team.id),
            ("demo", "team", demo_team.id),
        ):
            row, _plain = await api_key_service.issue_api_key(
                session,
                operator,
                name=f"reach-{label}",
                scope=scope,
                team_id=team_id,
                project_id=None,
            )
            keys[label] = row.id

        await service_account_service.create_service_account(
            session,
            operator,
            team_id=other_team.id,
            slug=f"bot-{unique_suffix()}",
            display_name="Bot",
        )

        other_policy = await license_policy_service.upsert_team_policy(
            session, operator, team_id=other_team.id, payload=LicensePolicyUpsertIn(name="other")
        )
        other_org_policy = await license_policy_service.upsert_org_policy(
            session,
            operator,
            organization_id=other_org.id,
            payload=LicensePolicyUpsertIn(name="org"),
        )
        demo_policy = await license_policy_service.upsert_team_policy(
            session, operator, team_id=demo_team.id, payload=LicensePolicyUpsertIn(name="demo")
        )

        await notification_routing_service.create_rule(
            session,
            operator,
            organization_id=other_org.id,
            team_id=None,
            payload=NotificationRoutingRuleIn(
                name="org-wide", email_recipients=["secops@example.com"]
            ),
        )
        await organization_verdict_service.open_verdict(
            session,
            operator,
            organization_id=other_org.id,
            component_id=await _component(session),
            justification="reviewed centrally for the whole organization",
        )

        rows: dict[str, Any] = {}
        for label, team, project in (
            ("other", other_team, other_project),
            ("demo", demo_team, demo_project),
        ):
            approval = ComponentApproval(
                component_id=await _component(session),
                project_id=project.id,
                team_id=team.id,
                status="pending",
            )
            intake = ComponentIntakeRequest(
                project_id=project.id,
                team_id=team.id,
                purl=f"pkg:npm/intake-{unique_suffix()}",
                justification="needed for the build",
            )
            credential = GitHubAppCredential(
                team_id=team.id, app_id=str(100000 + len(rows)), private_key_encrypted="x"
            )
            transition = TransitionApproval(
                finding_id=await _finding(session, project),
                team_id=team.id,
                target_status="suppressed",
                justification="fixture justification text",
            )
            session.add_all([approval, intake, credential, transition])
            await session.commit()
            rows[f"{label}_approval"] = approval.id
            rows[f"{label}_intake"] = intake.id
            rows[f"{label}_credential"] = credential.id
            rows[f"{label}_transition"] = transition.id

        built = _World(
            admin=admin,
            admin_id=user.id,
            demo_team_id=demo_team.id,
            demo_project_id=demo_project.id,
            other_org_id=other_org.id,
            other_team_id=other_team.id,
            other_project_id=other_project.id,
            org_key_id=keys["org"],
            other_team_key_id=keys["other"],
            demo_team_key_id=keys["demo"],
            other_policy_id=other_policy.id,
            other_org_policy_id=other_org_policy.id,
            demo_policy_id=demo_policy.id,
            other_approval_id=rows["other_approval"],
            demo_approval_id=rows["demo_approval"],
            other_intake_id=rows["other_intake"],
            demo_intake_id=rows["demo_intake"],
            other_credential_id=rows["other_credential"],
            demo_credential_id=rows["demo_credential"],
            other_transition_id=rows["other_transition"],
            demo_transition_id=rows["demo_transition"],
        )

    yield built

    # Leave no demo organisation behind: later tests seed the demo and only
    # top up an existing one. The drop needs one super admin outside the demo.
    from scripts import reset_demo

    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    async with db_factory() as session:
        await make_user(session, is_superuser=True)
    await reset_demo._drop_demo()


@pytest.fixture
async def session(db_factory: async_sessionmaker[Any]) -> AsyncIterator[AsyncSession]:
    async with db_factory() as s:
        yield s


def _set_demo(monkeypatch: pytest.MonkeyPatch, demo: bool) -> None:
    if demo:
        monkeypatch.setenv("DEMO_READ_ONLY", "true")
    else:
        monkeypatch.delenv("DEMO_READ_ONLY", raising=False)


# ---------------------------------------------------------------------------
# Reads that used to list every organisation's rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_api_key_list(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.api_key_service import list_api_keys

    _set_demo(monkeypatch, demo)
    rows, _total = await list_api_keys(session, world.admin, page_size=200)
    ids = {row.id for row in rows}
    assert world.demo_team_key_id in ids
    assert (world.org_key_id in ids) is not demo
    assert (world.other_team_key_id in ids) is not demo


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_api_key_revoke_outside_demo(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    """Visibility on the single-key path (revoke) follows the same reach."""
    from services.api_key_service import APIKeyNotFound, revoke_api_key

    _set_demo(monkeypatch, demo)
    if demo:
        for key_id in (world.org_key_id, world.other_team_key_id):
            with pytest.raises(APIKeyNotFound):
                await revoke_api_key(session, world.admin, key_id)
    else:
        row = await revoke_api_key(session, world.admin, world.other_team_key_id)
        assert row.revoked_at is not None


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_org_scoped_key_issuance(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.api_key_service import APIKeyForbidden, issue_api_key

    _set_demo(monkeypatch, demo)
    if demo:
        with pytest.raises(APIKeyForbidden):
            await issue_api_key(
                session, world.admin, name="x", scope="org", team_id=None, project_id=None
            )
    else:
        row, _ = await issue_api_key(
            session, world.admin, name="x", scope="org", team_id=None, project_id=None
        )
        assert row.scope == "org"


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_service_account_list(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.service_account_service import (
        ServiceAccountNotFound,
        list_service_accounts,
    )

    _set_demo(monkeypatch, demo)
    if demo:
        with pytest.raises(ServiceAccountNotFound):
            await list_service_accounts(session, world.admin, team_id=world.other_team_id)
    else:
        assert await list_service_accounts(session, world.admin, team_id=world.other_team_id)


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_license_policy_list_and_org_default(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.license_policy_service import (
        LicensePolicyNotFound,
        get_org_policy,
        list_policies,
    )

    _set_demo(monkeypatch, demo)
    rows, _ = await list_policies(session, world.admin, page_size=200)
    ids = {row.id for row in rows}
    assert world.demo_policy_id in ids
    assert (world.other_policy_id in ids) is not demo
    assert (world.other_org_policy_id in ids) is not demo

    if demo:
        with pytest.raises(LicensePolicyNotFound):
            await get_org_policy(session, world.admin, organization_id=world.other_org_id)
    else:
        row = await get_org_policy(session, world.admin, organization_id=world.other_org_id)
        assert row.id == world.other_org_policy_id


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_org_routing_rules(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.notification_routing_service import RoutingRuleScopeNotFound, list_rules

    _set_demo(monkeypatch, demo)
    if demo:
        with pytest.raises(RoutingRuleScopeNotFound):
            await list_rules(session, world.admin, organization_id=world.other_org_id, team_id=None)
    else:
        rules = await list_rules(
            session, world.admin, organization_id=world.other_org_id, team_id=None
        )
        assert [r.name for r in rules] == ["org-wide"]


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_organization_rulings(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.organization_verdict_service import VerdictNotFound, list_verdicts

    _set_demo(monkeypatch, demo)
    if demo:
        with pytest.raises(VerdictNotFound):
            await list_verdicts(session, world.admin, organization_id=world.other_org_id)
    else:
        rows, total = await list_verdicts(session, world.admin, organization_id=world.other_org_id)
        assert total == 1 and len(rows) == 1


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_component_approval_list(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.component_approval_service import list_approvals

    _set_demo(monkeypatch, demo)
    rows, _ = await list_approvals(session, world.admin, page_size=200)
    ids = {row.approval.id for row in rows}
    assert world.demo_approval_id in ids
    assert (world.other_approval_id in ids) is not demo


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_intake_request_list(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.component_intake_service import list_requests

    _set_demo(monkeypatch, demo)
    monkeypatch.setenv("INTAKE_REQUESTS_ENABLED", "true")
    ids = {row.id for row in await list_requests(session, world.admin)}
    assert world.demo_intake_id in ids
    assert (world.other_intake_id in ids) is not demo


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_github_app_credential_list(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from services.github_app_service import list_credentials

    _set_demo(monkeypatch, demo)
    rows, _ = await list_credentials(session, world.admin, page_size=200)
    ids = {row.id for row in rows}
    assert world.demo_credential_id in ids
    assert (world.other_credential_id in ids) is not demo


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_transition_approval_queue_over_http(
    world: _World, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    """The queue's administrator view is decided in the router, so read it there."""
    from main import app

    _set_demo(monkeypatch, demo)
    token = create_access_token(subject=str(world.admin_id), role=None)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(
            "/v1/transition-approvals", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200, resp.text
    ids = {uuid.UUID(item["id"]) for item in resp.json()["items"]}
    assert world.demo_transition_id in ids
    assert (world.other_transition_id in ids) is not demo


# ---------------------------------------------------------------------------
# Writes judged by a local role helper with no access check in front of it.
# The demo middleware refuses these over HTTP already; the service layer must
# not depend on that.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_project_update_outside_demo(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from schemas.scan import ProjectUpdate
    from services.project_service import ProjectForbidden, update_project

    _set_demo(monkeypatch, demo)
    payload = ProjectUpdate(description="changed by an administrator")
    if demo:
        with pytest.raises(ProjectForbidden):
            await update_project(
                session, project_id=world.other_project_id, payload=payload, actor=world.admin
            )
    else:
        project = await update_project(
            session, project_id=world.other_project_id, payload=payload, actor=world.admin
        )
        assert project.description == "changed by an administrator"


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_gate_policy_write_outside_demo(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from schemas.gate_policy import GatePolicyUpsertIn
    from services.gate_policy_service import GatePolicyForbidden, upsert_team_policy

    _set_demo(monkeypatch, demo)
    payload = GatePolicyUpsertIn()
    if demo:
        with pytest.raises(GatePolicyForbidden):
            await upsert_team_policy(
                session, world.admin, team_id=world.other_team_id, payload=payload
            )
    else:
        row = await upsert_team_policy(
            session, world.admin, team_id=world.other_team_id, payload=payload
        )
        assert row.team_id == world.other_team_id


@pytest.mark.parametrize("demo", DEMO_STATES)
async def test_scan_schedule_write_outside_demo(
    world: _World, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, demo: bool
) -> None:
    from schemas.scan_schedule import ScanScheduleUpsertIn
    from services.scan_schedule_service import ScanScheduleForbidden, upsert_project_schedule

    _set_demo(monkeypatch, demo)
    payload = ScanScheduleUpsertIn(is_active=False)
    if demo:
        with pytest.raises(ScanScheduleForbidden):
            await upsert_project_schedule(
                session, world.admin, project_id=world.other_project_id, payload=payload
            )
    else:
        row = await upsert_project_schedule(
            session, world.admin, project_id=world.other_project_id, payload=payload
        )
        assert row.project_id == world.other_project_id
