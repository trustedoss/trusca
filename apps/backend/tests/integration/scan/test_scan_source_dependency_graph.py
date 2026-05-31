"""
Integration — dependency-graph ingest end to end (v2.2 2.2-a2).

Drives ``tasks.scan_source.scan_source_task`` against a real Postgres with a
monkeypatched cdxgen that emits an SBOM carrying a ``dependencies`` graph. We
assert that after a succeeded scan:

  * ``scan_components.depth`` is stamped (1 = direct, 2+ = transitive) and the
    ``direct`` flag agrees,
  * ``component_dependency_edges`` holds one row per RESOLVED parent/child edge,
  * a re-run on the same scan_id is idempotent — depths + edges do not double.

Requires DATABASE_URL (dev Postgres). Skips cleanly when unset. The DT client +
breaker are faked so no network / Redis is touched.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from integrations.cdxgen import CdxgenResult
from models import (
    Component,
    ComponentDependencyEdge,
    ComponentVersion,
    Scan,
    ScanComponent,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip dependency-graph integration")
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
            f"alembic upgrade head failed; dependency-graph integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def sync_session() -> Iterator[Session]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# A purl namespace unique to this test so its components / versions never collide
# with another suite's ``uq_components_purl`` / ``uq_component_versions`` rows.
_NS = "depgraphit"


def _graph_sbom(app_ref: str) -> tuple[dict[str, object], tuple[str, str, str]]:
    """SBOM with metadata.component root + a small 3-level dependency graph.

    app → a, b  (direct, depth 1)
    a   → c      (transitive, depth 2)
    + a dangling child "ghost" that is NOT a component → edge dropped.
    """
    a = f"pkg:npm/{_NS}-a@1.0.0"
    b = f"pkg:npm/{_NS}-b@1.0.0"
    c = f"pkg:npm/{_NS}-c@1.0.0"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {"component": {"type": "application", "name": "root", "bom-ref": app_ref}},
        "components": [
            {"type": "library", "bom-ref": a, "name": f"{_NS}-a", "version": "1.0.0", "purl": a},
            {"type": "library", "bom-ref": b, "name": f"{_NS}-b", "version": "1.0.0", "purl": b},
            {"type": "library", "bom-ref": c, "name": f"{_NS}-c", "version": "1.0.0", "purl": c},
        ],
        "dependencies": [
            {"ref": app_ref, "dependsOn": [a, b]},
            {"ref": a, "dependsOn": [c, f"pkg:npm/{_NS}-ghost@9"]},  # ghost = dangling
            {"ref": b, "dependsOn": []},
            {"ref": c, "dependsOn": []},
        ],
    }, (a, b, c)


def _seed_queued_scan(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from core.config import database_url
        from models import Scan as ScanModel

        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
            scan = ScanModel(
                project_id=project.id,
                kind="source",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            ids = (scan.id, project.id)
        await engine.dispose()
        return ids

    return asyncio.run(_build())


def _stub_trivy_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """W6: replace ``run_trivy_sbom`` with an empty-report stub."""
    from integrations.trivy import TrivyResult

    def _fake_run(
        sbom_path: Path,  # noqa: ARG001
        output_dir: Path,
        *,
        timeout_seconds: int = 0,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
        # feat/scan-log-verbosity threads line_callback + verbose into
        # run_trivy_sbom; absorb them so this stub matches the new signature.
        **_kwargs: object,  # noqa: ARG001
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report = {"SchemaVersion": 2, "ArtifactType": "cyclonedx", "Results": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake_run)


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sbom: dict[str, object]
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_empty(monkeypatch)

    def _fake_run_cdxgen(
        *, source_dir: Path, output_dir: Path, **_kwargs: object
    ) -> CdxgenResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        sbom_path = output_dir / "cdxgen.cdx.json"
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        return CdxgenResult(sbom_path=sbom_path, sbom=sbom)

    monkeypatch.setattr("tasks.scan_source.cdxgen_adapter.run_cdxgen", _fake_run_cdxgen)
    # scancode is best-effort; force-skip so the test stays focused on the graph.
    import integrations.scancode as scancode_adapter

    def _skip_scancode(*, source_dir: Path, output_dir: Path, **_kwargs: object):  # noqa: ARG001
        raise scancode_adapter.ScancodeError("skipped for graph test")

    monkeypatch.setattr("tasks.scan_source.scancode_adapter.run_scancode", _skip_scancode)


def _cleanup(session: Session, purls: tuple[str, ...]) -> None:
    # Best-effort teardown of this suite's components so re-runs stay isolated.
    cv_ids = [
        r[0]
        for r in session.execute(
            select(ComponentVersion.id)
            .join(Component, Component.id == ComponentVersion.component_id)
            .where(Component.purl.like(f"pkg:npm/{_NS}-%"))
        ).all()
    ]
    if cv_ids:
        session.execute(
            delete(ComponentVersion).where(ComponentVersion.id.in_(cv_ids))
        )
    session.execute(delete(Component).where(Component.purl.like(f"pkg:npm/{_NS}-%")))
    session.commit()


def test_graph_depth_and_edges_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    app_ref = f"pkg:npm/{_NS}-root@1.0.0"
    sbom, (a, b, c) = _graph_sbom(app_ref)
    _patch_pipeline(monkeypatch, tmp_path, sbom)

    scan_id, _project_id = _seed_queued_scan(sync_session)
    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    # depth + direct per component (joined to purl for readability).
    rows = sync_session.execute(
        select(ComponentVersion.purl_with_version, ScanComponent.depth, ScanComponent.direct)
        .join(ScanComponent, ScanComponent.component_version_id == ComponentVersion.id)
        .where(ScanComponent.scan_id == scan_id)
    ).all()
    by_purl = {r[0]: (r[1], r[2]) for r in rows}

    assert by_purl[a] == (1, True), "a is a direct dep (depth 1)"
    assert by_purl[b] == (1, True), "b is a direct dep (depth 1)"
    assert by_purl[c] == (2, False), "c is transitive (depth 2)"

    # Edges: a→c resolves; app→a / app→b dropped (app not a persisted comp);
    # a→ghost dropped (dangling).
    edge_rows = sync_session.execute(
        select(ComponentDependencyEdge).where(ComponentDependencyEdge.scan_id == scan_id)
    ).scalars().all()
    cv_by_purl = {
        r[0]: r[1]
        for r in sync_session.execute(
            select(ComponentVersion.purl_with_version, ComponentVersion.id).where(
                ComponentVersion.purl_with_version.in_([a, b, c])
            )
        ).all()
    }
    edge_pairs = {(e.parent_component_version_id, e.child_component_version_id) for e in edge_rows}
    assert edge_pairs == {(cv_by_purl[a], cv_by_purl[c])}

    _cleanup(sync_session, (a, b, c))


def test_graph_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    app_ref = f"pkg:npm/{_NS}-root@1.0.0"
    sbom, (a, b, c) = _graph_sbom(app_ref)
    _patch_pipeline(monkeypatch, tmp_path, sbom)

    scan_id, _project_id = _seed_queued_scan(sync_session)
    from tasks.scan_source import scan_source_task

    # First run.
    assert scan_source_task.apply(args=[str(scan_id)]).successful()

    # Force a re-run: flip the scan back to queued so the task does NOT
    # short-circuit on 'succeeded' (it goes through _reset_scan_for_rerun).
    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    scan.status = "queued"
    sync_session.commit()

    assert scan_source_task.apply(args=[str(scan_id)]).successful()

    sync_session.expire_all()
    # Exactly one edge after the re-run (no doubling — reset deletes prior edges).
    edge_count = len(
        sync_session.execute(
            select(ComponentDependencyEdge).where(
                ComponentDependencyEdge.scan_id == scan_id
            )
        ).scalars().all()
    )
    assert edge_count == 1, f"re-run must not duplicate edges; got {edge_count}"

    # depths still correct (one ScanComponent per cv).
    depth_rows = sync_session.execute(
        select(ComponentVersion.purl_with_version, ScanComponent.depth)
        .join(ScanComponent, ScanComponent.component_version_id == ComponentVersion.id)
        .where(ScanComponent.scan_id == scan_id)
    ).all()
    depths = {r[0]: r[1] for r in depth_rows}
    assert depths[a] == 1 and depths[c] == 2

    _cleanup(sync_session, (a, b, c))
