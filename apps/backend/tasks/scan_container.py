# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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

``SCAN_CONTAINER_ENGINE=bomlens`` (preview) dispatches
:func:`scan_container_engine_task` instead, which runs on the engine worker:
the same row checks and registry allow-list as here, then
:mod:`tasks.scan_container_bomlens` in place of the Trivy stage.
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import (
    scan_load_test_delay_seconds,
    scan_soft_time_limit_seconds,
    workspace_root,
)
from core.db import sync_session_scope
from integrations import trivy as trivy_adapter
from integrations._size_guard import enforce_jsonb_row_size_limit
from integrations.bomlens import contract as bomlens_contract
from integrations.bomlens import runner as bomlens_runner
from models import (
    Component,
    ComponentDependencyEdge,
    ComponentVersion,
    LicenseFinding,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    Team,
)
from services import scan_outcome
from services.registry_allowlist import (
    allowed_registries,
    is_registry_allowed,
    shown_registry,
)
from services.registry_credential_service import credentials_for_image
from services.vulnerability_matching import (
    FindingReconciler,
    _build_purl,
    _purl_from_identifier,
    _upsert_vulnerability_from_trivy,
)
from tasks._progress import make_line_callback, publish_progress
from tasks._registry_auth import registry_auth_dir, sweep_stale_auth_dirs
from tasks._scan_pipeline import claim_success, mark_failed
from tasks.celery_app import celery_app
from tasks.scan_source import _extract_spdx_ids, _get_or_create_license

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
# A Trivy license that is one SPDX-style token (``MIT``, ``GPL-2.0-only``).
_SINGLE_LICENSE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_\-]*")

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
    """Run a Trivy-based container scan to completion (in-process engine)."""
    _run_container_task(self, scan_id, engine="inprocess")


# SCAN_CONTAINER_ENGINE=bomlens: the same rule as the BomLens source task. It
# runs on worker-engine only (queue trustedoss.engine), the engine is decided
# by which task was dispatched, and neither task falls back to the other.
@celery_app.task(  # type: ignore[misc]
    name="trustedoss.scan_container_engine",
    bind=True,
)
def scan_container_engine_task(self: Any, scan_id: str) -> None:
    """Run a container scan to completion on the BomLens engine."""
    _run_container_task(self, scan_id, engine="bomlens")


def _run_container_task(self: Any, scan_id: str, *, engine: str) -> None:
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
            # Only a queued scan, or a running one Celery redelivered after a
            # worker died, is (re)started. A scan that already ended keeps its
            # ending: a scan cancelled while it waited in the queue, whose
            # revoke never reached worker-engine, must not be flipped back to
            # running (security review on #522, N-M2, as for source scans).
            if scan.status not in ("queued", "running"):
                log.info("scan_container_not_restartable", status=scan.status)
                return
            project = session.get(Project, scan.project_id)
            if project is None:
                _mark_failed(session, scan, "project no longer exists")
                return

            image_ref = _resolve_image_ref(scan.scan_metadata)
            if not image_ref:
                _mark_failed(session, scan, "scan.metadata.image_ref is required")
                return

            # ER3. Enforced here as well as at trigger time, not instead of it.
            # The API check is what gives a caller a useful error; this one is
            # what actually protects the worker, because a row can reach this
            # task without passing through that schema (a re-run of a scan
            # queued before the list was tightened, or any future producer).
            # The registry is named in the failure because the operator's fix
            # is to add it to the list or correct the reference.
            allowed = allowed_registries()
            if not is_registry_allowed(image_ref, allowed):
                # A host carrying credentials (user:token@host) is never
                # quoted: the error message is readable by the whole team.
                shown = shown_registry(image_ref)
                named = (
                    f"registry {shown!r}"
                    if shown is not None
                    else "the registry part of this image reference carries credentials "
                    "(user@host), and that registry"
                )
                _mark_failed(
                    session,
                    scan,
                    f"{named} is not in "
                    "CONTAINER_SCAN_ALLOWED_REGISTRIES, so this image was not pulled",
                )
                return

            # Scan-log verbosity (feat/scan-log-verbosity): snapshot the
            # per-scan flag while the row is loaded. "verbose" flips Trivy into
            # --debug; absence / any other value stays the quiet "normal" trace.
            verbose = str(scan.scan_metadata.get("verbosity", "normal")) == "verbose"

            _reset_for_rerun(session, scan)
            _mark_running(session, scan)

        # M1/M2 (concurrency-scaling plan) load-test mode: see
        # tasks.scan_source._run_load_test_delay for the full rationale. Gated
        # to APP_ENV=dev with an explicit opt-in (scan_load_test_delay_seconds
        # returns 0.0 on every other deployment), so this branch is
        # unreachable outside a local load test.
        delay_seconds = scan_load_test_delay_seconds()
        if delay_seconds > 0:
            _run_load_test_delay(scan_uuid=scan_uuid, delay_seconds=delay_seconds)
        else:
            _run_pipeline(
                scan_uuid=scan_uuid,
                image_ref=image_ref,
                workspace=workspace,
                verbose=verbose,
                engine=engine,
            )
    except bomlens_runner.EngineScanCancelled as exc:
        # The engine noticed the scan was cancelled: the row already says so.
        log.info("scan_container_engine_stopped", reason=str(exc)[:100])
    except bomlens_contract.BomLensError as exc:
        # SCAN_CONTAINER_ENGINE=bomlens: the image could not be scanned, or
        # the result could not become a succeeded scan (a vulnerability scan
        # that did not end ok included). The message names roles, steps,
        # settings and the registry host, never a workspace path; it may end
        # with the engine container's last output lines, credentials removed on
        # a best effort only (integrations.bomlens.contract.BomLensError).
        log.error("scan_container_engine_failed", engine="bomlens", error=str(exc)[:300])
        _record_terminal_failure(scan_uuid, f"scan engine: {exc}")
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


def _resolve_registry_credentials(
    *, scan_uuid: uuid.UUID, image_ref: str
) -> dict[str, tuple[str, str]]:
    """The organization's login for the registry this image comes from, if any.

    Resolves scan -> project -> team -> organization, because credentials are
    organization-scoped: a private registry is shared infrastructure rather
    than something each team configures separately.

    Returns an empty mapping for a public image or an organization with no
    credential, which is the ordinary case and means no ``config.json`` is
    written at all.
    """
    with sync_session_scope() as session:
        organization_id = session.execute(
            select(Team.organization_id)
            .select_from(Scan)
            .join(Project, Project.id == Scan.project_id)
            .join(Team, Team.id == Project.team_id)
            .where(Scan.id == scan_uuid)
        ).scalar_one_or_none()
        if organization_id is None:
            return {}
        return credentials_for_image(
            session, organization_id=organization_id, image_ref=image_ref
        )


def _run_pipeline(
    *,
    scan_uuid: uuid.UUID,
    image_ref: str,
    workspace: Path,
    verbose: bool = False,
    engine: str = "inprocess",
) -> None:
    """The container stages. ``engine`` comes from the task that was
    dispatched, never from this worker's environment."""
    _set_stage(scan_uuid, "bootstrap")
    workspace.mkdir(parents=True, exist_ok=True)

    if engine == "bomlens":
        from tasks.scan_container_bomlens import run_bomlens_container_pipeline

        run_bomlens_container_pipeline(
            scan_uuid=scan_uuid,
            image_ref=image_ref,
            workspace=workspace,
            set_stage=_set_stage,
            mark_succeeded=_mark_succeeded,
        )
        return

    # Clear anything a SIGKILLed run left behind before writing our own. The
    # `finally` below cannot run when the process is killed outright, so this
    # is the admitted gap being closed rather than a claim that it never
    # happens.
    sweep_stale_auth_dirs()

    credentials = _resolve_registry_credentials(scan_uuid=scan_uuid, image_ref=image_ref)

    _set_stage(scan_uuid, "trivy")
    with registry_auth_dir(credentials, scan_id=scan_uuid) as auth_dir:
        trivy_result = trivy_adapter.run_trivy_image(
            image_ref=image_ref,
            output_dir=workspace / "trivy",
            # Stream Trivy's progress/diagnostic lines onto the scan log
            # (feat/scan-log-verbosity); ``verbose`` adds --debug.
            line_callback=make_line_callback(scan_uuid, stage="trivy"),
            verbose=verbose,
            # A directory path, never the credential itself. The context
            # manager deletes it in a `finally`, so a Trivy failure does not
            # leave the file behind.
            docker_config_dir=auth_dir,
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


def _run_load_test_delay(*, scan_uuid: uuid.UUID, delay_seconds: float) -> None:
    """M1 (concurrency-scaling plan) queue-wait / processing-time load test mode.

    Mirrors ``tasks.scan_source._run_load_test_delay`` for the container
    pipeline: skips the Trivy image scan entirely, sleeps ``delay_seconds``,
    then reuses ``_set_stage``/``_mark_succeeded`` so ``started_at`` /
    ``current_step`` / ``progress_percent`` / ``completed_at`` fill in exactly
    the way a real container scan's do. No workspace, Trivy report, or
    vulnerability finding is produced, by design.
    """
    log.warning(
        "scan_container_load_test_delay_mode",
        scan_id=str(scan_uuid),
        delay_seconds=delay_seconds,
    )
    _set_stage(scan_uuid, "bootstrap")
    time.sleep(delay_seconds)
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
    # Vulnerability findings are not deleted here (D1): ``_persist_trivy_report``
    # reconciles them, so their ids and the transition approvals that reference
    # them (ON DELETE CASCADE) survive a redelivered run.
    session.execute(delete(LicenseFinding).where(LicenseFinding.scan_id == scan.id))
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
    scan.scan_metadata = scan_outcome.reset_stage_records(scan.scan_metadata)
    session.commit()


def _mark_failed(session: Session, scan: Scan, message: str) -> None:
    """The shared failure writer: a scan that already ended (cancelled while
    the engine ran, say) keeps its ending (security review on #522, N-L4)."""
    mark_failed(session, scan, message)


def _record_terminal_failure(scan_uuid: uuid.UUID, message: str) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        _mark_failed(session, scan, message)


def _mark_succeeded(scan_uuid: uuid.UUID) -> None:
    """Record success only over ``running``: a scan cancelled after the
    pipeline's last status check stays cancelled (``claim_success``)."""
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        if not claim_success(session, scan):
            return
        session.commit()
    publish_progress(scan_uuid, step="succeeded", percent=100)


def _set_stage(scan_uuid: uuid.UUID, stage: str) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.current_step = stage
        scan.progress_percent = _STAGE_PROGRESS.get(stage, scan.progress_percent)
        scan.scan_metadata = scan_outcome.advance_stage_timings(
            scan.scan_metadata, stage=stage, now=datetime.now(UTC)
        )
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
    """Persist Trivy results into ScanComponent + VulnerabilityFinding rows.

    Findings are reconciled on ``(component_version_id, vulnerability_id)``
    rather than replaced (D1, see ``FindingReconciler``): a pair the scan
    already has keeps its row and id, a pair the report no longer has is
    deleted. The same pair reported under two targets is one finding.
    """
    results = report.get("Results", []) or []
    # X1 SLA and the verdict carry forward from the project's other scans
    # exactly as before; containers never carried the assignment, and this
    # change does not start to.
    reconciler = FindingReconciler(session, scan_uuid=scan_uuid, carry_assignment=False)
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
    # ER8: catalog rows a concurrent inserter won during this call. Reported on
    # the summary line so an operator can alarm on a field rather than grep for
    # the per-CVE warning.
    catalog_races = 0
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
            identity = _component_identity(
                vuln,
                ecosystem=result.get("Type"),
                pkg_name=pkg_name,
                installed=installed,
            )
            if identity is None:
                log.info(
                    "container_finding_skipped_no_purl",
                    scan_id=str(scan_uuid),
                    ecosystem=result.get("Type"),
                    pkg=pkg_name,
                )
                continue
            purl, component_purl, package_type = identity
            component = _get_or_create_component(
                session,
                purl=component_purl,
                name=pkg_name,
                package_type=package_type,
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

            # ER8: through the shared upsert, not a hand-rolled INSERT. Two
            # workers scanning at once both miss the same CVE and both insert
            # it; the loser's flush trips the unique constraint on
            # ``vulnerabilities.external_id``. Here that flush also writes the
            # ScanComponent and VulnerabilityFinding rows staged earlier in
            # this loop, so the violation aborted the whole transaction and
            # the container scan failed outright. The shared helper runs the
            # insert in a SAVEPOINT and re-reads the winner's row. OS packages
            # are the likeliest collision of all: ``glibc``, ``openssl`` and
            # ``curl`` appear in nearly every image and in source scans too,
            # and both paths feed this one catalog.
            vuln_row, raced = _upsert_vulnerability_from_trivy(
                session, external_id=cve_id, vuln=vuln, scan_uuid=scan_uuid
            )
            if raced:
                catalog_races += 1
            if vuln_row is None:
                log.warning(
                    "container_finding_skipped_no_vuln_row",
                    scan_id=str(scan_uuid),
                    vuln_id=cve_id,
                )
                continue

            guarded_finding = enforce_jsonb_row_size_limit(
                vuln,
                context={
                    "scan_id": str(scan_uuid),
                    "column": "vulnerability_findings.analysis_response",
                    "external_id": cve_id,
                },
            )
            # fixed_version stays unset on the container path, as before.
            reconciler.apply(
                component_version_id=cv.id,
                vulnerability_id=vuln_row.id,
                analysis_response=guarded_finding,
                fixed_version=None,
            )

    inventory = _persist_package_inventory(
        session, scan_uuid=scan_uuid, results=results, seen_components=seen_components
    )

    # M-6: per-finding create audit rows (same transaction as the findings).
    emitted = reconciler.finish()
    created_findings = reconciler.created
    if emitted != len(created_findings):
        log.warning(
            "container_finding_audit_gap",
            scan_id=str(scan_uuid),
            findings=len(created_findings),
            audits_emitted=emitted,
        )
    log.info(
        "container_findings_persisted",
        scan_id=str(scan_uuid),
        inserted=len(created_findings),
        updated=reconciler.updated,
        removed=reconciler.deleted,
        catalog_races=catalog_races,
        audits_emitted=emitted,
        inventory_components=inventory.components,
        inventory_license_findings=inventory.license_findings,
        inventory_edges=inventory.edges,
    )


class _InventoryCounts(NamedTuple):
    components: int
    license_findings: int
    edges: int


def _license_ids(pkg: dict[str, Any]) -> list[str]:
    """Trivy's ``Licenses`` strings as separate ids, one per license.

    Not joined with ``OR`` the way the source pipeline joins a cdxgen list. A
    Debian package lists every license found in its copyright file, which is
    routinely a dozen entries; the joined string overflows the 64-character
    ``licenses.spdx_id`` column and ``_extract_spdx_ids`` then drops all of it
    (51 of 282 licensed packages in the recorded image), and those are the
    packages with the most copyleft. Separate findings keep each license, and a
    forbidden one among them still shows.

    A single-token id is kept as declared; anything else goes through the alias
    table and is dropped when it is not recognised.
    """
    raw = pkg.get("Licenses")
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        key = "id" if _SINGLE_LICENSE_TOKEN.fullmatch(text) else "name"
        for spdx_id, _url in _extract_spdx_ids({"licenses": [{"license": {key: text}}]}):
            if spdx_id not in ids:
                ids.append(spdx_id)
    return ids


def _persist_package_inventory(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    results: list[Any],
    seen_components: set[tuple[uuid.UUID, str]],
) -> _InventoryCounts:
    """Store every package Trivy listed, not only the vulnerable ones (U3-F).

    Runs after the finding loop, so a package that carries a CVE already has
    its ScanComponent (``seen_components``) and gets no second row; the rest get one,
    their declared licenses, and the ``DependsOn`` edges between them. Licenses
    are stored for every package, vulnerable or not.
    """
    components = license_findings = 0
    licensed: set[uuid.UUID] = set()
    # (target, Trivy package ID) -> component version, for the edge pass.
    by_package_id: dict[tuple[str, str], uuid.UUID] = {}
    package_lists: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        target = result.get("Target", "")
        packages = [p for p in result.get("Packages", []) or [] if isinstance(p, dict)]
        package_lists.append((target, result, packages))
        for pkg in packages:
            pkg_name = pkg.get("Name")
            installed = pkg.get("Version")
            if not pkg_name or not installed:
                continue
            identity = _component_identity(
                {"PkgIdentifier": pkg.get("Identifier")},
                ecosystem=result.get("Type"),
                pkg_name=pkg_name,
                installed=installed,
            )
            if identity is None:
                continue
            purl, component_purl, package_type = identity
            component = _get_or_create_component(
                session, purl=component_purl, name=pkg_name, package_type=package_type
            )
            cv = _get_or_create_component_version(
                session, component=component, version=installed, purl_with_version=purl
            )
            if isinstance(pkg.get("ID"), str):
                by_package_id[(target, pkg["ID"])] = cv.id
            key = (cv.id, target)
            if key not in seen_components:
                seen_components.add(key)
                session.add(
                    ScanComponent(
                        scan_id=scan_uuid,
                        component_version_id=cv.id,
                        dependency_scope="runtime",
                        dependency_path=target,
                        direct=True,
                        raw_data=enforce_jsonb_row_size_limit(
                            {k: v for k, v in pkg.items() if k != "DependsOn"},
                            context={
                                "scan_id": str(scan_uuid),
                                "column": "scan_components.raw_data",
                                "target": target,
                            },
                        ),
                    )
                )
                components += 1
            # Licenses are per component version, and a vulnerable package
            # already has its row from the finding loop but no license yet.
            if cv.id in licensed:
                continue
            licensed.add(cv.id)
            for spdx_id in _license_ids(pkg):
                license_row = _get_or_create_license(session, spdx_id=spdx_id, reference_url=None)
                session.add(
                    LicenseFinding(
                        scan_id=scan_uuid,
                        component_version_id=cv.id,
                        license_id=license_row.id,
                        kind="declared",
                        source_path=None,
                        raw_data={"source": "trivy"},
                    )
                )
                license_findings += 1

    edges = 0
    seen_edges: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for target, _result, packages in package_lists:
        for pkg in packages:
            parent = by_package_id.get((target, pkg.get("ID", "")))
            depends_on = pkg.get("DependsOn")
            if parent is None or not isinstance(depends_on, list):
                continue
            for child_id in depends_on:
                child = by_package_id.get((target, child_id)) if isinstance(child_id, str) else None
                if child is None or child == parent or (parent, child) in seen_edges:
                    continue
                seen_edges.add((parent, child))
                session.add(
                    ComponentDependencyEdge(
                        scan_id=scan_uuid,
                        parent_component_version_id=parent,
                        child_component_version_id=child,
                    )
                )
                edges += 1
    return _InventoryCounts(components, license_findings, edges)


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


def _component_identity(
    vuln: dict[str, Any],
    *,
    ecosystem: Any,
    pkg_name: str,
    installed: str,
) -> tuple[str, str, str] | None:
    """Return ``(purl_with_version, component_purl, package_type)`` for a finding.

    This used to be three hardcoded strings: ``pkg:apk/{name}@{version}``,
    ``pkg:apk/{name}`` and ``"apk"`` — for every package in every image. A
    Rocky image's rpms were persisted as apk packages, a Debian image's debs
    likewise, and pip inside a python image became ``pkg:apk/pip``. No finding
    was lost, so the screens looked right and only the values were wrong: the
    component inventory, the SBOM export and any policy that reads a package
    type all saw an ecosystem the image does not have.

    Trivy states the identity itself. Every Result it emits — os-pkgs and
    lang-pkgs alike — carries ``PkgIdentifier.PURL``
    (``pkg:rpm/rocky/bzip2-libs@1.0.8-8.el9?arch=x86_64&distro=rocky-9.3``), so
    that is read first, exactly as the SBOM matcher does.

    Qualifiers are dropped from what we store. ``distro=rocky-9.3`` would make
    the same package version a NEW component on every point release of the base
    image, and the first-detected clock and carried triage are keyed on the
    component version — so the whole image's findings would read as new after a
    9.3 → 9.4 rebuild. The rule is ``services.purl_build.purl_without_qualifiers``,
    and the BomLens container path stores its packages through the same
    function, so a project can switch engines without every package becoming a
    new component. It drops ``epoch=1`` on Debian packages as well; that is
    lost information, accepted so the stored identity stays what it has been.

    Returns None when no identity can be established, and the caller skips the
    finding. Calling it apk would not make it apk.
    """
    purl = _purl_from_identifier(vuln) or _build_purl(ecosystem, pkg_name, installed)
    if purl is None:
        return None

    component_purl, separator, _version = purl.rpartition("@")
    if not separator or not component_purl:
        # A version-less PURL cannot name a component version; Trivy always
        # writes one, so this is a malformed report rather than a real case.
        return None

    scheme, _, remainder = purl.partition(":")
    package_type = remainder.split("/", 1)[0]
    if scheme != "pkg" or not package_type:
        return None

    return purl, component_purl, package_type


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


__all__ = ["scan_container_engine_task", "scan_container_task"]
