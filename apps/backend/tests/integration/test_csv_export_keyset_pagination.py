# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Keyset pagination for the CSV export path (#463).

``_stream``'s own unit tests (``tests/unit/services/test_table_export_service.py``)
already cover the walk mechanics against a fake list service. These prove
the real thing: that ``list_project_vulnerabilities`` /
``list_components_for_project`` / ``list_inventory_components``'s
``keyset=True`` branch, wired through the real ``stream_*_csv`` closures
against real Postgres, visits every row exactly once (no skip, no repeat)
and still respects the caller's filters, the whole reason
``table_export_service.py`` pages the list service instead of a second
query (see its module docstring).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.inventory_service import list_inventory_components
from services.project_detail_service import list_components_for_project
from services.table_export_service import stream_vulnerabilities_csv
from services.vulnerability_service import list_project_vulnerabilities
from tests._db_required import migrate_to_head
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


async def _seed_project_with_findings(
    session: AsyncSession, *, count: int, severity: str = "high"
):
    """One project/scan with `count` findings, each on its own component_version.

    `flush()` per row (not `commit()`) assigns PKs without the per-row
    round trip `_seed_finding`-style helpers pay elsewhere in this suite;
    the single `commit()` at the end is what actually persists everything.
    """
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    project.updated_at = datetime.now(tz=UTC)
    await session.flush()

    finding_ids: set[uuid.UUID] = set()
    cv_ids: set[uuid.UUID] = set()
    for _ in range(count):
        suffix = unique_suffix()
        purl = f"pkg:npm/keyset-{suffix}"
        component = Component(purl=purl, package_type="npm", name=f"keyset-{suffix}")
        session.add(component)
        await session.flush()
        cv = ComponentVersion(
            component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
        )
        session.add(cv)
        await session.flush()
        session.add(
            ScanComponent(scan_id=scan.id, component_version_id=cv.id, direct=True, raw_data={})
        )
        vuln = Vulnerability(
            external_id=f"CVE-2099-{suffix}", source="NVD", severity=severity, summary="keyset test"
        )
        session.add(vuln)
        await session.flush()
        finding = VulnerabilityFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status="new",
            analysis_state="new",
        )
        session.add(finding)
        await session.flush()
        finding_ids.add(finding.id)
        cv_ids.add(cv.id)

    await session.commit()
    actor = principal_for(user, team_ids=[team.id], role="developer")
    return project, actor, finding_ids, cv_ids


async def _walk_vulnerabilities_keyset(session, *, project_id, actor, page_size):
    seen: list[uuid.UUID] = []
    after_id = None
    while True:
        items, _total, _dist = await list_project_vulnerabilities(
            session,
            project_id=project_id,
            actor=actor,
            limit=page_size,
            keyset=True,
            after_id=after_id,
        )
        if not items:
            break
        seen.extend(item["id"] for item in items)
        after_id = items[-1]["id"]
    return seen


async def test_vulnerabilities_keyset_walk_visits_every_row_exactly_once(
    db_session: AsyncSession,
) -> None:
    project, actor, finding_ids, _cv_ids = await _seed_project_with_findings(
        db_session, count=37
    )

    seen = await _walk_vulnerabilities_keyset(
        db_session, project_id=project.id, actor=actor, page_size=10
    )

    assert len(seen) == 37
    assert len(set(seen)) == 37, "the keyset walk repeated at least one row"
    assert set(seen) == finding_ids, "the keyset walk skipped or invented a row"


async def test_vulnerabilities_keyset_walk_still_applies_filters(
    db_session: AsyncSession,
) -> None:
    """Filters cannot drift between the OFFSET and keyset branches: same
    `base` query, only the final ORDER BY / pagination step differs."""
    project, actor, _finding_ids, _cv_ids = await _seed_project_with_findings(
        db_session, count=5, severity="critical"
    )
    _p2, actor2, low_ids, _cv2 = await _seed_project_with_findings(
        db_session, count=3, severity="low"
    )

    critical_seen = await _walk_vulnerabilities_keyset(
        db_session, project_id=project.id, actor=actor, page_size=2
    )
    assert len(critical_seen) == 5

    items, total, _dist = await list_project_vulnerabilities(
        db_session,
        project_id=project.id,
        actor=actor,
        limit=10,
        keyset=True,
        severity=["low"],
    )
    assert items == []
    assert total == 0
    assert set(critical_seen).isdisjoint(low_ids)


async def test_components_keyset_walk_visits_every_row_exactly_once(
    db_session: AsyncSession,
) -> None:
    project, actor, _finding_ids, cv_ids = await _seed_project_with_findings(
        db_session, count=23
    )

    seen: list[uuid.UUID] = []
    after_id = None
    while True:
        items, _total = await list_components_for_project(
            db_session,
            project_id=project.id,
            actor=actor,
            limit=7,
            keyset=True,
            after_id=after_id,
        )
        if not items:
            break
        seen.extend(item["id"] for item in items)
        after_id = items[-1]["id"]

    assert len(seen) == 23
    assert len(set(seen)) == 23, "the keyset walk repeated at least one component_version"
    assert set(seen) == cv_ids


async def test_inventory_keyset_walk_visits_every_row_exactly_once(
    db_session: AsyncSession,
) -> None:
    _project, actor, _finding_ids, cv_ids = await _seed_project_with_findings(
        db_session, count=19
    )
    # component_id != component_version_id; the inventory rollup is one row
    # per Component, and this fixture's components are 1:1 with cvs (each
    # loop iteration makes a fresh Component), so the expected COUNT matches.
    expected_count = len(cv_ids)

    seen: list[uuid.UUID] = []
    after_id = None
    while True:
        page = await list_inventory_components(
            db_session,
            actor=actor,
            limit=6,
            keyset=True,
            after_id=after_id,
        )
        if not page.items:
            break
        seen.extend(row.component_id for row in page.items)
        after_id = page.items[-1].component_id

    assert len(seen) == expected_count
    assert len(set(seen)) == expected_count, "the keyset walk repeated at least one component"


async def test_vulnerabilities_csv_export_walks_multiple_keyset_pages(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the real `stream_vulnerabilities_csv` closure,
    not just the list-service call it wraps, proves the wiring
    (`fetch_page` threading `after_id` back into `list_project_
    vulnerabilities`, `_stream` threading it into the next `fetch_page`
    call) and not only the underlying keyset SQL."""
    import services.table_export_service as svc

    monkeypatch.setattr(svc, "CSV_STREAM_CHUNK_ROWS", 5)

    project, actor, finding_ids, _cv_ids = await _seed_project_with_findings(
        db_session, count=17
    )

    body = "".join(
        [
            chunk
            async for chunk in stream_vulnerabilities_csv(
                db_session,
                project_id=project.id,
                actor=actor,
                filters={
                    "search": None,
                    "severity": None,
                    "status": None,
                    "license_category": None,
                    "min_epss": None,
                    "reachable": None,
                    "sla": None,
                    "assignee": None,
                    "sort": "severity",
                    "order": "desc",
                    "snapshot_scan_id": None,
                },
            )
        ]
    )

    lines = body.splitlines()
    assert lines[-1] == "# rows: 17"
    # header + 17 data rows + trailer.
    assert len(lines) == 19
    finding_id_column = lines[0].lstrip("﻿").split(",").index("finding_id")
    exported_ids = {row.split(",")[finding_id_column] for row in lines[1:-1]}
    assert exported_ids == {str(fid) for fid in finding_ids}
