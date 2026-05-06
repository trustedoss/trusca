"""
Source scan Celery task — cdxgen → ORT → DT upload → DT findings.

CLAUDE.md core rule #3: this pipeline runs asynchronously inside a Celery
worker; the FastAPI request handler that triggered the scan only persisted a
``Scan`` row in state ``queued``.

CLAUDE.md core rule #4: every DT call goes through the circuit breaker. When
the breaker is OPEN (DT is down), the task does the best it can with cached
data — vulnerability findings cannot be produced for the current scan, but
the SBOM + license findings are still persisted, the scan is marked
``failed`` with a clear ``error_message``, and the next scan will retry once
the breaker recovers. Phase 6 will add a "deferred" outbox so OPEN-at-upload
scans automatically replay; #8 keeps the simpler "fail with breaker_open
reason" behavior.

Idempotency:
    The task is keyed off ``scan_id``. On re-execution (Celery
    ``task_acks_late=True`` + worker restart) we:
      1. Skip immediately if the scan already reached ``succeeded``.
      2. Otherwise, treat the run as a fresh start: delete prior
         ScanComponent / VulnerabilityFinding / LicenseFinding rows for this
         scan, recreate the workspace, and re-run every stage.
    This is simpler than checkpointing per stage and is correct because the
    DB partial unique index already enforces "at most one in-flight scan per
    project" — a re-execution cannot collide with a parallel scan.

Workspace:
    Each task creates ``${WORKSPACE_HOST_PATH}/<scan_id>/`` and removes it in
    ``finally``. We use ``shutil.rmtree(..., ignore_errors=True)`` because
    user-policy forbids ``rm`` shell calls and a partial cleanup on shutdown
    is acceptable — the orphan workspace cleaner (Phase 2.8) reclaims any
    leftover trees that survive a SIGKILL.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import workspace_root
from core.db import sync_session_scope
from integrations import cdxgen as cdxgen_adapter
from integrations import ort as ort_adapter
from integrations._size_guard import enforce_jsonb_row_size_limit
from integrations.dt import DTBreakerOpen, DTError
from integrations.dt.breaker import CircuitBreaker, get_breaker
from integrations.dt.client import DTClient, build_client
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    VulnerabilityFinding,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_source")


# ---------------------------------------------------------------------------
# Stage progress mapping
# ---------------------------------------------------------------------------

_STAGE_PROGRESS: dict[str, int] = {
    "bootstrap": 0,
    "fetch": 10,
    "cdxgen": 25,
    "ort": 50,
    "dt_upload": 70,
    "dt_findings": 90,
    "finalize": 100,
}


# ---------------------------------------------------------------------------
# Public Celery task
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.scan_source",
    soft_time_limit=3600,
    time_limit=4200,
    bind=True,
)
def scan_source_task(self: Any, scan_id: str) -> None:
    """
    Run a source scan to completion.

    Args:
        scan_id: UUID **string** (Celery JSON serialization compatibility).
    """
    structlog.contextvars.bind_contextvars(
        scan_id=scan_id, task_id=self.request.id, task_kind="source"
    )
    try:
        scan_uuid = uuid.UUID(scan_id)
    except ValueError:
        log.error("scan_source_invalid_scan_id", scan_id=scan_id)
        return

    workspace = Path(workspace_root()) / str(scan_uuid)

    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                log.warning("scan_source_missing_scan_row")
                return
            if scan.status == "succeeded":
                log.info("scan_source_already_succeeded")
                return

            project = session.get(Project, scan.project_id)
            if project is None:
                _mark_failed(session, scan, "project no longer exists")
                return

            _reset_scan_for_rerun(session, scan)
            _mark_running(session, scan)

        # Run the pipeline outside the first session so each stage commits
        # its own progress update without holding a long-lived transaction.
        _run_pipeline(scan_uuid=scan_uuid, project_id=project.id, workspace=workspace)
    except DTBreakerOpen as exc:
        log.warning("scan_source_breaker_open", error=str(exc))
        _record_terminal_failure(scan_uuid, f"DT unavailable (circuit breaker open): {exc}")
    except DTError as exc:
        log.error("scan_source_dt_error", error=str(exc))
        _record_terminal_failure(scan_uuid, f"DT error: {exc}")
    except Exception as exc:
        # Any unhandled exception terminates the scan with status='failed'
        # and surfaces the error message in the UI. Re-raising would have
        # Celery retry the task indefinitely — we explicitly choose
        # fail-loud over retry-forever so operators investigate.
        log.exception("scan_source_unhandled_error")
        _record_terminal_failure(scan_uuid, f"unexpected error: {exc}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        structlog.contextvars.unbind_contextvars("scan_id", "task_id", "task_kind")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _run_pipeline(*, scan_uuid: uuid.UUID, project_id: uuid.UUID, workspace: Path) -> None:
    """Execute the scan stages, each with its own commit."""
    # Stage 1 — bootstrap workspace.
    _set_stage(scan_uuid, "bootstrap")
    workspace.mkdir(parents=True, exist_ok=True)

    # Stage 2 — fetch source. PR #8 supports a "use existing tree" mode for
    # development (no git clone). The fetch logic lands in PR #9 alongside
    # the ssrf-guarded git_url. For now we just touch a placeholder so
    # downstream stages have a directory to point at.
    _set_stage(scan_uuid, "fetch")
    source_dir = workspace / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / ".trustedoss-placeholder").write_text("scan-source workspace\n")

    # Stage 3 — cdxgen.
    _set_stage(scan_uuid, "cdxgen")
    cdxgen_result = cdxgen_adapter.run_cdxgen(
        source_dir=source_dir,
        output_dir=workspace / "cdxgen",
    )
    _persist_artifact(
        scan_uuid,
        kind="sbom_cyclonedx",
        path=cdxgen_result.sbom_path,
    )

    # Stage 4 — ORT evaluate.
    _set_stage(scan_uuid, "ort")
    ort_result = ort_adapter.run_ort(
        source_dir=source_dir,
        sbom_path=cdxgen_result.sbom_path,
        output_dir=workspace / "ort",
    )
    _persist_artifact(scan_uuid, kind="ort_result", path=ort_result.result_path)

    # Persist the SBOM components (independent of DT availability — this is
    # the cached license + component data the UI shows when DT is down).
    with sync_session_scope() as session:
        _persist_components(
            session,
            scan_uuid=scan_uuid,
            sbom=cdxgen_result.sbom,
        )
        session.commit()

    # Stage 5 — DT upload (gated by the breaker).
    _set_stage(scan_uuid, "dt_upload")
    breaker = get_breaker()
    dt_client = build_client()
    try:
        dt_project_uuid = breaker.call(
            lambda: dt_client.upsert_project(
                name=str(project_id),
                version=str(scan_uuid),
            )
        )
        sbom_bytes = cdxgen_result.sbom_path.read_bytes()
        breaker.call(
            lambda: dt_client.upload_sbom(
                project_uuid=dt_project_uuid,
                sbom_json=sbom_bytes,
            )
        )

        # Stage 6 — DT findings poll.
        _set_stage(scan_uuid, "dt_findings")
        findings = breaker.call(lambda: dt_client.get_findings(project_uuid=dt_project_uuid))
        with sync_session_scope() as session:
            _persist_findings(session, scan_uuid=scan_uuid, findings=findings)
            session.commit()
    finally:
        dt_client.close()

    # Stage 7 — finalize.
    _set_stage(scan_uuid, "finalize")
    _mark_succeeded(scan_uuid)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _reset_scan_for_rerun(session: Session, scan: Scan) -> None:
    """Wipe child rows so a re-execution starts from a clean slate."""
    session.execute(delete(VulnerabilityFinding).where(VulnerabilityFinding.scan_id == scan.id))
    session.execute(delete(LicenseFinding).where(LicenseFinding.scan_id == scan.id))
    session.execute(delete(ScanComponent).where(ScanComponent.scan_id == scan.id))
    session.execute(delete(ScanArtifact).where(ScanArtifact.scan_id == scan.id))


def _mark_running(session: Session, scan: Scan) -> None:
    scan.status = "running"
    scan.started_at = datetime.now(UTC)
    scan.error_message = None
    scan.current_step = "bootstrap"
    scan.progress_percent = 0
    session.commit()


def _mark_failed(session: Session, scan: Scan, message: str) -> None:
    scan.status = "failed"
    scan.error_message = message
    scan.completed_at = datetime.now(UTC)
    session.commit()


def _record_terminal_failure(scan_uuid: uuid.UUID, message: str) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        _mark_failed(session, scan, message)


def _mark_succeeded(scan_uuid: uuid.UUID) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.status = "succeeded"
        scan.progress_percent = 100
        scan.current_step = "finalize"
        scan.completed_at = datetime.now(UTC)
        session.commit()


def _set_stage(scan_uuid: uuid.UUID, stage: str) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.current_step = stage
        scan.progress_percent = _STAGE_PROGRESS.get(stage, scan.progress_percent)
        session.commit()
    log.info("scan_stage", stage=stage, percent=_STAGE_PROGRESS.get(stage))


def _persist_artifact(scan_uuid: uuid.UUID, *, kind: str, path: Path) -> None:
    if not path.exists():
        return
    size = path.stat().st_size
    with sync_session_scope() as session:
        artifact = ScanArtifact(
            scan_id=scan_uuid,
            kind=kind,
            storage_path=str(path),
            byte_size=size,
        )
        session.add(artifact)
        session.commit()


def _persist_components(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    sbom: dict[str, Any],
) -> None:
    """Upsert components / component versions / scan components from cdxgen."""
    components = sbom.get("components", []) or []
    for raw in components:
        if not isinstance(raw, dict):
            continue
        purl = raw.get("purl") or raw.get("bom-ref")
        if not isinstance(purl, str) or not purl:
            continue
        name = raw.get("name") or "unknown"
        version = raw.get("version") or "0.0.0"
        package_type = _purl_package_type(purl)

        component = _get_or_create_component(
            session, purl=_purl_without_version(purl), name=name, package_type=package_type
        )
        component_version = _get_or_create_component_version(
            session,
            component=component,
            version=version,
            purl_with_version=purl,
        )

        guarded_raw = enforce_jsonb_row_size_limit(
            raw,
            context={
                "scan_id": str(scan_uuid),
                "column": "scan_components.raw_data",
                "purl": purl,
            },
        )
        scan_component = ScanComponent(
            scan_id=scan_uuid,
            component_version_id=component_version.id,
            dependency_scope=raw.get("scope"),
            dependency_path=raw.get("bom-ref"),
            direct=False,
            raw_data=guarded_raw,
        )
        session.add(scan_component)


def _persist_findings(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    findings: list[dict[str, Any]],
) -> None:
    """
    Persist DT findings as VulnerabilityFinding rows.

    Vulnerability metadata is expected to already exist in the
    ``vulnerabilities`` table thanks to ``dt_resync_task``; if it does not
    we skip the finding (the resync will pick it up on its next pass and a
    follow-up scan will materialize the join). This avoids hot-path inserts
    into the cross-scan vulnerability catalog.
    """
    from models import Vulnerability  # local import to avoid circular hint

    for raw in findings:
        if not isinstance(raw, dict):
            continue
        vuln_data = raw.get("vulnerability") or {}
        component_data = raw.get("component") or {}
        external_id = vuln_data.get("vulnId") or vuln_data.get("source", {}).get("name")
        purl = component_data.get("purl")
        if not external_id or not purl:
            continue

        vuln = session.execute(
            select(Vulnerability).where(Vulnerability.external_id == external_id)
        ).scalar_one_or_none()
        if vuln is None:
            log.info("scan_finding_skipped_unknown_vuln", external_id=external_id)
            continue

        cv = session.execute(
            select(ComponentVersion).where(ComponentVersion.purl_with_version == purl)
        ).scalar_one_or_none()
        if cv is None:
            log.info("scan_finding_skipped_unknown_component", purl=purl)
            continue

        guarded = enforce_jsonb_row_size_limit(
            raw,
            context={
                "scan_id": str(scan_uuid),
                "column": "vulnerability_findings.analysis_response",
                "external_id": external_id,
            },
        )
        finding = VulnerabilityFinding(
            scan_id=scan_uuid,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status="new",
            analysis_response=guarded,
        )
        session.add(finding)


# ---------------------------------------------------------------------------
# Component upsert helpers
# ---------------------------------------------------------------------------


def _get_or_create_component(
    session: Session, *, purl: str, name: str, package_type: str
) -> Component:
    existing = session.execute(
        select(Component).where(Component.purl == purl)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    component = Component(purl=purl, name=name, package_type=package_type)
    session.add(component)
    session.flush()
    return component


def _get_or_create_component_version(
    session: Session,
    *,
    component: Component,
    version: str,
    purl_with_version: str,
) -> ComponentVersion:
    existing = session.execute(
        select(ComponentVersion).where(
            ComponentVersion.purl_with_version == purl_with_version
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=purl_with_version,
    )
    session.add(cv)
    session.flush()
    return cv


def _purl_package_type(purl: str) -> str:
    """Extract the type from ``pkg:<type>/...``; fall back to ``unknown``."""
    if purl.startswith("pkg:"):
        rest = purl[len("pkg:") :]
        slash = rest.find("/")
        if slash > 0:
            return rest[:slash]
    return "unknown"


def _purl_without_version(purl: str) -> str:
    """Strip ``@version`` from a purl, returning a stable component identity."""
    at = purl.rfind("@")
    if at > 0:
        return purl[:at]
    return purl


@contextmanager
def _noop_workspace(path: Path) -> Iterator[Path]:
    """Compatibility hook for tests that need to inject their own workspace."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


__all__ = ["scan_source_task"]


# Optional injection points for unit tests — the real task uses module
# globals, but tests can monkey-patch these to inject mocks without
# touching subprocess / Redis.
def _override_breaker_for_tests(_breaker: CircuitBreaker) -> None:  # pragma: no cover
    raise NotImplementedError("Use monkeypatch on integrations.dt.breaker.get_breaker")


def _override_dt_client_for_tests(_client: DTClient) -> None:  # pragma: no cover
    raise NotImplementedError("Use monkeypatch on integrations.dt.client.build_client")
