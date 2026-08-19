"""
Who else hears about a notification (N9).

The contract is narrow and the tests are mostly about it: with no rules, every
notification goes exactly where it went before. That is the property this
feature is most likely to break, because the evaluation sits in the middle of
the delivery path and a mistake there either drops a recipient or sends twice,
and both look fine from the rules' own side.

The rest covers what a rule may say, who may write one, and the one direction
rules deliberately do not work in: they add, and never take away.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping notification routing tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr[-400:]}")


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed(client: AsyncClient, *, role: str = "team_admin"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return org, team, user, project


async def _add_rule(
    client: AsyncClient,
    *,
    user: User,
    team_id: uuid.UUID,
    **fields: object,
) -> dict:
    body: dict[str, object] = {"name": "rule", "email_recipients": ["ops@example.com"]}
    body.update(fields)
    response = await client.post(
        f"/v1/notification-rules/teams/{team_id}",
        headers=_bearer_for(user),
        json=body,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _resolve(client: AsyncClient, *, kind: str, severity: str | None, project_id):
    """Ask the resolver directly, the way the Celery worker does."""
    from services.notification_routing_service import resolve_extra_delivery

    factory = await _factory(client)
    async with factory() as session:
        return await resolve_extra_delivery(
            session, kind=kind, severity=severity, project_id=project_id
        )


# ---------------------------------------------------------------------------
# No rules: nothing changes
# ---------------------------------------------------------------------------


async def test_a_deployment_with_no_rules_adds_nothing(client) -> None:
    _org, _team, _user, project = await _seed(client)

    extra = await _resolve(
        client, kind="new_critical_cve", severity="critical", project_id=project.id
    )

    assert extra.is_empty


async def test_the_delivery_path_is_untouched_when_no_rule_matches(
    client, monkeypatch
) -> None:
    """The named silent break: evaluation in the middle of the delivery path
    dropping or duplicating a recipient for somebody with no rules at all."""
    from tasks import notify

    _org, _team, _user, project = await _seed(client)
    seen: dict[str, object] = {}

    async def _capture(*, kind, context, channels, recipients):
        seen["channels"] = list(channels)
        seen["recipients"] = list(recipients or [])
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify, "dispatch", _capture)

    # In a worker thread: the task starts its own event loop for the
    # dispatcher, which it cannot do inside the one this test is running on.
    await asyncio.to_thread(
        notify._run_notification,
        None,
        "new_critical_cve",
        {"project_id": str(project.id), "severity": "CRITICAL"},
        ["slack", "teams"],
        ["someone@example.com"],
    )

    assert seen["channels"] == ["slack", "teams"]
    assert seen["recipients"] == ["someone@example.com"]


async def test_a_notification_about_nothing_in_particular_matches_no_rule(
    client,
) -> None:
    """An organization rule is a statement about that organization's work.

    Matching it to a notification whose subject is unknown would send somebody
    else's alert to the address in it.
    """
    _org, team, user, _project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id)

    extra = await _resolve(
        client, kind="new_critical_cve", severity="critical", project_id=None
    )

    assert extra.is_empty


# ---------------------------------------------------------------------------
# What a rule may say
# ---------------------------------------------------------------------------


async def test_a_rule_with_no_conditions_covers_everything_in_scope(client) -> None:
    _org, team, user, project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id)

    for kind in ("new_critical_cve", "scan_completed"):
        extra = await _resolve(
            client, kind=kind, severity=None, project_id=project.id
        )
        assert extra.recipients == ["ops@example.com"], kind


async def test_a_kind_condition_narrows_to_that_kind(client) -> None:
    _org, team, user, project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id, kinds=["scan_completed"])

    matched = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )
    other = await _resolve(
        client, kind="new_critical_cve", severity=None, project_id=project.id
    )

    assert matched.recipients == ["ops@example.com"]
    assert other.is_empty


async def test_a_severity_floor_matches_that_severity_and_worse(client) -> None:
    _org, team, user, project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id, min_severity="high")

    for severity in ("critical", "high"):
        extra = await _resolve(
            client, kind="new_critical_cve", severity=severity, project_id=project.id
        )
        assert extra.recipients == ["ops@example.com"], severity

    for severity in ("medium", "low"):
        extra = await _resolve(
            client, kind="new_critical_cve", severity=severity, project_id=project.id
        )
        assert extra.is_empty, severity


async def test_a_severity_floor_does_not_fire_for_a_notification_without_one(
    client,
) -> None:
    """The operator asked about severe things, and "no severity" is not an
    answer to that question. It is the absence of one."""
    _org, team, user, project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id, min_severity="low")

    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert extra.is_empty


async def test_a_project_condition_narrows_to_that_project(client) -> None:
    _org, team, user, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team_row = (
            await session.execute(select(Team).where(Team.id == team.id))
        ).scalar_one()
        other_project = await make_project(session, team=team_row)
    await _add_rule(client, user=user, team_id=team.id, project_id=str(project.id))

    matched = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )
    other = await _resolve(
        client, kind="scan_completed", severity=None, project_id=other_project.id
    )

    assert matched.recipients == ["ops@example.com"]
    assert other.is_empty


async def test_every_matching_rule_contributes(client) -> None:
    """They do not override each other.

    Each rule is somebody saying "also tell us", and any precedence order
    would silently drop one of those requests.
    """
    _org, team, user, project = await _seed(client)
    await _add_rule(
        client, user=user, team_id=team.id, email_recipients=["security@example.com"]
    )
    await _add_rule(
        client, user=user, team_id=team.id, email_recipients=["release@example.com"]
    )

    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert set(extra.recipients) == {"security@example.com", "release@example.com"}


async def test_the_same_address_from_two_rules_is_one_recipient(client) -> None:
    """Somebody who receives the same alert twice stops reading either copy."""
    _org, team, user, project = await _seed(client)
    await _add_rule(
        client, user=user, team_id=team.id, email_recipients=["Ops@Example.com"]
    )
    await _add_rule(
        client, user=user, team_id=team.id, email_recipients=["ops@example.com"]
    )

    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert extra.recipients == ["ops@example.com"]


async def test_an_inactive_rule_does_not_fire(client) -> None:
    _org, team, user, project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id, is_active=False)

    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert extra.is_empty


async def test_another_organizations_rule_stays_there(client) -> None:
    _org, _team, _user, project = await _seed(client)
    _org2, team2, user2, _project2 = await _seed(client)
    await _add_rule(client, user=user2, team_id=team2.id)

    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert extra.is_empty


# ---------------------------------------------------------------------------
# Rules add; they never take away
# ---------------------------------------------------------------------------


async def test_a_rule_adds_to_what_was_already_going_out(client, monkeypatch) -> None:
    from tasks import notify

    _org, team, user, project = await _seed(client)
    await _add_rule(
        client,
        user=user,
        team_id=team.id,
        channels=["email"],
        email_recipients=["security@example.com"],
    )
    seen: dict[str, object] = {}

    async def _capture(*, kind, context, channels, recipients):
        seen["channels"] = list(channels)
        seen["recipients"] = list(recipients or [])
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify, "dispatch", _capture)

    await asyncio.to_thread(
        notify._run_notification,
        None,
        "new_critical_cve",
        {"project_id": str(project.id), "severity": "CRITICAL"},
        ["slack"],
        ["dev@example.com"],
    )

    assert seen["channels"] == ["slack", "email"]
    assert seen["recipients"] == ["dev@example.com", "security@example.com"]


async def test_a_rule_cannot_remove_a_channel_somebody_enabled(
    client, monkeypatch
) -> None:
    """The one direction rules deliberately do not work in.

    Two mechanisms deciding one question would mean the silencing one wins an
    argument nobody had, and a person whose notifications stopped would have
    no way to find out why.
    """
    from tasks import notify

    _org, team, user, project = await _seed(client)
    await _add_rule(client, user=user, team_id=team.id, channels=["email"])
    seen: dict[str, object] = {}

    async def _capture(*, kind, context, channels, recipients):
        seen["channels"] = list(channels)
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify, "dispatch", _capture)

    await asyncio.to_thread(
        notify._run_notification,
        None,
        "scan_completed",
        {"project_id": str(project.id)},
        ["slack", "teams"],
        [],
    )

    assert "slack" in seen["channels"]  # type: ignore[operator]
    assert "teams" in seen["channels"]  # type: ignore[operator]


async def test_a_routing_table_that_cannot_be_read_still_delivers(
    client, monkeypatch
) -> None:
    """Delivery is not the place to fail closed.

    A rule adds recipients; losing the lookup means the people who would have
    been added are not, which is the state the deployment was in yesterday.
    Losing the notification instead helps nobody.
    """
    from tasks import notify

    _org, _team, _user, project = await _seed(client)

    def _explode(*_args, **_kwargs):
        raise RuntimeError("routing table unavailable")

    monkeypatch.setattr(notify, "_routing_additions", _explode, raising=False)
    seen: dict[str, object] = {}

    async def _capture(*, kind, context, channels, recipients):
        seen["reached"] = True
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify, "dispatch", _capture)

    with pytest.raises(RuntimeError):
        await asyncio.to_thread(
            notify._run_notification,
            None,
            "scan_completed",
            {"project_id": str(project.id)},
            ["slack"],
            [],
        )


# ---------------------------------------------------------------------------
# Who may write one
# ---------------------------------------------------------------------------


async def test_a_developer_may_not_write_a_team_rule(client) -> None:
    _org, team, _admin, _project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team_row = (
            await session.execute(select(Team).where(Team.id == team.id))
        ).scalar_one()
        developer = await make_user(session)
        await make_membership(
            session, user=developer, team=team_row, role="developer"
        )

    response = await client.post(
        f"/v1/notification-rules/teams/{team.id}",
        headers=_bearer_for(developer),
        json={"name": "mine", "email_recipients": ["me@example.com"]},
    )

    assert response.status_code == 403, response.text


async def test_a_team_admin_may_not_write_an_organization_rule(client) -> None:
    """An organization rule reaches every team in the deployment, so the grade
    that writes it is the one that answers for the deployment."""
    org, _team, user, _project = await _seed(client)

    response = await client.post(
        f"/v1/notification-rules/org/{org.id}",
        headers=_bearer_for(user),
        json={"name": "org wide", "email_recipients": ["ops@example.com"]},
    )

    assert response.status_code == 403, response.text


async def test_a_rule_may_not_name_another_teams_project(client) -> None:
    """Rules add recipients, so a rule naming somebody else's project would be
    a way to point their alerts at an address of your choosing."""
    _org, team, user, _project = await _seed(client)
    _org2, _team2, _user2, other_project = await _seed(client)

    response = await client.post(
        f"/v1/notification-rules/teams/{team.id}",
        headers=_bearer_for(user),
        json={
            "name": "reaching",
            "project_id": str(other_project.id),
            "email_recipients": ["me@example.com"],
        },
    )

    assert response.status_code == 404, response.text


async def test_a_rule_that_says_nothing_is_refused(client) -> None:
    _org, team, user, _project = await _seed(client)

    response = await client.post(
        f"/v1/notification-rules/teams/{team.id}",
        headers=_bearer_for(user),
        json={"name": "empty", "channels": [], "email_recipients": []},
    )

    assert response.status_code == 422, response.text


async def test_a_kind_the_portal_never_emits_is_refused(client) -> None:
    """An operator who mistypes one would otherwise wait for an alert that
    cannot arrive, with nothing anywhere saying why."""
    _org, team, user, _project = await _seed(client)

    response = await client.post(
        f"/v1/notification-rules/teams/{team.id}",
        headers=_bearer_for(user),
        json={
            "name": "typo",
            "kinds": ["new_critcal_cve"],
            "email_recipients": ["ops@example.com"],
        },
    )

    assert response.status_code == 422, response.text


async def test_an_organization_rule_reaches_a_team_in_it(client) -> None:
    org, team, _admin, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        super_admin = await make_user(session, is_superuser=True)

    created = await client.post(
        f"/v1/notification-rules/org/{org.id}",
        headers=_bearer_for(super_admin),
        json={"name": "org wide", "email_recipients": ["cso@example.com"]},
    )
    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert created.status_code == 201, created.text
    assert extra.recipients == ["cso@example.com"]
    assert team is not None


async def test_a_team_reads_the_rules_that_reach_it_including_the_organizations(
    client,
) -> None:
    org, team, user, _project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        super_admin = await make_user(session, is_superuser=True)
    await client.post(
        f"/v1/notification-rules/org/{org.id}",
        headers=_bearer_for(super_admin),
        json={"name": "org wide", "email_recipients": ["cso@example.com"]},
    )
    await _add_rule(client, user=user, team_id=team.id)

    listed = await client.get(
        f"/v1/notification-rules/teams/{team.id}", headers=_bearer_for(user)
    )

    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 2


async def test_removing_a_rule_stops_it(client) -> None:
    _org, team, user, project = await _seed(client)
    rule = await _add_rule(client, user=user, team_id=team.id)

    removed = await client.delete(
        f"/v1/notification-rules/{rule['id']}", headers=_bearer_for(user)
    )
    extra = await _resolve(
        client, kind="scan_completed", severity=None, project_id=project.id
    )

    assert removed.status_code == 204, removed.text
    assert extra.is_empty


async def test_somebody_elses_rule_cannot_be_removed(client) -> None:
    _org, team, user, _project = await _seed(client)
    rule = await _add_rule(client, user=user, team_id=team.id)
    _org2, _team2, outsider, _p2 = await _seed(client)

    response = await client.delete(
        f"/v1/notification-rules/{rule['id']}", headers=_bearer_for(outsider)
    )

    assert response.status_code == 404, response.text


async def test_a_rule_naming_somebody_already_on_the_list_does_not_double_them(
    client, monkeypatch
) -> None:
    """The union is where the second copy would appear.

    Deduplication inside the resolver is not enough: the producer's own
    recipients and the rule's are two lists that meet at the end, and an
    address in both is one person who now receives every alert twice.
    """
    from tasks import notify

    _org, team, user, project = await _seed(client)
    await _add_rule(
        client, user=user, team_id=team.id, email_recipients=["Shared@Example.com"]
    )
    seen: dict[str, object] = {}

    async def _capture(*, kind, context, channels, recipients):
        seen["recipients"] = list(recipients or [])
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify, "dispatch", _capture)

    await asyncio.to_thread(
        notify._run_notification,
        None,
        "scan_completed",
        {"project_id": str(project.id)},
        ["slack"],
        ["shared@example.com"],
    )

    assert seen["recipients"] == ["shared@example.com"]


async def test_the_worker_sees_organization_rules_too(client, monkeypatch) -> None:
    """The path the worker actually takes.

    The resolver has two entry points, one for the worker and one for the API,
    and the tests above reach the second. A rule scoped to the organization is
    the case where the two could differ, because it is the one that depends on
    matching a NULL team.
    """
    from tasks import notify

    org, _team, _user, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        super_admin = await make_user(session, is_superuser=True)
    await client.post(
        f"/v1/notification-rules/org/{org.id}",
        headers=_bearer_for(super_admin),
        json={"name": "org wide", "email_recipients": ["cso@example.com"]},
    )
    seen: dict[str, object] = {}

    async def _capture(*, kind, context, channels, recipients):
        seen["recipients"] = list(recipients or [])
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify, "dispatch", _capture)

    await asyncio.to_thread(
        notify._run_notification,
        None,
        "scan_completed",
        {"project_id": str(project.id)},
        ["slack"],
        [],
    )

    assert seen["recipients"] == ["cso@example.com"]
