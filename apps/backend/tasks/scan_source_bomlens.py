# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The source-scan stages after fetch when ``SCAN_ENGINE=bomlens`` (preview).

``tasks.scan_source._run_pipeline`` fetches the source, detects the language
and records the input manifests as for any scan, then hands over here. BomLens
replaces build-prep, cdxgen, the CocoaPods fill-in, the scope filter, scancode
and Trivy as one unit (``integrations.bomlens.runner``); TRUSCA keeps what it
governs: persisting through its own write paths (``tasks.bomlens_result``),
signing and attestation, the approval queue, source preservation, the
component outcome and the reachability follow-up.

Progress, in the order the in-process pipeline shows it: ``prep`` (the
execution plan), ``cdxgen`` (stage 1), ``scancode`` (stage 2, which also runs
the vulnerability match), ``sign`` without moving the percent, ``approvals``,
``finalize``.

Not done here yet: reusing the SBOM of an earlier scan with the same
dependency fingerprint. The fingerprint does not yet include the BomLens image
and contract version (plan unit T2-4), so a BomLens scan neither reuses an
earlier SBOM nor records a fingerprint a later scan could reuse.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Final

import structlog

from core.config import (
    cdxgen_fetch_license,
    scan_scope_filter_enabled,
    scan_scope_filter_non_deployable_enabled,
    scancode_enabled,
)
from core.db import sync_session_scope
from integrations.bomlens import runner as bomlens_runner
from models import Project, Scan
from services import scan_outcome
from tasks import scan_source as pipeline
from tasks._progress import make_line_callback
from tasks._scan_pipeline import set_stage
from tasks.bomlens_result import apply_bomlens_result, bomlens_component_outcome

log = structlog.get_logger("tasks.scan_source_bomlens")

#: Where the run's execution record is kept in ``scan_metadata``.
ENGINE_RUN_METADATA_KEY: Final = "scan_engine_run"


def _project_identity(scan_uuid: uuid.UUID, project_id: uuid.UUID) -> tuple[str, str]:
    """``PROJECT_NAME`` and ``PROJECT_VERSION`` for BomLens (it names the SBOM after them)."""
    with sync_session_scope() as session:
        project = session.get(Project, project_id)
        scan = session.get(Scan, scan_uuid)
        name = project.name if project is not None else "project"
        version = (scan.ref if scan is not None else None) or scan_uuid.hex[:12]
    return name, version


def _record_engine_run(scan_uuid: uuid.UUID, report: bomlens_runner.RunReport) -> None:
    """Keep what the runner did (plan status, stage 1 exit, restore) on the scan.

    Its own transaction and best-effort: it runs in a ``finally`` and must not
    replace the error that ended the run.
    """
    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                return
            metadata: dict[str, Any] = dict(scan.scan_metadata or {})
            metadata[ENGINE_RUN_METADATA_KEY] = report.as_metadata()
            scan.scan_metadata = metadata
            session.commit()
    except Exception:  # noqa: BLE001 - a record of the run must not fail it
        log.warning("bomlens_run_record_failed", exc_info=True)


def _record_component_outcome(scan_uuid: uuid.UUID, outcome: str) -> None:
    """Stamp ``scan_metadata['component_outcome']``; best-effort, like the in-process one."""
    log.info("scan_component_outcome", outcome=outcome)
    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is not None:
                merged = dict(scan.scan_metadata or {})
                merged[scan_outcome.METADATA_KEY] = outcome
                scan.scan_metadata = merged
                session.commit()
    except Exception:  # noqa: BLE001 - a scan must not die describing itself
        log.warning("scan_component_outcome_persist_failed", exc_info=True)


def run_bomlens_pipeline(
    *,
    scan_uuid: uuid.UUID,
    project_id: uuid.UUID,
    workspace: Path,
    source_dir: Path,
    project_root: Path,
    manifest_inventory: dict[str, Any] | None,
) -> None:
    """Run the BomLens engine on ``project_root`` and finish the scan.

    Raises a :class:`~integrations.bomlens.contract.BomLensError` when the
    engine cannot be run or its result cannot become a succeeded scan; the
    task body records that as a terminal failure.
    """
    if shutil.which("docker") is None:
        raise bomlens_runner.BomLensEngineError(
            "the BomLens engine needs the docker CLI and a Docker endpoint on the worker"
        )
    images = bomlens_runner.resolve_images(deep_license=scancode_enabled())
    name, version = _project_identity(scan_uuid, project_id)
    runner = bomlens_runner.BomLensRunner(
        scan_uuid=scan_uuid,
        layout=bomlens_runner.EngineLayout(scan_dir=workspace, source=project_root),
        images=images,
        project_name=name,
        project_version=version,
        fetch_license=cdxgen_fetch_license(),
        include_non_shipped=not (
            scan_scope_filter_enabled() and scan_scope_filter_non_deployable_enabled()
        ),
        line_callback=lambda stage: make_line_callback(scan_uuid, stage=stage),
    )
    log.info(
        "bomlens_engine_start",
        stage="prep",
        image=images.plan.run_ref,
        stage2_image=images.stage2.run_ref,
        deep_license=images.deep_license,
    )
    try:
        output_dir = runner.run(stage=lambda stage: pipeline._set_stage(scan_uuid, stage))
    finally:
        _record_engine_run(scan_uuid, runner.report)

    # Every engine container has exited (runner.run guarantees it), so nothing
    # writes to output_dir while the adapter reads it. The verified copies go
    # to a folder only the worker writes: outside every folder an engine
    # container mounted, created now, after the containers are gone.
    applied = apply_bomlens_result(
        scan_uuid=scan_uuid,
        output_dir=output_dir,
        source_dir=project_root,
        artifact_dir=runner.verified_dir(),
    )

    # Signing follows generation, as in the in-process pipeline; its percent
    # (30) is below stage 2's, so the row keeps the percent it has.
    set_stage(scan_uuid, "sign", None)
    if pipeline._sign_sbom(scan_uuid=scan_uuid, sbom_path=applied.sbom_path, workspace=workspace):
        pipeline._attest_sbom(
            scan_uuid=scan_uuid,
            project_id=project_id,
            sbom_path=applied.sbom_path,
            workspace=workspace,
        )

    pipeline._set_stage(scan_uuid, "approvals")
    pipeline._auto_create_conditional_approvals(scan_uuid=scan_uuid, project_id=project_id)

    pipeline._preserve_source_tree(
        scan_uuid=scan_uuid,
        project_id=project_id,
        source_dir=source_dir,
        scancode_json_path=None,
        sbom_path=applied.sbom_path,
    )
    # The engine's own "declared dependencies but resolved none" finding
    # decides an empty result here, which the in-process count alone cannot.
    _record_component_outcome(
        scan_uuid,
        bomlens_component_outcome(applied, manifest_inventory=manifest_inventory),
    )
    pipeline._set_stage(scan_uuid, "finalize")
    pipeline._mark_succeeded(scan_uuid)
    pipeline._dispatch_reachability(scan_uuid)


__all__ = ["ENGINE_RUN_METADATA_KEY", "run_bomlens_pipeline"]
