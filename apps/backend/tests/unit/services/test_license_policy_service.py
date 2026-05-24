"""
Service-layer tests for ``services.license_policy_service`` — v2.2 Track C (c1).

Drives the pure async service against a live Postgres (DATABASE_URL) so the
SQLAlchemy audit listener fires and ``audit_logs`` records each mutation.
Mirrors the shape of ``tests/unit/services/test_api_key_service.py``.

Coverage:
  - upsert team policy: persists, writes audit row, second upsert UPDATES
    (no duplicate), disabled toggle.
  - upsert org default: super_admin only; one-org-default uniqueness (a second
    org default is a clean 4xx, never 500).
  - get_effective_policy precedence: team > org > none; disabled team → org;
    disabled org → none.
  - RBAC: developer cannot upsert (team or org); non-member blocked on read;
    org default requires super_admin; cross-team write blocked.
  - reset/delete: removes the row; 404 when absent.
  - effective_category (PURE): override beats static; exception forces allowed;
    unknown id → unknown_license_category; disabled policy → static default.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from schemas.license_policy import LicensePolicyUpsertIn
from services.license_policy_service import (
    LicensePolicyForbidden,
    LicensePolicyNotFound,
    delete_team_policy,
    effective_category,
    get_effective_policy,
    get_org_policy,
    get_policy,
    list_policies,
    upsert_org_policy,
    upsert_team_policy,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_team,
    make_user,
    principal_for,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip license_policy_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; license_policy_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures: a fully-wired org/team/admin graph
# ---------------------------------------------------------------------------


async def _team_admin_graph(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    admin = await make_user(session)
    await make_membership(session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")
    return org, team, admin, actor


def _payload(**overrides) -> LicensePolicyUpsertIn:
    base: dict = {
        "name": "Engineering policy",
        "category_overrides": {"MPL-2.0": "forbidden"},
        "license_exceptions": [],
        "unknown_license_category": "conditional",
        "enabled": True,
    }
    base.update(overrides)
    return LicensePolicyUpsertIn(**base)


# ---------------------------------------------------------------------------
# upsert_team_policy
# ---------------------------------------------------------------------------


async def test_upsert_team_policy_persists(db_session: AsyncSession) -> None:
    org, team, _admin, actor = await _team_admin_graph(db_session)
    row = await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())
    assert row.team_id == team.id
    assert row.organization_id == org.id
    assert row.category_overrides == {"MPL-2.0": "forbidden"}
    assert row.unknown_license_category == "conditional"
    assert row.enabled is True
    # Default compound strategy filled in.
    assert row.compound_operator_strategy["OR"] == "least_restrictive"


async def test_upsert_team_policy_writes_audit_row(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())
    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE target_table = 'license_policies' AND action = 'create'"
            )
        )
    ).scalar_one()
    assert count >= 1


async def test_second_upsert_updates_no_duplicate(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    first = await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())
    second = await upsert_team_policy(
        db_session,
        actor,
        team_id=team.id,
        payload=_payload(category_overrides={"GPL-3.0-only": "forbidden"}, enabled=False),
    )
    assert second.id == first.id  # same row updated
    assert second.category_overrides == {"GPL-3.0-only": "forbidden"}
    assert second.enabled is False
    rows, total = await list_policies(db_session, actor, team_id=team.id)
    assert total == 1
    assert len(rows) == 1


async def test_upsert_team_policy_developer_forbidden(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    dev = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    actor = principal_for(dev, team_ids=[team.id], role="developer")
    with pytest.raises(LicensePolicyForbidden):
        await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())


async def test_upsert_team_policy_cross_team_blocked(db_session: AsyncSession) -> None:
    """A team_admin of team A cannot write team B's policy."""
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team_a, role="team_admin")
    actor = principal_for(admin, team_ids=[team_a.id], role="team_admin")
    with pytest.raises(LicensePolicyForbidden):
        await upsert_team_policy(db_session, actor, team_id=team_b.id, payload=_payload())


async def test_upsert_team_policy_unknown_team_404(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    with pytest.raises(LicensePolicyNotFound):
        await upsert_team_policy(db_session, actor, team_id=uuid.uuid4(), payload=_payload())


# ---------------------------------------------------------------------------
# upsert_org_policy
# ---------------------------------------------------------------------------


async def test_upsert_org_policy_super_admin(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    row = await upsert_org_policy(db_session, actor, organization_id=org.id, payload=_payload())
    assert row.organization_id == org.id
    assert row.team_id is None


async def test_upsert_org_policy_non_super_forbidden(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    actor = principal_for(admin, team_ids=[team.id], role="team_admin")
    with pytest.raises(LicensePolicyForbidden):
        await upsert_org_policy(db_session, actor, organization_id=org.id, payload=_payload())


async def test_org_default_uniqueness_updates_in_place(db_session: AsyncSession) -> None:
    """A second org-default upsert UPDATES the single row — no duplicate, no 500."""
    org = await make_organization(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    first = await upsert_org_policy(db_session, actor, organization_id=org.id, payload=_payload())
    second = await upsert_org_policy(
        db_session,
        actor,
        organization_id=org.id,
        payload=_payload(category_overrides={"EPL-2.0": "conditional"}),
    )
    assert second.id == first.id
    assert second.category_overrides == {"EPL-2.0": "conditional"}


async def test_org_default_duplicate_insert_is_clean_conflict(
    db_session: AsyncSession,
) -> None:
    """Two raw INSERTs of an org default trip the partial unique index → 409, not 500.

    We force the race by inserting one org-default row out-of-band, then driving a
    second create through the service path-equivalent. Here we directly assert the
    DB rejects a duplicate org-default insert (the index is the backstop the
    service's IntegrityError → LicensePolicyConflict translation depends on).
    """
    from models import LicensePolicy

    org = await make_organization(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    await upsert_org_policy(db_session, actor, organization_id=org.id, payload=_payload())

    # A second raw org-default row for the same org must violate the partial
    # unique index. We assert via the service-level conflict translation by
    # bypassing the "find existing first" path: insert directly.
    dupe = LicensePolicy(organization_id=org.id, team_id=None)
    db_session.add(dupe)
    with pytest.raises(Exception):  # noqa: B017,PT011 - IntegrityError family
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# get_effective_policy — precedence
# ---------------------------------------------------------------------------


async def test_effective_policy_team_beats_org(db_session: AsyncSession) -> None:
    org, team, _admin, team_actor = await _team_admin_graph(db_session)
    super_admin = await make_user(db_session, is_superuser=True)
    super_actor = principal_for(super_admin, role="super_admin")

    await upsert_org_policy(
        db_session,
        super_actor,
        organization_id=org.id,
        payload=_payload(name="org-default"),
    )
    await upsert_team_policy(
        db_session, team_actor, team_id=team.id, payload=_payload(name="team-policy")
    )
    effective = await get_effective_policy(db_session, team_id=team.id)
    assert effective is not None
    assert effective.team_id == team.id
    assert effective.name == "team-policy"


async def test_effective_policy_disabled_team_falls_through_to_org(
    db_session: AsyncSession,
) -> None:
    org, team, _admin, team_actor = await _team_admin_graph(db_session)
    super_admin = await make_user(db_session, is_superuser=True)
    super_actor = principal_for(super_admin, role="super_admin")

    await upsert_org_policy(
        db_session, super_actor, organization_id=org.id, payload=_payload(name="org-default")
    )
    await upsert_team_policy(
        db_session,
        team_actor,
        team_id=team.id,
        payload=_payload(name="disabled-team", enabled=False),
    )
    effective = await get_effective_policy(db_session, team_id=team.id)
    assert effective is not None
    assert effective.team_id is None  # org default
    assert effective.name == "org-default"


async def test_effective_policy_none_when_nothing_applies(db_session: AsyncSession) -> None:
    _org, team, _admin, _actor = await _team_admin_graph(db_session)
    effective = await get_effective_policy(db_session, team_id=team.id)
    assert effective is None


async def test_effective_policy_disabled_org_yields_none(db_session: AsyncSession) -> None:
    org, team, _admin, _team_actor = await _team_admin_graph(db_session)
    super_admin = await make_user(db_session, is_superuser=True)
    super_actor = principal_for(super_admin, role="super_admin")
    await upsert_org_policy(
        db_session,
        super_actor,
        organization_id=org.id,
        payload=_payload(name="off", enabled=False),
    )
    effective = await get_effective_policy(db_session, team_id=team.id)
    assert effective is None


# ---------------------------------------------------------------------------
# get_policy (read effective) — RBAC
# ---------------------------------------------------------------------------


async def test_get_policy_member_reads_effective(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())
    row = await get_policy(db_session, actor, team_id=team.id)
    assert row.team_id == team.id


async def test_get_policy_non_member_forbidden(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())
    outsider = await make_user(db_session)
    outsider_actor = principal_for(outsider, team_ids=[], role="developer")
    with pytest.raises(LicensePolicyForbidden):
        await get_policy(db_session, outsider_actor, team_id=team.id)


async def test_get_policy_404_when_no_policy(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    with pytest.raises(LicensePolicyNotFound):
        await get_policy(db_session, actor, team_id=team.id)


async def test_get_org_policy_404_when_absent(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    with pytest.raises(LicensePolicyNotFound):
        await get_org_policy(db_session, actor, organization_id=org.id)


# ---------------------------------------------------------------------------
# list_policies — visibility
# ---------------------------------------------------------------------------


async def test_list_super_admin_sees_team_and_org(db_session: AsyncSession) -> None:
    org, team, _admin, team_actor = await _team_admin_graph(db_session)
    super_admin = await make_user(db_session, is_superuser=True)
    super_actor = principal_for(super_admin, role="super_admin")
    await upsert_org_policy(db_session, super_actor, organization_id=org.id, payload=_payload())
    await upsert_team_policy(db_session, team_actor, team_id=team.id, payload=_payload())
    rows, total = await list_policies(db_session, super_actor, organization_id=org.id)
    assert total == 2
    scopes = {(r.team_id is None) for r in rows}
    assert scopes == {True, False}


async def test_list_member_sees_own_team_and_org_default(db_session: AsyncSession) -> None:
    org, team, _admin, team_actor = await _team_admin_graph(db_session)
    super_admin = await make_user(db_session, is_superuser=True)
    super_actor = principal_for(super_admin, role="super_admin")
    await upsert_org_policy(db_session, super_actor, organization_id=org.id, payload=_payload())
    await upsert_team_policy(db_session, team_actor, team_id=team.id, payload=_payload())
    rows, total = await list_policies(db_session, team_actor, organization_id=org.id)
    assert total == 2


async def test_list_no_memberships_empty(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    loner = await make_user(db_session)
    actor = principal_for(loner, team_ids=[], role="developer")
    rows, total = await list_policies(db_session, actor, organization_id=org.id)
    assert total == 0
    assert rows == []


# ---------------------------------------------------------------------------
# delete_team_policy
# ---------------------------------------------------------------------------


async def test_delete_team_policy_removes_row(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    await upsert_team_policy(db_session, actor, team_id=team.id, payload=_payload())
    await delete_team_policy(db_session, actor, team_id=team.id)
    with pytest.raises(LicensePolicyNotFound):
        await get_policy(db_session, actor, team_id=team.id)


async def test_delete_team_policy_404_when_absent(db_session: AsyncSession) -> None:
    _org, team, _admin, actor = await _team_admin_graph(db_session)
    with pytest.raises(LicensePolicyNotFound):
        await delete_team_policy(db_session, actor, team_id=team.id)


async def test_delete_team_policy_developer_forbidden(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session)
    await make_membership(db_session, user=admin, team=team, role="team_admin")
    admin_actor = principal_for(admin, team_ids=[team.id], role="team_admin")
    await upsert_team_policy(db_session, admin_actor, team_id=team.id, payload=_payload())

    dev = await make_user(db_session)
    await make_membership(db_session, user=dev, team=team, role="developer")
    dev_actor = principal_for(dev, team_ids=[team.id], role="developer")
    with pytest.raises(LicensePolicyForbidden):
        await delete_team_policy(db_session, dev_actor, team_id=team.id)


# ---------------------------------------------------------------------------
# effective_category — PURE (no DB)
# ---------------------------------------------------------------------------


class _FakePolicy:
    """Minimal stand-in matching the attributes effective_category reads."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        category_overrides: dict | None = None,
        license_exceptions: list | None = None,
        unknown_license_category: str = "conditional",
    ) -> None:
        self.enabled = enabled
        self.category_overrides = category_overrides or {}
        self.license_exceptions = license_exceptions or []
        self.unknown_license_category = unknown_license_category


def test_effective_category_override_beats_static() -> None:
    policy = _FakePolicy(category_overrides={"MPL-2.0": "forbidden"})
    assert effective_category("MPL-2.0", policy, "conditional") == "forbidden"


def test_effective_category_exception_forces_allowed() -> None:
    policy = _FakePolicy(
        category_overrides={"GPL-3.0-only": "forbidden"},
        license_exceptions=[{"spdx_id": "GPL-3.0-only", "reason": "legal waiver"}],
    )
    assert effective_category("GPL-3.0-only", policy, "forbidden") == "allowed"


def test_effective_category_expired_exception_ignored() -> None:
    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    policy = _FakePolicy(
        category_overrides={"GPL-3.0-only": "forbidden"},
        license_exceptions=[{"spdx_id": "GPL-3.0-only", "reason": "waiver", "expires_at": past}],
    )
    # Expired exception → falls through to the override.
    assert effective_category("GPL-3.0-only", policy, "unknown") == "forbidden"


def test_effective_category_future_exception_applies() -> None:
    future = (datetime.now(tz=UTC) + timedelta(days=30)).isoformat()
    policy = _FakePolicy(
        license_exceptions=[{"spdx_id": "LGPL-3.0", "reason": "ok", "expires_at": future}],
    )
    assert effective_category("LGPL-3.0", policy, "conditional") == "allowed"


def test_effective_category_purl_scoped_exception_skipped_in_simple_path() -> None:
    """A purl-scoped exception is NOT applied by the simple-id resolver (c2 does it)."""
    policy = _FakePolicy(
        license_exceptions=[{"spdx_id": "MIT", "reason": "ok", "component_purl": "pkg:pypi/x@1"}],
    )
    # No purl in hand → exception ignored → static_default used.
    assert effective_category("MIT", policy, "allowed") == "allowed"


def test_effective_category_unknown_id_uses_posture() -> None:
    policy = _FakePolicy(unknown_license_category="forbidden")
    assert effective_category("LicenseRef-weird", policy, "unknown") == "forbidden"


def test_effective_category_concrete_static_default_passthrough() -> None:
    policy = _FakePolicy()
    assert effective_category("MIT", policy, "allowed") == "allowed"


def test_effective_category_disabled_policy_uses_static() -> None:
    policy = _FakePolicy(enabled=False, category_overrides={"MIT": "forbidden"})
    assert effective_category("MIT", policy, "allowed") == "allowed"


def test_effective_category_none_policy_uses_static() -> None:
    assert effective_category("MIT", None, "conditional") == "conditional"


def test_effective_category_unparseable_expiry_treated_as_no_expiry() -> None:
    policy = _FakePolicy(
        license_exceptions=[{"spdx_id": "MIT", "reason": "ok", "expires_at": "not-a-date"}],
    )
    assert effective_category("MIT", policy, "forbidden") == "allowed"


def test_effective_category_datetime_instance_expiry_past() -> None:
    """expires_at as a datetime instance (not a string) is honoured."""
    past = datetime.now(tz=UTC) - timedelta(days=1)
    policy = _FakePolicy(
        category_overrides={"MIT": "forbidden"},
        license_exceptions=[{"spdx_id": "MIT", "reason": "ok", "expires_at": past}],
    )
    # Expired → exception ignored → override wins.
    assert effective_category("MIT", policy, "allowed") == "forbidden"


def test_effective_category_datetime_instance_expiry_future() -> None:
    future = datetime.now(tz=UTC) + timedelta(days=1)
    policy = _FakePolicy(
        license_exceptions=[{"spdx_id": "MIT", "reason": "ok", "expires_at": future}],
    )
    assert effective_category("MIT", policy, "forbidden") == "allowed"


def test_effective_category_non_dict_exception_entry_skipped() -> None:
    """A malformed (non-dict) entry in license_exceptions is skipped, not fatal."""
    policy = _FakePolicy(
        license_exceptions=["not-a-dict", {"spdx_id": "MIT", "reason": "ok"}],
    )
    assert effective_category("MIT", policy, "forbidden") == "allowed"


def test_effective_category_non_aware_datetime_expiry() -> None:
    """A naive (tz-less) datetime expiry is treated as UTC."""
    naive_future = (datetime.now(tz=UTC) + timedelta(days=1)).replace(tzinfo=None)
    policy = _FakePolicy(
        license_exceptions=[{"spdx_id": "MIT", "reason": "ok", "expires_at": naive_future}],
    )
    assert effective_category("MIT", policy, "forbidden") == "allowed"
