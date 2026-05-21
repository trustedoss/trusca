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
import subprocess
import time
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
from core.pii_mask import redact_url_userinfo
from core.url_guard import GitUrlValidationError, validate_git_url_with_ip
from integrations import cdxgen as cdxgen_adapter
from integrations import ort as ort_adapter
from integrations._size_guard import enforce_jsonb_row_size_limit
from integrations._subprocess_env import scrubbed_env_for_prep
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
from services.source_archive_service import (
    SourceArchiveError,
    delete_archive,
    resolve_existing_archive,
    safe_extract_archive,
)
from tasks._progress import publish_progress
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_source")


# ---------------------------------------------------------------------------
# Stage progress mapping
# ---------------------------------------------------------------------------

_STAGE_PROGRESS: dict[str, int] = {
    "bootstrap": 0,
    "fetch": 10,
    "prep": 18,
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
            project_git_url = project.git_url
            # feat/zip-upload: snapshot the scan_metadata blob while the row is
            # session-attached. After the `with` block the ORM attribute is
            # expired and touching it would trigger a sync lazy-load on the
            # async engine. A plain dict copy is safe to carry into the
            # pipeline.
            scan_metadata = dict(scan.scan_metadata or {})

        # Run the pipeline outside the first session so each stage commits
        # its own progress update without holding a long-lived transaction.
        _run_pipeline(
            scan_uuid=scan_uuid,
            project_id=project.id,
            workspace=workspace,
            git_url=project_git_url,
            scan_metadata=scan_metadata,
        )
    except _FetchAborted as exc:
        # SSRF guard / fetch refused the project URL — terminal, not a
        # transient. Mark failed with the validator's human-readable reason
        # and let the user (or admin) update the project row.
        log.warning("scan_source_fetch_aborted", error=str(exc))
        _record_terminal_failure(scan_uuid, f"fetch aborted: {exc}")
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


def _run_pipeline(
    *,
    scan_uuid: uuid.UUID,
    project_id: uuid.UUID,
    workspace: Path,
    git_url: str | None,
    scan_metadata: dict[str, Any] | None = None,
) -> None:
    """Execute the scan stages, each with its own commit."""
    # Stage 1 — bootstrap workspace.
    _set_stage(scan_uuid, "bootstrap")
    workspace.mkdir(parents=True, exist_ok=True)

    # Stage 2 — fetch source.
    _set_stage(scan_uuid, "fetch")
    source_dir = _fetch_source(
        scan_uuid=scan_uuid,
        workspace=workspace,
        git_url=git_url,
        project_id=project_id,
        scan_metadata=scan_metadata or {},
        mock_only=False,
    )

    # Stage 2.5 — multi-language pre-cdxgen prep. cdxgen needs a populated
    # lockfile to enumerate transitive deps for Ruby / Rust / Go / .NET; the
    # 2026-05-07 ecosystem-matrix UAT showed bare-source scans returned 0 or
    # only direct deps for those four ecosystems. Best-effort: a failed prep
    # logs a warning and the scan continues with whatever cdxgen can extract.
    _set_stage(scan_uuid, "prep")
    _prepare_for_cdxgen(source_dir=source_dir, scan_uuid=scan_uuid)

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
    # UAT patch (2026-05-07): ORT integration is currently broken — it
    # passes the cdxgen CycloneDX SBOM to `ort evaluate --ort-file ...`
    # which expects an OrtResult JSON produced by `ort analyze`. The
    # KotlinInvalidNullException at parse time aborts every scan. Until
    # the integration is fixed (separate `ort analyze` stage feeding the
    # evaluator), wrap the call in a try/except so the rest of the
    # pipeline (component + license persistence, DT upload, CVE
    # matching) still runs.
    _set_stage(scan_uuid, "ort")
    try:
        ort_result = ort_adapter.run_ort(
            source_dir=source_dir,
            sbom_path=cdxgen_result.sbom_path,
            output_dir=workspace / "ort",
        )
        _persist_artifact(scan_uuid, kind="ort_result", path=ort_result.result_path)
    except Exception as exc:
        log.warning("ort_stage_skipped", error=str(exc)[:300])

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
        # DT runs vulnerability matching asynchronously after BOM upload
        # (BOM_UPLOAD_ANALYSIS event). The first poll within ~1 second of
        # upload typically returns 0 findings even when matches exist —
        # this was the false-empty path observed during the 2026-05-07
        # UAT (54 Maven CVEs that DT had matched, but the scan persisted
        # 0 because the synchronous poll fired too early). Retry with
        # exponential backoff (≤60s budget) so the eventual findings make
        # it onto the scan row before the user sees it.
        _set_stage(scan_uuid, "dt_findings")
        findings = _poll_dt_findings_with_retry(
            dt_client=dt_client,
            breaker=breaker,
            dt_project_uuid=dt_project_uuid,
        )
        with sync_session_scope() as session:
            _persist_findings(session, scan_uuid=scan_uuid, findings=findings)
            session.commit()
    finally:
        dt_client.close()

    # Stage 7 — finalize.
    _set_stage(scan_uuid, "finalize")
    _mark_succeeded(scan_uuid)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


class _FetchAborted(Exception):
    """Raised when the fetch step rejects a project — caught by the task body."""


def _fetch_source(
    *,
    scan_uuid: uuid.UUID,
    workspace: Path,
    git_url: str | None,
    project_id: uuid.UUID | None = None,
    scan_metadata: dict[str, Any] | None = None,
    mock_only: bool = True,
) -> Path:
    """Stage 2 fetch — git clone (default) or uploaded-zip extraction.

    Source selection (feat/zip-upload): ``scan_metadata["source_type"]`` picks
    the strategy. Absent / ``"git"`` keeps the existing git-clone path;
    ``"upload"`` extracts a previously-uploaded zip identified by
    ``scan_metadata["archive_id"]`` through :func:`safe_extract_archive`
    (zip-slip / zip-bomb / symlink hardened) into ``source/``.

    Git behaviour (when ``mock_only=False``):
        - Validate ``git_url`` via :func:`validate_git_url_with_ip` so a
          worker that runs minutes after schema validation re-checks the
          host. This closes I-1 (DNS rebinding TOCTOU) at the worker boundary.
        - Spawn ``git -c http.curloptResolve=<host>:443:<resolved_ip> clone``
          with the validated URL. Pinning the resolved IP at the libcurl
          layer means even if the DNS for the host has rotated to an
          internal address since validation, the connection lands on the
          public IP we already screened.

    Raises:
        _FetchAborted: when the URL fails the SSRF guard, or when an uploaded
            archive is missing / fails a safety check. The task body catches
            this and transitions the scan to ``failed`` with a human-readable,
            credential-free message — same termination path as DT errors.
    """
    source_dir = workspace / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    metadata = scan_metadata or {}
    source_type = metadata.get("source_type", "git")

    # feat/zip-upload: extract an uploaded archive instead of cloning.
    if source_type == "upload":
        return _fetch_uploaded_archive(
            scan_uuid=scan_uuid,
            source_dir=source_dir,
            project_id=project_id,
            archive_id=str(metadata.get("archive_id", "")),
        )

    # Backward-compat path: PR #7/#8 allowed Projects with a NULL git_url
    # (the schema column is still nullable). Refusing those rows would
    # break legacy data + every integration test that seeds a Project via
    # `make_project()` without a git_url. Instead we log + fall through to
    # the legacy placeholder. SSRF risk is zero in this branch because no
    # network I/O happens — cdxgen consumes the empty workspace.
    if not git_url:
        log.info(
            "scan_source_fetch_no_git_url",
            scan_id=str(scan_uuid),
            note="legacy placeholder; no validation needed",
        )
        (source_dir / ".trustedoss-placeholder").write_text("scan-source workspace\n")
        return source_dir

    try:
        normalized_url, resolved_ip = validate_git_url_with_ip(git_url)
    except GitUrlValidationError as exc:
        # The schema layer already validated this URL on insert — getting
        # here means either DNS has rotated (rebinding) or the row was
        # mutated past the schema. Either way we refuse to proceed.
        # M-1 fix: never log raw git_url — userinfo may carry a PAT or
        # similar bearer credential. Redact userinfo before structlog emits
        # the JSON line; the validator's `exc` text only references the
        # parsed host, never the credential, so it is safe to include.
        log.warning(
            "scan_source_fetch_url_rejected",
            git_url=redact_url_userinfo(git_url),
            error=str(exc),
        )
        # The exception message is captured into `scan.error_message` and
        # may surface in the UI / audit log; keep it credential-free.
        raise _FetchAborted("git_url failed worker-side validation") from exc

    if mock_only:
        # Placeholder today — keeps existing tests green while the IP-pin
        # validation runs unconditionally.
        (source_dir / ".trustedoss-placeholder").write_text("scan-source workspace\n")
        # M-1 fix: validate_git_url_with_ip's normalized_url comes from
        # urlsplit(...).hostname so userinfo is already stripped — but
        # redact defensively in case a future refactor changes the
        # normalization contract.
        log.info(
            "scan_source_fetch_mock",
            normalized_url=redact_url_userinfo(normalized_url),
            resolved_ip=resolved_ip,
            scan_id=str(scan_uuid),
        )
        return source_dir

    # Real clone path (dead today; activated when mock_only=False).
    # IP-pin format: host:port:ip. We default to 443 for https and 22 for
    # ssh; the curl option only matters for HTTPS, so SSH skips the -c
    # flag entirely.
    from urllib.parse import urlsplit

    parts = urlsplit(normalized_url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if scheme == "https" else 80 if scheme == "http" else 22)
    target = source_dir / "repo"

    if scheme in ("http", "https"):
        cmd = [
            "git",
            "-c",
            f"http.curloptResolve={host}:{port}:{resolved_ip}",
            "clone",
            "--depth",
            "1",
            normalized_url,
            str(target),
        ]
    else:
        cmd = ["git", "clone", "--depth", "1", normalized_url, str(target)]

    # subprocess is imported at module scope so the prep helper can use it
    # too (chore PR #4); the dead-code branch below shares that import.
    log.info(  # pragma: no cover — dead-code branch
        "scan_source_fetch_real",
        normalized_url=redact_url_userinfo(normalized_url),
        resolved_ip=resolved_ip,
        host=host,
        port=port,
    )
    completed = subprocess.run(  # noqa: S603  # pragma: no cover — dead-code branch
        # cmd is built from validate_git_url_with_ip output (allowlisted scheme,
        # screened IP) — there is no shell execution and no user-controlled
        # arguments past the URL itself. Bandit's "untrusted input" warning
        # is a false positive for this controlled invocation.
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover — dead-code branch
        raise _FetchAborted(
            f"git clone exited {completed.returncode}: {completed.stderr.strip()[:500]}"
        )
    return source_dir


def _fetch_uploaded_archive(
    *,
    scan_uuid: uuid.UUID,
    source_dir: Path,
    project_id: uuid.UUID | None,
    archive_id: str,
) -> Path:
    """Materialise an uploaded zip into ``source_dir`` (feat/zip-upload).

    Resolution + extraction both run inside the worker, never trusting the
    queued metadata: ``archive_id`` is re-parsed as a UUID by
    :func:`resolve_existing_archive`, and :func:`safe_extract_archive` rejects
    zip-slip / zip-bomb / symlink members before any byte lands in the
    workspace.

    Raises:
        _FetchAborted: archive missing on disk, or the archive failed a safety
            check. The message is credential-free and surfaces on the scan row.
    """
    if project_id is None:
        raise _FetchAborted("upload source scan is missing its project id")

    try:
        zip_path = resolve_existing_archive(project_id, archive_id)
    except SourceArchiveError as exc:
        log.warning(
            "scan_source_fetch_archive_missing",
            scan_id=str(scan_uuid),
            project_id=str(project_id),
            error=str(exc),
        )
        raise _FetchAborted(f"uploaded archive unavailable: {exc}") from exc

    try:
        safe_extract_archive(archive_path=zip_path, target_dir=source_dir)
    except SourceArchiveError as exc:
        # ArchiveExtractionRejected (zip slip/bomb/symlink) or ArchiveInvalid
        # (corrupt zip). Either way the scan terminates; the message never
        # echoes archive contents. H-fix (part a): a rejected archive is dead
        # weight — delete it here too so a hostile / corrupt upload cannot sit
        # on the volume forever (it can never produce a successful scan).
        deleted = delete_archive(project_id, archive_id)
        log.warning(
            "scan_source_fetch_archive_rejected",
            scan_id=str(scan_uuid),
            project_id=str(project_id),
            archive_deleted=deleted,
            error=str(exc),
        )
        raise _FetchAborted(f"uploaded archive rejected: {exc}") from exc

    # H-fix (part a): the archive has been fully extracted into the workspace;
    # the source of truth from here on is ``source_dir``. Delete the saved zip
    # so it does not accumulate on the workspace volume after every scan
    # (disk-exhaustion DoS). Best-effort — a failed unlink is swept by the
    # retention beat. We never trust info from the queued metadata for the
    # path: ``delete_archive`` re-validates ``archive_id`` as a UUID.
    deleted = delete_archive(project_id, archive_id)
    log.info(
        "scan_source_fetch_archive_extracted",
        scan_id=str(scan_uuid),
        project_id=str(project_id),
        archive_deleted=deleted,
    )
    return source_dir


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
    # Snapshot the percent under the row (defaults to 0 when None — protects
    # against an early-failure path where progress was never initialised).
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
    # Publish AFTER the DB commit so a subscriber that reads the row on
    # receipt sees the same state as the published payload.
    publish_progress(scan_uuid, step=stage, percent=committed_percent)


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


# ---------------------------------------------------------------------------
# Multi-language pre-cdxgen prep
# ---------------------------------------------------------------------------


# Per-language step timeout. 5 minutes is enough for `bundle lock` /
# `cargo generate-lockfile` / `go mod tidy` / `dotnet restore` on the
# pilot repos in the 2026-05-07 matrix (none exceeded ~60s) while still
# capping a runaway resolver before it eats the scan's 60-min budget.
_PREP_STEP_TIMEOUT_SECONDS = 300


# subprocess env scrubbing was promoted to ``integrations._subprocess_env``
# in chore PR #6 so the same helper covers prep / cdxgen / ORT. The
# alias below preserves the legacy module path used by tests and the
# ``_run_prep`` call site below.
_scrubbed_env = scrubbed_env_for_prep


def _prepare_for_cdxgen(*, source_dir: Path, scan_uuid: uuid.UUID) -> None:
    """Run language-specific lockfile / dependency-resolution steps before
    handing the workspace to cdxgen.

    cdxgen reads existing lockfiles (Gemfile.lock / Cargo.lock / go.sum /
    `obj/project.assets.json`) to enumerate transitive dependencies. When
    those are absent the SBOM only lists direct deps — or zero, depending
    on the ecosystem (see docs/sessions/2026-05-07-uat-multi-ecosystem-
    matrix.md for the per-ecosystem breakdown).

    Each step runs at most once per scan and is best-effort: a failure
    logs a warning and the scan continues. We never raise from here — the
    surrounding `_run_pipeline` would map any exception onto the scan's
    terminal-failure path, but a missing transitive deps list is a
    degraded-output scenario, not a fatal one.
    """
    timeout = _PREP_STEP_TIMEOUT_SECONDS

    if (source_dir / "Gemfile").exists() and not (source_dir / "Gemfile.lock").exists():
        _run_prep(
            "bundle lock", ["bundle", "lock"], source_dir, timeout, scan_uuid
        )
    if (source_dir / "Cargo.toml").exists() and not (source_dir / "Cargo.lock").exists():
        _run_prep(
            "cargo generate-lockfile",
            ["cargo", "generate-lockfile"],
            source_dir,
            timeout,
            scan_uuid,
        )
    if (source_dir / "go.mod").exists():
        # `go mod tidy` is idempotent — re-running with go.sum already
        # present just verifies the graph. Run unconditionally so a
        # partial / out-of-date go.sum is healed before cdxgen reads it.
        _run_prep("go mod tidy", ["go", "mod", "tidy"], source_dir, timeout, scan_uuid)
    if any(source_dir.glob("*.csproj")) and shutil.which("dotnet"):
        _run_prep("dotnet restore", ["dotnet", "restore"], source_dir, timeout, scan_uuid)


def _run_prep(
    name: str,
    cmd: list[str],
    cwd: Path,
    timeout: int,
    scan_uuid: uuid.UUID,
) -> None:
    """Best-effort prep — log failure but don't abort the scan.

    cdxgen still produces a partial SBOM from raw source if prep fails,
    so a Gemfile-only repo with a flaky network is degraded but not
    broken. We capture stdout/stderr (text) so structlog can record
    actionable failure context — limited to 500 chars to bound a runaway
    resolver's diagnostic spew, which has been seen on cargo network
    timeouts.

    Security: ``cmd`` is a hardcoded list that originates in
    ``_prepare_for_cdxgen`` (no user input). ``cwd`` is the scan's own
    workspace directory, which the worker created earlier in this
    pipeline. There is no shell interpolation. Bandit's S603 warning
    ("subprocess call - check for execution of untrusted input") is a
    false positive for this controlled invocation.

    The subprocess receives a scrubbed env (``_scrubbed_env``) — worker
    secrets like ``DT_API_KEY`` / ``SECRET_KEY`` / ``DATABASE_URL`` /
    ``*_WEBHOOK_URL`` are not inherited, so a hostile clone cannot use
    a malicious NuGet feed or Go ``replace`` directive to exfiltrate
    them through resolver telemetry.
    """
    try:
        result = subprocess.run(  # noqa: S603 — see docstring
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_scrubbed_env(),
        )
        log.info(
            "prep_finished",
            step=name,
            scan_id=str(scan_uuid),
            returncode=result.returncode,
        )
        if result.returncode != 0:
            log.warning(
                "prep_failed",
                step=name,
                scan_id=str(scan_uuid),
                stderr=(result.stderr or "")[:500],
            )
    except subprocess.TimeoutExpired:
        log.warning(
            "prep_timeout",
            step=name,
            scan_id=str(scan_uuid),
            timeout=timeout,
        )
    except OSError as exc:
        # FileNotFoundError (no language layer in the worker image) +
        # PermissionError (workspace mounted noexec) + the wider OSError
        # family — all are "host condition is degraded, prep cannot run"
        # rather than "scan should abort". Log and let cdxgen extract
        # whatever it can from the bare source. We deliberately do NOT
        # catch bare ``Exception`` here so a real bug in our wrapper still
        # bubbles up to the surrounding terminal-failure path.
        log.warning(
            "prep_unavailable",
            step=name,
            scan_id=str(scan_uuid),
            cmd=cmd[0],
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# DT findings retry-with-backoff
# ---------------------------------------------------------------------------


_DT_FINDINGS_POLL_DELAYS_SECONDS: tuple[int, ...] = (2, 4, 8, 16, 30)


def _poll_dt_findings_with_retry(
    *,
    dt_client: DTClient,
    breaker: CircuitBreaker,
    dt_project_uuid: str,
) -> list[dict[str, Any]]:
    """Poll DT for findings with exponential backoff.

    DT runs the OSV / NVD matcher asynchronously when a BOM is uploaded
    (BOM_UPLOAD_ANALYSIS event). The first poll within ~1s of upload
    typically returns 0 findings even when matches will eventually
    materialise — this was the false-empty seen across the UAT pilots.

    Strategy: sleep, then poll. Total budget is the sum of
    ``_DT_FINDINGS_POLL_DELAYS_SECONDS`` (~60s for the default
    2/4/8/16/30 schedule). Return as soon as we see a non-empty result —
    DT's matcher emits the full set in one go, not a streaming partial
    view. If every attempt returns empty we return an empty list rather
    than raising; the caller persists zero findings, which matches the
    current "no matches" behaviour.

    The breaker still wraps each poll, so a DT outage mid-retry trips
    the breaker and short-circuits the remaining attempts.

    Tests inject a no-op delay schedule via
    ``monkeypatch.setattr("tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,))``
    or replace ``tasks.scan_source.time.sleep`` directly.
    """
    findings: list[dict[str, Any]] = []
    for attempt, delay in enumerate(_DT_FINDINGS_POLL_DELAYS_SECONDS, start=1):
        time.sleep(delay)
        findings = breaker.call(
            lambda: dt_client.get_findings(project_uuid=dt_project_uuid)
        )
        log.info(
            "dt_findings_poll",
            attempt=attempt,
            delay=delay,
            count=len(findings),
        )
        if findings:
            return findings
    return findings


def _persist_components(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    sbom: dict[str, Any],
) -> None:
    """Upsert components / component versions / scan components / license
    findings from a cdxgen CycloneDX SBOM.

    UAT patch (2026-05-07): the original implementation only persisted the
    component graph and left ``license_findings`` empty (the original design
    relied on ORT's evaluator output, but the ORT integration is broken —
    see scan_source._run_pipeline). cdxgen does emit each component's
    declared SPDX license inside ``components[].licenses``, so we now also
    upsert ``licenses`` + ``license_findings`` rows here. License kind is
    fixed to ``"declared"`` because cdxgen's data is package-metadata-derived
    (npm `license`, maven `<licenses>`, gradle resolved POM); ORT would
    additionally emit ``concluded`` / ``detected`` after running the license
    scanner, which we'll wire when the analyzer stage is fixed.
    """
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

        _persist_component_licenses(
            session,
            scan_uuid=scan_uuid,
            component_version_id=component_version.id,
            cdxgen_component=raw,
            purl=purl,
        )


# ---------------------------------------------------------------------------
# License extraction (cdxgen → license_findings)
# ---------------------------------------------------------------------------


# CycloneDX `licenses[].license.id` (SPDX) or `licenses[].expression` is what
# we read. Permissive defaults — the entries are just the well-known SPDX
# identifiers we expect to see most often. Anything else lands in `unknown`.
_LICENSE_CATEGORY_DEFAULTS: dict[str, str] = {
    # Allowed
    "MIT": "allowed",
    "Apache-2.0": "allowed",
    "BSD-2-Clause": "allowed",
    "BSD-3-Clause": "allowed",
    "ISC": "allowed",
    "Unlicense": "allowed",
    "CC0-1.0": "allowed",
    "0BSD": "allowed",
    "Zlib": "allowed",
    "WTFPL": "allowed",
    "Python-2.0": "allowed",
    # Conditional
    "LGPL-2.0-only": "conditional",
    "LGPL-2.0-or-later": "conditional",
    "LGPL-2.1-only": "conditional",
    "LGPL-2.1-or-later": "conditional",
    "LGPL-3.0-only": "conditional",
    "LGPL-3.0-or-later": "conditional",
    "MPL-1.1": "conditional",
    "MPL-2.0": "conditional",
    "EPL-1.0": "conditional",
    "EPL-2.0": "conditional",
    "CDDL-1.0": "conditional",
    "CDDL-1.1": "conditional",
    "Apache-1.1": "conditional",
    # Forbidden
    "GPL-2.0-only": "forbidden",
    "GPL-2.0-or-later": "forbidden",
    "GPL-3.0-only": "forbidden",
    "GPL-3.0-or-later": "forbidden",
    "AGPL-3.0-only": "forbidden",
    "AGPL-3.0-or-later": "forbidden",
    "SSPL-1.0": "forbidden",
    "BUSL-1.1": "forbidden",
}


def _classify_license_category(spdx_id: str | None) -> str:
    if not spdx_id:
        return "unknown"
    return _LICENSE_CATEGORY_DEFAULTS.get(spdx_id, "unknown")


def _extract_spdx_ids(cdxgen_component: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Pull (spdx_id, reference_url) tuples out of a cdxgen component entry.

    CycloneDX shapes the ``licenses`` field as a list, where each entry is
    one of:
      - ``{"license": {"id": "<spdx>", "url": "<reference>"}}``
      - ``{"license": {"name": "<free-text>", "url": "<reference>"}}``
      - ``{"expression": "<spdx-expression>"}``

    We accept the first form (preferred — exact SPDX), accept the third when
    it parses as a single SPDX id (no AND/OR/WITH), and skip free-text
    license names — those would require a license-text identifier scanner
    (ORT or scancode) to map to SPDX, which is out of scope for the cdxgen
    fast-path.
    """
    out: list[tuple[str, str | None]] = []
    licenses = cdxgen_component.get("licenses") or []
    if not isinstance(licenses, list):
        return out
    for entry in licenses:
        if not isinstance(entry, dict):
            continue
        lic = entry.get("license") or {}
        if isinstance(lic, dict):
            spdx = lic.get("id")
            url = lic.get("url")
            if isinstance(spdx, str) and spdx:
                out.append((spdx, url if isinstance(url, str) else None))
                continue
        expression = entry.get("expression")
        if isinstance(expression, str) and expression and not any(
            kw in expression for kw in (" AND ", " OR ", " WITH ")
        ):
            out.append((expression.strip(), None))
    return out


def _get_or_create_license(
    session: Session,
    *,
    spdx_id: str,
    reference_url: str | None,
) -> Any:
    from models import License as LicenseModel

    existing = session.execute(
        select(LicenseModel).where(LicenseModel.spdx_id == spdx_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    lic = LicenseModel(
        spdx_id=spdx_id,
        name=spdx_id,
        category=_classify_license_category(spdx_id),
        reference_url=reference_url,
    )
    session.add(lic)
    session.flush()
    return lic


def _persist_component_licenses(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    component_version_id: uuid.UUID,
    cdxgen_component: dict[str, Any],
    purl: str | None = None,
) -> None:
    """For each SPDX license on the cdxgen component, upsert a License row
    and emit a ``declared`` LicenseFinding tying it to this scan.

    chore PR #5 Part B (`docs/sessions/_next-session-prompt-chore-pr5.md`):
    when cdxgen produced **no** SPDX ids for the component, fall back to
    the multi-ecosystem license fetcher. The fetcher hits the relevant
    registry (Maven Central / PyPI / crates.io / pkg.go.dev), caches
    the answer in ``license_fetch_cache`` (24h TTL, positive +
    negative), and returns a single ``LicenseFetchResult``. We then
    emit a *concluded* LicenseFinding so downstream consumers can tell
    a registry-derived licence apart from a package-metadata-derived
    one (cdxgen → ``declared``, fetcher → ``concluded``). When ORT's
    real concluded analysis lands the two paths reconcile via the
    existing ``kind`` discriminator.
    """
    spdx_pairs = _extract_spdx_ids(cdxgen_component)
    for spdx_id, ref_url in spdx_pairs:
        license_row = _get_or_create_license(
            session, spdx_id=spdx_id, reference_url=ref_url
        )
        finding = LicenseFinding(
            scan_id=scan_uuid,
            component_version_id=component_version_id,
            license_id=license_row.id,
            kind="declared",
            source_path=None,
            raw_data={"source": "cdxgen"},
        )
        session.add(finding)

    if spdx_pairs:
        # cdxgen had something — fetcher fall-back is a cost-saver, no
        # value added when we already have a declared license.
        return
    if not purl:
        return

    # Lazy import to keep `models`/scan_source import order stable —
    # the fetcher imports back into `models` for the cache table.
    from integrations.license_fetcher import fetch_license

    try:
        result = fetch_license(purl, session=session)
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment
        # The fetcher already swallows network / parse errors and
        # returns None; this catches the unlikely case where the cache
        # write itself blows up (e.g. unique-violation race) so a bad
        # cache row never aborts a scan.
        log.warning(
            "license_fetcher_unexpected_error",
            purl=purl,
            error=str(exc)[:300],
        )
        return
    if result is None:
        return
    license_row = _get_or_create_license(
        session, spdx_id=result.spdx_id, reference_url=result.reference_url
    )
    finding = LicenseFinding(
        scan_id=scan_uuid,
        component_version_id=component_version_id,
        license_id=license_row.id,
        kind="concluded",
        source_path=None,
        raw_data={"source": result.source},
    )
    session.add(finding)


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
