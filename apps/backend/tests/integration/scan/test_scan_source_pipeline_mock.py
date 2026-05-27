"""
End-to-end source scan pipeline — mock backend + W6 Trivy stub.

We drive `tasks.scan_source.scan_source_task` directly (NOT through Celery's
broker) with `TRUSTEDOSS_SCAN_BACKEND=mock` so cdxgen + scancode emit fixture
JSON, and we monkeypatch `run_trivy_sbom` to return a stub Trivy report so
the test never spawns a Trivy subprocess.

What we pin:

  - Happy path: a queued scan reaches `status='succeeded'` with progress=100,
    artifacts persisted (`scan_artifacts`, including `trivy_sbom_report`),
    and a non-empty `scan_components` set derived from the cdxgen mock SBOM.
  - Stage progression updates `current_step` / `progress_percent` along the
    way (not just at the end). The historical `dt_upload` / `dt_findings`
    stage labels are still emitted (rename deferred to #43f).
  - Idempotency: invoking the task again on a `succeeded` scan is a no-op.
  - cdxgen failure → scan transitions to `status='failed'` with the cdxgen
    error message; the workspace is cleaned up (the `finally` shutil.rmtree
    is in the task module, so we just assert no leftover dir on the host).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import (
    AuditLog,
    ComponentApproval,
    LicenseFinding,
    Scan,
    ScanArtifact,
    ScanComponent,
)
from models.component_approval import ApprovalStatus
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
        pytest.skip("DATABASE_URL not set — skip scan_source pipeline integration")
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
            f"alembic upgrade head failed; pipeline integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def sync_session() -> Iterator[Session]:
    """A sync session pointing at the same Postgres the worker uses.

    The scan task module uses `core.db.sync_session_scope`, which lazily
    builds an engine off `DATABASE_URL`. Here we open our own engine to
    seed rows AND read them back after the task has run — both engines
    point at the same DB so the writes are visible.
    """
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
# Test helpers
# ---------------------------------------------------------------------------


def _seed_queued_scan(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Set up a project + queued scan via the async helpers, sync-flushed."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            # git_url=None keeps the worker on the no-source placeholder fetch
            # path: these tests drive the task directly with the mock cdxgen
            # backend (which emits a fixture SBOM regardless of source), so a
            # real git_url would make _fetch_source attempt an actual clone
            # (no `git` binary in the test image). The trigger-layer
            # ScanSourceUnavailable guard is bypassed here because we insert the
            # Scan row directly rather than going through trigger_scan.
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


def _stub_trivy_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``run_trivy_sbom`` with a stub that writes + returns an empty
    Trivy report.

    The stub mirrors what ``run_trivy_sbom(backend="mock")`` would produce in
    shape (Schema 2, ArtifactType cyclonedx) but with zero vulnerabilities,
    so the persistence path runs end-to-end and the integration test stays
    focused on pipeline / artifact shape rather than vuln matching itself.
    """
    import json

    from integrations.trivy import TrivyResult

    def _fake_run(
        sbom_path: Path,  # noqa: ARG001
        output_dir: Path,
        *,
        timeout_seconds: int = 0,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report = {
            "SchemaVersion": 2,
            "ArtifactName": str(sbom_path),
            "ArtifactType": "cyclonedx",
            "Results": [],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake_run)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_scan_source_pipeline_completes_with_mock_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A full pipeline run against mock cdxgen / scancode + stub Trivy must reach `succeeded`."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    # W6: stub Trivy so no subprocess is spawned.
    _stub_trivy_empty(monkeypatch)

    scan_id, project_id = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    # Direct invocation: scan_source_task is a Celery `bind=True` task. Calling
    # `.run(scan_id=...)` would normally need self.request — we use `.apply()`
    # which executes the task in-process synchronously and constructs `self`.
    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    # Refresh state from the DB.
    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100
    assert scan.current_step == "finalize"
    assert scan.completed_at is not None
    assert scan.error_message is None

    # The cdxgen + scancode artifacts were persisted.
    artifacts = (
        sync_session.execute(
            select(ScanArtifact).where(ScanArtifact.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    kinds = {a.kind for a in artifacts}
    assert "sbom_cyclonedx" in kinds
    assert "scancode_result" in kinds

    # v2.3-s1 — the mock cosign backend signs the SBOM, so the detached signature
    # artifact must be persisted with the SBOM's sha256 recorded on the row.
    assert "sbom_cyclonedx_sig" in kinds, (
        "Stage 3.5 must persist a `sbom_cyclonedx_sig` artifact (mock cosign "
        f"backend); got kinds={sorted(kinds)}"
    )
    sig_artifacts = [a for a in artifacts if a.kind == "sbom_cyclonedx_sig"]
    assert len(sig_artifacts) == 1
    assert sig_artifacts[0].sha256 and len(sig_artifacts[0].sha256) == 64

    # v2.3-s2 — signing succeeded, so the in-toto SLSA provenance attestation must
    # also be persisted (the pipeline gates attestation on a successful sign). Its
    # sha256 binds the attestation to the exact SBOM bytes, same as the signature.
    assert "sbom_attestation" in kinds, (
        "Stage 3.5 must persist a `sbom_attestation` artifact after a successful "
        f"sign (mock cosign backend); got kinds={sorted(kinds)}"
    )
    attest_artifacts = [a for a in artifacts if a.kind == "sbom_attestation"]
    assert len(attest_artifacts) == 1
    assert attest_artifacts[0].sha256 == sig_artifacts[0].sha256

    # Stage 6.5 (G3.1) — the in-task source preservation must have run AND
    # recorded a `source_tarball` ScanArtifact row pointing at a tarball that
    # actually exists on disk. Before this assertion nothing verified the
    # in-task preserve_scan_source(...) call automatically; a regression that
    # silently dropped Stage 6.5 (or wrote the artifact row but no file) would
    # have gone unnoticed until the source-tree e2e 404'd.
    assert "source_tarball" in kinds, (
        "Stage 6.5 must persist a `source_tarball` artifact for a succeeded "
        f"source scan; got kinds={sorted(kinds)}"
    )
    source_tarballs = [a for a in artifacts if a.kind == "source_tarball"]
    assert len(source_tarballs) == 1, (
        "exactly one source_tarball artifact expected per succeeded scan"
    )
    tarball = source_tarballs[0]
    # The recorded path must exist (the worker wrote the file under
    # WORKSPACE_HOST_PATH, which the monkeypatch points at tmp_path) and the
    # row's byte_size must match the on-disk size.
    tarball_path = Path(tarball.storage_path)
    assert tarball_path.is_file(), (
        f"source_tarball artifact path does not exist on disk: {tarball_path}"
    )
    assert tarball.byte_size == tarball_path.stat().st_size
    # The tarball is a readable gzip tar that folds in the scancode JSON under
    # the reserved member name — the exact shape source_tree_service reads.
    import tarfile

    from services.source_preservation_service import SCANCODE_MEMBER_NAME

    with tarfile.open(tarball_path, "r:gz") as tar:
        member_names = {m.name for m in tar.getmembers()}
    assert SCANCODE_MEMBER_NAME in member_names, (
        "preserved tarball must fold in the scancode JSON under "
        f"{SCANCODE_MEMBER_NAME!r}; got {sorted(member_names)}"
    )

    # cdxgen mock emits at least one component → ScanComponent rows exist.
    components = (
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    assert len(components) >= 1


# ---------------------------------------------------------------------------
# Idempotency — succeeded scan re-run is a no-op
# ---------------------------------------------------------------------------


def test_scan_source_succeeded_run_is_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_empty(monkeypatch)

    scan_id, _ = _seed_queued_scan(sync_session)

    # First run completes.
    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    completed_at_first = scan.completed_at

    # Second run on the same scan_id must short-circuit (no re-running cdxgen,
    # no completed_at update). We assert by ensuring completed_at is unchanged.
    scan_source_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    scan_again = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan_again.completed_at == completed_at_first
    assert scan_again.status == "succeeded"


# ---------------------------------------------------------------------------
# Blast-radius isolation — detected-license failure must NOT roll back the
# declared findings + component graph (security-reviewer Medium #1).
# ---------------------------------------------------------------------------


def test_detected_license_failure_does_not_roll_back_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A SQLAlchemyError while persisting scancode-detected licenses is wrapped
    in a SAVEPOINT: the high-value declared findings + components still commit
    and the scan still reaches ``succeeded``.

    This is the cache the UI shows when vuln matching itself is unavailable —
    it must survive a hostile file that trips an unexpected constraint in the
    detected-license write.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_empty(monkeypatch)

    # Sabotage detected-license persistence to raise mid-SAVEPOINT. We let it
    # add a row first (so the nested transaction is non-trivially dirty) then
    # raise, mirroring an INSERT that fails on flush.
    from sqlalchemy.exc import DataError

    def _boom(session, *, scan_uuid, sbom, detections):  # type: ignore[no-untyped-def]
        raise DataError("simulated detected-license INSERT failure", None, Exception())

    monkeypatch.setattr("tasks.scan_source._persist_detected_licenses", _boom)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    # The scan still succeeds — detected-license failure is degraded, not fatal.
    assert scan.status == "succeeded"

    # Components committed despite the detected-license failure.
    components = (
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    assert len(components) >= 1

    # No detected (scancode) findings were persisted — the SAVEPOINT rolled
    # them back without touching the declared findings / components.
    detected = (
        sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.scan_id == scan_id,
                LicenseFinding.kind == "detected",
            )
        )
        .scalars()
        .all()
    )
    assert detected == []


# ---------------------------------------------------------------------------
# Failure path — cdxgen raises
# ---------------------------------------------------------------------------


def test_scan_source_cdxgen_failure_marks_scan_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """When cdxgen blows up the scan must transition to `failed` with a message."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_empty(monkeypatch)

    # Sabotage the cdxgen adapter from inside the scan_source module.
    from integrations import cdxgen as cdxgen_adapter

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise cdxgen_adapter.CdxgenFailed("test cdxgen exit 1")

    monkeypatch.setattr("tasks.scan_source.cdxgen_adapter.run_cdxgen", _boom)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "failed"
    assert scan.error_message
    assert "cdxgen" in scan.error_message.lower() or "unexpected" in scan.error_message.lower()
    assert scan.completed_at is not None

    # Workspace must have been cleaned up by the task's `finally`.
    workspace_dir = tmp_path / str(scan_id)
    assert not workspace_dir.exists(), "workspace must be cleaned up after failure"


# ---------------------------------------------------------------------------
# BUG-010 — conditional-license component auto-enrols into the approval queue
# ---------------------------------------------------------------------------


def _conditional_cdxgen_factory():  # type: ignore[no-untyped-def]
    """Return a fake ``run_cdxgen`` that emits an SBOM with one conditional
    (MPL-2.0) and one allowed (MIT) component.

    It writes a real CycloneDX file to ``output_dir`` so the downstream artifact
    + Trivy-SBOM stages (which read ``sbom_path``) keep working, and returns a
    ``CdxgenResult`` whose ``sbom`` drives ``_persist_components``.
    """
    import json

    from integrations.cdxgen import CdxgenResult

    def _fake_run_cdxgen(  # noqa: ARG001
        *, source_dir: Path, output_dir: Path, **_kwargs: object
    ) -> CdxgenResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        sbom = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": f"urn:uuid:cond-{source_dir.name}",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": source_dir.name,
                    "version": "0.0.0",
                }
            },
            "components": [
                {
                    "type": "library",
                    "bom-ref": "pkg:npm/permissive@1.0.0",
                    "name": "permissive",
                    "version": "1.0.0",
                    "purl": "pkg:npm/permissive@1.0.0",
                    "licenses": [{"license": {"id": "MIT"}}],
                },
                {
                    "type": "library",
                    "bom-ref": "pkg:npm/conditional-lib@2.0.0",
                    "name": "conditional-lib",
                    "version": "2.0.0",
                    "purl": "pkg:npm/conditional-lib@2.0.0",
                    "licenses": [{"license": {"id": "MPL-2.0"}}],
                },
            ],
        }
        sbom_path = output_dir / "cdxgen.cdx.json"
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        return CdxgenResult(sbom_path=sbom_path, sbom=sbom)

    return _fake_run_cdxgen


def test_conditional_license_component_creates_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A scan over an SBOM with a conditional (MPL-2.0) component must, after
    finalize, leave exactly one Pending, system-created (NULL actor) approval
    for that component — and none for the permissive (MIT) component.

    This is the BUG-010 fix end-to-end: the guide promised auto-enrolment but
    the pipeline never created the approval.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_empty(monkeypatch)
    monkeypatch.setattr(
        "tasks.scan_source.cdxgen_adapter.run_cdxgen",
        _conditional_cdxgen_factory(),
    )

    scan_id, project_id = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    # Exactly one approval was created, against the conditional component, in
    # Pending, with a NULL requester (system-created).
    approvals = (
        sync_session.execute(
            select(ComponentApproval).where(
                ComponentApproval.project_id == project_id
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 1, (
        f"expected one auto-created approval for the conditional component; "
        f"got {len(approvals)}"
    )
    approval = approvals[0]
    assert approval.status == ApprovalStatus.pending
    assert approval.requested_by_user_id is None

    # Confirm it points at the conditional component (purl namespace), not the
    # permissive one.
    from models import Component, ComponentVersion, License
    from models import LicenseFinding as LF

    conditional_finding = (
        sync_session.execute(
            select(Component.purl)
            .join(ComponentVersion, ComponentVersion.component_id == Component.id)
            .join(LF, LF.component_version_id == ComponentVersion.id)
            .join(License, License.id == LF.license_id)
            .where(Component.id == approval.component_id, LF.scan_id == scan_id)
        )
        .scalars()
        .first()
    )
    assert conditional_finding == "pkg:npm/conditional-lib"

    # QA follow-up Medium — the full pipeline (real Celery task path, sync
    # session, system context) must have written exactly ONE auto-enrolment
    # audit summary row past the audit_logs append-only trigger: NULL actor,
    # action 'approvals.auto_enrolled', created_count 1 for this conditional
    # component.
    audit_rows = (
        sync_session.execute(
            select(AuditLog).where(AuditLog.action == "approvals.auto_enrolled")
        )
        .scalars()
        .all()
    )
    scan_audit_rows = [
        r
        for r in audit_rows
        if isinstance(r.diff, dict) and r.diff.get("scan_id") == str(scan_id)
    ]
    assert len(scan_audit_rows) == 1
    audit_row = scan_audit_rows[0]
    assert audit_row.actor_user_id is None  # system context
    assert audit_row.target_table == "component_approvals"
    assert audit_row.target_id is None
    audit_diff = audit_row.diff
    assert isinstance(audit_diff, dict)
    assert audit_diff["created_count"] == 1
    assert audit_diff["component_ids"] == [str(approval.component_id)]


def test_conditional_approval_is_idempotent_across_reruns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """Re-running the scan (which purges + re-creates findings) must NOT create a
    second approval — the open-approval guard keeps it at one.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_empty(monkeypatch)
    monkeypatch.setattr(
        "tasks.scan_source.cdxgen_adapter.run_cdxgen",
        _conditional_cdxgen_factory(),
    )

    scan_id, project_id = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])

    # Force a re-run by flipping the scan back to queued (mirrors what the
    # re-scan endpoint does — the task's idempotency reset re-creates findings).
    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    scan.status = "queued"
    sync_session.commit()

    scan_source_task.apply(args=[str(scan_id)])

    sync_session.expire_all()
    approvals = (
        sync_session.execute(
            select(ComponentApproval).where(
                ComponentApproval.project_id == project_id
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 1, (
        f"re-run must not duplicate the auto-created approval; got {len(approvals)}"
    )

    # QA follow-up Medium — the re-run created 0 new approvals, so it must NOT
    # have written a second audit summary row. Exactly one auto-enrolment audit
    # row exists for this scan (from the first run).
    scan_audit_rows = [
        r
        for r in sync_session.execute(
            select(AuditLog).where(AuditLog.action == "approvals.auto_enrolled")
        )
        .scalars()
        .all()
        if isinstance(r.diff, dict) and r.diff.get("scan_id") == str(scan_id)
    ]
    assert len(scan_audit_rows) == 1, (
        f"idempotent re-run must not add a second audit summary row; "
        f"got {len(scan_audit_rows)}"
    )
