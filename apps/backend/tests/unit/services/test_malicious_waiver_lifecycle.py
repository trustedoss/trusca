"""
Malicious waivers through the API, and the bounds on their lifetime (#26).

These exist because the first version of this feature shipped a waiver that
never reached the database: the schema had the field, the model had the
column, and the upsert service quietly dropped it. Every test passed, because
they all built policies through the ORM and never went through a request.

So the round-trip here is the point — PUT, read it back, watch the gate change
its mind. A test that constructs the row directly cannot fail the way that bug
failed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


def _future(days: int = 7) -> str:
    return (datetime.now(tz=UTC) + timedelta(days=days)).isoformat()


async def _seed(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="team_admin")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    return org, team, user, project, scan


async def _flagged_component(session: AsyncSession, scan_id: uuid.UUID) -> str:
    from models import Component, ComponentVersion, ScanComponent

    suffix = unique_suffix()
    purl = f"pkg:npm/waiver-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"waiver-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
        malicious_state="flagged",
        malicious_id="MAL-0000-RT",
        malicious_source="osv.dev@seed",
        malicious_evaluated_at=datetime.now(tz=UTC),
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    session.add(ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True))
    await session.commit()
    return purl


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


async def test_a_waiver_saved_through_the_service_reaches_the_gate(
    db_session: AsyncSession,
) -> None:
    """PUT → read back → the gate changes its verdict.

    The regression this pins: the upsert dropped `malicious_exceptions` on the
    floor and answered 200, so an operator saw success while the build stayed
    blocked. Nothing raised, and every ORM-built test still passed.
    """
    from schemas.license_policy import LicensePolicyUpsertIn, MaliciousException
    from services.license_policy_service import get_team_policy_row, upsert_team_policy
    from services.policy_gate import evaluate_gate

    _, team, user, project, scan = await _seed(db_session)
    purl = await _flagged_component(db_session, scan.id)

    from core.security import CurrentUser

    actor = CurrentUser(
        id=user.id,
        email=user.email,
        role="team_admin",
        team_ids=[team.id],
        team_roles={team.id: "team_admin"},
        is_superuser=False,
    )

    assert (await evaluate_gate(db_session, project.id, scan_id=scan.id)).gate == "fail"

    await upsert_team_policy(
        db_session,
        actor,
        team_id=team.id,
        payload=LicensePolicyUpsertIn(
            malicious_exceptions=[
                MaliciousException(
                    component_purl=purl,
                    reason="challenged upstream",
                    expires_at=datetime.fromisoformat(_future()),
                )
            ]
        ),
    )

    # Readable afterwards — a waiver nobody can enumerate is one nobody reviews.
    stored = await get_team_policy_row(db_session, team_id=team.id)
    assert stored is not None
    assert len(stored.malicious_exceptions) == 1
    assert stored.malicious_exceptions[0]["component_purl"] == purl

    assert (await evaluate_gate(db_session, project.id, scan_id=scan.id)).gate == "pass"


# ---------------------------------------------------------------------------
# Lifetime bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expires_at", "expected_fragment"),
    [
        # Beyond the cap — the open-ended waiver the required field was meant
        # to prevent, written as a date instead of an omission.
        ("9999-12-31T00:00:00Z", "maximum"),
        # No timezone: read as UTC downstream, so a KST operator's "midnight"
        # would outlive their intent by nine hours.
        ("2099-01-01T00:00:00", "timezone"),
    ],
)
async def test_waiver_expiry_bounds_are_enforced(
    db_session: AsyncSession, expires_at: str, expected_fragment: str
) -> None:
    from schemas.license_policy import LicensePolicyUpsertIn, MaliciousException
    from services.license_policy_service import (
        LicensePolicyValidationError,
        upsert_team_policy,
    )

    _, team, user, _, _ = await _seed(db_session)
    from core.security import CurrentUser

    actor = CurrentUser(
        id=user.id,
        email=user.email,
        role="team_admin",
        team_ids=[team.id],
        team_roles={team.id: "team_admin"},
        is_superuser=False,
    )

    with pytest.raises(LicensePolicyValidationError) as excinfo:
        await upsert_team_policy(
            db_session,
            actor,
            team_id=team.id,
            payload=LicensePolicyUpsertIn(
                malicious_exceptions=[
                    MaliciousException(
                        component_purl="pkg:npm/whatever",
                        reason="r",
                        expires_at=datetime.fromisoformat(expires_at),
                    )
                ]
            ),
        )
    assert expected_fragment in str(excinfo.value)


async def test_an_expired_waiver_is_pruned_rather_than_blocking_the_edit(
    db_session: AsyncSession,
) -> None:
    """A lapsed waiver must not lock the policy.

    Rejecting already-expired entries would mean that once a waiver lapses,
    every later edit — including ones with nothing to do with this axis —
    fails validation until someone hand-strips the payload. They are dropped
    on write instead, which also keeps the stored array equal to the live set.
    """
    from schemas.license_policy import LicensePolicyUpsertIn, MaliciousException
    from services.license_policy_service import get_team_policy_row, upsert_team_policy

    _, team, user, _, _ = await _seed(db_session)
    from core.security import CurrentUser

    actor = CurrentUser(
        id=user.id,
        email=user.email,
        role="team_admin",
        team_ids=[team.id],
        team_roles={team.id: "team_admin"},
        is_superuser=False,
    )

    await upsert_team_policy(
        db_session,
        actor,
        team_id=team.id,
        payload=LicensePolicyUpsertIn(
            category_overrides={"MIT": "forbidden"},
            malicious_exceptions=[
                MaliciousException(
                    component_purl="pkg:npm/lapsed",
                    reason="r",
                    expires_at=datetime.now(tz=UTC) - timedelta(days=1),
                )
            ],
        ),
    )

    stored = await get_team_policy_row(db_session, team_id=team.id)
    assert stored is not None
    assert stored.malicious_exceptions == []
    # The unrelated part of the edit still landed.
    assert stored.category_overrides == {"MIT": "forbidden"}
