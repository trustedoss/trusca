"""
DB-backed service tests for `services/dependency_graph_service.py` — BomLens H-1.

Covers :func:`services.dependency_graph_service.get_dependency_graph`, the
serializer behind ``GET /v1/projects/{id}/dependency-graph``.

Structure mirrors ``test_project_detail_service.py`` (integration mark + one-time
alembic upgrade) because the graph shape depends on the live Postgres schema
(``component_dependency_edges`` FK/indexes, the vuln-severity ENUM CASE, the
``bool_or`` / ``MIN`` aggregates). Mocking the DB would test the mock, not the
contract.

Scenarios (task DoD):
  - a realistic graph with a CYCLE (A→B→C→A) and an ORPHAN node (in the scan but
    touched by no edge): node_count / edge_count exact, per-node direct / depth /
    vulnerability_count / max_severity, and every edge's source / target.
  - a DIAMOND node reached at two dependency paths: depth collapses to the
    shallowest (MIN), direct OR's the flags, and the vulnerability count does NOT
    double from the extra scan_components row (cartesian guard).
  - node-cap exceeded (env monkeypatched low): truncated=true, nodes/edges EMPTY,
    counts still exact.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.dependency_graph_service import get_dependency_graph
from services.project_service import ProjectNotFound
from services.scan_resolution import SnapshotScanNotFound
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

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip dependency-graph service tests")
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
            f"alembic upgrade head failed; dependency-graph tests cannot run\n"
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
# Local factories
# ---------------------------------------------------------------------------


async def _make_cv(
    session: AsyncSession,
    *,
    name: str,
    version: str = "1.0.0",
    namespace: str | None = None,
    package_type: str = "npm",
):
    from models import Component, ComponentVersion

    purl = f"pkg:{package_type}/{name}"
    component = Component(
        purl=purl, package_type=package_type, name=name, namespace=namespace
    )
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
    return cv


async def _attach(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    direct: bool,
    depth: int | None,
    dependency_path: str | None = None,
) -> None:
    from models import ScanComponent

    session.add(
        ScanComponent(
            scan_id=scan_id,
            component_version_id=cv_id,
            direct=direct,
            depth=depth,
            dependency_path=dependency_path,
            raw_data={},
        )
    )
    await session.commit()


async def _edge(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    parent_cv_id: uuid.UUID,
    child_cv_id: uuid.UUID,
) -> None:
    from models import ComponentDependencyEdge

    session.add(
        ComponentDependencyEdge(
            scan_id=scan_id,
            parent_component_version_id=parent_cv_id,
            child_component_version_id=child_cv_id,
        )
    )
    await session.commit()


async def _make_vuln(session: AsyncSession, *, severity: str):
    from models import Vulnerability

    suffix = unique_suffix()
    v = Vulnerability(
        external_id=f"CVE-2024-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"vuln {suffix}",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vulnerability_id: uuid.UUID,
) -> None:
    from models import VulnerabilityFinding

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv_id,
            vulnerability_id=vulnerability_id,
        )
    )
    await session.commit()


async def _seed_team_project_scan(session: AsyncSession, *, role: str = "developer"):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role=role)
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    return team, user, project, scan


# ---------------------------------------------------------------------------
# Happy path — cycle + orphan node + per-node vuln aggregation
# ---------------------------------------------------------------------------


async def test_graph_cycle_and_orphan_and_vuln_aggregation(db_session) -> None:
    team, user, project, scan = await _seed_team_project_scan(db_session)
    suffix = unique_suffix()

    a = await _make_cv(db_session, name=f"app-{suffix}", version="1.0.0")
    b = await _make_cv(db_session, name=f"beta-{suffix}", version="2.0.0")
    c = await _make_cv(
        db_session,
        name=f"log4j-{suffix}",
        version="2.14.1",
        namespace="org.apache.logging.log4j",
        package_type="maven",
    )
    d = await _make_cv(db_session, name=f"orphan-{suffix}", version="9.9.9")

    # A is a direct root; B/C transitive; D an orphan (in the scan, no edges).
    await _attach(db_session, scan_id=scan.id, cv_id=a.id, direct=True, depth=1)
    await _attach(db_session, scan_id=scan.id, cv_id=b.id, direct=False, depth=2)
    await _attach(db_session, scan_id=scan.id, cv_id=c.id, direct=False, depth=2)
    await _attach(db_session, scan_id=scan.id, cv_id=d.id, direct=False, depth=None)

    # Cycle: A→B→C→A, plus a chord A→C (so C has two incoming edges).
    await _edge(db_session, scan_id=scan.id, parent_cv_id=a.id, child_cv_id=b.id)
    await _edge(db_session, scan_id=scan.id, parent_cv_id=b.id, child_cv_id=c.id)
    await _edge(db_session, scan_id=scan.id, parent_cv_id=c.id, child_cv_id=a.id)
    await _edge(db_session, scan_id=scan.id, parent_cv_id=a.id, child_cv_id=c.id)

    # B has a high CVE; C a critical CVE; A/D none.
    high = await _make_vuln(db_session, severity="high")
    crit = await _make_vuln(db_session, severity="critical")
    await _finding(db_session, scan_id=scan.id, cv_id=b.id, vulnerability_id=high.id)
    await _finding(db_session, scan_id=scan.id, cv_id=c.id, vulnerability_id=crit.id)

    actor = principal_for(user, team_ids=[team.id], role="developer")
    graph = await get_dependency_graph(
        db_session, project_id=project.id, actor=actor
    )

    assert graph["scan_id"] == scan.id
    assert graph["node_count"] == 4
    assert graph["edge_count"] == 4
    assert graph["node_cap"] == 5000
    assert graph["truncated"] is False

    nodes = {n["id"]: n for n in graph["nodes"]}
    assert set(nodes) == {str(a.id), str(b.id), str(c.id), str(d.id)}

    na = nodes[str(a.id)]
    assert na["direct"] is True and na["depth"] == 1
    assert na["vulnerability_count"] == 0 and na["max_severity"] == "none"

    nb = nodes[str(b.id)]
    assert nb["direct"] is False and nb["depth"] == 2
    assert nb["vulnerability_count"] == 1 and nb["max_severity"] == "high"

    nc = nodes[str(c.id)]
    assert nc["namespace"] == "org.apache.logging.log4j"
    assert nc["vulnerability_count"] == 1 and nc["max_severity"] == "critical"
    assert nc["purl"].endswith("@2.14.1")

    nd = nodes[str(d.id)]
    assert nd["direct"] is False and nd["depth"] is None
    assert nd["vulnerability_count"] == 0 and nd["max_severity"] == "none"

    edge_set = {(e["source"], e["target"]) for e in graph["edges"]}
    assert edge_set == {
        (str(a.id), str(b.id)),
        (str(b.id), str(c.id)),
        (str(c.id), str(a.id)),
        (str(a.id), str(c.id)),
    }


# ---------------------------------------------------------------------------
# Diamond node — shallowest depth, OR'd direct, no vuln double-count
# ---------------------------------------------------------------------------


async def test_graph_diamond_node_collapses_paths_without_overcount(db_session) -> None:
    team, user, project, scan = await _seed_team_project_scan(db_session)
    suffix = unique_suffix()

    shared = await _make_cv(db_session, name=f"shared-{suffix}", version="1.0.0")

    # Same cv on TWO dependency paths: one transitive (depth 3), one direct
    # (depth 1). Distinct dependency_path keys so both scan_components rows persist.
    await _attach(
        db_session,
        scan_id=scan.id,
        cv_id=shared.id,
        direct=False,
        depth=3,
        dependency_path="root>a>shared",
    )
    await _attach(
        db_session,
        scan_id=scan.id,
        cv_id=shared.id,
        direct=True,
        depth=1,
        dependency_path="root>shared",
    )

    # One CVE, one finding — the extra scan_components row must NOT inflate it.
    vuln = await _make_vuln(db_session, severity="medium")
    await _finding(
        db_session, scan_id=scan.id, cv_id=shared.id, vulnerability_id=vuln.id
    )

    actor = principal_for(user, team_ids=[team.id], role="developer")
    graph = await get_dependency_graph(
        db_session, project_id=project.id, actor=actor
    )

    assert graph["node_count"] == 1
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["depth"] == 1  # shallowest across the two paths
    assert node["direct"] is True  # OR of the per-path flags
    assert node["vulnerability_count"] == 1  # cartesian guard held
    assert node["max_severity"] == "medium"


# ---------------------------------------------------------------------------
# Node cap exceeded — truncated, empty lists, exact counts
# ---------------------------------------------------------------------------


async def test_graph_truncates_when_over_node_cap(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    team, user, project, scan = await _seed_team_project_scan(db_session)
    suffix = unique_suffix()

    # Two nodes + one edge; cap set to 1 so node_count (2) exceeds it.
    p = await _make_cv(db_session, name=f"parent-{suffix}")
    ch = await _make_cv(db_session, name=f"child-{suffix}")
    await _attach(db_session, scan_id=scan.id, cv_id=p.id, direct=True, depth=1)
    await _attach(db_session, scan_id=scan.id, cv_id=ch.id, direct=False, depth=2)
    await _edge(db_session, scan_id=scan.id, parent_cv_id=p.id, child_cv_id=ch.id)

    monkeypatch.setenv("DEPENDENCY_GRAPH_MAX_NODES", "1")

    actor = principal_for(user, team_ids=[team.id], role="developer")
    graph = await get_dependency_graph(
        db_session, project_id=project.id, actor=actor
    )

    assert graph["node_cap"] == 1
    assert graph["truncated"] is True
    # Counts are EXACT even when truncated.
    assert graph["node_count"] == 2
    assert graph["edge_count"] == 1
    # Heavy enumerations skipped.
    assert graph["nodes"] == []
    assert graph["edges"] == []


# ---------------------------------------------------------------------------
# Authorization + snapshot resolution guards (service layer)
# ---------------------------------------------------------------------------


async def test_graph_non_member_is_existence_hidden_404(db_session) -> None:
    team, _owner, project, scan = await _seed_team_project_scan(db_session)
    await _attach(
        db_session,
        scan_id=scan.id,
        cv_id=(await _make_cv(db_session, name=f"x-{unique_suffix()}")).id,
        direct=True,
        depth=1,
    )
    outsider = await make_user(db_session)
    # Outsider holds NO membership in the owning team → existence-hide 404
    # (ProjectNotFound), never 403.
    actor = principal_for(outsider, team_ids=[], role="developer")
    with pytest.raises(ProjectNotFound):
        await get_dependency_graph(db_session, project_id=project.id, actor=actor)


async def test_graph_super_admin_bypasses_membership(db_session) -> None:
    _team, _owner, project, scan = await _seed_team_project_scan(db_session)
    await _attach(
        db_session,
        scan_id=scan.id,
        cv_id=(await _make_cv(db_session, name=f"y-{unique_suffix()}")).id,
        direct=True,
        depth=1,
    )
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    graph = await get_dependency_graph(db_session, project_id=project.id, actor=actor)
    assert graph["scan_id"] == scan.id
    assert graph["node_count"] == 1


async def test_graph_missing_project_is_404(db_session) -> None:
    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[], role="developer")
    with pytest.raises(ProjectNotFound):
        await get_dependency_graph(
            db_session, project_id=uuid.uuid4(), actor=actor
        )


async def test_graph_no_succeeded_scan_is_snapshot_not_found(db_session) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    # Only a FAILED scan exists → no succeeded snapshot to read.
    await make_scan(db_session, project=project, status="failed")
    actor = principal_for(user, team_ids=[team.id], role="developer")
    with pytest.raises(SnapshotScanNotFound):
        await get_dependency_graph(db_session, project_id=project.id, actor=actor)


async def test_graph_cross_project_scan_id_is_snapshot_not_found(db_session) -> None:
    team, user, project, _scan = await _seed_team_project_scan(db_session)
    # A succeeded scan of a DIFFERENT project must not be pinnable here.
    _t2, _u2, _p2, foreign_scan = await _seed_team_project_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    with pytest.raises(SnapshotScanNotFound):
        await get_dependency_graph(
            db_session, project_id=project.id, actor=actor, scan_id=foreign_scan.id
        )
