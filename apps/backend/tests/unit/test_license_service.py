"""
Backend service tests for ``services/license_service.py`` — Phase 3 PR #12.

Covers two entry points:

- :func:`list_project_licenses`
- :func:`get_license_finding_detail`

Plus the pure normalization helpers + ``_escape_like`` (re-imported from
``services.vulnerability_service``) regression for ``%`` / ``_`` collapse.

Mirrors :file:`tests/unit/test_vulnerability_service.py` structurally:

  - Pure cases (filter normalisation, search escape) run on every PR — no
    DB dependency, so they survive a downed local Postgres.
  - DB-backed cases are gated on ``DATABASE_URL`` + ``alembic upgrade head``
    via the ``integration`` marker. CI brings up a real Postgres testcontainer.
  - The ``_isolate_engine_per_test`` autouse fixture in tests/conftest.py
    keeps asyncpg's connection pool from leaking across the per-test event
    loop pytest-asyncio creates.

Read-only domain — no mutation cases.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog
import structlog.testing
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.sql_safety import escape_like as _escape_like
from services.license_service import (
    LicenseFindingNotFound,
    _normalize_category_filter,
    _normalize_kind_filter,
    get_license_finding_detail,
    list_project_licenses,
)
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
def test_normalize_category_filter(
    raw: list[str] | None, expected: list[str] | None
) -> None:
    assert _normalize_category_filter(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ([], []),
        (["declared"], ["declared"]),
        (["concluded", "detected"], ["concluded", "detected"]),
        (["declared", "BOGUS"], ["declared"]),
        (["BOGUS"], []),
    ],
)
def test_normalize_kind_filter(raw: list[str] | None, expected: list[str] | None) -> None:
    assert _normalize_kind_filter(raw) == expected


def test_escape_like_collapses_wildcards_to_literals() -> None:
    """
    Regression coverage for the ``%`` / ``_`` escape contract — license_service
    re-uses :func:`services.vulnerability_service._escape_like` so a search
    of bare ``%`` MUST be converted to ``\\%`` and matched literally.
    """
    assert _escape_like("foo") == "foo"
    assert _escape_like("50%") == r"50\%"
    assert _escape_like("a_b") == r"a\_b"
    # Backslash itself escapes so the ESCAPE clause sees a single backslash
    # exactly when one was supplied.
    assert _escape_like("a\\b") == r"a\\b"


# ---------------------------------------------------------------------------
# DB-backed tests start here — gated on DATABASE_URL.
# ---------------------------------------------------------------------------


pytestmark_db = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip license service DB tests")
    return url


@pytest.fixture(scope="module")
def _migrate_once() -> None:
    """Run alembic upgrade head once per module — only for tests that need DB.

    Not autouse because the pure cases above have no DB dependency. Tests
    that pull in ``db_session`` transitively activate this.
    """
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
            "alembic upgrade head failed; license service tests cannot run\n"
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
    is_osi_approved: bool = False,
    is_fsf_libre: bool = False,
    is_deprecated_license_id: bool = False,
    reference_url: str | None = None,
    review_flag: str | None = None,
):
    from models import License as LicenseModel

    suffix = unique_suffix()
    lic = LicenseModel(
        spdx_id=spdx_id if spdx_id is not None else f"SPDX-{suffix}",
        name=name or f"License {suffix}",
        category=category,
        is_osi_approved=is_osi_approved,
        is_fsf_libre=is_fsf_libre,
        is_deprecated_license_id=is_deprecated_license_id,
        reference_url=reference_url,
        review_flag=review_flag,
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
    source_path: str | None = None,
    raw_data: dict[str, object] | None = None,
):
    from models import LicenseFinding

    suffix = unique_suffix()
    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind=kind,
        source_path=source_path or f"path/{suffix}",
        raw_data=raw_data or {},
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf


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
# list_project_licenses — happy / pagination / filters / search / sort
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_list_returns_empty_when_project_has_no_latest_scan(
    db_session: AsyncSession,
) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    items, distribution, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor
    )
    assert items == []
    assert total == 0
    # All four buckets present, all zero — chart still renders.
    assert distribution == {
        "forbidden": 0,
        "conditional": 0,
        "allowed": 0,
        "unknown": 0,
    }


@pytestmark_db
async def test_list_happy_path_returns_items_distribution_and_total(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    # Two cv's with MIT (allowed), one cv with GPL-3.0 (forbidden).
    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    _, cv3 = await _make_component_version(db_session)
    mit = await _make_license(
        db_session, spdx_id=f"MIT-{suffix}", name="MIT", category="allowed"
    )
    gpl = await _make_license(
        db_session, spdx_id=f"GPL-3.0-{suffix}", name="GPL-3.0", category="forbidden"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=mit.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=mit.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv3.id, license_id=gpl.id)

    items, distribution, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor
    )
    assert total == 2
    # One row per license (aggregated). MIT has 2 affected cvs; GPL has 1.
    by_spdx = {row["spdx_id"]: row for row in items}
    assert by_spdx[f"MIT-{suffix}"]["affected_count"] == 2
    assert by_spdx[f"MIT-{suffix}"]["category"] == "allowed"
    assert by_spdx[f"GPL-3.0-{suffix}"]["affected_count"] == 1
    assert by_spdx[f"GPL-3.0-{suffix}"]["category"] == "forbidden"

    # Distribution always emits all four keys with non-zero where applicable.
    assert set(distribution.keys()) == {"forbidden", "conditional", "allowed", "unknown"}
    assert distribution["allowed"] == 2
    assert distribution["forbidden"] == 1
    assert distribution["conditional"] == 0
    assert distribution["unknown"] == 0


@pytestmark_db
async def test_list_paginates_and_returns_total(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    # 5 distinct licenses, 1 cv each.
    for i in range(5):
        _, cv = await _make_component_version(db_session)
        lic = await _make_license(
            db_session,
            spdx_id=f"PAG-{suffix}-{i}",
            name=f"Pag {suffix} {i}",
            category="allowed",
        )
        await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    p1, _, total1 = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, limit=2, offset=0
    )
    assert len(p1) == 2
    assert total1 == 5

    p2, _, total2 = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, limit=2, offset=2
    )
    assert len(p2) == 2
    assert total2 == 5
    # Stable tie-break → disjoint pages.
    assert {row["license_id"] for row in p1} & {row["license_id"] for row in p2} == set()


@pytestmark_db
async def test_list_filter_category_single_and_multi(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_a = await _make_component_version(db_session)
    _, cv_b = await _make_component_version(db_session)
    _, cv_c = await _make_component_version(db_session)
    forb = await _make_license(
        db_session, spdx_id=f"GPL-{suffix}", name="GPL", category="forbidden"
    )
    cond = await _make_license(
        db_session, spdx_id=f"LGPL-{suffix}", name="LGPL", category="conditional"
    )
    allow = await _make_license(
        db_session, spdx_id=f"MIT-{suffix}", name="MIT", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_a.id, license_id=forb.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_b.id, license_id=cond.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_c.id, license_id=allow.id)

    items, _, total = await list_project_licenses(
        db_session,
        project_id=project.id,
        actor=actor,
        categories=["forbidden"],
    )
    assert total == 1
    assert items[0]["category"] == "forbidden"

    items, _, total = await list_project_licenses(
        db_session,
        project_id=project.id,
        actor=actor,
        categories=["forbidden", "conditional"],
    )
    assert total == 2
    assert {r["category"] for r in items} == {"forbidden", "conditional"}


@pytestmark_db
async def test_list_filter_kind_single_and_multi(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_a = await _make_component_version(db_session)
    _, cv_b = await _make_component_version(db_session)
    declared_lic = await _make_license(
        db_session, spdx_id=f"DECL-{suffix}", name="Declared", category="allowed"
    )
    concluded_lic = await _make_license(
        db_session, spdx_id=f"CONC-{suffix}", name="Concluded", category="allowed"
    )
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv_a.id, license_id=declared_lic.id, kind="declared"
    )
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv_b.id, license_id=concluded_lic.id, kind="concluded"
    )

    items, _, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, kinds=["declared"]
    )
    assert total == 1
    assert items[0]["kind"] == "declared"

    items, _, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, kinds=["concluded"]
    )
    assert total == 1
    assert items[0]["kind"] == "concluded"


@pytestmark_db
async def test_list_search_matches_spdx_id_and_name_substring(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    needle = await _make_license(
        db_session, spdx_id=f"NDL-{suffix}", name=f"Needle license {suffix}", category="allowed"
    )
    decoy = await _make_license(
        db_session, spdx_id=f"DCY-{suffix}", name=f"Decoy {suffix}", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=needle.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=decoy.id)

    # Hit by spdx_id substring.
    items, _, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, search="NDL"
    )
    assert total == 1
    assert items[0]["spdx_id"] == f"NDL-{suffix}"

    # Hit by name substring.
    items, _, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, search="Needle"
    )
    assert total == 1
    assert items[0]["spdx_id"] == f"NDL-{suffix}"


@pytestmark_db
async def test_list_search_escapes_like_wildcards(
    db_session: AsyncSession,
) -> None:
    """A bare ``%`` search MUST NOT collapse to "match everything".

    Regression for the ``_escape_like`` integration shared with
    :file:`services/vulnerability_service.py` (PR #11). We seed one license
    whose name carries a literal ``%`` and one without; searching for ``%``
    must return only the literal-percent row.
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_pct = await _make_component_version(db_session)
    _, cv_nopct = await _make_component_version(db_session)
    pct = await _make_license(
        db_session,
        spdx_id=f"PCT-{suffix}",
        name=f"50% off license {suffix}",
        category="allowed",
    )
    nopct = await _make_license(
        db_session,
        spdx_id=f"NOPCT-{suffix}",
        name=f"plain license {suffix}",
        category="allowed",
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_pct.id, license_id=pct.id)
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv_nopct.id, license_id=nopct.id
    )

    items, _, total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, search="50%"
    )
    assert total == 1
    assert items[0]["spdx_id"] == f"PCT-{suffix}"


@pytestmark_db
async def test_list_sort_by_category_desc_puts_forbidden_first(
    db_session: AsyncSession,
) -> None:
    """Default sort=category, order=desc must surface forbidden before allowed.

    Rank: forbidden=3, conditional=2, allowed=1, unknown=0.
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_a = await _make_component_version(db_session)
    _, cv_b = await _make_component_version(db_session)
    _, cv_c = await _make_component_version(db_session)
    allow = await _make_license(
        db_session, spdx_id=f"AL-{suffix}", name="Allow", category="allowed"
    )
    forb = await _make_license(
        db_session, spdx_id=f"FB-{suffix}", name="Forbid", category="forbidden"
    )
    cond = await _make_license(
        db_session, spdx_id=f"CD-{suffix}", name="Cond", category="conditional"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_a.id, license_id=allow.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_b.id, license_id=forb.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_c.id, license_id=cond.id)

    items, _, _ = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, sort="category", order="desc"
    )
    cats = [r["category"] for r in items]
    assert cats.index("forbidden") < cats.index("conditional") < cats.index("allowed")


@pytestmark_db
async def test_list_sort_by_name_asc(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    for n in ("zeta", "alpha", "mu"):
        _, cv = await _make_component_version(db_session)
        lic = await _make_license(
            db_session, spdx_id=f"S-{n}-{suffix}", name=f"{n}-{suffix}", category="allowed"
        )
        await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    items, _, _ = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, sort="name", order="asc"
    )
    names = [r["name"] for r in items]
    assert names == sorted(names)


@pytestmark_db
async def test_list_sort_by_spdx_id_asc(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    for code in ("ZZZ", "AAA", "MMM"):
        _, cv = await _make_component_version(db_session)
        lic = await _make_license(
            db_session, spdx_id=f"{code}-{suffix}", name=f"name {code}", category="allowed"
        )
        await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    items, _, _ = await list_project_licenses(
        db_session, project_id=project.id, actor=actor, sort="spdx_id", order="asc"
    )
    spdx_values = [r["spdx_id"] for r in items]
    assert spdx_values == sorted(spdx_values)


@pytestmark_db
async def test_list_sort_by_affected_count_desc(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    big = await _make_license(
        db_session, spdx_id=f"BIG-{suffix}", name="big", category="allowed"
    )
    small = await _make_license(
        db_session, spdx_id=f"SML-{suffix}", name="small", category="allowed"
    )
    # `big` covers 3 cvs, `small` covers 1.
    for _ in range(3):
        _, cv = await _make_component_version(db_session)
        await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=big.id)
    _, cv = await _make_component_version(db_session)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=small.id)

    items, _, _ = await list_project_licenses(
        db_session,
        project_id=project.id,
        actor=actor,
        sort="affected_count",
        order="desc",
    )
    counts = [r["affected_count"] for r in items]
    # The license with 3 cvs comes before the one with 1.
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 3


@pytestmark_db
async def test_list_distribution_always_includes_all_four_buckets_with_zero(
    db_session: AsyncSession,
) -> None:
    """Even when a category has zero cvs, distribution must emit it as 0."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Seed only `allowed` rows so forbidden/conditional/unknown are zero.
    suffix = unique_suffix()
    lic = await _make_license(
        db_session, spdx_id=f"ZERO-{suffix}", name="zero", category="allowed"
    )
    _, cv = await _make_component_version(db_session)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    _, distribution, _ = await list_project_licenses(
        db_session, project_id=project.id, actor=actor
    )
    assert distribution == {
        "forbidden": 0,
        "conditional": 0,
        "allowed": 1,
        "unknown": 0,
    }


@pytestmark_db
async def test_list_idor_other_team_returns_403_and_logs(
    db_session: AsyncSession,
) -> None:
    """List endpoint surfaces 403 + emits ``authz.cross_team_attempt``."""
    from services.project_service import ProjectForbidden

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    # ``capture_logs()`` swaps the wrapper class so log calls are routed
    # through a list-collector regardless of when the cached logger proxy
    # materialised — same lesson learned in PR #11.
    with structlog.testing.capture_logs() as captured:
        with pytest.raises(ProjectForbidden):
            await list_project_licenses(
                db_session, project_id=project.id, actor=actor
            )

    assert any(
        evt.get("event") == "authz.cross_team_attempt"
        and evt.get("resource") == "project_licenses"
        for evt in captured
    )


@pytestmark_db
async def test_list_unknown_project_is_404(db_session: AsyncSession) -> None:
    from services.project_service import ProjectNotFound

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(ProjectNotFound):
        await list_project_licenses(
            db_session, project_id=uuid.uuid4(), actor=actor
        )


@pytestmark_db
async def test_list_invalid_sort_raises_license_error(
    db_session: AsyncSession,
) -> None:
    from services.license_service import LicenseError

    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(LicenseError):
        await list_project_licenses(
            db_session, project_id=project.id, actor=actor, sort="bogus"
        )


@pytestmark_db
async def test_list_invalid_order_raises_license_error(
    db_session: AsyncSession,
) -> None:
    from services.license_service import LicenseError

    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(LicenseError):
        await list_project_licenses(
            db_session, project_id=project.id, actor=actor, order="sideways"
        )


@pytestmark_db
async def test_list_empty_category_filter_after_normalization_returns_empty_items(
    db_session: AsyncSession,
) -> None:
    """If every value in `categories` is invalid, list returns no items.

    Distribution still reflects the underlying scan so the chart isn't
    zeroed out behind a stale filter.
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    lic = await _make_license(
        db_session, spdx_id=f"X-{suffix}", name="x", category="allowed"
    )
    _, cv = await _make_component_version(db_session)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)

    items, distribution, total = await list_project_licenses(
        db_session,
        project_id=project.id,
        actor=actor,
        categories=["nope", "BOGUS"],
    )
    assert items == []
    assert total == 0
    # Distribution was computed unfiltered.
    assert distribution["allowed"] >= 1


# ---------------------------------------------------------------------------
# get_license_finding_detail — happy + cross-team existence-hide
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_detail_returns_payload_with_meta_match_and_affected(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv1 = await _make_component_version(db_session, name=f"alpha-{suffix}")
    _, cv2 = await _make_component_version(db_session, name=f"bravo-{suffix}")
    lic = await _make_license(
        db_session,
        spdx_id=f"DET-{suffix}",
        name="Detail license",
        category="conditional",
        is_osi_approved=True,
        is_fsf_libre=False,
        reference_url="https://example.com/license",
        review_flag="behavioral_use",
    )
    raw = {"rule_name": "rule_x", "score": 0.97, "matched_text": "permission is granted"}
    finding = await _attach_license_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv1.id,
        license_id=lic.id,
        kind="concluded",
        raw_data=raw,
    )
    # Second cv with the same license — affected_components has ≥ 2 rows.
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv2.id, license_id=lic.id, kind="declared"
    )

    payload = await get_license_finding_detail(
        db_session, finding_id=finding.id, actor=actor
    )
    assert payload["id"] == finding.id
    assert payload["spdx_id"] == f"DET-{suffix}"
    assert payload["category"] == "conditional"
    assert payload["is_osi_approved"] is True
    assert payload["is_fsf_libre"] is False
    assert payload["reference_url"] == "https://example.com/license"
    # review_flag is exposed on the detail payload too (FE deep-link / reload:
    # the drawer must render the AI flag without depending on the list page).
    assert payload["review_flag"] == "behavioral_use"
    # raw_data passes through verbatim.
    assert payload["ort_match"] == raw
    # finding_kind reflects the row at finding.id, not the other cv.
    assert payload["finding_kind"] == "concluded"
    # Both cvs surface in affected_components.
    assert len(payload["affected_components"]) >= 2


@pytestmark_db
async def test_detail_with_null_raw_data_returns_none_ort_match(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session, spdx_id=f"NRAW-{suffix}", name="no raw", category="allowed"
    )
    finding = await _attach_license_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        license_id=lic.id,
        raw_data={},  # default server_default is empty JSONB obj
    )

    payload = await get_license_finding_detail(
        db_session, finding_id=finding.id, actor=actor
    )
    # Empty dict is falsy → None.
    assert payload["ort_match"] is None
    # Ordinary license carries no AI review flag.
    assert payload["review_flag"] is None


@pytestmark_db
async def test_detail_unknown_finding_id_is_404(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    with pytest.raises(LicenseFindingNotFound):
        await get_license_finding_detail(
            db_session, finding_id=uuid.uuid4(), actor=actor
        )


@pytestmark_db
async def test_detail_other_team_user_gets_404_not_403(
    db_session: AsyncSession,
) -> None:
    """We hide existence of cross-team license findings rather than 403.

    Mirrors the PR #11 vulnerability drawer policy. Verifies that the
    ``authz.cross_team_attempt`` warning fires before the existence-hide.
    """
    _, _, _, scan = await _make_project_with_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session, spdx_id=f"CR-{suffix}", name="cross-team", category="allowed"
    )
    finding = await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
    )

    org2 = await make_organization(db_session)
    other_team = await make_team(db_session, organization=org2)
    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=other_team, role="developer")
    actor = principal_for(outsider, team_ids=[other_team.id], role="developer")

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(LicenseFindingNotFound):
            await get_license_finding_detail(
                db_session, finding_id=finding.id, actor=actor
            )

    assert any(
        evt.get("event") == "authz.cross_team_attempt"
        and evt.get("resource") == "license_finding"
        for evt in captured
    )
