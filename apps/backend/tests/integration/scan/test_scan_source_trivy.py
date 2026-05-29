"""
End-to-end Trivy vuln matching → vulnerability_findings (W6-#41).

This integration test pins the full ``cdxgen → persist_components → trivy →
persist_trivy_findings`` flow against a real Postgres. We monkeypatch
``run_trivy_sbom`` to return a canned Trivy JSON report so the test does not
shell out to the Trivy binary — but every other step (cdxgen mock fixture,
component upsert, Vulnerability autocreate, VulnerabilityFinding insert) hits
the actual database via the same ``sync_session_scope`` the worker uses.

What we pin:

  - Happy path: a Trivy report with one (lodash × synthetic CVE) yields
    exactly one ``vulnerability_findings`` row, plus an autocreated
    ``vulnerabilities`` row (source='trivy') for the CVE. The CVE id is a
    synthetic ``CVE-TRIVY-INT-2026-*`` so it cannot collide with a real
    catalog row left over from a prior ``dt_resync`` run.
  - The Trivy report path is persisted as a ``trivy_sbom_report`` ScanArtifact.
  - Idempotency: re-running the same scan_id deletes the prior findings via
    ``_reset_scan_for_rerun`` and recreates them — no duplicates.
  - Unknown component: a Trivy finding for a package cdxgen never emitted
    (no ComponentVersion row matches the PURL) is dropped silently — scan
    still reaches succeeded.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from integrations.cdxgen import CdxgenResult
from integrations.trivy import TrivyResult
from models import (
    ComponentVersion,
    Scan,
    ScanArtifact,
    Vulnerability,
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

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip scan_source Trivy integration")
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
            f"alembic upgrade head failed; Trivy integration cannot run\n"
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


# ---------------------------------------------------------------------------
# Fixtures: synthetic cdxgen SBOM + Trivy report stubs
# ---------------------------------------------------------------------------


def _make_cdxgen_fixture(workspace: Path) -> CdxgenResult:
    """Build a CdxgenResult whose SBOM contains ONE lodash component.

    The SBOM is written to disk so ``run_trivy_sbom`` (even stubbed) and
    the artifact persistence have a real file to point at.
    """
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:trivy-test",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "trivy-test-app",
                "version": "0.0.0",
            }
        },
        "components": [
            {
                "type": "library",
                "bom-ref": "pkg:npm/lodash@4.17.20",
                "name": "lodash",
                "version": "4.17.20",
                "purl": "pkg:npm/lodash@4.17.20",
                "licenses": [{"license": {"id": "MIT"}}],
            }
        ],
    }
    cdxgen_dir = workspace / "cdxgen"
    cdxgen_dir.mkdir(parents=True, exist_ok=True)
    sbom_path = cdxgen_dir / "cdxgen.cdx.json"
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    return CdxgenResult(sbom_path=sbom_path, sbom=sbom)


def _trivy_stub_with_lodash_cve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``run_trivy_sbom`` to report one CVE against pkg:npm/lodash@4.17.20."""

    def _fake(
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
        report = {
            "SchemaVersion": 2,
            "ArtifactName": str(sbom_path),
            "ArtifactType": "cyclonedx",
            "Results": [
                {
                    "Target": "pkg:npm/lodash@4.17.20",
                    "Class": "lang-pkgs",
                    "Type": "npm",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-TRIVY-INT-2026-0001",
                            "PkgName": "lodash",
                            "InstalledVersion": "4.17.20",
                            "FixedVersion": "4.17.21",
                            "Severity": "HIGH",
                            "Title": "Synthetic CVE for W6-#41 integration",
                            "Description": "Trivy integration test stub.",
                            "References": [
                                "https://example.invalid/CVE-TRIVY-INT-2026-0001",
                            ],
                        }
                    ],
                }
            ],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake)


def _trivy_stub_with_unknown_component(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Trivy to report a CVE against a package cdxgen DID NOT emit."""

    def _fake(
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
        report = {
            "SchemaVersion": 2,
            "ArtifactType": "cyclonedx",
            "Results": [
                {
                    "Type": "npm",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-GHOST-2026-1",
                            "PkgName": "package-cdxgen-never-saw",
                            "InstalledVersion": "9.9.9",
                            "Severity": "CRITICAL",
                        }
                    ],
                }
            ],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake)


def _seed_queued_scan(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed an org/team/user/project/scan via the async helpers."""
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
            from models import Scan as ScanModel

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
            scan_id = scan.id
            project_id = project.id
        await engine.dispose()
        return scan_id, project_id

    return asyncio.run(_build())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_trivy_persists_finding_against_known_component(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A Trivy report with a CVE against a known component yields exactly
    one VulnerabilityFinding row + an autocreated Vulnerability catalog row.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    # cdxgen: emit our synthetic lodash SBOM (writes the file the Trivy
    # stub will read).
    monkeypatch.setattr(
        "tasks.scan_source.cdxgen_adapter.run_cdxgen",
        lambda *, source_dir, output_dir, **_kwargs: _make_cdxgen_fixture(  # noqa: ARG005
            output_dir.parent
        ),
    )
    _trivy_stub_with_lodash_cve(monkeypatch)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()

    # Scan reached succeeded.
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    # ComponentVersion exists for lodash@4.17.20.
    cv = sync_session.execute(
        select(ComponentVersion).where(
            ComponentVersion.purl_with_version == "pkg:npm/lodash@4.17.20"
        )
    ).scalar_one()

    # Vulnerability row was autocreated (source='trivy').
    vuln = sync_session.execute(
        select(Vulnerability).where(
            Vulnerability.external_id == "CVE-TRIVY-INT-2026-0001"
        )
    ).scalar_one()
    assert vuln.source == "trivy"
    assert vuln.severity == "high"

    # Exactly one VulnerabilityFinding row exists for this scan.
    findings = (
        sync_session.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.scan_id == scan_id
            )
        )
        .scalars()
        .all()
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.component_version_id == cv.id
    assert finding.vulnerability_id == vuln.id
    assert finding.status == "new"
    assert finding.fixed_version == "4.17.21"

    # The Trivy report was persisted as a scan_artifact.
    artifacts = (
        sync_session.execute(
            select(ScanArtifact).where(ScanArtifact.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    kinds = {a.kind for a in artifacts}
    assert "trivy_sbom_report" in kinds


def test_trivy_rerun_does_not_duplicate_findings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """Re-executing the task on the same scan_id (after flipping back to
    queued) must NOT yield a second VulnerabilityFinding row — the
    ``_reset_scan_for_rerun`` purge keeps the count at one.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr(
        "tasks.scan_source.cdxgen_adapter.run_cdxgen",
        lambda *, source_dir, output_dir, **_kwargs: _make_cdxgen_fixture(  # noqa: ARG005
            output_dir.parent
        ),
    )
    _trivy_stub_with_lodash_cve(monkeypatch)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    first = (
        sync_session.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.scan_id == scan_id
            )
        )
        .scalars()
        .all()
    )
    assert len(first) == 1

    # Flip back to queued and re-run.
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    scan.status = "queued"
    sync_session.commit()

    scan_source_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    second = (
        sync_session.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.scan_id == scan_id
            )
        )
        .scalars()
        .all()
    )
    assert len(second) == 1, (
        f"re-run must not duplicate findings; got {len(second)} on second run"
    )


def test_trivy_unknown_component_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A Trivy finding for a package cdxgen did NOT emit must be silently
    skipped — the scan still reaches succeeded with zero findings persisted.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr(
        "tasks.scan_source.cdxgen_adapter.run_cdxgen",
        lambda *, source_dir, output_dir, **_kwargs: _make_cdxgen_fixture(  # noqa: ARG005
            output_dir.parent
        ),
    )
    _trivy_stub_with_unknown_component(monkeypatch)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    findings = (
        sync_session.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.scan_id == scan_id
            )
        )
        .scalars()
        .all()
    )
    assert len(findings) == 0
