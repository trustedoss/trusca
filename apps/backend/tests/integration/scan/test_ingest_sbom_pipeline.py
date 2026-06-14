"""
End-to-end SBOM-ingest Celery task pipeline — realistic-density fixture + stub Trivy.

We drive ``tasks.ingest_sbom.ingest_sbom_task`` directly (NOT through Celery's
broker) against a queued ``kind="sbom"`` scan whose ``scan_metadata["sbom_path"]``
points at a REAL-density CycloneDX document on disk under WORKSPACE_HOST_PATH.
``run_trivy_sbom`` is monkeypatched to return a hand-recorded Trivy ``sbom``
report whose Results mirror the fixture's packages with MULTIPLE CVEs per
package (CLAUDE.md §2 rule 3: realistic density — not a synthetic 1-CVE blob).

What we pin (the back-half of the source pipeline, reused for ingest):
  - components stage: ``persist_sbom_components`` populates Component /
    ComponentVersion rows + ScanComponent edges + declared LicenseFinding rows
    from the uploaded SBOM (multiple ecosystems, nested + dependencies).
  - trivy stage: ``persist_trivy_findings`` matches the dense report to the
    persisted ComponentVersions by PURL and writes VulnerabilityFinding rows —
    multiple findings against a single component version (lodash → 3 CVEs).
  - finalize: ``mark_succeeded`` flips status='succeeded', progress=100,
    current_step='finalize', completed_at set; the durable SBOM ScanArtifact
    (kind 'sbom_cyclonedx') is preserved.
  - ref-keyed supersede: an older succeeded scan on the same ref is superseded.

These mirror the source-pipeline integration tests
(``test_scan_source_pipeline_mock.py``) but exercise the ingest task's
condensed stage set (no fetch / cdxgen / scancode / sign).
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from integrations.trivy import TrivyResult
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    SbomConformance,
    Scan,
    ScanArtifact,
    ScanComponent,
    VulnerabilityFinding,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "sbom_ingest"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip ingest_sbom pipeline integration")
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
            f"alembic upgrade head failed; ingest pipeline integration cannot run\n"
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


def _stub_trivy_from_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``run_trivy_sbom`` with a stub that emits the recorded dense
    Trivy ``sbom`` report fixture (multiple CVEs per package)."""
    report = json.loads((FIXTURES / "realistic-trivy-sbom.json").read_text())

    def _fake_run(
        sbom_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: int = 0,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
        **_kwargs: object,  # noqa: ARG001
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report["ArtifactName"] = str(sbom_path)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.ingest_sbom.run_trivy_sbom", _fake_run)


def _seed_queued_sbom_scan(
    workspace: Path, *, ref: str | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed project + queued sbom scan and write the realistic SBOM to its
    durable on-disk ingest path. Returns (scan_id, project_id)."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    sbom_bytes = (FIXTURES / "realistic.cdx.json").read_bytes()

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
            scan = Scan(
                project_id=project.id,
                kind="sbom",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                ref=ref,
                scan_metadata={"source_type": "sbom"},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
            project_id = project.id
            # Write the durable SBOM at the path the service stamps, then store
            # that path in scan_metadata (the task reads it from there).
            ingest_dir = workspace / "sbom-ingest" / str(project_id)
            ingest_dir.mkdir(parents=True, exist_ok=True)
            dest = ingest_dir / f"{scan_id}.cdx.json"
            dest.write_bytes(sbom_bytes)
            scan.scan_metadata = {"source_type": "sbom", "sbom_path": str(dest)}
            await s.commit()
        await engine.dispose()
        return scan_id, project_id

    return asyncio.run(_build())


def _findings_for_scan(session: Session, scan_id: uuid.UUID) -> list[VulnerabilityFinding]:
    return list(
        session.execute(
            select(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan_id)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Happy path — full ingest pipeline with realistic density
# ---------------------------------------------------------------------------


def test_ingest_pipeline_persists_components_and_dense_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_from_fixture(monkeypatch)

    scan_id, _project_id = _seed_queued_sbom_scan(tmp_path)

    from tasks.ingest_sbom import ingest_sbom_task

    result = ingest_sbom_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100
    assert scan.current_step == "finalize"
    assert scan.completed_at is not None
    assert scan.error_message is None

    # Components: the fixture declares 4 top-level + 1 nested = 5 component
    # purls (lodash, minimist, conditional-lib, nested-transitive, jinja2). The
    # ingest persister records ScanComponent edges for the uploaded graph.
    component_rows = list(
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        ).scalars()
    )
    assert len(component_rows) >= 4, (
        f"expected the multi-ecosystem fixture's components persisted; "
        f"got {len(component_rows)}"
    )

    # Declared licenses: at least the MPL-2.0 conditional + MIT permissive +
    # BSD jinja2 declared findings exist (declared kind).
    declared = list(
        sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.scan_id == scan_id, LicenseFinding.kind == "declared"
            )
        ).scalars()
    )
    assert declared, "declared license findings must be persisted from the SBOM"

    # Vulnerabilities: the dense report carries 3 lodash CVEs + 1 minimist + 1
    # jinja2 = 5 findings, ALL matched by PURL to persisted ComponentVersions.
    findings = _findings_for_scan(sync_session, scan_id)
    assert len(findings) == 5, (
        f"realistic density: 5 findings (lodash×3, minimist×1, jinja2×1); "
        f"got {len(findings)}"
    )

    # Multiple CVEs against ONE component version — the density rule 3 case that
    # a synthetic 1-CVE fixture would miss.
    lodash = sync_session.execute(
        select(ComponentVersion)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(Component.purl == "pkg:npm/lodash")
    ).scalar_one_or_none()
    assert lodash is not None, "lodash component version must be persisted"
    lodash_findings = sync_session.execute(
        select(func.count())
        .select_from(VulnerabilityFinding)
        .where(
            VulnerabilityFinding.scan_id == scan_id,
            VulnerabilityFinding.component_version_id == lodash.id,
        )
    ).scalar_one()
    assert lodash_findings == 3, f"lodash must carry 3 CVEs; got {lodash_findings}"

    # The durable SBOM artifact is preserved (download surface).
    kinds = {
        a.kind
        for a in sync_session.execute(
            select(ScanArtifact).where(ScanArtifact.scan_id == scan_id)
        ).scalars()
    }
    assert "sbom_cyclonedx" in kinds

    # Conformance: scored on the ORIGINAL uploaded bytes. The realistic fixture
    # is a well-formed CycloneDX (full PURLs + graph + licenses) but carries no
    # component hashes → all mandatory checks pass, the recommended hash check
    # warns → overall verdict 'warn'. Exactly one verdict row per scan.
    verdicts = list(
        sync_session.execute(
            select(SbomConformance).where(SbomConformance.scan_id == scan_id)
        ).scalars()
    )
    assert len(verdicts) == 1, "exactly one conformance verdict per ingested scan"
    verdict = verdicts[0]
    assert verdict.source_format == "cyclonedx"
    assert verdict.result == "warn"
    assert verdict.n_fail == 0
    assert verdict.purl_coverage_pct == 100
    assert verdict.component_count == 4
    assert verdict.checks, "the per-check detail array is persisted"
    # The denormalised project pointer matches the scan's project (used by the
    # tenant-scoped read endpoint's belongs-to-project predicate).
    assert verdict.project_id == _project_id


# ---------------------------------------------------------------------------
# Idempotency — re-running a succeeded ingest is a no-op
# ---------------------------------------------------------------------------


def test_ingest_pipeline_succeeded_rerun_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_from_fixture(monkeypatch)

    scan_id, _ = _seed_queued_sbom_scan(tmp_path)

    from tasks.ingest_sbom import ingest_sbom_task

    ingest_sbom_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    completed_first = scan.completed_at

    ingest_sbom_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    again = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert again.completed_at == completed_first
    assert again.status == "succeeded"


# ---------------------------------------------------------------------------
# Lifecycle — ref-keyed supersede (生成→succeed→supersede prior)
# ---------------------------------------------------------------------------


def test_ingest_supersedes_prior_succeeded_scan_on_same_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """When the ingest succeeds on a ref that already has a succeeded scan, the
    older scan is superseded (scan-retention ref-keyed latest contract)."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_from_fixture(monkeypatch)

    ref = "refs/heads/main"
    scan_id, project_id = _seed_queued_sbom_scan(tmp_path, ref=ref)

    # Seed a PRIOR succeeded scan on the same ref (must end up superseded). It
    # cannot be active (the partial unique index forbids two in-flight), so it
    # is already succeeded.
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _seed_prior() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            from datetime import UTC, datetime

            from models import Project

            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one()
            from tests._helpers import make_scan

            prior = await make_scan(
                s, project=project, kind="sbom", status="succeeded", ref=ref
            )
            prior.completed_at = datetime.now(tz=UTC)
            await s.commit()
            await s.refresh(prior)
            pid = prior.id
        await engine.dispose()
        return pid

    prior_id = asyncio.run(_seed_prior())

    from tasks.ingest_sbom import ingest_sbom_task

    result = ingest_sbom_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    new_scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    prior = sync_session.execute(select(Scan).where(Scan.id == prior_id)).scalar_one()
    assert new_scan.status == "succeeded"
    assert new_scan.superseded_at is None, "the newest succeeded scan is live"
    assert prior.superseded_at is not None, "the prior same-ref scan was superseded"


# ---------------------------------------------------------------------------
# Lifecycle — a forced re-entry REPLACES the conformance verdict (no dupe)
# ---------------------------------------------------------------------------


def test_ingest_rerun_replaces_conformance_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """``_reset_scan_for_rerun`` does not touch ``sbom_conformance``; the verdict
    persist is delete-then-insert, so a Celery acks_late re-entry on a scan that
    is NOT yet succeeded replaces the row rather than tripping the
    ``uq_sbom_conformance_scan_id`` unique constraint."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_from_fixture(monkeypatch)

    scan_id, _ = _seed_queued_sbom_scan(tmp_path)

    from tasks.ingest_sbom import ingest_sbom_task

    # First run → one verdict, scan succeeded.
    ingest_sbom_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    first = sync_session.execute(
        select(SbomConformance).where(SbomConformance.scan_id == scan_id)
    ).scalar_one()
    first_id = first.id

    # Force a genuine re-entry: flip the scan back to queued so the task re-runs
    # the pipeline (instead of the succeeded-skip) and re-scores conformance.
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    scan.status = "queued"
    scan.completed_at = None
    scan.superseded_at = None
    sync_session.commit()

    ingest_sbom_task.apply(args=[str(scan_id)])
    sync_session.expire_all()

    # Still exactly one verdict (replaced, not duplicated) — and a fresh row.
    rows = list(
        sync_session.execute(
            select(SbomConformance).where(SbomConformance.scan_id == scan_id)
        ).scalars()
    )
    assert len(rows) == 1, "re-entry must REPLACE the verdict, not duplicate it"
    assert rows[0].id != first_id, "the verdict row was re-created (delete-then-insert)"
    assert rows[0].result == "warn"
