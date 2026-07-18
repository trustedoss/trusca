"""
Container scan Celery task — Trivy.

CLAUDE.md core rule #3: container scans run asynchronously, never inline.

Pipeline (much simpler than the source pipeline):
    bootstrap (0%) → trivy (60%) → persist (90%) → finalize (100%)

DT is not consulted for container scans in Phase 2 — Trivy's own database
covers OS package CVEs, and we persist findings directly into the
``vulnerability_findings`` / ``vulnerabilities`` tables. Phase 3.5 will
optionally cross-reference DT for license metadata on container components,
which is when the breaker becomes relevant here too.

Idempotency rules match :mod:`tasks.scan_source` — see that module's docstring.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import scan_soft_time_limit_seconds, workspace_root
from core.db import sync_session_scope
from integrations import trivy as trivy_adapter
from integrations._size_guard import enforce_jsonb_row_size_limit
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from services.vulnerability_matching import emit_finding_create_audits
from tasks._progress import make_line_callback, publish_progress
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_container")


_STAGE_PROGRESS: dict[str, int] = {
    "bootstrap": 0,
    "trivy": 60,
    "persist": 90,
    "finalize": 100,
}

# K-f1: clamp bounds for the image OS block before it lands in scan_metadata.
# family/name derive from the scanned image's release files and are therefore
# attacker-influenced; a real OS family/version is a few chars, so these caps
# keep the write far under the API's 16 KiB scan_metadata invariant regardless
# of image contents (worker-side writes bypass the inbound validator).
_OS_FAMILY_MAX = 64
_OS_NAME_MAX = 128


# PR-A1 (scan stability): time limits are passed per dispatch by
# ``tasks.enqueue_scan`` (read from env at call time, rule #11) rather than
# pinned on the decorator. See ``tasks.scan_source.scan_source_task`` for the
# full rationale.
@celery_app.task(  # type: ignore[misc]
    name="trustedoss.scan_container",
    bind=True,
)
def scan_container_task(self: Any, scan_id: str) -> None:
    """Run a Trivy-based container scan to completion."""
    structlog.contextvars.bind_contextvars(
        scan_id=scan_id, task_id=self.request.id, task_kind="container"
    )
    try:
        scan_uuid = uuid.UUID(scan_id)
    except ValueError:
        log.error("scan_container_invalid_scan_id", scan_id=scan_id)
        return

    workspace = Path(workspace_root()) / str(scan_uuid)

    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                log.warning("scan_container_missing_scan_row")
                return
            if scan.status == "succeeded":
                log.info("scan_container_already_succeeded")
                return
            project = session.get(Project, scan.project_id)
            if project is None:
                _mark_failed(session, scan, "project no longer exists")
                return

            image_ref = _resolve_image_ref(scan.scan_metadata)
            if not image_ref:
                _mark_failed(session, scan, "scan.metadata.image_ref is required")
                return

            # Scan-log verbosity (feat/scan-log-verbosity): snapshot the
            # per-scan flag while the row is loaded. "verbose" flips Trivy into
            # --debug; absence / any other value stays the quiet "normal" trace.
            verbose = str(scan.scan_metadata.get("verbosity", "normal")) == "verbose"

            _reset_for_rerun(session, scan)
            _mark_running(session, scan)

        _run_pipeline(
            scan_uuid=scan_uuid,
            image_ref=image_ref,
            workspace=workspace,
            verbose=verbose,
        )
    except SoftTimeLimitExceeded:
        # PR-A1: Trivy (or a future container stage) exceeded
        # SCAN_SOFT_TIME_LIMIT_SECONDS. Mark failed with a clear message; the
        # shared `finally` reclaims the workspace. Caught before the bare
        # `Exception` handler so the timeout message is not masked.
        soft_limit = scan_soft_time_limit_seconds()
        log.warning("scan_timed_out", scan_id=str(scan_uuid), soft_limit_seconds=soft_limit)
        _record_terminal_failure(
            scan_uuid, f"scan exceeded the time limit ({soft_limit}s)"
        )
    except Exception as exc:
        log.exception("scan_container_unhandled_error")
        _record_terminal_failure(scan_uuid, f"unexpected error: {exc}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        structlog.contextvars.unbind_contextvars("scan_id", "task_id", "task_kind")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _run_pipeline(
    *, scan_uuid: uuid.UUID, image_ref: str, workspace: Path, verbose: bool = False
) -> None:
    _set_stage(scan_uuid, "bootstrap")
    workspace.mkdir(parents=True, exist_ok=True)

    _set_stage(scan_uuid, "trivy")
    trivy_result = trivy_adapter.run_trivy_image(
        image_ref=image_ref,
        output_dir=workspace / "trivy",
        # Stream Trivy's progress/diagnostic lines onto the scan log
        # (feat/scan-log-verbosity); ``verbose`` adds --debug.
        line_callback=make_line_callback(scan_uuid, stage="trivy"),
        verbose=verbose,
    )
    _persist_artifact(scan_uuid, kind="trivy_json", path=trivy_result.report_path)

    _set_stage(scan_uuid, "persist")
    with sync_session_scope() as session:
        _persist_trivy_report(session, scan_uuid=scan_uuid, report=trivy_result.report)
        session.commit()

    # K-f1: the OS/EOSL block is optional telemetry — record it in its OWN
    # best-effort transaction so a malformed report shape never rolls back the
    # vulnerability findings we just committed (mirrors the scan_source
    # detected_env writes: "observation must never fail a scan").
    _persist_os_metadata(scan_uuid=scan_uuid, report=trivy_result.report)

    _set_stage(scan_uuid, "finalize")
    _mark_succeeded(scan_uuid)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _resolve_image_ref(metadata: dict[str, Any]) -> str:
    """Pull the Docker image reference from ``scan.metadata`` (PR #7 schema)."""
    raw = metadata.get("image_ref") if isinstance(metadata, dict) else None
    if isinstance(raw, str) and raw:
        return raw
    return ""


def _reset_for_rerun(session: Session, scan: Scan) -> None:
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
    last_percent = scan.progress_percent or 0
    publish_progress(scan.id, step="failed", percent=last_percent)


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
    publish_progress(scan_uuid, step="succeeded", percent=100)


def _set_stage(scan_uuid: uuid.UUID, stage: str) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.current_step = stage
        scan.progress_percent = _STAGE_PROGRESS.get(stage, scan.progress_percent)
        session.commit()
        committed_percent = scan.progress_percent or 0
    log.info("scan_stage", stage=stage, percent=_STAGE_PROGRESS.get(stage))
    publish_progress(scan_uuid, step=stage, percent=committed_percent)


def _persist_artifact(scan_uuid: uuid.UUID, *, kind: str, path: Path) -> None:
    if not path.exists():
        return
    with sync_session_scope() as session:
        artifact = ScanArtifact(
            scan_id=scan_uuid,
            kind=kind,
            storage_path=str(path),
            byte_size=path.stat().st_size,
        )
        session.add(artifact)
        session.commit()


def _persist_trivy_report(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    report: dict[str, Any],
) -> None:
    """Persist Trivy results into ScanComponent + VulnerabilityFinding rows."""
    results = report.get("Results", []) or []
    created_findings: list[VulnerabilityFinding] = []
    # H-1: a single OS package routinely carries several CVEs. ScanComponent is
    # unique on (scan_id, component_version_id, dependency_path)
    # (``uq_scan_components_scan_version_path``), so the row must be created
    # once per (component, target) and every finding attached to it. Inserting
    # one ScanComponent per vulnerability violates that constraint and fails
    # the whole container scan once any package has >1 CVE — i.e. almost every
    # real image. The source pipeline avoids this by keying dependency_path on
    # each component's unique bom-ref; the container target string is shared,
    # so we dedup explicitly here.
    seen_components: set[tuple[uuid.UUID, str]] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        target = result.get("Target", "")
        for vuln in result.get("Vulnerabilities", []) or []:
            if not isinstance(vuln, dict):
                continue
            pkg_name = vuln.get("PkgName")
            installed = vuln.get("InstalledVersion")
            cve_id = vuln.get("VulnerabilityID")
            if not pkg_name or not installed or not cve_id:
                continue
            purl = f"pkg:apk/{pkg_name}@{installed}"
            component = _get_or_create_component(
                session,
                purl=f"pkg:apk/{pkg_name}",
                name=pkg_name,
                package_type="apk",
            )
            cv = _get_or_create_component_version(
                session,
                component=component,
                version=installed,
                purl_with_version=purl,
            )
            component_key = (cv.id, target)
            if component_key not in seen_components:
                seen_components.add(component_key)
                guarded_raw = enforce_jsonb_row_size_limit(
                    vuln,
                    context={
                        "scan_id": str(scan_uuid),
                        "column": "scan_components.raw_data",
                        "target": target,
                    },
                )
                session.add(
                    ScanComponent(
                        scan_id=scan_uuid,
                        component_version_id=cv.id,
                        dependency_scope="runtime",
                        dependency_path=target,
                        direct=True,
                        raw_data=guarded_raw,
                    )
                )

            vuln_row = session.execute(
                select(Vulnerability).where(Vulnerability.external_id == cve_id)
            ).scalar_one_or_none()
            if vuln_row is None:
                vuln_row = Vulnerability(
                    external_id=cve_id,
                    source="trivy",
                    severity=_normalize_severity(vuln.get("Severity")),
                    summary=vuln.get("Title"),
                    details=vuln.get("Description"),
                    references=vuln.get("References") or [],
                )
                session.add(vuln_row)
                session.flush()

            guarded_finding = enforce_jsonb_row_size_limit(
                vuln,
                context={
                    "scan_id": str(scan_uuid),
                    "column": "vulnerability_findings.analysis_response",
                    "external_id": cve_id,
                },
            )
            finding = VulnerabilityFinding(
                scan_id=scan_uuid,
                component_version_id=cv.id,
                vulnerability_id=vuln_row.id,
                status="new",
                analysis_response=guarded_finding,
            )
            session.add(finding)
            created_findings.append(finding)

    # M-6: per-finding create audit rows (same transaction as the findings).
    emit_finding_create_audits(session, scan_uuid=scan_uuid, findings=created_findings)


def extract_os_metadata(report: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the image OS block from a Trivy image report (K-f1).

    Trivy image scans carry a top-level ``Metadata.OS`` with the detected base
    image OS and — via its bundled vulnerability DB — an ``EOSL`` flag that is
    True when that OS release is past its end-of-service-life (no upstream
    security fixes). This is a scan-level fact (one per image), distinct from
    the component-level EOL that ``services/eol`` stamps on source-scan
    packages. We surface it so an image built on, e.g., an EOL Debian release
    is flagged even when no individual package CVE fires.

    Returns ``{"family", "name", "eosl"}`` (name may be absent), or ``None``
    when the report carries no OS block (mock reports, SBOM-mode reports, a
    scan target Trivy could not fingerprint). The ``eosl`` verdict depends on
    Trivy DB freshness — a stale DB may not yet know a newly-EOL release.

    ``family``/``name`` originate from the SCANNED image's release files
    (``/etc/os-release`` etc.), so they are attacker-influenced. They are
    clamped to short bounds before storage — the API's inbound 16 KiB
    ``scan_metadata`` cap (``ScanCreate._validate_metadata``) does not cover
    worker-side writes, and a real OS family/version is a handful of chars.
    """
    metadata = report.get("Metadata")
    if not isinstance(metadata, dict):
        return None
    os_block = metadata.get("OS")
    if not isinstance(os_block, dict):
        return None
    family = os_block.get("Family")
    if not isinstance(family, str) or not family:
        return None
    os_meta: dict[str, Any] = {
        "family": family[:_OS_FAMILY_MAX],
        "eosl": bool(os_block.get("EOSL")),
    }
    name = os_block.get("Name")
    if isinstance(name, str) and name:
        os_meta["name"] = name[:_OS_NAME_MAX]
    return os_meta


def _persist_os_metadata(
    *,
    scan_uuid: uuid.UUID,
    report: dict[str, Any],
) -> None:
    """Record the image OS / EOSL block into ``scan_metadata`` (JSONB, no migration).

    Best-effort and self-contained: opens its own transaction and swallows any
    failure so optional OS telemetry never fails an otherwise-good scan.
    """
    os_meta = extract_os_metadata(report)
    if os_meta is None:
        return
    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                return
            merged = dict(scan.scan_metadata or {})
            merged["os"] = os_meta
            scan.scan_metadata = merged
            session.commit()
    except Exception:  # noqa: BLE001 — OS telemetry is best-effort, never fatal
        log.warning("container_os_metadata_persist_failed", scan_id=str(scan_uuid), exc_info=True)


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


def _normalize_severity(value: Any) -> str:
    """Map Trivy severity strings to our ``vuln_severity`` enum."""
    raw = (str(value or "")).lower()
    if raw in ("critical", "high", "medium", "low", "info"):
        return raw
    return "unknown"


__all__ = ["scan_container_task"]
