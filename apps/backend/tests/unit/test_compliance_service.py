"""
Backend service tests for ``services/compliance_service.py`` — W9-#58.

Covers the single entry point :func:`list_project_compliance` plus the
pure normalisation / summary helpers.

Mirrors :file:`tests/unit/test_license_service.py` structurally:

  - Pure cases (filter normalisation, summary trim) run on every PR — no
    DB dependency.
  - DB-backed cases are gated on ``DATABASE_URL`` + ``alembic upgrade head``
    via the ``integration`` marker. CI brings up a real Postgres testcontainer.
  - The ``_isolate_engine_per_test`` autouse fixture in tests/conftest.py
    keeps asyncpg's connection pool from leaking across the per-test event
    loop pytest-asyncio creates.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.compliance_service import (
    ComplianceError,
    _normalize_category_filter,
    _normalize_kind_filter,
    _summarize_obligation,
    list_project_compliance,
)
from services.project_service import ProjectForbidden, ProjectNotFound
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Pure-helper tests (no DB) — run on every PR.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ([], []),
        (["forbidden"], ["forbidden"]),
        (["forbidden", "allowed"], ["forbidden", "allowed"]),
        (["BOGUS"], []),
        (["BOGUS", "forbidden"], ["forbidden"]),
        (["BOGUS", "ALSOBAD"], []),
    ],
)
def test_normalize_category_filter(raw, expected) -> None:
    assert _normalize_category_filter(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ([], []),
        (["attribution"], ["attribution"]),
        (["attribution", "notice"], ["attribution", "notice"]),
        (["attribution", "attribution"], ["attribution"]),  # dedup
        (["  "], []),  # blank only
        (["x" * 65], []),  # length-cap
        ([" attribution "], ["attribution"]),  # trim
    ],
)
def test_normalize_kind_filter(raw, expected) -> None:
    assert _normalize_kind_filter(raw) == expected


def test_summarize_obligation_short_text_round_trips() -> None:
    assert _summarize_obligation("ship a NOTICE file") == "ship a NOTICE file"


def test_summarize_obligation_collapses_whitespace() -> None:
    raw = "line one\n\n\nline two\n  trailing   spaces"
    assert _summarize_obligation(raw) == "line one line two trailing spaces"


def test_summarize_obligation_truncates_long_text_with_ellipsis() -> None:
    long = "a" * 1000
    out = _summarize_obligation(long)
    assert len(out) <= 240
    assert out.endswith("…")


def test_summarize_obligation_empty_and_none() -> None:
    assert _summarize_obligation(None) == ""
    assert _summarize_obligation("") == ""


# ---------------------------------------------------------------------------
# DB-backed tests start here — gated on DATABASE_URL.
# ---------------------------------------------------------------------------


pytestmark_db = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip compliance service DB tests")
    return url


@pytest.fixture(scope="module")
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
            "alembic upgrade head failed; compliance service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session(_migrate_once) -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Local fixture builders
# ---------------------------------------------------------------------------


async def _make_component_version(
    session: AsyncSession,
    *,
    name: str | None = None,
    version: str = "1.0.0",
    package_type: str = "npm",
):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    cname = name or f"pkg-{suffix}"
    purl = f"pkg:{package_type}/{cname}"
    component = Component(purl=purl, package_type=package_type, name=cname)
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"{purl}@{version}",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return component, cv


async def _make_license(
    session: AsyncSession,
    *,
    spdx_id: str | None = None,
    name: str | None = None,
    category: str = "allowed",
):
    from models import License as LicenseModel

    suffix = unique_suffix()
    lic = LicenseModel(
        spdx_id=spdx_id if spdx_id is not None else f"SPDX-{suffix}",
        name=name or f"License {suffix}",
        category=category,
    )
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _attach_license_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    license_id: uuid.UUID,
    kind: str = "concluded",
):
    from models import LicenseFinding

    suffix = unique_suffix()
    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind=kind,
        source_path=f"path/{suffix}",
        raw_data={},
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf


async def _attach_obligation(
    session: AsyncSession,
    *,
    license_id: uuid.UUID,
    kind: str,
    text: str = "Default obligation text.",
    link: str | None = None,
):
    from models import Obligation

    ob = Obligation(license_id=license_id, kind=kind, text=text, link=link)
    session.add(ob)
    await session.commit()
    await session.refresh(ob)
    return ob


async def _make_project_with_scan(session: AsyncSession):
    """Set up org → team → user → membership → project → succeeded scan."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    project.updated_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(project)
    return team, user, project, scan


# ---------------------------------------------------------------------------
# Authorization & shape
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_unknown_project_raises_project_not_found(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[], role="developer")
    with pytest.raises(ProjectNotFound):
        await list_project_compliance(
            db_session, project_id=uuid.uuid4(), actor=actor
        )


@pytestmark_db
async def test_cross_team_raises_project_forbidden(db_session: AsyncSession) -> None:
    team_a, _, project, _ = await _make_project_with_scan(db_session)

    other_org = await make_organization(db_session)
    other_team = await make_team(db_session, organization=other_org)
    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=other_team, role="developer")
    actor = principal_for(outsider, team_ids=[other_team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await list_project_compliance(
            db_session, project_id=project.id, actor=actor
        )


@pytestmark_db
async def test_empty_when_project_has_no_succeeded_scan(
    db_session: AsyncSession,
) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    items, distribution, total, generated_at = await list_project_compliance(
        db_session, project_id=project.id, actor=actor
    )
    assert items == []
    assert total == 0
    assert distribution == {
        "forbidden": 0,
        "conditional": 0,
        "allowed": 0,
        "unknown": 0,
    }
    assert isinstance(generated_at, datetime)


@pytestmark_db
async def test_invalid_sort_raises_compliance_error(db_session: AsyncSession) -> None:
    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    with pytest.raises(ComplianceError):
        await list_project_compliance(
            db_session,
            project_id=project.id,
            actor=actor,
            sort="BOGUS",
        )


# ---------------------------------------------------------------------------
# Happy path — license + obligation join
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_happy_path_returns_grid_with_join(db_session: AsyncSession) -> None:
    """A license with two components + one obligation produces one row that
    carries both the affected preview and the obligation summary inline."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv1 = await _make_component_version(db_session, name=f"alpha-{unique_suffix()}")
    _, cv2 = await _make_component_version(db_session, name=f"beta-{unique_suffix()}")
    apache = await _make_license(
        db_session, spdx_id=f"Apache-2.0-{unique_suffix()}", name="Apache License 2.0",
        category="allowed",
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=apache.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=apache.id)
    await _attach_obligation(
        db_session,
        license_id=apache.id,
        kind="attribution",
        text="Provide attribution in NOTICE.",
    )

    items, distribution, total, _ = await list_project_compliance(
        db_session, project_id=project.id, actor=actor
    )
    # Exactly one row for the one license.
    assert total >= 1
    matching = [r for r in items if r["license_id"] == apache.id]
    assert len(matching) == 1
    row = matching[0]
    assert row["affected_component_count"] == 2
    assert {c["component_version_id"] for c in row["affected_components"]} == {
        cv1.id,
        cv2.id,
    }
    assert len(row["obligations"]) == 1
    assert row["obligations"][0]["kind"] == "attribution"
    assert row["obligations"][0]["summary"] == "Provide attribution in NOTICE."
    assert row["notice_required"] is True
    assert row["category"] == "allowed"
    assert row["category_source"] == "static"
    assert row["category_override_source"] is None
    # Distribution counts the license once.
    assert distribution["allowed"] >= 1


@pytestmark_db
async def test_affected_components_preview_capped_at_five(
    db_session: AsyncSession,
) -> None:
    """A license attached to 7 component_versions yields only 5 in the
    preview, but the count reflects the true total."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    lic = await _make_license(db_session, category="allowed")
    for i in range(7):
        _, cv = await _make_component_version(
            db_session,
            name=f"pkg-{unique_suffix()}-{i:02d}",
        )
        await _attach_license_finding(
            db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
        )

    items, _, _, _ = await list_project_compliance(
        db_session, project_id=project.id, actor=actor
    )
    matching = [r for r in items if r["license_id"] == lic.id]
    assert len(matching) == 1
    row = matching[0]
    assert row["affected_component_count"] == 7
    assert len(row["affected_components"]) == 5


@pytestmark_db
async def test_distribution_unfiltered_under_category_filter(
    db_session: AsyncSession,
) -> None:
    """Distribution always reflects the underlying scan, regardless of the
    active category filter — chart axis stays stable."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    mit = await _make_license(db_session, category="allowed")
    gpl = await _make_license(db_session, category="forbidden")
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=mit.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=gpl.id)

    items, distribution, total, _ = await list_project_compliance(
        db_session,
        project_id=project.id,
        actor=actor,
        categories=["forbidden"],
    )
    # The list narrowed to one row.
    assert all(r["category"] == "forbidden" for r in items)
    assert total >= 1
    # But the distribution still reports both buckets.
    assert distribution["allowed"] >= 1
    assert distribution["forbidden"] >= 1


@pytestmark_db
async def test_has_obligations_filter_isolates_licenses_with_obligations(
    db_session: AsyncSession,
) -> None:
    """``has_obligations=True`` returns only licenses with at least one row;
    ``False`` returns only licenses with NONE."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    apache = await _make_license(db_session, category="allowed")
    isc = await _make_license(db_session, category="allowed")
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=apache.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=isc.id)
    # Only Apache carries an obligation.
    await _attach_obligation(
        db_session, license_id=apache.id, kind="attribution", text="See NOTICE."
    )

    with_obs, _, _, _ = await list_project_compliance(
        db_session,
        project_id=project.id,
        actor=actor,
        has_obligations=True,
    )
    assert apache.id in {r["license_id"] for r in with_obs}
    assert isc.id not in {r["license_id"] for r in with_obs}

    without_obs, _, _, _ = await list_project_compliance(
        db_session,
        project_id=project.id,
        actor=actor,
        has_obligations=False,
    )
    assert isc.id in {r["license_id"] for r in without_obs}
    assert apache.id not in {r["license_id"] for r in without_obs}


@pytestmark_db
async def test_kind_filter_returns_only_licenses_with_matching_obligations(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    gpl = await _make_license(db_session, category="forbidden")
    mpl = await _make_license(db_session, category="conditional")
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=gpl.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=mpl.id)
    await _attach_obligation(
        db_session, license_id=gpl.id, kind="source-disclosure", text="Distribute source."
    )
    await _attach_obligation(
        db_session, license_id=mpl.id, kind="attribution", text="Attribute MPL."
    )

    items, _, _, _ = await list_project_compliance(
        db_session,
        project_id=project.id,
        actor=actor,
        kinds=["source-disclosure"],
    )
    license_ids = {r["license_id"] for r in items}
    assert gpl.id in license_ids
    assert mpl.id not in license_ids


@pytestmark_db
async def test_search_matches_spdx_id_or_name(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    suffix = unique_suffix()
    apache = await _make_license(
        db_session, spdx_id=f"Apache-2.0-{suffix}", name=f"Apache {suffix}",
        category="allowed",
    )
    mit = await _make_license(
        db_session, spdx_id=f"MIT-{suffix}", name=f"MIT License {suffix}",
        category="allowed",
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=apache.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=mit.id)

    items, _, _, _ = await list_project_compliance(
        db_session,
        project_id=project.id,
        actor=actor,
        search=f"Apache-2.0-{suffix}",
    )
    license_ids = {r["license_id"] for r in items}
    assert apache.id in license_ids
    assert mit.id not in license_ids


@pytestmark_db
async def test_pagination_returns_consistent_total(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    licenses_made: list[uuid.UUID] = []
    for _ in range(5):
        _, cv = await _make_component_version(db_session)
        lic = await _make_license(db_session, category="allowed")
        await _attach_license_finding(
            db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
        )
        licenses_made.append(lic.id)

    items_p1, _, total_p1, _ = await list_project_compliance(
        db_session, project_id=project.id, actor=actor, limit=2, offset=0,
    )
    items_p2, _, total_p2, _ = await list_project_compliance(
        db_session, project_id=project.id, actor=actor, limit=2, offset=2,
    )
    # Total is stable across pages.
    assert total_p1 == total_p2
    assert total_p1 >= 5
    assert len(items_p1) <= 2
    # First and second pages don't overlap.
    p1_ids = {r["license_finding_id"] for r in items_p1}
    p2_ids = {r["license_finding_id"] for r in items_p2}
    assert p1_ids.isdisjoint(p2_ids)


@pytestmark_db
async def test_snapshot_scan_id_pins_to_specific_scan(db_session: AsyncSession) -> None:
    """A pinned ``scan_id`` reflects the historical scan's licenses, not the
    project's latest scan."""
    team, user, project, scan_old = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Add a license to scan_old.
    _, cv_old = await _make_component_version(db_session)
    lic_old = await _make_license(db_session, category="allowed")
    await _attach_license_finding(
        db_session, scan_id=scan_old.id, cv_id=cv_old.id, license_id=lic_old.id
    )

    # Now spin a newer succeeded scan with a different license.
    scan_new = await make_scan(db_session, project=project, status="succeeded")
    project.latest_scan_id = scan_new.id
    await db_session.commit()
    _, cv_new = await _make_component_version(db_session)
    lic_new = await _make_license(db_session, category="forbidden")
    await _attach_license_finding(
        db_session, scan_id=scan_new.id, cv_id=cv_new.id, license_id=lic_new.id
    )

    # Default → latest scan, sees ``lic_new``, not ``lic_old``.
    items_latest, _, _, _ = await list_project_compliance(
        db_session, project_id=project.id, actor=actor,
    )
    latest_lic_ids = {r["license_id"] for r in items_latest}
    assert lic_new.id in latest_lic_ids
    assert lic_old.id not in latest_lic_ids

    # Pinned to scan_old → sees ``lic_old``, not ``lic_new``.
    items_old, _, _, _ = await list_project_compliance(
        db_session, project_id=project.id, actor=actor, snapshot_scan_id=scan_old.id,
    )
    old_lic_ids = {r["license_id"] for r in items_old}
    assert lic_old.id in old_lic_ids
    assert lic_new.id not in old_lic_ids
