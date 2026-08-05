"""
DB-backed unit tests for the malicious axis of the build gate (#26 MAL-2a).

The axis blocks regardless of severity, which is the whole point: a malicious
package has no honest version to upgrade to, so it must not be weighed against
a CVE threshold. What needs pinning is therefore not "does it fail" alone but
the lifecycle around it — a waiver that expires puts the block back, and
switching the axis off must not read as a clean build.

Cases:
  - a flagged component fails the gate, with the count and reason surfaced.
  - `clear` and never-assessed components do not fail it.
  - GATE_MALICIOUS_ENABLED=false skips the axis (count 0, enforced False).
  - lifecycle sequence: flagged → fail → waiver → pass → waiver expires → fail.
  - a waiver for a different package does not lift the block.
  - the waiver matches on base purl, so it covers the versioned row.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip malicious gate tests")
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
            f"alembic upgrade head failed; malicious gate tests cannot run\n"
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
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_project(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="team_admin")
    project = await make_project(session, team=team)
    return org, team, project


async def _make_component(
    session: AsyncSession,
    *,
    malicious_state: str | None,
    advisory_id: str | None = None,
):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    purl = f"pkg:npm/gate-mal-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"gate-mal-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
        malicious_state=malicious_state,
        malicious_id=advisory_id,
        malicious_source="osv.dev@seed" if malicious_state else None,
        malicious_evaluated_at=datetime.now(tz=UTC) if malicious_state else None,
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return purl, cv


async def _attach(session: AsyncSession, *, scan_id, cv_id):
    from models import ScanComponent

    session.add(ScanComponent(scan_id=scan_id, component_version_id=cv_id, direct=True))
    await session.commit()


async def _make_policy(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    team_id: uuid.UUID,
    malicious_exceptions: list | None = None,
):
    from models import LicensePolicy

    policy = LicensePolicy(
        organization_id=org_id,
        team_id=team_id,
        name="gate-mal-policy",
        category_overrides={},
        license_exceptions=[],
        malicious_exceptions=malicious_exceptions or [],
        unknown_license_category="conditional",
        enabled=True,
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy


async def _seeded_scan(session: AsyncSession, project):
    scan = await make_scan(session, project=project, status="succeeded")
    return scan


# ---------------------------------------------------------------------------
# The axis itself
# ---------------------------------------------------------------------------


async def test_a_flagged_component_fails_the_gate(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    _, cv = await _make_component(
        db_session, malicious_state="flagged", advisory_id="MAL-0000-SEED"
    )
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)

    assert result.gate == "fail"
    assert result.malicious_component_count == 1
    assert result.malicious_gate_enforced is True
    assert result.reason is not None and "known-malicious" in result.reason
    # It is not a vulnerability: the CVE axis stays untouched.
    assert result.critical_cve_count == 0


async def test_clear_and_unassessed_components_do_not_block(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    _, clear_cv = await _make_component(db_session, malicious_state="clear")
    _, unassessed_cv = await _make_component(db_session, malicious_state=None)
    await _attach(db_session, scan_id=scan.id, cv_id=clear_cv.id)
    await _attach(db_session, scan_id=scan.id, cv_id=unassessed_cv.id)

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)

    assert result.gate == "pass"
    assert result.malicious_component_count == 0


async def test_disabled_axis_reports_zero_but_says_it_was_not_checked(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 with the axis off must be distinguishable from 0 with it on.

    Both render as "no malicious packages" if a consumer reads only the count,
    which is why the flag rides alongside it.
    """
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_MALICIOUS_ENABLED", "false")

    _, _, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    _, cv = await _make_component(db_session, malicious_state="flagged")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)

    assert result.gate == "pass"
    assert result.malicious_component_count == 0
    assert result.malicious_gate_enforced is False


# ---------------------------------------------------------------------------
# Waiver lifecycle
# ---------------------------------------------------------------------------


async def test_waiver_lifts_the_block_then_expiry_puts_it_back(
    db_session: AsyncSession,
) -> None:
    """The sequence the waiver exists for, start to finish.

    A single-state test would pass on an implementation that never expires
    anything — the expiry is the whole reason the field is mandatory, so the
    return to blocking is what needs pinning.
    """
    from services.policy_gate import evaluate_gate

    org, team, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    purl, cv = await _make_component(db_session, malicious_state="flagged")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    # 1. Flagged, no waiver → blocked.
    first = await evaluate_gate(db_session, project.id, scan_id=scan.id)
    assert first.gate == "fail"

    # 2. Waiver with a future expiry → the build moves again.
    future = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
    policy = await _make_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        malicious_exceptions=[
            {
                "component_purl": purl,
                "reason": "advisory challenged upstream, awaiting retraction",
                "expires_at": future,
            }
        ],
    )
    second = await evaluate_gate(db_session, project.id, scan_id=scan.id)
    assert second.gate == "pass"
    assert second.malicious_component_count == 0

    # 3. The waiver expires → blocked again, with no action from anyone.
    past = (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat()
    policy.malicious_exceptions = [
        {
            "component_purl": purl,
            "reason": "advisory challenged upstream, awaiting retraction",
            "expires_at": past,
        }
    ]
    db_session.add(policy)
    await db_session.commit()

    third = await evaluate_gate(db_session, project.id, scan_id=scan.id)
    assert third.gate == "fail"
    assert third.malicious_component_count == 1


async def test_a_waiver_for_another_package_does_not_lift_the_block(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    org, team, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    _, cv = await _make_component(db_session, malicious_state="flagged")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    future = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
    await _make_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        malicious_exceptions=[
            {
                "component_purl": "pkg:npm/some-other-package",
                "reason": "unrelated",
                "expires_at": future,
            }
        ],
    )

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)
    assert result.gate == "fail"
    assert result.malicious_component_count == 1


async def test_waiver_written_with_a_version_still_matches(
    db_session: AsyncSession,
) -> None:
    """Waivers match on the base purl.

    An operator reaching for this during an incident will paste whatever the
    UI showed them, which carries a version. Matching only the exact string
    would silently fail to lift the block.
    """
    from services.policy_gate import evaluate_gate

    org, team, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    purl, cv = await _make_component(db_session, malicious_state="flagged")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    future = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
    await _make_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        malicious_exceptions=[
            {
                "component_purl": f"{purl}@1.0.0",
                "reason": "pasted from the drawer",
                "expires_at": future,
            }
        ],
    )

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)
    assert result.gate == "pass"


async def test_a_partly_evaluated_scan_does_not_claim_to_be_assessed(
    db_session: AsyncSession,
) -> None:
    """One evaluated row must not vouch for the ones nobody looked at.

    The persist hook turns its evaluator off on the first exception and leaves
    the rest of the scan unstamped, so "some rows have verdicts" is the shape
    a failed enrichment leaves behind — not the shape of a healthy scan. An
    `assessed > 0` test would call this assessed and report a clean zero.
    """
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    _, evaluated = await _make_component(db_session, malicious_state="clear")
    _, never_evaluated = await _make_component(db_session, malicious_state=None)
    await _attach(db_session, scan_id=scan.id, cv_id=evaluated.id)
    await _attach(db_session, scan_id=scan.id, cv_id=never_evaluated.id)

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)

    assert result.gate == "pass"
    assert result.malicious_component_count == 0
    assert result.malicious_scan_assessed is False


async def test_a_fully_evaluated_scan_reports_assessed(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    for _ in range(2):
        _, cv = await _make_component(db_session, malicious_state="clear")
        await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)

    assert result.malicious_scan_assessed is True


async def test_a_malformed_waiver_array_leaves_the_block_in_place(
    db_session: AsyncSession,
) -> None:
    """JSONB holds whatever was written to it.

    A shape the reader cannot parse is not a reason to 500 every gate read for
    the team, and not a reason to honour waivers it cannot read either.
    """
    from models import LicensePolicy
    from services.policy_gate import evaluate_gate

    org, team, project = await _seed_project(db_session)
    scan = await _seeded_scan(db_session, project)
    _, cv = await _make_component(db_session, malicious_state="flagged")
    await _attach(db_session, scan_id=scan.id, cv_id=cv.id)

    policy = LicensePolicy(
        organization_id=org.id,
        team_id=team.id,
        name="malformed",
        category_overrides={},
        license_exceptions=[],
        malicious_exceptions={"not": "a list"},
        unknown_license_category="conditional",
        enabled=True,
    )
    db_session.add(policy)
    await db_session.commit()

    result = await evaluate_gate(db_session, project.id, scan_id=scan.id)

    assert result.gate == "fail"
    assert result.malicious_component_count == 1

