"""
External CycloneDX SBOM ingest Celery task.

A user uploads a CycloneDX JSON SBOM (no first-party source, no clone, no
cdxgen, no scancode, no signing). This task reuses the *back half* of the
source-scan pipeline — component persistence → Trivy SBOM matching →
findings persistence → finalize — against the uploaded document.

CLAUDE.md core rule #3: like the source/container scans, this runs
asynchronously inside a Celery worker. The FastAPI request handler that
accepted the upload only persisted a ``Scan`` row in state ``queued`` with
``kind="sbom"`` and wrote the validated CycloneDX file to disk under
``{workspace_root()}/sbom-ingest/{project_id}/{scan_id}.cdx.json``; the path
is carried in ``scan_metadata["sbom_path"]``.

CLAUDE.md core rule #4 (post-W6): Trivy is the single vulnerability-matching
engine. ``persist_sbom_components`` runs BEFORE ``run_trivy_sbom`` because the
Trivy persister matches findings to ``ComponentVersion`` rows by PURL — the
component graph must exist first.

Idempotency:
    Keyed off ``scan_id`` (Celery ``task_acks_late=True`` + a worker restart
    can re-enter on the same id). We:
      1. Skip immediately if the scan already reached ``succeeded``.
      2. Otherwise treat the run as a fresh start: ``_reset_scan_for_rerun``
         wipes prior ScanComponent / VulnerabilityFinding / LicenseFinding /
         ScanArtifact / edge rows for this scan, then every stage re-runs.
    The DB partial-unique index ("at most one in-flight scan per project")
    already prevents a parallel collision.

Workspace:
    A fresh ``{workspace_root()}/<scan_id>/`` holds the transient Trivy output
    and is removed in ``finally`` (``shutil.rmtree(ignore_errors=True)`` — the
    orphan workspace cleaner reclaims anything a SIGKILL leaves behind). The
    uploaded SBOM lives OUTSIDE this per-scan tree (under
    ``{workspace_root()}/sbom-ingest/...``) so the ``finally`` cleanup never
    deletes it — that durable copy backs the ``sbom_cyclonedx`` ScanArtifact
    so the SBOM signature/bundle download endpoints keep working for an
    ingested scan.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded

from core.config import scan_soft_time_limit_seconds, workspace_root
from core.db import sync_session_scope
from integrations.trivy import (
    TrivyError,
    TrivyFailed,
    TrivyNotInstalled,
    TrivyTimeout,
    run_trivy_sbom,
)
from models import Project, Scan
from services.vulnerability_matching import persist_trivy_findings
from tasks._progress import (
    close_log_file,
    make_line_callback,
    reset_log_counter,
)
from tasks._scan_pipeline import (
    mark_failed,
    mark_succeeded,
    record_terminal_failure,
    set_stage,
)
from tasks.celery_app import celery_app
from tasks.scan_source import (
    _mark_running,
    _persist_artifact,
    _reset_scan_for_rerun,
    persist_sbom_components,
)

log = structlog.get_logger("tasks.ingest_sbom")


# ---------------------------------------------------------------------------
# Stage progress mapping
# ---------------------------------------------------------------------------
#
# A condensed view of the source pipeline's stages — the ingest path skips
# fetch / prep / cdxgen / sign / scancode / approvals / preserve. Percentages
# stay monotonic so the WS progress frame contract holds for clients that also
# render source scans.
_STAGE_PROGRESS: dict[str, int] = {
    "bootstrap": 0,
    "components": 40,
    "trivy": 80,
    "finalize": 100,
}


def _set_stage(scan_uuid: uuid.UUID, stage: str) -> None:
    """Advance to ``stage`` using this task's percent mapping."""
    set_stage(scan_uuid, stage, _STAGE_PROGRESS.get(stage))


# ScanArtifact.kind for the ingested CycloneDX document. We reuse the SAME kind
# the source pipeline writes for the cdxgen SBOM so the signature/bundle
# download surface (``services.sbom_signature.KIND_SBOM == "sbom_cyclonedx"``)
# resolves it uniformly; the SBOM *export* endpoint rebuilds from DB rows and
# does not depend on this artifact.
_SBOM_ARTIFACT_KIND = "sbom_cyclonedx"


# ---------------------------------------------------------------------------
# Public Celery task
# ---------------------------------------------------------------------------


# Time limits are passed per dispatch by ``tasks.enqueue_scan`` via
# ``apply_async(soft_time_limit=..., time_limit=...)`` (read from env at
# dispatch time — CLAUDE.md rule #11), NOT pinned on the decorator. Mirrors
# ``scan_source_task``.
@celery_app.task(  # type: ignore[misc]
    name="trustedoss.ingest_sbom",
    bind=True,
)
def ingest_sbom_task(self: Any, scan_id: str) -> None:
    """
    Ingest an uploaded CycloneDX SBOM to completion.

    Args:
        scan_id: UUID **string** (Celery JSON serialization compatibility).
    """
    structlog.contextvars.bind_contextvars(
        scan_id=scan_id, task_id=self.request.id, task_kind="sbom"
    )
    try:
        scan_uuid = uuid.UUID(scan_id)
    except ValueError:
        log.error("ingest_sbom_invalid_scan_id", scan_id=scan_id)
        return

    # Drop any per-scan log-line budget left from a previous run (acks_late +
    # worker restart can re-enter on the same scan_id). Symmetric with the
    # prior-rows wipe in _reset_scan_for_rerun. Idempotent on a first run.
    reset_log_counter(scan_uuid)

    workspace = Path(workspace_root()) / str(scan_uuid)

    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                log.warning("ingest_sbom_missing_scan_row")
                return
            if scan.status == "succeeded":
                log.info("ingest_sbom_already_succeeded")
                return

            project = session.get(Project, scan.project_id)
            if project is None:
                mark_failed(session, scan, "project no longer exists")
                return

            _reset_scan_for_rerun(session, scan)
            _mark_running(session, scan)
            project_id = project.id
            # Snapshot the metadata blob while the row is session-attached; after
            # the `with` block the ORM attribute is expired and touching it would
            # trigger a sync lazy-load on the async engine. A plain dict copy is
            # safe to carry into the pipeline.
            scan_metadata = dict(scan.scan_metadata or {})

        _run_pipeline(
            scan_uuid=scan_uuid,
            project_id=project_id,
            workspace=workspace,
            scan_metadata=scan_metadata,
        )
    except _IngestAborted as exc:
        # The uploaded SBOM is missing / outside the workspace / not JSON.
        # Terminal — the synchronous service Pass does the authoritative
        # validation; this is a minimal defensive backstop.
        log.warning("ingest_sbom_aborted", error=str(exc))
        record_terminal_failure(scan_uuid, f"SBOM ingest aborted: {exc}")
    except TrivyNotInstalled as exc:
        log.error("ingest_sbom_trivy_not_installed", error=str(exc))
        record_terminal_failure(scan_uuid, f"Trivy binary missing: {exc}")
    except TrivyTimeout as exc:
        log.warning("ingest_sbom_trivy_timeout", error=str(exc))
        record_terminal_failure(scan_uuid, f"Trivy scan timed out: {exc}")
    except TrivyFailed as exc:
        log.error("ingest_sbom_trivy_failed", error=str(exc))
        record_terminal_failure(scan_uuid, f"Trivy scan failed: {exc}")
    except TrivyError as exc:
        # Catch-all for any other Trivy adapter subclass added later.
        log.error("ingest_sbom_trivy_error", error=str(exc))
        record_terminal_failure(scan_uuid, f"Trivy error: {exc}")
    except SoftTimeLimitExceeded:
        # Mirrors scan_source: a timed-out ingest is terminal, not retryable.
        # Caught BEFORE the bare Exception handler so the message stays
        # specific.
        soft_limit = scan_soft_time_limit_seconds()
        log.warning(
            "ingest_sbom_timed_out",
            scan_id=str(scan_uuid),
            soft_limit_seconds=soft_limit,
        )
        record_terminal_failure(
            scan_uuid, f"SBOM ingest exceeded the time limit ({soft_limit}s)"
        )
    except Exception as exc:
        # Fail-loud over retry-forever: any unhandled exception terminates the
        # scan with status='failed' and a visible error message.
        log.exception("ingest_sbom_unhandled_error")
        record_terminal_failure(scan_uuid, f"unexpected error: {exc}")
    finally:
        # Release the per-scan disk-log handle BEFORE rmtree so the FD does not
        # race with the directory removal. Idempotent: no handle was ever opened
        # for a scan that emitted no log lines. The rmtree removes ONLY the
        # transient per-scan workspace — the uploaded SBOM (and its durable
        # ScanArtifact path) live under {workspace_root()}/sbom-ingest/... and
        # survive.
        close_log_file(scan_uuid)
        shutil.rmtree(workspace, ignore_errors=True)
        structlog.contextvars.unbind_contextvars("scan_id", "task_id", "task_kind")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class _IngestAborted(Exception):
    """Raised when the uploaded SBOM cannot be loaded — caught by the task body."""


def _run_pipeline(
    *,
    scan_uuid: uuid.UUID,
    project_id: uuid.UUID,
    workspace: Path,
    scan_metadata: dict[str, Any],
) -> None:
    """Execute the ingest stages, each committing its own progress update."""
    # Scan-log verbosity parity with the source pipeline: a per-scan
    # ``metadata.verbosity == "verbose"`` flips Trivy into its verbose mode.
    verbose = str(scan_metadata.get("verbosity", "normal")) == "verbose"

    # Stage 1 — bootstrap workspace.
    _set_stage(scan_uuid, "bootstrap")
    workspace.mkdir(parents=True, exist_ok=True)

    # Load + minimally validate the uploaded SBOM. The synchronous service Pass
    # does the authoritative CycloneDX validation; here we only guard against an
    # absent file, a path that escaped the workspace root, or non-JSON content.
    sbom_path, sbom_dict = _load_uploaded_sbom(scan_metadata)

    # Stage 2 — persist components + declared licenses. MUST run before Trivy:
    # ``persist_trivy_findings`` matches each finding to a ``ComponentVersion``
    # by PURL, so the component graph has to exist first. ``source_dir=None``
    # because an ingested SBOM has no first-party source tree (no npm-lockfile
    # enrichment, no scancode detections).
    _set_stage(scan_uuid, "components")
    with sync_session_scope() as session:
        persist_sbom_components(
            session,
            scan_uuid=scan_uuid,
            sbom=sbom_dict,
            source_dir=None,
        )
        session.commit()

    # Preserve the uploaded SBOM as a ScanArtifact so the signature/bundle
    # download surface resolves it (same ``kind`` the source pipeline writes for
    # the cdxgen SBOM). We point the artifact at the DURABLE upload path under
    # {workspace_root()}/sbom-ingest/... (NOT the per-scan workspace, which the
    # `finally` rmtree deletes). _persist_artifact no-ops if the path is gone.
    _persist_artifact(scan_uuid, kind=_SBOM_ARTIFACT_KIND, path=sbom_path)

    # Stage 3 — Trivy SBOM matching. ``run_trivy_sbom`` re-validates that
    # ``sbom_path`` resolves inside WORKSPACE_HOST_PATH (it does — the ingest
    # path is under workspace_root()), then writes its report into the transient
    # per-scan workspace. Trivy errors propagate to the task body's typed except
    # blocks; the component graph above is already committed, so a matching
    # failure still leaves the user a populated component view (degraded, not
    # empty) — same philosophy as the source pipeline.
    _set_stage(scan_uuid, "trivy")
    trivy_result = run_trivy_sbom(
        sbom_path=sbom_path,
        output_dir=workspace / "trivy",
        line_callback=make_line_callback(scan_uuid, stage="trivy"),
        verbose=verbose,
    )
    # Persist the Trivy report alongside (transient) so admin/debug can diff what
    # Trivy consumed against what we matched — mirrors the source pipeline.
    _persist_artifact(
        scan_uuid, kind="trivy_sbom_report", path=trivy_result.report_path
    )
    with sync_session_scope() as session:
        inserted = persist_trivy_findings(
            session,
            scan_uuid=scan_uuid,
            trivy_report=trivy_result.report,
        )
        session.commit()
    log.info(
        "ingest_sbom_trivy_done",
        scan_id=str(scan_uuid),
        findings_persisted=inserted,
    )

    # Stage 4 — finalize. ``mark_succeeded`` itself sets current_step="finalize",
    # progress_percent=100, completed_at, supersedes prior ref-keyed scans, and
    # publishes the final frame — so a separate set_stage("finalize") would be a
    # redundant frame. We rely on it directly (matching the documented contract).
    mark_succeeded(scan_uuid)


def _load_uploaded_sbom(scan_metadata: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Resolve, containment-check, and parse the uploaded CycloneDX SBOM.

    Returns ``(sbom_path, parsed_dict)``. Raises :class:`_IngestAborted` on a
    missing path key, a path that resolves outside ``workspace_root()``, an
    absent file, or invalid JSON. This is a minimal defensive backstop — the
    synchronous service Pass owns the authoritative CycloneDX schema validation.
    """
    raw_path = scan_metadata.get("sbom_path")
    if not raw_path or not isinstance(raw_path, str):
        raise _IngestAborted("scan_metadata.sbom_path is missing")

    try:
        root = Path(workspace_root()).resolve()
        candidate = Path(raw_path).resolve()
    except OSError as exc:
        raise _IngestAborted(f"sbom_path could not be resolved: {exc}") from exc

    # Containment guard (defense-in-depth): the path is operator/worker-written
    # but a tampered/garbled metadata row must never let this task read an
    # arbitrary file. ``run_trivy_sbom`` re-checks this too, but failing here is
    # clearer (and avoids spawning a Trivy process on a bad input).
    if not candidate.is_relative_to(root):
        raise _IngestAborted("sbom_path resolves outside the workspace root")

    if not candidate.is_file():
        raise _IngestAborted(f"SBOM file not found: {candidate}")

    try:
        with candidate.open("rb") as fh:
            parsed = json.loads(fh.read())
    except (OSError, ValueError) as exc:
        raise _IngestAborted(f"SBOM file is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise _IngestAborted("SBOM document is not a JSON object")

    return candidate, parsed
