"""
DB-backed unit tests for the DYNAMIC (policy-aware) build gate — v2.2 c2.

These cover the c2 wiring in ``services.policy_gate``: when the project's owning
team has an effective, enabled ``LicensePolicy``, the forbidden-license count is
re-classified through the compound-SPDX evaluator (overrides / exceptions /
strategy / unknown posture) instead of the static persisted category.

Companion to ``test_policy_gate.py`` (which pins the STATIC / no-policy golden
contract — those tests create NO policy and MUST remain green; this file proves
they still pass under the c2 refactor by exercising the no-policy branch here
too).

Cases:
  - no policy → byte-identical static behaviour (a forbidden persisted license
    fails; the static count is used).
  - team policy override flips a normally-ALLOWED license to forbidden → fail.
  - team policy exception allows an otherwise-forbidden license → pass.
  - disabled team policy → static behaviour (override ignored).
  - org-default policy applies when the team has none.
  - compound expression under a policy (OR strategy relaxation).
  - adversarial / unparseable stored expression under a policy → unknown posture
    (forbidden posture here → fail) and never crashes the gate.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
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
        pytest.skip("DATABASE_URL not set — skip dynamic policy_gate tests")
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
            f"alembic upgrade head failed; dynamic policy_gate tests cannot run\n"
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


async def _seed_project_with_team(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="team_admin")
    project = await make_project(session, team=team)
    return org, team, user, project


async def _make_component_version(session: AsyncSession):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    purl = f"pkg:npm/gate-dyn-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"gate-dyn-{suffix}")
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
    return component, cv


async def _attach_scan_component(session: AsyncSession, *, scan_id, cv_id):
    from models import ScanComponent

    sc = ScanComponent(scan_id=scan_id, component_version_id=cv_id, direct=True)
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _resolve_or_make_license(session: AsyncSession, *, spdx_id: str, category: str):
    from models import License

    existing = await session.scalar(select(License).where(License.spdx_id == spdx_id))
    if existing is not None:
        return existing
    licence = License(spdx_id=spdx_id, name=spdx_id, category=category)
    session.add(licence)
    await session.commit()
    await session.refresh(licence)
    return licence


async def _attach_license_finding(session: AsyncSession, *, scan_id, cv_id, license_id):
    from models import LicenseFinding

    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind="concluded",
        source_path=f"path-{unique_suffix()}",
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf


async def _make_team_policy(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    team_id: uuid.UUID | None,
    category_overrides: dict | None = None,
    license_exceptions: list | None = None,
    unknown_license_category: str = "conditional",
    compound_operator_strategy: dict | None = None,
    enabled: bool = True,
):
    from models import LicensePolicy

    policy = LicensePolicy(
        organization_id=org_id,
        team_id=team_id,
        name="test-policy",
        category_overrides=category_overrides or {},
        license_exceptions=license_exceptions or [],
        unknown_license_category=unknown_license_category,
        compound_operator_strategy=compound_operator_strategy
        or {"AND": "most_restrictive", "OR": "least_restrictive", "WITH": "most_restrictive"},
        enabled=enabled,
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy


async def _component_with_license(session: AsyncSession, *, scan, spdx_id: str, category: str):
    """Attach a component carrying one license (spdx_id/category) to *scan*."""
    _, cv = await _make_component_version(session)
    await _attach_scan_component(session, scan_id=scan.id, cv_id=cv.id)
    licence = await _resolve_or_make_license(session, spdx_id=spdx_id, category=category)
    await _attach_license_finding(session, scan_id=scan.id, cv_id=cv.id, license_id=licence.id)
    return cv


# ---------------------------------------------------------------------------
# No-policy path — must remain byte-identical to the static golden contract
# ---------------------------------------------------------------------------


async def test_no_policy_uses_static_forbidden_category(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    _org, _team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _component_with_license(
        db_session, scan=scan, spdx_id="GPL-3.0-only", category="forbidden"
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.forbidden_license_count == 1


async def test_no_policy_allowed_license_passes(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    _org, _team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _component_with_license(db_session, scan=scan, spdx_id="MIT", category="allowed")

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.forbidden_license_count == 0


# ---------------------------------------------------------------------------
# Team policy: override flips a normally-allowed license to forbidden
# ---------------------------------------------------------------------------


async def test_team_override_flips_allowed_to_forbidden(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    # The component carries MIT, persisted as "allowed" by the static classifier.
    await _component_with_license(db_session, scan=scan, spdx_id="MIT", category="allowed")
    # The team policy forbids MIT.
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        category_overrides={"MIT": "forbidden"},
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.forbidden_license_count == 1
    assert result.reason is not None
    assert "forbidden" in result.reason


# ---------------------------------------------------------------------------
# Team policy: exception allows an otherwise-forbidden license
# ---------------------------------------------------------------------------


async def test_team_exception_allows_forbidden_license(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    # GPL-3.0-only persisted forbidden by the static classifier.
    await _component_with_license(
        db_session, scan=scan, spdx_id="GPL-3.0-only", category="forbidden"
    )
    # The team grants an (org/team-wide, non-expiring) waiver for it.
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        license_exceptions=[
            {"spdx_id": "GPL-3.0-only", "reason": "legal waiver TICKET-1"}
        ],
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.forbidden_license_count == 0


async def test_expired_exception_does_not_allow(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _component_with_license(
        db_session, scan=scan, spdx_id="GPL-3.0-only", category="forbidden"
    )
    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        license_exceptions=[
            {"spdx_id": "GPL-3.0-only", "reason": "expired waiver", "expires_at": past}
        ],
    )

    result = await evaluate_gate(db_session, project.id)

    # Expired waiver is treated as absent → GPL-3.0-only resolves forbidden.
    assert result.gate == "fail"
    assert result.forbidden_license_count == 1


# ---------------------------------------------------------------------------
# Disabled policy → static behaviour
# ---------------------------------------------------------------------------


async def test_disabled_team_policy_uses_static(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    # MIT persisted allowed; a DISABLED policy that would forbid MIT must be
    # ignored → the static "allowed" category wins → pass.
    await _component_with_license(db_session, scan=scan, spdx_id="MIT", category="allowed")
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        category_overrides={"MIT": "forbidden"},
        enabled=False,
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.forbidden_license_count == 0


# ---------------------------------------------------------------------------
# Org-default applies when the team has none
# ---------------------------------------------------------------------------


async def test_org_default_applies_when_team_has_no_policy(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _component_with_license(db_session, scan=scan, spdx_id="MIT", category="allowed")
    # Org-default (team_id=None) forbids MIT; the team has no policy of its own.
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=None,
        category_overrides={"MIT": "forbidden"},
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.forbidden_license_count == 1


async def test_team_policy_takes_precedence_over_org_default(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _component_with_license(db_session, scan=scan, spdx_id="MIT", category="allowed")
    # Org-default forbids MIT, but the team policy explicitly allows it → pass.
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=None,
        category_overrides={"MIT": "forbidden"},
    )
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        category_overrides={"MIT": "allowed"},
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.forbidden_license_count == 0


# ---------------------------------------------------------------------------
# Compound expression under a policy + adversarial stored expression
# ---------------------------------------------------------------------------


async def test_compound_or_relaxation_under_policy(db_session: AsyncSession) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    # A compound dual-license stored (the static classifier may have persisted it
    # "forbidden" via most-restrictive). The team's OR=least_restrictive policy
    # re-reads it as allowed (the MIT alternative wins).
    await _component_with_license(
        db_session,
        scan=scan,
        spdx_id="MIT OR GPL-3.0-only",
        category="forbidden",
    )
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        compound_operator_strategy={
            "AND": "most_restrictive",
            "OR": "least_restrictive",
            "WITH": "most_restrictive",
        },
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.forbidden_license_count == 0


async def test_adversarial_stored_expression_uses_unknown_posture(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    # A hostile / unparseable stored expression. Persisted category is irrelevant
    # under a policy — the evaluator re-reads the spdx_id and, being unparseable,
    # falls back to the policy's unknown posture (here: forbidden) → fail, no crash.
    await _component_with_license(
        db_session,
        scan=scan,
        spdx_id="(((MIT AND",
        category="unknown",
    )
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        unknown_license_category="forbidden",
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.forbidden_license_count == 1


async def test_dynamic_count_collapses_duplicate_findings(db_session: AsyncSession) -> None:
    """Multiple license_findings for the same cv collapse to one in the dynamic
    count, mirroring the static DISTINCT-component semantics."""
    from models import LicenseFinding
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    licence = await _resolve_or_make_license(db_session, spdx_id="MIT", category="allowed")
    db_session.add(
        LicenseFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            license_id=licence.id,
            kind="declared",
            source_path=f"a-{unique_suffix()}",
        )
    )
    db_session.add(
        LicenseFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            license_id=licence.id,
            kind="concluded",
            source_path=f"b-{unique_suffix()}",
        )
    )
    await db_session.commit()
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        category_overrides={"MIT": "forbidden"},
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.forbidden_license_count == 1


# ---------------------------------------------------------------------------
# c3 — component-scoped (purl) exceptions
# ---------------------------------------------------------------------------


async def test_purl_scoped_exception_waives_only_that_component(
    db_session: AsyncSession,
) -> None:
    """A component-scoped exception removes only the matching component from the
    forbidden count — another component with the SAME license still fails."""
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    cv_waived = await _component_with_license(
        db_session, scan=scan, spdx_id="GPL-3.0-only", category="forbidden"
    )
    await _component_with_license(
        db_session, scan=scan, spdx_id="GPL-3.0-only", category="forbidden"
    )
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        license_exceptions=[
            {
                "spdx_id": "GPL-3.0-only",
                "component_purl": cv_waived.purl_with_version,
                "reason": "cdxgen misclassified — actually dual-licensed",
            }
        ],
    )

    result = await evaluate_gate(db_session, project.id)

    # Only cv_waived is removed; the other GPL component still fails the gate.
    assert result.forbidden_license_count == 1
    assert result.gate == "fail"


async def test_purl_scoped_exception_nonmatching_purl_does_not_waive(
    db_session: AsyncSession,
) -> None:
    """A component-scoped exception whose purl matches no component waives nothing."""
    from services.policy_gate import evaluate_gate

    org, team, _user, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _component_with_license(
        db_session, scan=scan, spdx_id="GPL-3.0-only", category="forbidden"
    )
    await _make_team_policy(
        db_session,
        org_id=org.id,
        team_id=team.id,
        license_exceptions=[
            {
                "spdx_id": "GPL-3.0-only",
                "component_purl": "pkg:pypi/not-present@9.9.9",
                "reason": "scoped to a component not in this scan",
            }
        ],
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.forbidden_license_count == 1
    assert result.gate == "fail"
