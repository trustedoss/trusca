"""
Source scan Celery task — cdxgen → scancode (first-party) → Trivy SBOM matching.

W6 (ADR-0001): DT was replaced by ``trivy sbom`` for CVE matching. cdxgen
still enumerates components and writes a CycloneDX JSON SBOM; Trivy now
matches that SBOM against its bundled DB (NVD + GHSA + OSV + EPSS + KEV)
and produces a Trivy JSON report which the persister
(``services.vulnerability_matching.persist_trivy_findings``) folds into
``vulnerability_findings``. No DT calls, no circuit breaker, no async
HTTP upload — Trivy is a local subprocess that runs against a file.

PR-A2: the ORT ``evaluate`` stage was removed (it was broken — it fed a
CycloneDX SBOM to ``ort evaluate --ort-file``, which expects an OrtResult JSON,
and aborted every scan with a KotlinInvalidNullException; we had been swallowing
that with a try/except). License classification for *third-party* dependencies
remains *declared* (cdxgen package metadata, persisted in ``_persist_components``
→ ``_persist_component_licenses``). PR-A2 adds *detected* license findings for
*first-party* source via scancode — third-party dependency sources are NOT
downloaded (that deep-scan path is out of scope; it would blow the budget).

CLAUDE.md core rule #3: this pipeline runs asynchronously inside a Celery
worker; the FastAPI request handler that triggered the scan only persisted a
``Scan`` row in state ``queued``.

CLAUDE.md core rule #4 (post-W6): Trivy is the single vulnerability-matching
engine. A Trivy subprocess failure is terminal for the matching stage but
does NOT abort the scan — the SBOM + license findings are still persisted,
the scan is marked ``failed`` with a clear ``error_message`` so the user can
see what went wrong.

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

import hashlib
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import String, case, cast, delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.config import (
    scan_soft_time_limit_seconds,
    slsa_builder_id,
    slsa_builder_version,
    workspace_root,
)
from core.db import sync_session_scope
from core.pii_mask import mask_pii, redact_url_userinfo
from core.url_guard import GitUrlValidationError, validate_git_url_with_ip
from integrations import attestation as attestation_builder
from integrations import cdxgen as cdxgen_adapter
from integrations import cosign as cosign_adapter
from integrations import scancode as scancode_adapter
from integrations._size_guard import enforce_jsonb_row_size_limit
from integrations._subprocess_env import scrubbed_env_for_prep
from integrations.dependency_graph import (
    compute_depths,
    graph_depths_from_sbom,
    parse_dependency_graph,
)
from integrations.npm_lockfile import NpmLockfileData, read_lockfile
from integrations.trivy import (
    TrivyError,
    TrivyFailed,
    TrivyNotInstalled,
    TrivyTimeout,
    run_trivy_sbom,
)
from models import (
    AuditLog,
    Component,
    ComponentDependencyEdge,
    ComponentVersion,
    License,
    LicenseFinding,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    VulnerabilityFinding,
)
from services.component_approval_service import auto_create_pending_approvals
from services.license_expression import evaluate_expression
from services.source_archive_service import (
    SourceArchiveError,
    delete_archive,
    resolve_existing_archive,
    safe_extract_archive,
)
from services.source_preservation_service import preserve_scan_source
from services.vulnerability_matching import persist_trivy_findings
from tasks._progress import (
    close_log_file,
    publish_progress,
    reset_log_counter,
)
from tasks._progress import (
    make_line_callback as _make_line_callback,
)
from tasks.celery_app import celery_app
from tasks.scan_retention import supersede_prior_ref_scans

log = structlog.get_logger("tasks.scan_source")


# ---------------------------------------------------------------------------
# Stage progress mapping
# ---------------------------------------------------------------------------

_STAGE_PROGRESS: dict[str, int] = {
    "bootstrap": 0,
    "fetch": 10,
    "prep": 18,
    "cdxgen": 25,
    # v2.3-s1: cosign SBOM signing runs right after the SBOM is generated +
    # persisted, before scancode. Slotted at 30 (between cdxgen=25 and
    # scancode=50) so the WS progress frame stays monotonic.
    "sign": 30,
    # PR-A2: the "ort" stage slug (50) is replaced by "scancode" at the same
    # percent so the WS progress frame contract stays monotonic — clients that
    # rendered "50%" for the license stage keep rendering 50% for it.
    "scancode": 50,
    # BUG-010: conditional-license components are auto-enrolled into the legal
    # review queue right after the component graph commits, before vuln
    # matching. Slotted between "scancode" (50) and "trivy" (90) so the WS
    # progress frame stays monotonic.
    "approvals": 60,
    # Stage label for ``trivy sbom`` matching (replaces the W6-era
    # ``dt_findings`` slot). v2.4.0 not yet publicly released, so no back-compat
    # shim is needed — the FE PIPELINE_STEPS is updated in the matching FE PR.
    "trivy": 90,
    "finalize": 100,
}


# ---------------------------------------------------------------------------
# Public Celery task
# ---------------------------------------------------------------------------


# PR-A1 (scan stability): the soft/hard time limits are NOT pinned on the
# decorator. Import-time decorator constants would (a) cache the value at
# module load — violating CLAUDE.md rule #11 — and (b) bypass per-dispatch
# env tuning. The limits are passed per call by ``tasks.enqueue_scan`` via
# ``apply_async(soft_time_limit=..., time_limit=...)``, read from
# ``SCAN_SOFT_TIME_LIMIT_SECONDS`` / ``SCAN_HARD_TIME_LIMIT_SECONDS`` at
# dispatch time. Celery preserves those message options across an
# ``acks_late`` redelivery, so a re-executed task stays time-boxed too.
@celery_app.task(  # type: ignore[misc]
    name="trustedoss.scan_source",
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

    # P2 #8c — drop any per-scan log line budget left over from a previous
    # run (Celery acks_late + worker restart can re-enter this task on the
    # same scan_id). Resetting here means a re-execution starts from zero
    # used budget — symmetric with the prior-rows wipe in
    # _reset_scan_for_rerun. Idempotent on first-run scans.
    reset_log_counter(scan_uuid)

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
    except TrivyNotInstalled as exc:
        # W6: an operator deployment without the Trivy binary. Terminal —
        # the worker image MUST ship Trivy 0.50+. Surface a precise message
        # so the user knows it is a deployment issue, not a code defect.
        log.error("scan_source_trivy_not_installed", error=str(exc))
        _record_terminal_failure(scan_uuid, f"Trivy binary missing: {exc}")
    except TrivyTimeout as exc:
        log.warning("scan_source_trivy_timeout", error=str(exc))
        _record_terminal_failure(scan_uuid, f"Trivy scan timed out: {exc}")
    except TrivyFailed as exc:
        log.error("scan_source_trivy_failed", error=str(exc))
        _record_terminal_failure(scan_uuid, f"Trivy scan failed: {exc}")
    except TrivyError as exc:
        # Catch-all for any other Trivy adapter error subclass added later.
        log.error("scan_source_trivy_error", error=str(exc))
        _record_terminal_failure(scan_uuid, f"Trivy error: {exc}")
    except SoftTimeLimitExceeded:
        # PR-A1: the scan exceeded SCAN_SOFT_TIME_LIMIT_SECONDS. Celery raised
        # this inside the worker thread; we mark the scan failed with a clear
        # message and let the shared `finally` reclaim the workspace. We catch
        # this BEFORE the bare `Exception` handler so the message stays
        # specific (a generic "unexpected error" would be misleading for a
        # timeout). Re-raising is intentionally avoided — a timed-out scan is
        # terminal, not retryable.
        soft_limit = scan_soft_time_limit_seconds()
        log.warning("scan_timed_out", scan_id=str(scan_uuid), soft_limit_seconds=soft_limit)
        _record_terminal_failure(
            scan_uuid, f"scan exceeded the time limit ({soft_limit}s)"
        )
    except Exception as exc:
        # Any unhandled exception terminates the scan with status='failed'
        # and surfaces the error message in the UI. Re-raising would have
        # Celery retry the task indefinitely — we explicitly choose
        # fail-loud over retry-forever so operators investigate.
        log.exception("scan_source_unhandled_error")
        _record_terminal_failure(scan_uuid, f"unexpected error: {exc}")
    finally:
        # Release the per-scan disk-log file handle BEFORE rmtree so the FD
        # does not race with the directory removal on a kernel that holds the
        # open fd's inode (worker SIGKILL would leak it; the explicit close
        # makes the happy / terminal path tidy). Idempotent: a scan that never
        # emitted a log line never opened a handle, and close_log_file no-ops
        # for missing keys.
        close_log_file(scan_uuid)
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
    # Scan-log verbosity (feat/scan-log-verbosity): a per-scan
    # ``metadata.verbosity == "verbose"`` flips every tool into its debug /
    # verbose mode so the scan-log drawer renders a full diagnostic trace. The
    # schema validator already constrains the value to {"normal","verbose"};
    # any other value (or absence) falls back to the quiet "normal" trace.
    verbose = str((scan_metadata or {}).get("verbosity", "normal")) == "verbose"

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
        # P2 #8c — stream cdxgen stdout/stderr lines onto the scan WebSocket
        # so the drawer can render a live tool trace. Best-effort: a publish
        # error inside the callback never propagates, and the per-scan line
        # budget caps runaway tools (publish_log enforces it internally).
        line_callback=_make_line_callback(scan_uuid, stage="cdxgen"),
        verbose=verbose,
    )
    _persist_artifact(
        scan_uuid,
        kind="sbom_cyclonedx",
        path=cdxgen_result.sbom_path,
    )

    # Stage 3.5 — cosign SBOM signing (v2.3-s1) + in-toto attestation (v2.3-s2).
    # BEST-EFFORT: a missing cosign binary, an unconfigured key, or a cosign
    # failure logs a WARNING and the scan continues unsigned/un-attested — both
    # are degraded, never fatal (same philosophy as scancode / preserve). cosign
    # runs inside this Celery worker (CLAUDE.md core rule #3 — never on the
    # synchronous API path). Idempotent on re-run: _reset_scan_for_rerun clears
    # prior scan_artifacts, so re-signing/re-attesting cannot accumulate
    # duplicate rows. Attestation only runs when signing produced a signature, so
    # we never claim "attested" over an SBOM whose integrity we could not sign.
    _set_stage(scan_uuid, "sign")
    signed = _sign_sbom(
        scan_uuid=scan_uuid, sbom_path=cdxgen_result.sbom_path, workspace=workspace
    )
    if signed:
        _attest_sbom(
            scan_uuid=scan_uuid,
            project_id=project_id,
            sbom_path=cdxgen_result.sbom_path,
            workspace=workspace,
        )

    # W6 (ADR-0001): cdxgen produced the SBOM on disk. From here:
    #   1. run scancode first-party license detection (best-effort, unchanged),
    #   2. persist components + scancode-detected licenses,
    #   3. auto-create conditional-license approvals,
    #   4. run ``trivy sbom`` against the cdxgen SBOM to produce the vuln
    #      report under the ``trivy`` stage,
    #   5. persist Trivy findings into vulnerability_findings.
    #
    # The pre-W6 parallel layout submitted a DT BOM upload to a background
    # thread so DT's server-side matcher could warm up during scancode. Trivy
    # runs locally and is CPU-bound; there is no server-side warm-up to hide,
    # and an additional thread would just contend with scancode for CPU. We go
    # back to a sequential layout — simpler, no thread-safety risk, and the
    # wall-time difference is negligible (Trivy matches a typical SBOM in
    # seconds, not the 10-30s of the old DT upload + analyzer ramp-up). The
    # legacy ``dt_upload`` intermediate label has been removed in this PR —
    # post-W6 it was a 35% no-op marker between cdxgen and scancode and the FE
    # already omits it from PIPELINE_STEPS.

    # Stage 4 — scancode first-party license detection (PR-A2, replaces ORT).
    # scancode runs over the cloned first-party tree only (vendored deps /
    # build output / VCS metadata are excluded — see scancode.EXCLUDED_DIR_NAMES).
    # It is best-effort: a scancode failure / timeout / too-large tree logs a
    # WARNING and the scan continues with declared (cdxgen) licenses only. We
    # never raise from this stage onto the terminal-failure path — a missing
    # detected-license set is a degraded-output scenario, not a fatal one
    # (same philosophy as the prep stage). Third-party dependency sources are
    # NOT downloaded; their licenses stay declared via _persist_components.
    _set_stage(scan_uuid, "scancode")
    scancode_detections: list[scancode_adapter.DetectedLicense] = []
    # G3.1: capture the scancode result JSON path so the preservation stage can
    # fold it into the source tarball. The JSON is the only place per-line
    # license-match data survives the workspace rmtree (the adapter discards line
    # numbers; license_findings keeps only spdx + source_path). None when
    # scancode was skipped — preservation then archives the source tree alone.
    scancode_json_path: Path | None = None
    try:
        scancode_result = scancode_adapter.run_scancode(
            source_dir=source_dir,
            output_dir=workspace / "scancode",
            # P2 #8c — same WS streaming as cdxgen; see the cdxgen
            # call site above for the contract.
            line_callback=_make_line_callback(scan_uuid, stage="scancode"),
            verbose=verbose,
        )
        scancode_detections = scancode_result.detections
        scancode_json_path = scancode_result.result_path
        _persist_artifact(
            scan_uuid, kind="scancode_result", path=scancode_result.result_path
        )
        log.info("scancode_stage_done", detections=len(scancode_detections))
    except scancode_adapter.ScancodeError as exc:
        # ScancodeNotInstalled / Failed / Timeout / TooLarge all land here —
        # all are "detected-license enrichment unavailable", not "abort scan".
        log.warning("scancode_stage_skipped", error=str(exc)[:300])

    # Persist the SBOM components + declared (cdxgen) licenses, then attach the
    # scancode-detected first-party licenses to the project's own component.
    #
    # Blast-radius isolation (security-reviewer Medium #1): the components +
    # declared (cdxgen) licenses are the HIGH-VALUE cache the UI shows when the
    # vulnerability matcher is unavailable. The detected (scancode) licenses are
    # auxiliary and are derived from attacker-controlled file content. We
    # therefore wrap the detected write in a SAVEPOINT (``begin_nested``) so a
    # failure there (e.g. an unexpected constraint violation from a hostile path /
    # SPDX token that slipped the adapter caps) rolls back ONLY the detected
    # findings — the declared findings and component graph still commit. A
    # detected-license failure is degraded, never fatal.
    with sync_session_scope() as session:
        _persist_components(
            session,
            scan_uuid=scan_uuid,
            sbom=cdxgen_result.sbom,
            source_dir=source_dir,
        )
        if scancode_detections:
            try:
                with session.begin_nested():
                    _persist_detected_licenses(
                        session,
                        scan_uuid=scan_uuid,
                        sbom=cdxgen_result.sbom,
                        detections=scancode_detections,
                    )
            except SQLAlchemyError as exc:
                # SAVEPOINT rolled back; declared findings + components survive.
                log.warning(
                    "detected_license_persist_skipped",
                    error=str(exc)[:300],
                    detections=len(scancode_detections),
                )
        session.commit()

    # Stage 4.5 — auto-enrol conditional-license components into the legal
    # review queue (BUG-010). MUST run AFTER the component + license findings
    # commit above (it reads them back to decide which components are
    # conditional). Best-effort, mirroring the scancode / preserve stages: a
    # failure here logs a WARNING and the scan still succeeds. Idempotent on
    # re-run because the helper skips components that already have an open
    # approval (and _reset_scan_for_rerun never deletes approvals).
    _set_stage(scan_uuid, "approvals")
    _auto_create_conditional_approvals(scan_uuid=scan_uuid, project_id=project_id)

    # Stage 6 — Trivy SBOM matching (W6, replaces DT findings poll).
    # ``trivy sbom`` consumes the CycloneDX JSON we already wrote in the cdxgen
    # stage and produces a Trivy JSON report whose ``Results[].Vulnerabilities[]``
    # array we fold into ``vulnerability_findings`` via the persister.
    #
    # Trivy errors (binary missing / non-zero exit / per-stage timeout) are
    # terminal for the scan: they propagate out of ``_run_pipeline`` and the
    # task-body except blocks above map each subclass to a clear
    # ``error_message``. The pre-W6 cdxgen + scancode commits are already on
    # disk, so the user still sees the component graph + license findings even
    # when matching fails — same degraded-but-not-empty behaviour the DT path
    # had during a breaker-OPEN run.
    _set_stage(scan_uuid, "trivy")
    trivy_started_at = time.monotonic()
    trivy_result = run_trivy_sbom(
        sbom_path=cdxgen_result.sbom_path,
        output_dir=workspace / "trivy",
        # Stream Trivy's DB-update + matching progress onto the scan log
        # (feat/scan-log-verbosity) — previously Trivy ran with --quiet and
        # captured output, so the trivy stage showed no lines at all.
        line_callback=_make_line_callback(scan_uuid, stage="trivy"),
        verbose=verbose,
    )
    trivy_elapsed = time.monotonic() - trivy_started_at
    # Persist the Trivy report alongside the cdxgen SBOM so admin / debug can
    # diff what Trivy actually consumed against what we matched. Same pattern
    # as the ``scancode_result`` artifact above.
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
        "trivy_stage_done",
        scan_id=str(scan_uuid),
        trivy_seconds=round(trivy_elapsed, 2),
        findings_persisted=inserted,
    )

    # Stage 6.5 — preserve the source tree + scancode JSON (G3.1).
    #
    # This MUST run before the shared `finally: shutil.rmtree(workspace)` deletes
    # `source_dir`. It is best-effort, mirroring the scancode stage above: a
    # preservation failure (quota, over-cap tree, I/O error) logs a WARNING and
    # the scan still succeeds — we never raise onto the terminal-failure path. We
    # call it here (not in `finally`) so a failed scan does NOT leave a tarball
    # behind, and so the retained tarball reflects only succeeded scans.
    _preserve_source_tree(
        scan_uuid=scan_uuid,
        project_id=project_id,
        source_dir=source_dir,
        scancode_json_path=scancode_json_path,
        sbom_path=cdxgen_result.sbom_path,
    )

    # Stage 7 — finalize.
    _set_stage(scan_uuid, "finalize")
    _mark_succeeded(scan_uuid)

    # v2.3 r1 — fan out a follow-up reachability enrichment task (Go govulncheck
    # over the just-preserved source). MUST run AFTER `_mark_succeeded` and
    # AFTER `_preserve_source_tree` above (the reachability task reads the
    # preserved tarball). Best-effort, mirroring the scancode / preserve stages:
    # a dispatch failure logs a WARNING and the scan still reports succeeded — a
    # missing reachability signal is degraded, never fatal. The enqueue itself
    # honours REACHABILITY_ENABLED and returns None when disabled (no-op here).
    _dispatch_reachability(scan_uuid)


def _dispatch_reachability(scan_uuid: uuid.UUID) -> None:
    """Best-effort dispatch of the v2.3 r1 reachability follow-up task.

    Swallows and logs any error so a broker hiccup at dispatch time cannot turn
    a succeeded scan into a failure (the scan row is already ``succeeded`` by the
    time we get here).
    """
    try:
        from tasks import enqueue_reachability

        task_id = enqueue_reachability(str(scan_uuid))
        if task_id is not None:
            log.info("reachability_enqueued", scan_id=str(scan_uuid), task_id=task_id)
    except Exception as exc:  # noqa: BLE001 — dispatch must never fail the scan
        log.warning(
            "reachability_enqueue_failed", scan_id=str(scan_uuid), error=str(exc)[:300]
        )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


class _FetchAborted(Exception):
    """Raised when the fetch step rejects a project — caught by the task body."""


# GitHub / GitLab convention for token-over-https cloning: the username segment
# is a fixed placeholder and the token rides as the password
# (``https://x-access-token:<token>@host/path``). Both providers accept this; the
# username is ignored by GitHub and treated as the OAuth2 username by GitLab.
_HTTPS_CREDENTIAL_USERNAME = "x-access-token"


def build_authenticated_clone_url(normalized_url: str, credential: str | None) -> str:
    """Inject a git credential into an https clone URL as userinfo (#18 Part B).

    Returns the URL unchanged when:
      - ``credential`` is falsy (None / empty / blank), OR
      - the URL scheme is not ``https`` (ssh:// / git@ / git:// need key material,
        not a token — token injection is out of scope for them), OR
      - the URL already carries userinfo (we never double-inject / overwrite an
        operator-supplied credential).

    For an eligible https URL we produce
    ``https://x-access-token:<token>@host[:port]/path`` (the GitHub/GitLab
    convention). The token is URL-encoded so a token containing reserved
    characters (``@ : / ?`` ...) cannot break out of the userinfo segment or
    smuggle a different host.

    SECURITY: the returned URL contains the plaintext credential. Callers MUST
    pass it ONLY to the git clone subprocess and MUST route every logged copy
    through :func:`redact_url_userinfo`. This helper never logs.
    """
    if not credential or not credential.strip():
        return normalized_url

    from urllib.parse import quote, urlsplit, urlunsplit

    parts = urlsplit(normalized_url)
    if (parts.scheme or "").lower() != "https":
        # ssh:// / git@host: / git:// — token injection does not apply. Clone as
        # today (SSH relies on key material mounted into the worker).
        return normalized_url
    if parts.username or parts.password:
        # Already authenticated (operator put userinfo in git_url). Do not
        # overwrite — respect the explicit credential already present.
        return normalized_url

    host = parts.hostname or ""
    if not host:
        # Defensive: an https URL with no host is malformed; the SSRF guard
        # upstream would already have rejected it, but never build a broken URL.
        return normalized_url

    userinfo = f"{_HTTPS_CREDENTIAL_USERNAME}:{quote(credential, safe='')}"
    netloc = f"{userinfo}@{host}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# ``scheme://userinfo@`` matcher used by :func:`_scrub_clone_stderr`. We do NOT
# whitespace-tokenize first: git wraps the URL in single quotes and a trailing
# colon (e.g. ``... for 'https://x-access-token:<TOKEN>@host/repo.git':``), so a
# token-level urlsplit sees the leading ``'`` as a path, finds no userinfo, and
# leaks the credential. Matching the scheme://userinfo@ shape anywhere in the
# string is robust against any surrounding quoting / punctuation.
_USERINFO_IN_URL_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]+@")


def _scrub_clone_stderr(stderr: str, credential: str | None) -> str:
    """Redact any injected git credential from ``git clone`` stderr (#18 Part B).

    git can echo the remote URL — including the injected ``x-access-token:<TOKEN>``
    userinfo — into stderr on an auth failure. That stderr feeds
    ``_FetchAborted`` → ``scan.error_message``, an API/UI-exposed field readable
    by any team member (and likely captured in logs / screenshots), so the
    plaintext credential must never survive into the returned string.

    Layered defenses:
      (a) Regex-redact any ``scheme://userinfo@`` occurrence anywhere in the
          string. This is quoting/punctuation-independent (unlike a per-token
          ``urlsplit``, which the leading single quote git emits defeats).
      (b) Belt-and-suspenders when ``credential`` is non-empty: replace the raw
          credential AND its URL-encoded form (``quote(credential, safe="")`` —
          the percent-encoded shape that actually appears inside the injected
          URL) with ``"***"``. This catches a bare token echoed in prose with no
          URL wrapping, and a token whose reserved chars were percent-encoded.

    Finally truncate to the existing 500-char bound. Correct for
    ``credential is None`` (public repo / no credential): only (a) + truncate.
    """
    scrubbed = _USERINFO_IN_URL_RE.sub(r"\1***@", stderr)
    if credential:
        scrubbed = scrubbed.replace(credential, "***")
        from urllib.parse import quote

        encoded = quote(credential, safe="")
        if encoded != credential:
            scrubbed = scrubbed.replace(encoded, "***")
    return scrubbed[:500]


def _decrypt_project_credential(
    *, scan_uuid: uuid.UUID, project_id: uuid.UUID | None
) -> str | None:
    """Load + decrypt the project's git credential, or ``None`` if not configured.

    Reads ``projects.git_credential_encrypted`` (Fernet ciphertext) for
    ``project_id`` and decrypts it via :func:`core.crypto.decrypt_secret`.
    Returns ``None`` when no project / no credential is configured.

    Raises:
        _FetchAborted: a decrypt failure (rotated/unset key, corrupted ciphertext)
            with a credential-free message. The credential is NEVER logged.
    """
    if project_id is None:
        return None

    from core.crypto import SecretDecryptionError, decrypt_secret

    with sync_session_scope() as session:
        ciphertext = session.execute(
            select(Project.git_credential_encrypted).where(Project.id == project_id)
        ).scalar_one_or_none()

    if not ciphertext:
        return None

    try:
        return decrypt_secret(ciphertext)
    except SecretDecryptionError as exc:
        # The message from core.crypto is credential-free (it talks about the key
        # / corruption, never the plaintext). We still keep the surfaced
        # scan.error_message generic so nothing credential-shaped can leak.
        log.warning(
            "scan_source_credential_decrypt_failed",
            scan_id=str(scan_uuid),
            project_id=str(project_id),
            error=str(exc),
        )
        raise _FetchAborted(
            "the project git credential could not be decrypted; the encryption "
            "key may have been rotated or the stored credential is corrupted",
        ) from exc


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
    # Normalize to match the trigger-layer guard (services/scan_service.py): a
    # canonical strip().lower() so worker and API agree on the source strategy
    # and neither relies on the schema being the sole gatekeeper.
    source_type = str(metadata.get("source_type", "git")).strip().lower()

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

    # Real clone path (activated when mock_only=False). This whole branch needs a
    # live network + a real `git clone` subprocess, so it is `# pragma: no cover`
    # at the unit-test layer — the credential-bearing logic it relies on
    # (`build_authenticated_clone_url`, `_decrypt_project_credential`,
    # `redact_url_userinfo`) is each unit-tested independently above, and an
    # integration scan exercises the subprocess end-to-end.
    #
    # IP-pin format: host:port:ip. We default to 443 for https and 22 for
    # ssh; the curl option only matters for HTTPS, so SSH skips the -c
    # flag entirely.
    from urllib.parse import urlsplit  # pragma: no cover

    parts = urlsplit(normalized_url)  # pragma: no cover
    scheme = (parts.scheme or "").lower()  # pragma: no cover
    host = (parts.hostname or "").lower()  # pragma: no cover
    port = parts.port or (  # pragma: no cover
        443 if scheme == "https" else 80 if scheme == "http" else 22
    )
    target = source_dir / "repo"  # pragma: no cover

    # Feature #18 Part B — private-repo credential injection. We decrypt the
    # project's stored git credential (if any) and inject it into the clone URL
    # as userinfo, but ONLY for https (build_authenticated_clone_url returns the
    # URL unchanged for ssh:// / git@ / git://, and when no credential is set).
    # A decrypt failure raises _FetchAborted with a credential-free message
    # (terminal, not a crash). The plaintext credential lives ONLY in
    # `clone_url` from here on; every log line below uses `normalized_url`
    # (userinfo-free) or routes through `redact_url_userinfo`.
    credential = _decrypt_project_credential(  # pragma: no cover
        scan_uuid=scan_uuid, project_id=project_id
    )
    clone_url = build_authenticated_clone_url(normalized_url, credential)  # pragma: no cover
    credential_injected = clone_url != normalized_url  # pragma: no cover

    if scheme in ("http", "https"):  # pragma: no cover
        cmd = [
            "git",
            "-c",
            f"http.curloptResolve={host}:{port}:{resolved_ip}",
            "clone",
            "--depth",
            "1",
            clone_url,
            str(target),
        ]
    else:
        cmd = ["git", "clone", "--depth", "1", clone_url, str(target)]

    # subprocess is imported at module scope so the prep helper can use it
    # too (chore PR #4); the dead-code branch below shares that import.
    #
    # M-1 / #18-B: NEVER log `clone_url` (it may carry the injected credential).
    # We log `normalized_url` (the SSRF guard's userinfo-free form) and a boolean
    # `credential_injected` flag so operators can confirm auth was applied
    # without the value ever reaching structlog.
    log.info(  # pragma: no cover
        "scan_source_fetch_real",
        normalized_url=redact_url_userinfo(normalized_url),
        resolved_ip=resolved_ip,
        host=host,
        port=port,
        credential_injected=credential_injected,
    )
    completed = subprocess.run(  # noqa: S603  # pragma: no cover
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
    if completed.returncode != 0:  # pragma: no cover
        # git can echo the remote URL (with credential) into stderr on auth
        # failure ("fatal: could not read Username for 'https://...':"). The
        # single-quote/trailing-colon wrapping defeats a per-token urlsplit, so
        # scrub via `_scrub_clone_stderr`, which regex-redacts the
        # scheme://userinfo@ shape anywhere in the string AND (belt-and-
        # suspenders) replaces the raw + URL-encoded `credential` value. This is
        # the only sink for the credential on this failure path; the message
        # lands in `scan.error_message` (surfaced in the UI / audit).
        safe_stderr = _scrub_clone_stderr(completed.stderr.strip(), credential)
        raise _FetchAborted(f"git clone exited {completed.returncode}: {safe_stderr}")
    return source_dir  # pragma: no cover


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
    # v2.2 2.2-a2 — drop the prior dependency-graph edges BEFORE the
    # ScanComponent rows. The edge FK on (parent/child)_component_version_id is
    # ON DELETE CASCADE, but a ScanComponent delete does not delete a
    # component_version (those are cross-scan), so the edges would otherwise
    # survive a re-run and double up. Deleting by scan_id is exact + idempotent.
    session.execute(
        delete(ComponentDependencyEdge).where(ComponentDependencyEdge.scan_id == scan.id)
    )
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
        # scan-retention Layer 1: this scan is now the live snapshot for its
        # ref, so prior succeeded same-ref scans (without an explicit release
        # label) are superseded in the same transaction. No-op when the scan
        # carries no ref — those are reclaimed by the keep-last/max-age sweep.
        supersede_prior_ref_scans(
            session,
            project_id=scan.project_id,
            winner_scan_id=scan.id,
            ref=scan.ref,
        )
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


def _persist_artifact(
    scan_uuid: uuid.UUID, *, kind: str, path: Path, sha256: str | None = None
) -> None:
    if not path.exists():
        return
    size = path.stat().st_size
    with sync_session_scope() as session:
        artifact = ScanArtifact(
            scan_id=scan_uuid,
            kind=kind,
            storage_path=str(path),
            byte_size=size,
            sha256=sha256,
        )
        session.add(artifact)
        session.commit()


# Free-form ScanArtifact.kind values for cosign signing outputs (v2.3-s1). The
# column is String(32); all three are well under the limit
# ("sbom_cyclonedx_sig"=18, "sbom_cyclonedx_cert"=19).
_SBOM_SIG_KIND = "sbom_cyclonedx_sig"
_SBOM_CERT_KIND = "sbom_cyclonedx_cert"

# Free-form ScanArtifact.kind values for the in-toto attestation (v2.3-s2). The
# column is String(32); both fit ("sbom_attestation"=16, "sbom_attest_cert"=16).
_SBOM_ATTESTATION_KIND = "sbom_attestation"
_SBOM_ATTEST_CERT_KIND = "sbom_attest_cert"


def _sign_sbom(*, scan_uuid: uuid.UUID, sbom_path: Path, workspace: Path) -> bool:
    """Sign the generated SBOM with cosign and persist the signature artifact.

    BEST-EFFORT (v2.3-s1): the cosign adapter never raises into the scan — a
    missing binary, unconfigured key, decrypt failure, or cosign error returns a
    ``SignResult(signed=False, ...)`` and we simply skip persisting (the scan
    proceeds unsigned). We additionally wrap the whole helper in a broad
    try/except so any UNEXPECTED error (e.g. a transient DB failure persisting
    the artifact) is logged and swallowed rather than failing the scan — signing
    is auxiliary, the SBOM + components are the high-value output.

    On success we persist:
      - the detached signature as ``sbom_cyclonedx_sig`` (with the SBOM's sha256
        in ``ScanArtifact.sha256`` so a consumer can bind signature → exact bytes
        without re-reading the SBOM), and
      - (keyless only) the Fulcio certificate as ``sbom_cyclonedx_cert``.

    Returns:
        ``True`` iff a signature was produced + persisted. The caller gates the
        v2.3-s2 attestation on this so we never attest an SBOM whose integrity we
        could not sign. An unexpected error returns ``False`` (degraded).
    """
    try:
        result = cosign_adapter.sign_blob(
            blob_path=sbom_path,
            output_dir=workspace / "cosign",
        )

        if not result.signed:
            # The adapter already logged the specific skip reason; this is the
            # task-level breadcrumb tying it to the scan stage.
            log.warning("sbom_sign_skipped", reason=result.skip_reason)
            return False

        sbom_sha256 = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
        if result.signature_path is not None:
            _persist_artifact(
                scan_uuid,
                kind=_SBOM_SIG_KIND,
                path=result.signature_path,
                sha256=sbom_sha256,
            )
        if result.certificate_path is not None:
            _persist_artifact(
                scan_uuid,
                kind=_SBOM_CERT_KIND,
                path=result.certificate_path,
            )
        log.info("sbom_sign_persisted", mode=result.mode)
        return True
    except Exception as exc:  # noqa: BLE001 — signing is best-effort, never fatal
        # Covers an UNEXPECTED error anywhere in the stage: the adapter (which
        # itself is best-effort but could be patched / monkeypatched), the sha256
        # read, or the artifact persist (a transient DB failure). Signing is
        # auxiliary — the SBOM + components are the high-value output — so we log
        # and swallow rather than fail the scan. Route the (best-effort) exception
        # text through the documented masking helper before logging so an exception
        # message that happened to interpolate a secret-shaped value is redacted —
        # consistent with the cosign adapter's stderr scrub (CLAUDE.md §5).
        log.warning("sbom_sign_unexpected_error", error=str(mask_pii(str(exc)))[:300])
        return False


def _attest_sbom(
    *,
    scan_uuid: uuid.UUID,
    project_id: uuid.UUID,
    sbom_path: Path,
    workspace: Path,
) -> None:
    """Generate + persist an in-toto SLSA provenance attestation over the SBOM (v2.3-s2).

    BEST-EFFORT, identical contract to :func:`_sign_sbom`: the attestation
    adapter never raises into the scan, and a broad try/except swallows any
    UNEXPECTED error (predicate build, sha256 read, DB persist) so attestation —
    which is auxiliary metadata — can never break a scan.

    Only invoked when signing succeeded (the caller gates on the ``_sign_sbom``
    return), so we never claim "attested" over an SBOM whose integrity we could
    not sign. The predicate carries ONLY the scan/project ids + build context
    (timestamps, builder id/version) — NEVER secrets, the git URL, or paths (see
    ``integrations.attestation``).

    On success we persist:
      - the in-toto attestation (DSSE envelope) as ``sbom_attestation`` (with the
        SBOM's sha256 in ``ScanArtifact.sha256`` so a consumer can bind
        attestation → exact bytes), and
      - (keyless only) the Fulcio certificate as ``sbom_attest_cert``.
    """
    try:
        sbom_sha256 = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
        statement = attestation_builder.build_slsa_provenance_statement(
            # subject.name trust: the basename is worker-GENERATED (the cdxgen
            # stage always writes "cdxgen.cdx.json" into the scan workspace), NOT
            # a repo-controlled / attacker-influenced filename. So embedding it in
            # the in-toto subject leaks nothing and cannot be used to smuggle
            # attacker-chosen content into the signed statement.
            sbom_name=sbom_path.name,
            sbom_sha256=sbom_sha256,
            scan_id=str(scan_uuid),
            project_id=str(project_id),
            builder_id=slsa_builder_id(),
            builder_version=slsa_builder_version(),
            finished_on=datetime.now(UTC),
        )
        result = cosign_adapter.attest_blob(
            blob_path=sbom_path,
            predicate=statement["predicate"],
            predicate_type=attestation_builder.SLSA_PROVENANCE_PREDICATE_TYPE,
            output_dir=workspace / "cosign",
        )

        if not result.attested:
            log.warning("sbom_attest_skipped", reason=result.skip_reason)
            return

        if result.attestation_path is not None:
            _persist_artifact(
                scan_uuid,
                kind=_SBOM_ATTESTATION_KIND,
                path=result.attestation_path,
                sha256=sbom_sha256,
            )
        if result.certificate_path is not None:
            _persist_artifact(
                scan_uuid,
                kind=_SBOM_ATTEST_CERT_KIND,
                path=result.certificate_path,
            )
        log.info("sbom_attest_persisted", mode=result.mode)
    except Exception as exc:  # noqa: BLE001 — attestation is best-effort, never fatal
        # Mask the best-effort exception text before logging so a message that
        # happened to interpolate a secret-shaped value is redacted — consistent
        # with the cosign adapter's stderr scrub (CLAUDE.md §5).
        log.warning("sbom_attest_unexpected_error", error=str(mask_pii(str(exc)))[:300])


# ---------------------------------------------------------------------------
# Conditional-license auto-approval (BUG-010)
# ---------------------------------------------------------------------------

# Numeric rank for the conditional category, mirroring _CATEGORY_RANK below
# (defined later in this module). Defined here as a literal to avoid a forward
# reference; the value is asserted consistent with _CATEGORY_RANK by the unit
# tests so a future re-rank cannot silently drift.
_CONDITIONAL_RANK = 2

# QA follow-up Medium — the auto-enrolment audit summary records the affected
# component ids inline in the JSONB ``diff``. Cap the list so a scan that
# enrols thousands of conditional components cannot bloat a single audit row
# (and, transitively, the GIN index over ``audit_logs.diff``). When the created
# set exceeds the cap we store the first N ids plus a ``component_ids_truncated``
# flag; the ``created_count`` is always exact regardless of the cap.
_AUDIT_COMPONENT_IDS_CAP = 50

# Stable action verb + target for the auto-enrolment audit summary. ``action``
# must fit ``audit_logs.action`` (String(32)) and ``target_table`` must fit
# String(64) — "approvals.auto_enrolled" is 23 chars, "component_approvals" 19.
_AUTO_ENROL_AUDIT_ACTION = "approvals.auto_enrolled"
_AUTO_ENROL_AUDIT_TARGET_TABLE = "component_approvals"


def _build_auto_enrol_audit_diff(
    *,
    scan_uuid: uuid.UUID,
    project_id: uuid.UUID,
    created_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """Compose the JSONB ``diff`` payload for the auto-enrolment audit summary.

    ``created_count`` is exact; ``component_ids`` is capped at
    ``_AUDIT_COMPONENT_IDS_CAP`` with a ``component_ids_truncated`` flag so a
    mass-conditional scan cannot bloat the row / GIN index. All ids are
    stringified (JSONB has no native UUID type).
    """
    capped = created_ids[:_AUDIT_COMPONENT_IDS_CAP]
    return {
        "scan_id": str(scan_uuid),
        "project_id": str(project_id),
        "created_count": len(created_ids),
        "component_ids": [str(cid) for cid in capped],
        "component_ids_truncated": len(created_ids) > _AUDIT_COMPONENT_IDS_CAP,
    }


def _conditional_component_ids(
    session: Session, *, scan_uuid: uuid.UUID
) -> list[uuid.UUID]:
    """Return the components in this scan whose *effective* license category is
    exactly ``conditional``.

    Mirrors how the components-list API derives a per-component
    ``license_category``: a component may carry several license findings (one
    per declared / detected / concluded SPDX id), and the component's effective
    category is the **most restrictive** of them
    (forbidden > conditional > allowed > unknown — see ``_CATEGORY_RANK``). We
    group this scan's ``license_findings`` up to the owning ``component`` via
    ``component_versions`` and keep only components whose ``MAX(category rank)``
    equals the conditional rank.

    Why exactly-conditional (not ``>= conditional``):
      * ``forbidden`` is the build-gate's job, not the approval queue's — a
        component with any forbidden license must NOT silently land in legal
        review (the prompt + ``approvals.md`` scope auto-approval to
        conditional only). ``MAX(rank) == conditional`` excludes any component
        that also has a forbidden license.
      * ``allowed`` / ``unknown`` rank below conditional, so a component whose
        max is conditional cannot be one of those.

    The category lives on the ``licenses`` row (set by
    ``_classify_license_category`` at license-upsert time), so this is a pure
    read of already-committed state.
    """
    rank_case = case(
        {
            "forbidden": 3,
            "conditional": 2,
            "allowed": 1,
        },
        value=cast(License.category, String),
        else_=0,
    )
    stmt = (
        select(
            Component.id.label("component_id"),
            func.max(rank_case).label("max_rank"),
        )
        .select_from(LicenseFinding)
        .join(License, License.id == LicenseFinding.license_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == LicenseFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(LicenseFinding.scan_id == scan_uuid)
        .group_by(Component.id)
        .having(func.max(rank_case) == _CONDITIONAL_RANK)
    )
    return [row.component_id for row in session.execute(stmt).all()]


def _auto_create_conditional_approvals(
    *, scan_uuid: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Stage 4.5 — open a Pending approval for every conditional-license
    component in this scan (BUG-010).

    Best-effort: a failure here NEVER fails the scan (same swallow-and-log
    contract as the scancode + preserve stages). The work is idempotent
    (``auto_create_pending_approvals`` skips components that already have an
    open approval), so a re-run that re-creates the findings does not duplicate
    approvals, and a transient failure is healed on the next scan.

    QA follow-up Medium — audit trail: when one or more approvals are actually
    created, this stage writes ONE summary ``audit_logs`` row (action
    ``approvals.auto_enrolled``, system actor ``actor_user_id=NULL``,
    ``target_table='component_approvals'``, ``target_id=NULL`` because the row
    summarises many approvals) so a compliance reviewer can see when / how
    conditional licenses entered the legal-review queue. It is a SUMMARY, not
    one row per component, to keep ``audit_logs`` from ballooning — consistent
    with the un-audited per-row sync writes (see ``core/db.py``). The INSERT
    rides in the SAME transaction as the approval inserts and commits with them.
    The ``audit_logs`` append-only trigger (migration 0012) gates UPDATE/DELETE
    only, so this INSERT is permitted. ``actor_user_id``/``team_id`` are
    ON DELETE SET NULL FKs — NULL actor + the project's team_id never violate
    them. When NO approval is created (``created_ids`` empty — already enrolled
    or none conditional) NO audit row is written, so an idempotent re-run leaves
    no audit noise.
    """
    try:
        with sync_session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                # Project deleted mid-scan — nothing to enrol against.
                log.warning(
                    "approval.auto_create_project_missing",
                    scan_id=str(scan_uuid),
                    project_id=str(project_id),
                )
                return
            team_id = project.team_id
            component_ids = _conditional_component_ids(session, scan_uuid=scan_uuid)
            if not component_ids:
                log.info(
                    "approval.auto_create_none",
                    scan_id=str(scan_uuid),
                    project_id=str(project_id),
                )
                return
            created_ids = auto_create_pending_approvals(
                session,
                project_id=project_id,
                team_id=team_id,
                component_ids=component_ids,
                scan_id=scan_uuid,
            )
            # Summary audit row — only when at least one approval was created.
            # Same transaction as the approval inserts so the audit row and the
            # approvals commit (or roll back) together.
            if created_ids:
                session.add(
                    AuditLog(
                        actor_user_id=None,  # system context — no request actor
                        team_id=team_id,
                        action=_AUTO_ENROL_AUDIT_ACTION,
                        target_table=_AUTO_ENROL_AUDIT_TARGET_TABLE,
                        target_id=None,  # summary row spans many approvals
                        diff=_build_auto_enrol_audit_diff(
                            scan_uuid=scan_uuid,
                            project_id=project_id,
                            created_ids=created_ids,
                        ),
                    )
                )
            session.commit()
        log.info(
            "approval.auto_create_done",
            scan_id=str(scan_uuid),
            project_id=str(project_id),
            conditional_components=len(component_ids),
            created=len(created_ids),
        )
    except Exception as exc:  # noqa: BLE001 — auto-approval must never fail a scan
        log.warning(
            "approval.auto_create_stage_error",
            scan_id=str(scan_uuid),
            project_id=str(project_id),
            error=str(exc)[:300],
        )


# ---------------------------------------------------------------------------
# Source preservation (G3.1) — tar the source + scancode JSON for the tree view
# ---------------------------------------------------------------------------

# Free-form ScanArtifact.kind for the preserved source tarball. The column is
# String(32) (models/scan.py) and free-form, so no migration is needed.
_SOURCE_TARBALL_ARTIFACT_KIND = "source_tarball"


def _resolve_scancode_json_path(
    scan_uuid: uuid.UUID,
    workspace: Path,
    captured: Path | None,
) -> Path | None:
    """Best-effort resolve the scancode result JSON path for preservation.

    Resolution order:
      1. the path captured from the scancode stage in this run (authoritative),
      2. the ``kind='scancode_result'`` ScanArtifact row written this run,
      3. the known workspace location ``{workspace}/scancode/`` (a re-run whose
         scancode stage failed but a prior artifact path is unavailable).

    Returns ``None`` when no readable JSON can be found — preservation then
    archives the source tree alone (the per-line view degrades, the file tree
    still works).
    """
    if captured is not None and captured.is_file():
        return captured

    with sync_session_scope() as session:
        row = session.execute(
            select(ScanArtifact.storage_path).where(
                ScanArtifact.scan_id == scan_uuid,
                ScanArtifact.kind == "scancode_result",
            )
        ).scalar_one_or_none()
    if isinstance(row, str) and row:
        candidate = Path(row)
        if candidate.is_file():
            return candidate

    # Last resort: the adapter writes its result under {workspace}/scancode/.
    scancode_dir = workspace / "scancode"
    if scancode_dir.is_dir():
        for child in sorted(scancode_dir.glob("*.json")):
            if child.is_file():
                return child
    return None


def _preserve_source_tree(
    *,
    scan_uuid: uuid.UUID,
    project_id: uuid.UUID,
    source_dir: Path,
    scancode_json_path: Path | None,
    sbom_path: Path | None = None,
) -> None:
    """Preserve the source tree + scancode JSON + cdxgen SBOM as a tarball (G3.1 + W6-#42).

    Best-effort — mirrors the scancode stage's swallow-and-log contract: a
    failure here NEVER fails the scan. On success we write a free-form
    ``source_tarball`` ScanArtifact row pointing at the retained tar; on a re-run
    the prior artifact row was already deleted by ``_reset_scan_for_rerun`` and
    the tarball itself is overwritten atomically by the service.

    The workspace is the parent of ``source_dir`` (``{workspace}/source``); we
    derive it so the scancode-JSON fallback can probe ``{workspace}/scancode/``.

    W6-#42: ``sbom_path`` (the cdxgen CycloneDX output) is folded into the
    tarball under ``.trustedoss/cdxgen.cdx.json`` so the vulnerability rematch
    beat can re-run ``trivy sbom`` against the same exact bytes without paying
    cdxgen's cost. Optional — scans that never produced an SBOM (e.g. cdxgen
    aborted) still preserve source + scancode JSON for the file-tree viewer.
    """
    try:
        workspace = source_dir.parent
        json_path = _resolve_scancode_json_path(
            scan_uuid, workspace, scancode_json_path
        )
        tar_path = preserve_scan_source(
            scan_id=scan_uuid,
            project_id=project_id,
            source_dir=source_dir,
            scancode_json_path=json_path,
            sbom_path=sbom_path,
        )
        if tar_path is None:
            log.info("scan_source_preserve_skipped_stage", scan_id=str(scan_uuid))
            return
        _persist_artifact(
            scan_uuid, kind=_SOURCE_TARBALL_ARTIFACT_KIND, path=tar_path
        )
        log.info(
            "scan_source_preserve_artifact_written",
            scan_id=str(scan_uuid),
            storage_path=str(tar_path),
        )
    except Exception as exc:  # noqa: BLE001 — preservation must never fail a scan
        # The service already swallows its own errors and returns None; this
        # outer guard covers an unexpected failure in artifact persistence (e.g.
        # a transient DB error) so a degraded preservation never sinks a
        # succeeded scan. Same philosophy as the scancode stage.
        log.warning(
            "scan_source_preserve_stage_error",
            scan_id=str(scan_uuid),
            error=str(exc)[:300],
        )


# ---------------------------------------------------------------------------
# Multi-language pre-cdxgen prep
# ---------------------------------------------------------------------------


# Per-language step timeout. 5 minutes is enough for `bundle lock` /
# `cargo generate-lockfile` / `go mod tidy` / `dotnet restore` on the
# pilot repos in the 2026-05-07 matrix (none exceeded ~60s) while still
# capping a runaway resolver before it eats the scan's 60-min budget.
_PREP_STEP_TIMEOUT_SECONDS = 300


# subprocess env scrubbing was promoted to ``integrations._subprocess_env``
# in chore PR #6 so the same helper covers prep / cdxgen / scancode (the ORT
# variant was dropped in PR-A2). The alias below preserves the legacy module
# path used by tests and the ``_run_prep`` call site below.
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

    # Lockfile-only ecosystems where the worker image has no resolver binary
    # (no `yarn`, no `poetry`) and we cannot run a live install offline. cdxgen
    # parses these from on-disk manifests instead — see G4 fixtures batch.
    _prepare_yarn(source_dir=source_dir, scan_uuid=scan_uuid)
    _prepare_poetry(source_dir=source_dir, scan_uuid=scan_uuid)

    # npm LAST, on purpose: `_prepare_yarn` may have just removed an empty
    # `yarn.lock`. Only after that does a lockless `package.json` need a
    # ``--package-lock-only`` generation — otherwise cdxgen full-installs
    # node_modules and scrapes nested dependency ``flake.lock`` files, emitting
    # phantom ``pkg:nix/*`` components (node-yarn fixtures e2e regression).
    _prepare_npm(source_dir=source_dir, scan_uuid=scan_uuid)


def _prepare_npm(*, source_dir: Path, scan_uuid: uuid.UUID) -> None:
    """Generate a *lockfile-only* npm lock for a lockless npm project.

    Why: cdxgen, given a ``package.json`` with no lockfile, runs a full
    ``npm install`` to materialise ``node_modules`` and then resolves deps from
    it. That has two problems observed in the fixtures e2e:

    1. **Spurious components** — cdxgen recurses (``-r``) into the freshly
       installed ``node_modules`` and parses *nested, dependency-shipped*
       manifests (e.g. a ``flake.lock`` bundled inside ``node_modules/lodash``),
       emitting phantom ``pkg:nix/nixpkgs`` / ``pkg:nix/flake-utils`` components
       that are NOT dependencies of the scanned project.
    2. Cost — a full install is slower and pulls every package's contents.

    Pre-generating the lock with ``npm install --package-lock-only`` produces
    ``package-lock.json`` (the full *transitive* graph) WITHOUT a
    ``node_modules`` tree, so cdxgen reads the lock and never walks installed
    package internals — accurate transitive deps, no nested-manifest noise.

    Best-effort and offline-tolerant: skipped when ``npm`` is absent or a lock
    already exists; a failure (no network, private registry) logs a warning and
    cdxgen still falls back to ``package.json`` direct deps.
    """
    package_json = source_dir / "package.json"
    if not package_json.is_file():
        return
    existing_locks = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")
    if any((source_dir / name).is_file() for name in existing_locks):
        return  # A lock already exists — cdxgen parses it; do not full-install.
    if not shutil.which("npm"):
        return
    _run_prep(
        "npm package-lock-only",
        [
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        source_dir,
        _PREP_STEP_TIMEOUT_SECONDS,
        scan_uuid,
    )


def _prepare_yarn(*, source_dir: Path, scan_uuid: uuid.UUID) -> None:
    """Heal a yarn project whose ``yarn.lock`` is empty or missing.

    cdxgen's npm path *prefers* ``yarn.lock`` over ``package.json`` when the
    lock is present: it parses the lock and never falls back to the manifest.
    An **empty** ``yarn.lock`` (0 bytes — common when teams gitignore the
    lock, as the node-yarn fixture does) therefore yields 0 components even
    though ``package.json`` lists real dependencies.

    The worker image ships no ``yarn`` binary and a real ``yarn install``
    needs network anyway, so we cannot regenerate the lock offline. Instead
    we remove the empty/whitespace-only lock so cdxgen falls back to
    ``package.json`` and at least enumerates the **direct** dependencies. A
    populated ``yarn.lock`` (transitive graph already resolved) is left
    untouched — it is the richer source.
    """
    package_json = source_dir / "package.json"
    yarn_lock = source_dir / "yarn.lock"
    if not package_json.is_file() or not yarn_lock.is_file():
        return
    try:
        # An empty lock is the broken case. We treat whitespace-only as empty
        # too — some tools touch a placeholder header line with no entries.
        if yarn_lock.read_text(encoding="utf-8", errors="replace").strip():
            return  # Populated lock — cdxgen has the full transitive graph.
        yarn_lock.unlink()
    except OSError as exc:
        log.warning(
            "prep_yarn_unlock_failed",
            scan_id=str(scan_uuid),
            error=str(exc),
        )
        return
    log.info(
        "prep_yarn_empty_lock_removed",
        scan_id=str(scan_uuid),
        detail="empty yarn.lock removed so cdxgen falls back to package.json",
    )


def _prepare_poetry(*, source_dir: Path, scan_uuid: uuid.UUID) -> None:
    """Synthesize a ``requirements.txt`` for a lock-less legacy Poetry project.

    cdxgen resolves ``[tool.poetry.dependencies]`` (Poetry's legacy,
    non-PEP-621 table) only by shelling out to ``poetry``. The worker image
    ships no ``poetry`` binary (``spawnSync poetry ENOENT``) and there is no
    ``poetry.lock`` to parse, so cdxgen reports 0 components — even though the
    manifest pins concrete versions.

    cdxgen *does* parse ``requirements.txt`` via its pip path without any
    extra tooling, so we translate each pinned, exact-version Poetry dep into
    a ``name==version`` requirements line. This is best-effort and intended to
    surface **direct** dependencies; the full transitive graph still requires
    a real ``poetry.lock`` (devops hand-off if transitive depth matters).

    We never overwrite an existing ``requirements.txt`` (operator's source of
    truth), never act when a ``poetry.lock`` exists (richer), and skip
    PEP-621 ``[project.dependencies]`` projects — cdxgen handles those
    natively.
    """
    pyproject = source_dir / "pyproject.toml"
    if not pyproject.is_file():
        return
    if (source_dir / "poetry.lock").is_file():
        return  # cdxgen reads the lock — full graph available.
    if (source_dir / "requirements.txt").is_file():
        return  # Do not clobber an existing requirements source.

    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning(
            "prep_poetry_pyproject_unreadable",
            scan_id=str(scan_uuid),
            error=str(exc),
        )
        return

    poetry_deps = (
        data.get("tool", {}).get("poetry", {}).get("dependencies", {})
        if isinstance(data.get("tool"), dict)
        else {}
    )
    if not isinstance(poetry_deps, dict) or not poetry_deps:
        return  # Not a legacy-Poetry layout (PEP-621 handled by cdxgen).

    lines = _poetry_deps_to_requirements(poetry_deps)
    if not lines:
        log.info(
            "prep_poetry_no_pinned_deps",
            scan_id=str(scan_uuid),
            detail="no exact-version poetry deps to translate",
        )
        return

    try:
        (source_dir / "requirements.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        log.warning(
            "prep_poetry_requirements_write_failed",
            scan_id=str(scan_uuid),
            error=str(exc),
        )
        return
    log.info(
        "prep_poetry_requirements_synthesized",
        scan_id=str(scan_uuid),
        deps=len(lines),
    )


def _poetry_deps_to_requirements(poetry_deps: dict[str, Any]) -> list[str]:
    """Translate a ``[tool.poetry.dependencies]`` table to pip requirement lines.

    Poetry version specs use the caret/tilde families plus PEP-440 operators.
    cdxgen's requirements parser keys components off an exact ``==`` pin, so we
    only emit lines for **exact** pins (a bare ``"2.31.0"`` or an explicit
    ``"==2.31.0"``). Range specs (``^1``, ``~=2``, ``>=1,<2``, ``*``) cannot be
    pinned to a single version offline, and a table-form dep (git/path/extras)
    has no installable version here — both are skipped rather than guessed.
    The ``python`` interpreter constraint is always dropped (not a package).
    """
    requirements: list[str] = []
    for name, spec in poetry_deps.items():
        if name.lower() == "python":
            continue
        if not isinstance(spec, str):
            continue  # Table form: {git=..}, {path=..}, {version=.., extras=..}.
        version = spec.strip()
        if version.startswith("=="):
            version = version[2:].strip()
        # Reject any spec that still carries a range/wildcard operator after
        # stripping a leading ``==``. Exact pins are bare digits-and-dots(+pre).
        if not re.fullmatch(r"[0-9][0-9A-Za-z.\-+!]*", version):
            continue
        requirements.append(f"{name}=={version}")
    return requirements


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


def _persist_components(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    sbom: dict[str, Any],
    source_dir: Path | None = None,
) -> None:
    """Upsert components / component versions / scan components / license
    findings from a cdxgen CycloneDX SBOM.

    UAT patch (2026-05-07): the original design relied on ORT's evaluator
    output for ``license_findings``, but the ORT integration was broken (it fed
    a CycloneDX SBOM to ``ort evaluate --ort-file``, which aborted every scan).
    cdxgen does emit each component's declared SPDX license inside
    ``components[].licenses``, so we upsert ``licenses`` + ``license_findings``
    rows here. License kind is fixed to ``"declared"`` because cdxgen's data is
    package-metadata-derived (npm `license`, maven `<licenses>`, gradle
    resolved POM) — these are THIRD-PARTY dependency licenses.

    PR-A2: ORT was removed entirely. Detected (first-party) licenses now come
    from scancode and are persisted separately by ``_persist_detected_licenses``
    with ``kind='detected'`` against the synthetic first-party component — they
    do NOT flow through this function (which only walks ``sbom.components``,
    i.e. the third-party dependency graph).

    v2.2 2.2-a2 (dependency graph): cdxgen's top-level ``dependencies`` array
    (``ref`` → ``dependsOn[]``) is parsed into a depth map + adjacency by
    :mod:`integrations.dependency_graph` (cycle/self-ref/dangling/giant-fanout
    safe). As we persist each component we build a ``cdxgen ref →
    component_version_id`` map; afterwards we (a) stamp each ScanComponent's
    ``depth`` (and ``direct`` := ``depth == 1``) and (b) write the resolved
    parent/child edges to ``component_dependency_edges`` — only edges where BOTH
    endpoints resolved to a persisted component (dangling refs / the scanned
    project's own metadata component are dropped). NULL depth means the SBOM
    carried no usable graph.

    W4-D npm enrichment (2026-05-27): when ``source_dir`` is provided AND a
    ``package-lock.json`` is on disk, the lockfile is parsed once and used to
    fill the two cdxgen-shaped npm gaps observed in P3 #12 diagnostics:

      (a) cdxgen 12.3.3 does not emit ``scope`` for npm components — we read
          ``dev`` / ``optional`` / ``peer`` flags from the lockfile and the
          root ``package.json``'s dependency categories to derive a
          deterministic scope (USAGE column in the UI).
      (b) cdxgen sometimes emits ``components`` without a ``dependencies``
          graph — when ``sbom["dependencies"]`` parses to an empty adjacency
          AND the lockfile is available, we hand the lockfile-derived
          adjacency to :mod:`integrations.dependency_graph` so the per-row
          TYPE column shows direct/transitive instead of dash.

    Maven, Gradle, Cargo, Go, .NET are unaffected — cdxgen emits scope/graph
    for those ecosystems via their respective build files.
    """
    components = sbom.get("components", []) or []
    # W4-D: load npm lockfile once. None for non-npm projects or when the
    # lockfile is absent / malformed — downstream callers degrade silently.
    npm_lock = read_lockfile(source_dir) if source_dir is not None else None
    if npm_lock is not None:
        log.info(
            "npm_lockfile_loaded",
            scan_id=str(scan_uuid),
            packages=len(npm_lock.scope_by_purl),
            graph_nodes=len(npm_lock.adjacency),
        )
    # cdxgen ref → component_version_id, for stamping depth + resolving edges
    # once the whole component set is persisted. The graph's ``ref`` is a
    # component's bom-ref (== its purl in cdxgen output); we key the map on the
    # same string the graph uses.
    ref_to_cv_id: dict[str, uuid.UUID] = {}
    # cdxgen ref → the ScanComponent we created, so we can backfill depth.
    ref_to_scan_component: dict[str, ScanComponent] = {}

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
        # W4-D: scope precedence — cdxgen first (Maven POMs / Gradle DSL carry
        # explicit scope), then npm lockfile fallback (cdxgen never emits scope
        # for npm). The lookup is by purl, which is the same string cdxgen uses
        # — for non-npm purls the lockup misses harmlessly.
        cdxgen_scope = raw.get("scope")
        scope_value: str | None
        if isinstance(cdxgen_scope, str) and cdxgen_scope:
            scope_value = cdxgen_scope
        elif npm_lock is not None and package_type == "npm":
            scope_value = npm_lock.scope_for_purl(purl)
        else:
            scope_value = None
        scan_component = ScanComponent(
            scan_id=scan_uuid,
            component_version_id=component_version.id,
            dependency_scope=scope_value,
            dependency_path=raw.get("bom-ref"),
            direct=False,
            raw_data=guarded_raw,
        )
        session.add(scan_component)

        # Map BOTH the bom-ref and the purl to this component_version: cdxgen
        # graph refs are bom-refs, which usually equal the purl but can differ
        # (e.g. a scoped/aliased ref). Recording both maximises edge resolution.
        bom_ref = raw.get("bom-ref")
        if isinstance(bom_ref, str) and bom_ref:
            ref_to_cv_id[bom_ref] = component_version.id
            ref_to_scan_component[bom_ref] = scan_component
        ref_to_cv_id.setdefault(purl, component_version.id)
        ref_to_scan_component.setdefault(purl, scan_component)

        _persist_component_licenses(
            session,
            scan_uuid=scan_uuid,
            component_version_id=component_version.id,
            cdxgen_component=raw,
            purl=purl,
        )

    # v2.2 2.2-a2 — stamp depth + persist the resolved dependency edges. Runs
    # after the component loop so every ref the graph might reference is already
    # mapped. Best-effort and isolated: a malformed graph degrades to "no depth /
    # no edges", it never sinks the component persistence above.
    #
    # W4-D: when cdxgen emitted no usable graph but the npm lockfile gave us
    # one, the lockfile adjacency is used as a fallback so the Components tab
    # TYPE column (direct vs transitive) is filled for npm projects.
    _persist_dependency_graph(
        session,
        scan_uuid=scan_uuid,
        sbom=sbom,
        ref_to_cv_id=ref_to_cv_id,
        ref_to_scan_component=ref_to_scan_component,
        npm_lock=npm_lock,
    )


# ---------------------------------------------------------------------------
# Dependency graph (cdxgen dependencies → depth + edges) — v2.2 2.2-a2
# ---------------------------------------------------------------------------


def _persist_dependency_graph(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    sbom: dict[str, Any],
    ref_to_cv_id: dict[str, uuid.UUID],
    ref_to_scan_component: dict[str, ScanComponent],
    npm_lock: NpmLockfileData | None = None,
) -> None:
    """Stamp ``ScanComponent.depth`` + persist resolved dependency edges.

    Reads the cdxgen ``sbom["dependencies"]`` graph through
    :mod:`integrations.dependency_graph` (the trust boundary — cycle / self-ref /
    dangling / giant-fanout safe, depth clamped at ``MAX_DEPTH``). Then:

      1. For every graph ref that maps to a ScanComponent we created, set its
         ``depth`` and ``direct`` (``direct := depth == 1``). The scanned
         project's own metadata component sits at depth 0 and is NOT one of our
         ScanComponents (it has no purl in ``components``), so it is skipped.
      2. For every ``parent dependsOn child`` edge where BOTH refs resolve to a
         persisted component_version, insert a ``ComponentDependencyEdge``.
         Dangling refs (child not in ``ref_to_cv_id``) and self-edges are
         dropped. Duplicate edges are de-duplicated in-memory so the per-run
         insert set is unique even before the DB UNIQUE constraint.

    Best-effort: a malformed / absent graph leaves ``depth`` NULL and writes no
    edges — it never raises onto the caller's component-persistence path. The
    rows are added to the SAME session as the components, so they commit (or roll
    back) atomically with the component graph in the caller's ``session.commit()``.

    W4-D fallback: when ``npm_lock`` is provided AND the cdxgen graph parses to
    an empty adjacency, the lockfile's synthesized adjacency is used instead so
    npm projects whose cdxgen run produced ``components`` but no
    ``dependencies`` still get direct/transitive distinction. The fallback
    flows through the SAME ``parse_dependency_graph`` / ``compute_depths`` path
    so all adversarial-input guarantees (cycle / dangling / MAX_DEPTH cap)
    apply identically.
    """
    dependencies = sbom.get("dependencies")
    adjacency = parse_dependency_graph(dependencies)
    cdxgen_graph_empty = not adjacency
    used_lockfile_fallback = False

    if cdxgen_graph_empty and npm_lock is not None and npm_lock.adjacency:
        # W4-D fallback: re-parse through the same trust boundary so the
        # adversarial-input guarantees apply to lockfile-derived data too.
        adjacency = parse_dependency_graph(npm_lock.synthesize_cdxgen_dependencies())
        if adjacency:
            used_lockfile_fallback = True
            log.info(
                "dependency_graph_lockfile_fallback",
                scan_id=str(scan_uuid),
                source="package-lock.json",
                nodes=len(adjacency),
                component_count=len(ref_to_cv_id),
            )

    if not adjacency:
        # P3 #12 diagnostic (2026-05-26): a silent skip here is the dominant
        # reason ScanComponent.depth / .direct end up NULL across the whole
        # scan corpus. WARN so the next scan immediately surfaces whether the
        # cdxgen SBOM lacked a usable graph (empty / missing ``dependencies``
        # array) versus a downstream bug in the persistence loop. The scan
        # still succeeds — graph is best-effort, never fatal.
        raw_count = (
            len(dependencies)
            if isinstance(dependencies, list)
            else (-1 if dependencies is not None else 0)
        )
        log.warning(
            "dependency_graph_missing",
            scan_id=str(scan_uuid),
            raw_entries=raw_count,
            component_count=len(ref_to_cv_id),
            npm_lockfile_available=npm_lock is not None,
        )
        return  # No usable graph — depth stays NULL, no edges.

    if used_lockfile_fallback:
        # Lockfile root is the empty string ``""`` — force it as the graph
        # root so its immediate children become depth-1 (direct) deps,
        # matching the cdxgen ``metadata.component`` semantics.
        depths = compute_depths(adjacency, root_refs=[""])
    else:
        depths = graph_depths_from_sbom(sbom)

    # 1) Stamp depth + direct on the ScanComponents we created.
    for ref, depth in depths.items():
        scan_component = ref_to_scan_component.get(ref)
        if scan_component is None:
            # The ref is the project's metadata component or a graph-only node
            # with no matching SBOM component — nothing to stamp.
            continue
        scan_component.depth = depth
        scan_component.direct = depth == 1

    # 2) Persist resolved edges (both endpoints must be persisted components).
    seen_edges: set[tuple[uuid.UUID, uuid.UUID]] = set()
    edge_count = 0
    for parent_ref, child_refs in adjacency.items():
        parent_cv_id = ref_to_cv_id.get(parent_ref)
        if parent_cv_id is None:
            continue  # parent is the project metadata component / unresolved.
        for child_ref in child_refs:
            child_cv_id = ref_to_cv_id.get(child_ref)
            if child_cv_id is None:
                continue  # dangling child ref — skip (never invent a node).
            if child_cv_id == parent_cv_id:
                continue  # self-edge after resolution — skip.
            key = (parent_cv_id, child_cv_id)
            if key in seen_edges:
                continue  # duplicate edge collapsed.
            seen_edges.add(key)
            session.add(
                ComponentDependencyEdge(
                    scan_id=scan_uuid,
                    parent_component_version_id=parent_cv_id,
                    child_component_version_id=child_cv_id,
                )
            )
            edge_count += 1

    log.info(
        "dependency_graph_persisted",
        scan_id=str(scan_uuid),
        nodes=len(adjacency),
        depths=len(depths),
        edges=edge_count,
        source="npm_lockfile_fallback" if used_lockfile_fallback else "cdxgen",
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


_CATEGORY_RANK = {"forbidden": 3, "conditional": 2, "allowed": 1, "unknown": 0}


def _classify_license_category(spdx_id: str | None) -> str:
    """Map an SPDX id — single OR a compound expression — to a policy category.

    A single id is a direct dict lookup. A *compound* SPDX expression
    (``GPL-3.0-or-later AND GPL-3.0-only``, ``MIT OR Apache-2.0``,
    ``Apache-2.0 WITH LLVM-exception``) is resolved by the shared
    :func:`services.license_expression.evaluate_expression`, which applies the
    correct per-operator semantics: ``OR`` is **least-restrictive** (a
    disjunctive "either license satisfies" — e.g. ``GPL-2.0-or-later OR
    MPL-1.1`` is ``conditional``, NOT forbidden), while ``AND`` / ``WITH`` are
    **most-restrictive**. The previous split-and-take-most-restrictive logic
    wrongly treated ``OR`` like ``AND`` and flagged disjunctive multi-licensed
    packages as forbidden. Static catalog is the per-operand resolver; a miss
    resolves to ``unknown``.
    """
    if not spdx_id:
        return "unknown"
    direct = _LICENSE_CATEGORY_DEFAULTS.get(spdx_id)
    if direct is not None:
        return direct
    result = evaluate_expression(
        spdx_id,
        resolve_id=lambda tok: _LICENSE_CATEGORY_DEFAULTS.get(tok, "unknown"),
        unknown_category="unknown",
    )
    return result.category


def _extract_spdx_ids(cdxgen_component: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Pull (spdx_id, reference_url) tuples out of a cdxgen component entry.

    CycloneDX shapes the ``licenses`` field as a list, where each entry is
    one of:
      - ``{"license": {"id": "<spdx>", "url": "<reference>"}}``
      - ``{"license": {"name": "<free-text>", "url": "<reference>"}}``
      - ``{"expression": "<spdx-expression>"}``

    We accept the first form (preferred — exact SPDX), accept the third
    (including compound ``AND`` / ``OR`` / ``WITH`` expressions — they are
    resolved correctly by :func:`_classify_license_category` via the shared
    evaluator), and skip free-text license names — those would require a
    license-text identifier scanner (scancode) to map to SPDX, which is out of
    scope for the cdxgen fast-path.
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
        if isinstance(expression, str):
            expr = expression.strip()
            # Keep compound expressions too (previously dropped, which silently
            # lost a package's disjunctive license). Bound to the License.spdx_id
            # column width (64); a longer expression is skipped (rare, and the
            # column is the natural key). The classifier evaluates OR as
            # least-restrictive so a disjunctive expr is not over-flagged.
            if expr and len(expr) <= 64:
                out.append((expr, None))
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
        # Self-heal a stale ``unknown`` classification: the row may have been
        # created before the classifier learned this id (e.g. compound SPDX
        # expressions like "GPL-3.0-or-later AND GPL-3.0-only", which must read
        # as forbidden, not unknown — a build-gate-relevant policy fix). Only
        # upgrade unknown→known; never overwrite an already-classified category.
        if existing.category == "unknown":
            reclassified = _classify_license_category(spdx_id)
            if reclassified != "unknown":
                existing.category = reclassified
                session.flush()
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
    one (cdxgen → ``declared``, fetcher → ``concluded``, scancode →
    ``detected``). All three coexist on a component via the ``kind``
    discriminator (part of the ``uq_license_findings_*`` unique key).
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


# ---------------------------------------------------------------------------
# scancode detected first-party licenses → license_findings (PR-A2)
# ---------------------------------------------------------------------------


# Synthetic purl for the project's own first-party source. scancode detects
# licenses in code the team WROTE, which has no package identity of its own —
# so we anchor those findings on a deterministic per-scan first-party
# ComponentVersion. The purl is namespaced under a private `pkg:trustedoss/...`
# type so it can never collide with a real ecosystem purl from cdxgen.
_FIRST_PARTY_PURL_PREFIX = "pkg:trustedoss/first-party"

# Width of ``licenses.spdx_id`` (models/scan.py — ``String(64)``). Persistence-
# layer guard mirroring ``scancode.SPDX_ID_MAX_LENGTH``: a detected SPDX token
# wider than the column would raise StringDataRightTruncation on INSERT and roll
# back the whole transaction. We re-validate here (defence in depth) because the
# detection data is attacker-controlled and must not depend solely on the
# adapter having capped it.
_SPDX_ID_MAX_LENGTH = 64


def _persist_detected_licenses(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    sbom: dict[str, Any],
    detections: list[scancode_adapter.DetectedLicense],
) -> None:
    """Emit ``detected`` LicenseFindings for scancode's first-party results.

    PR-A2: scancode scans the cloned first-party tree and reports per-file
    detected SPDX licenses. Those describe the project's OWN source, which has
    no third-party package identity — so we anchor every detected finding on a
    single synthetic first-party ComponentVersion (purl
    ``pkg:trustedoss/first-party@<scan_id>``). The ``source_path`` column
    carries scancode's per-file path so the UI (PR-A3) can distinguish e.g.
    "LICENSE → MIT" from "src/foo.py → Apache-2.0".

    Provenance is unambiguous on three axes, so detected findings never collide
    with the declared (cdxgen) findings written by ``_persist_component_licenses``:
      * ``kind='detected'`` (vs ``'declared'`` / ``'concluded'``) — the primary
        discriminator, and part of the ``uq_license_findings_*`` unique key.
      * ``source_path`` set to the file (declared findings use ``NULL``).
      * ``raw_data['source'] = 'scancode'`` (declared use ``'cdxgen'``).

    Idempotency: ``_reset_scan_for_rerun`` deletes all of this scan's
    license_findings before a re-run, so re-execution cannot duplicate rows. The
    synthetic first-party ComponentVersion is upserted on its stable purl, so a
    re-run reuses the same row rather than creating a second.

    No-op when scancode produced no detections (binary repo, scancode skipped,
    or the tool not installed) — the declared licenses stand on their own.
    """
    if not detections:
        return

    fp_version = _get_or_create_first_party_component_version(
        session, scan_uuid=scan_uuid, sbom=sbom
    )

    # De-dupe on (spdx_id, source_path) defensively — the adapter already
    # de-dupes, but the unique constraint is (scan, cv, license, kind,
    # source_path), so two detections with the same spdx on the same path would
    # otherwise raise an IntegrityError on flush.
    seen: set[tuple[str, str]] = set()
    for det in detections:
        # Defence in depth (security-reviewer High): the adapter already drops
        # SPDX tokens wider than ``licenses.spdx_id`` (String(64)), but the
        # detection comes from attacker-controlled content — re-check here so a
        # bypass of the adapter cap cannot raise StringDataRightTruncation and
        # roll back the (already-committed-intent) declared findings + component
        # graph. Over-length tokens are skipped, not truncated (a truncated SPDX
        # id is meaningless and could collide).
        if len(det.spdx_id) > _SPDX_ID_MAX_LENGTH:
            log.warning(
                "detected_license_spdx_too_long",
                length=len(det.spdx_id),
                limit=_SPDX_ID_MAX_LENGTH,
                preview=det.spdx_id[:80],
            )
            continue
        key = (det.spdx_id, det.source_path)
        if key in seen:
            continue
        seen.add(key)
        license_row = _get_or_create_license(
            session, spdx_id=det.spdx_id, reference_url=None
        )
        finding = LicenseFinding(
            scan_id=scan_uuid,
            component_version_id=fp_version.id,
            license_id=license_row.id,
            kind="detected",
            source_path=det.source_path,
            raw_data={"source": "scancode"},
        )
        session.add(finding)


def _get_or_create_first_party_component_version(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    sbom: dict[str, Any],
) -> ComponentVersion:
    """Upsert the synthetic first-party ComponentVersion for this scan.

    The component name is taken from the SBOM's ``metadata.component.name``
    (the project root cdxgen identified) when available, falling back to a
    generic label. The version segment of the purl is the scan id, giving a
    stable-per-scan identity that ``_reset_scan_for_rerun`` does not need to
    delete (it is reused on re-run) while still being unique per scan so two
    scans of the same project do not share first-party finding rows.
    """
    metadata = sbom.get("metadata") if isinstance(sbom, dict) else None
    root_name = "first-party"
    if isinstance(metadata, dict):
        root = metadata.get("component")
        if isinstance(root, dict):
            candidate = root.get("name")
            if isinstance(candidate, str) and candidate:
                root_name = candidate

    purl_base = _FIRST_PARTY_PURL_PREFIX
    purl_with_version = f"{_FIRST_PARTY_PURL_PREFIX}@{scan_uuid}"
    component = _get_or_create_component(
        session,
        purl=purl_base,
        name=root_name,
        package_type="trustedoss",
    )
    return _get_or_create_component_version(
        session,
        component=component,
        version=str(scan_uuid),
        purl_with_version=purl_with_version,
    )


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


__all__ = [
    "scan_source_task",
]
