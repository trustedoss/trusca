# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Persist a BomLens run's output folder as a TRUSCA scan (``SCAN_ENGINE=bomlens``).

This is the adapter half of the BomLens scan engine: given the folder a
BomLens run wrote (result manifest, CycloneDX SBOM, Trivy's own JSON report),
it writes the scan through the same paths the in-process pipeline uses:
``persist_sbom_components`` for the inventory and declared licences, and
``persist_trivy_findings`` for vulnerabilities. Nothing here re-implements
either. Running the image is a later unit; its executor calls
:func:`apply_bomlens_result` once the run has ended and then continues with the
stages TRUSCA keeps for itself (signing, approvals, preservation, finalize).

Order matters, and follows the in-process pipeline:

1. The manifest is read and validated first. A manifest TRUSCA cannot trust
   (missing, malformed, a contract major other than 1, a file that does not
   match its recorded digest) writes nothing at all.
2. The run's stage records go to ``scan_metadata`` next, in their own
   transaction, so a scan that fails below still says which step failed.
3. A run the engine itself reports as failed or cancelled stops here.
4. Components are persisted and committed before the vulnerability verdict,
   as the in-process pipeline commits them before ``trivy sbom`` runs: a scan
   whose matching failed still shows its inventory. The licences scancode
   detected in the project's own files (``deep-license``) commit with them,
   through the in-process ``_persist_detected_licenses`` and its savepoint, and
   so do the records that say how complete the result is
   (:mod:`integrations.bomlens.records`).
5. A vulnerability scan that did not produce a trusted result fails the scan
   (:class:`~integrations.bomlens.contract.BomLensVulnerabilityScanFailed`).
   An empty report is never persisted as "no vulnerabilities" in its place.

The caller maps every :class:`~integrations.bomlens.contract.BomLensError` to a
terminal scan failure; ``tasks.scan_source`` already does.
"""

from __future__ import annotations

import copy
import errno
import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import structlog
from sqlalchemy.orm import Session

from core.config import scancode_max_detections, scancode_max_result_bytes
from core.db import sync_session_scope
from integrations import scancode as scancode_adapter
from integrations.bomlens import records, stages
from integrations.bomlens.contract import (
    ROLE_LICENSE_SCAN_SCANCODE,
    ROLE_SBOM_CYCLONEDX,
    ROLE_VULNERABILITIES_TRIVY,
    BomLensContractError,
    BomLensRunFailed,
    BomLensVulnerabilityScanFailed,
    EngineResult,
    OutputTooLarge,
    VerifiedOutput,
    clip,
    load_manifest,
    read_output,
    storable_text,
)
from models import Scan
from services import sbom_component_walk, scan_outcome
from services.vulnerability_matching import persist_trivy_findings
from tasks._scan_pipeline import record_stage_outcome
from tasks.scan_container import extract_os_metadata
from tasks.scan_source import (
    _persist_artifact,
    _persist_detected_licenses_or_skip,
    persist_sbom_components,
)

log = structlog.get_logger("tasks.bomlens_result")

#: Where the run's provenance is kept in ``scan_metadata``.
ENGINE_METADATA_KEY: Final = "scan_engine"

#: The synthetic operating-system component BomLens adds so Trivy picks the
#: right distribution feed. It describes the scan, not a package, and is kept
#: out of the inventory (the OS is recorded from the Trivy report instead).
OS_CONTEXT_BOM_REF: Final = "bomlens-os-context"

#: Component types that name something other than a package. syft lists every
#: file it read as a ``file`` component and the image's distribution as an
#: ``operating-system`` component; neither has a PURL, and the in-process
#: pipeline never persisted either.
_NON_PACKAGE_TYPES: Final = ("file", "operating-system")

#: BomLens writes a component's missing version as this; TRUSCA's persist path
#: has its own default for a missing version, which is what it should get.
_UNKNOWN_VERSION: Final = "UNKNOWN"

#: BomLens property naming the tool that identified a vendored component, and
#: the value the in-process pipeline records as ``raw_data.source`` for the
#: same thing (``tasks.scan_source._SCANOSS_SOURCE``).
_IDENTIFIED_BY: Final = "bomlens:identifiedBy"
_SCANOSS: Final = "scanoss"

#: The only Trivy JSON report schema this reader accepts.
_TRIVY_SCHEMA_VERSION: Final = 2

#: Bound on any manifest string copied into ``scan_metadata``.
_TEXT_MAX: Final = 200


@dataclass(frozen=True)
class AppliedBomLensResult:
    """What :func:`apply_bomlens_result` persisted."""

    result: EngineResult
    sbom_path: Path
    sbom: dict[str, Any]
    components_left_out: int
    findings_persisted: int
    #: Licences scancode detected in the project's own files and persisted.
    detections_persisted: int = 0
    #: BomLens found manifests that declare dependencies and no component
    #: came of them (``generate`` / ``no_components``).
    declared_but_empty: bool = False


def _clip(value: str | None) -> str | None:
    """A manifest string as stored in ``scan_metadata``: no control characters
    (:func:`storable_text`; a NUL would fail the JSONB write and the scan),
    at most :data:`_TEXT_MAX` characters. Cut once before cleaning so an
    oversized value is never walked whole, and again after."""
    if not isinstance(value, str):
        return None
    return storable_text(value[: _TEXT_MAX * 4])[:_TEXT_MAX]


def engine_record(result: EngineResult) -> dict[str, Any]:
    """The run's provenance, as kept in ``scan_metadata[ENGINE_METADATA_KEY]``.

    Every step is listed with its status and reason, including the ones that
    map to no TRUSCA stage, so nothing the engine reported is lost in the
    translation. ``detail`` is left out: it carries tool messages (network
    addresses, file names) that do not belong in a row users can read. The
    detail of a step that became a TRUSCA stage degradation is the exception,
    kept with that outcome once cut and scrubbed
    (``integrations.bomlens.stages.step_detail``), because without it a
    degraded ``generate`` does not say why. ``scan-security`` is never one, so
    Trivy's network messages stay out.
    """
    return {
        "name": "bomlens",
        "contract_version": _clip(result.contract_version),
        "engine_version": _clip(result.engine_version),
        "image": _clip(result.image),
        "image_digest": _clip(result.image_digest),
        "input_kind": _clip(result.input_kind),
        "run_status": result.run_status,
        "tools": {_clip(k): _clip(v) for k, v in list(result.tools.items())[:20]},
        "steps": [
            {"id": _clip(s.id), "status": s.status, "reason": _clip(s.reason)}
            for s in result.steps[:100]
        ],
    }


def _scancode_skip_reason(result: EngineResult) -> str | None:
    """The ``scancode_skipped`` value for a licence scan that did not run.

    Only for a step that did not run at all (failed or skipped); a scan that ran
    and reported errors on some files is a degraded ``scancode`` stage, not a
    skipped one.
    """
    step = result.step(stages.DEEP_LICENSE_STEP)
    if step is None or step.status not in ("failed", "skipped"):
        return None
    if not stages.is_degradation(step):
        return None
    reason = step.reason or "failed"
    return reason if reason in scan_outcome.SCANCODE_SKIP_REASONS else "failed"


def _record_engine_run(scan_uuid: uuid.UUID, result: EngineResult) -> None:
    """Write stage timings, stage outcomes and the engine record, and commit."""
    outcomes = stages.stage_outcomes(result)
    windows = stages.stage_windows(result)
    skip_reason = _scancode_skip_reason(result)
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        metadata = scan_outcome.add_stage_windows(scan.scan_metadata, windows)
        for outcome in outcomes:
            metadata = scan_outcome.add_stage_outcome(
                metadata,
                stage=outcome.stage,
                reason=outcome.reason,
                engine_reason=outcome.engine_reason,
                engine_detail=outcome.engine_detail,
            )
        # This run's answer replaces whatever an earlier run of the same scan
        # row left, in both directions.
        if skip_reason is not None:
            metadata[scan_outcome.SCANCODE_SKIP_KEY] = skip_reason
        else:
            metadata.pop(scan_outcome.SCANCODE_SKIP_KEY, None)
        # Written again with the components (_record_result_completeness);
        # an earlier run's record must not survive a run that has none.
        metadata.pop(scan_outcome.LICENSE_ENRICHMENT_KEY, None)
        metadata.pop(records.SCOPE_FILTER_KEY, None)
        metadata[ENGINE_METADATA_KEY] = engine_record(result)
        scan.scan_metadata = metadata
        session.commit()
    for outcome in outcomes:
        log.warning(
            "bomlens_stage_degraded",
            stage=outcome.stage,
            reason=outcome.reason,
            engine_reason=clip(outcome.engine_reason) if outcome.engine_reason else None,
            # Already cut, scrubbed and free of control characters (step_detail).
            engine_detail=outcome.engine_detail,
            step=clip(outcome.step),
            status=outcome.status,
        )


def _component_for_persistence(component: dict[str, Any]) -> dict[str, Any] | None:
    """A copy of ``component`` in the shape the persist path expects, or ``None``.

    ``None`` drops it: the BomLens OS-context component and PURL-less file or
    operating-system entries. Nested ``components`` are filtered the same way.
    """
    if component.get("bom-ref") == OS_CONTEXT_BOM_REF:
        return None
    if component.get("type") in _NON_PACKAGE_TYPES and not component.get("purl"):
        return None
    out = dict(component)
    if out.get("version") == _UNKNOWN_VERSION:
        out.pop("version")
    properties = out.get("properties")
    if isinstance(properties, list) and any(
        isinstance(p, dict) and p.get("name") == _IDENTIFIED_BY and p.get("value") == _SCANOSS
        for p in properties
    ):
        out.setdefault("source", _SCANOSS)
    nested = out.get("components")
    if isinstance(nested, list):
        out["components"] = _filter_components(nested)[0]
    return out


def _filter_components(components: list[Any]) -> tuple[list[Any], int]:
    kept: list[Any] = []
    dropped = 0
    for item in components:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        converted = _component_for_persistence(item)
        if converted is None:
            dropped += 1
        else:
            kept.append(converted)
    return kept, dropped


def sbom_for_persistence(sbom: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """The BomLens SBOM as the persist path should see it, and how many were left out.

    The file on disk is not changed; it stays the artifact exactly as BomLens
    wrote and hashed it.
    """
    document = copy.copy(sbom)
    components = sbom.get("components")
    if not isinstance(components, list):
        return document, 0
    kept, dropped = _filter_components(components)
    document["components"] = kept
    return document, dropped


def trivy_report_problem(report: dict[str, Any], *, sbom_has_components: bool) -> str | None:
    """Why ``report`` is not a vulnerability result, or ``None`` when it is one.

    An empty ``Results`` list is a real answer ("Trivy matched nothing") only
    when the rest of the report says Trivy ran over this SBOM: schema 2, a
    ``Results`` list of objects, and ``ArtifactType`` ``cyclonedx`` whenever
    there were components to match. ``{}``, ``{"SchemaVersion": 2}`` or a
    report about some other artifact would otherwise persist as zero CVEs and
    pass the build gate.
    """
    # The engine reports a Trivy ScanError as a failed step; this is the same
    # check on the report itself, so a manifest that missed it still cannot
    # turn "the scan did not run" into "no findings".
    if report.get("ScanError"):
        return "the Trivy report carries a scan error"
    if report.get("SchemaVersion") != _TRIVY_SCHEMA_VERSION:
        return "the Trivy report is not schema version 2"
    results = report.get("Results")
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        return "the Trivy report has no Results list"
    if sbom_has_components and report.get("ArtifactType") != "cyclonedx":
        return "the Trivy report is not about the CycloneDX SBOM"
    return None


def _keep_verified(artifact_dir: Path, verified: VerifiedOutput) -> Path:
    """Write the bytes that were checked to a file TRUSCA owns, and return it.

    The artifact row then points at exactly what was hashed and parsed, never
    at the engine's folder, where a file could change after it was read.

    A file already at that path that is a symbolic link is refused, never
    followed: the copy must land where the artifact row says it is.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / verified.name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise BomLensContractError(
                f"the kept copy of {clip(verified.role)} is a symbolic link"
            ) from exc
        raise
    with os.fdopen(fd, "wb") as fh:
        fh.write(verified.data)
    return path


@dataclass(frozen=True)
class _LicenseScan:
    """What the ``deep-license`` step left for persistence."""

    verified: VerifiedOutput | None = None
    detections: tuple[scancode_adapter.DetectedLicense, ...] = ()
    #: Set when the step ran but its result cannot be used; recorded as the
    #: ``scancode`` stage's reason.
    problem: str | None = None


def _read_license_scan(result: EngineResult, output_dir: Path) -> _LicenseScan:
    """Read the scancode result of a ``deep-license`` step that ran.

    A step that did not run has nothing to read; ``_record_engine_run`` has
    already recorded why. A result that does not match its digest, or is not
    JSON, raises like any other output and fails the scan: integrity comes
    first here, where the in-process stage would only degrade. A result larger
    than the in-process ceiling (``SCANCODE_MAX_RESULT_BYTES``) is not read
    past that ceiling, whatever size the manifest declared, and the stage is
    recorded as ``too_large``.

    Only the detections and the checked bytes are kept; the decoded document
    is dropped once they are taken from it.
    """
    step = result.step(stages.DEEP_LICENSE_STEP)
    if step is None or step.status not in ("ok", "degraded"):
        return _LicenseScan()
    output = result.output(ROLE_LICENSE_SCAN_SCANCODE)
    if output is None:
        log.warning("bomlens_license_scan_missing", stage="persist", status=step.status)
        return _LicenseScan(problem="failed")
    limit = scancode_max_result_bytes()
    verified: VerifiedOutput | None = None
    if output.bytes <= limit:
        try:
            verified = read_output(output_dir, output, limit=limit)
        except OutputTooLarge:
            verified = None
    if verified is None:
        log.warning(
            "bomlens_license_scan_too_large",
            stage="persist",
            declared_bytes=output.bytes,
            limit_bytes=limit,
        )
        return _LicenseScan(problem="too_large")
    detections = scancode_adapter.parse_detections_document(
        verified.document, cap=scancode_max_detections()
    )
    return _LicenseScan(verified=replace(verified, document={}), detections=tuple(detections))


def _record_result_completeness(
    session: Session,
    *,
    scan_uuid: uuid.UUID,
    result: EngineResult,
    sbom: dict[str, Any],
    license_scan: _LicenseScan,
) -> None:
    """Licence lookups not made, folders left out, and an unusable licence scan.

    On the caller's session, so the records commit with the components they
    describe. The persist path may have written its own ``license_enrichment``
    a moment ago (TRUSCA's registry lookups for what is still unlicensed);
    the engine's is merged into it rather than replacing it.
    """
    scan = session.get(Scan, scan_uuid)
    if scan is None:
        return
    metadata = dict(scan.scan_metadata or {})
    enrichment = records.merge_license_enrichment(
        metadata.get(scan_outcome.LICENSE_ENRICHMENT_KEY),
        records.license_enrichment_record(result),
    )
    if enrichment is not None:
        metadata[scan_outcome.LICENSE_ENRICHMENT_KEY] = enrichment
    components = sbom.get("components")
    scope = records.scope_filter_record(
        sbom, kept=len(components) if isinstance(components, list) else 0
    )
    if scope is not None:
        metadata[records.SCOPE_FILTER_KEY] = scope
    if license_scan.problem is not None:
        metadata = scan_outcome.add_stage_outcome(
            metadata, stage="scancode", reason=license_scan.problem
        )
    scan.scan_metadata = metadata


def _first_failed_step(result: EngineResult) -> str | None:
    return next((s.id for s in result.steps if s.status == "failed"), None)


def apply_bomlens_result(
    *,
    scan_uuid: uuid.UUID,
    output_dir: Path,
    source_dir: Path | None = None,
    artifact_dir: Path | None = None,
) -> AppliedBomLensResult:
    """Persist the BomLens run in ``output_dir`` as scan ``scan_uuid``.

    The caller must call this only after the engine container has exited, so
    nothing is still writing to ``output_dir`` (see
    ``integrations.bomlens.contract.read_output``).

    ``source_dir`` is the scanned project root, passed through to
    ``persist_sbom_components`` for its npm lockfile scope enrichment; ``None``
    for an input with no source tree. ``artifact_dir`` is where the verified
    bytes are kept for the scan's artifact rows; by default a sibling of
    ``output_dir`` (``<name>.verified``), outside the engine's folder.

    Raises a :class:`~integrations.bomlens.contract.BomLensError` subclass when
    the result cannot become a succeeded scan (see the module docstring for
    what has been written by then).
    """
    result = load_manifest(output_dir)
    log.info(
        "bomlens_result_loaded",
        stage="persist",
        contract_version=result.contract_version,
        engine_version=result.engine_version,
        image_digest=result.image_digest,
        run_status=result.run_status,
    )
    _record_engine_run(scan_uuid, result)

    if result.run_status in ("failed", "cancelled"):
        raise BomLensRunFailed(result.run_status, _first_failed_step(result))

    sbom_output = result.output(ROLE_SBOM_CYCLONEDX)
    if sbom_output is None:
        raise BomLensContractError("the BomLens run recorded no CycloneDX SBOM")
    keep_dir = artifact_dir or output_dir.with_name(f"{output_dir.name}.verified")
    sbom = read_output(output_dir, sbom_output)
    sbom_components = sbom.document.get("components")
    sbom_has_components = isinstance(sbom_components, list) and bool(sbom_components)

    # Read the vulnerability result before writing anything else, so a report
    # that does not match its digest fails the scan before half of it lands.
    problem = stages.vulnerability_failure(result)
    trivy: VerifiedOutput | None = None
    if problem is None:
        trivy_output = result.output(ROLE_VULNERABILITIES_TRIVY)
        if trivy_output is None:
            problem = "the run recorded no Trivy report"
        else:
            trivy = read_output(output_dir, trivy_output)
            problem = trivy_report_problem(trivy.document, sbom_has_components=sbom_has_components)
    license_scan = _read_license_scan(result, output_dir)

    persisted, left_out = sbom_for_persistence(sbom.document)
    detected_failed = False
    with sync_session_scope() as session:
        persist_sbom_components(session, scan_uuid=scan_uuid, sbom=persisted, source_dir=source_dir)
        if license_scan.detections:
            detected_failed = _persist_detected_licenses_or_skip(
                session,
                scan_uuid=scan_uuid,
                sbom=persisted,
                detections=list(license_scan.detections),
            )
        _record_result_completeness(
            session,
            scan_uuid=scan_uuid,
            result=result,
            sbom=persisted,
            license_scan=license_scan,
        )
        session.commit()
    if detected_failed:
        record_stage_outcome(scan_uuid, "detected_licenses")
    sbom_path = _keep_verified(keep_dir, sbom)
    _persist_artifact(scan_uuid, kind="sbom_cyclonedx", path=sbom_path, sha256=sbom.sha256)
    if license_scan.verified is not None:
        scancode_path = _keep_verified(keep_dir, license_scan.verified)
        _persist_artifact(
            scan_uuid,
            kind="scancode_result",
            path=scancode_path,
            sha256=license_scan.verified.sha256,
        )
    detections_persisted = 0 if detected_failed else len(license_scan.detections)
    log.info(
        "bomlens_components_persisted",
        stage="persist",
        left_out=left_out,
        detections=detections_persisted,
    )
    declared_but_empty = records.declared_but_empty(result, sbom.document)

    if problem is not None or trivy is None:
        log.error("bomlens_vulnerability_scan_failed", stage="persist", problem=problem)
        raise BomLensVulnerabilityScanFailed(problem or "the run recorded no Trivy report")

    trivy_report = trivy.document
    trivy_path = _keep_verified(keep_dir, trivy)
    _persist_artifact(scan_uuid, kind="trivy_sbom_report", path=trivy_path, sha256=trivy.sha256)
    with sync_session_scope() as session:
        inserted = persist_trivy_findings(session, scan_uuid=scan_uuid, trivy_report=trivy_report)
        os_meta = extract_os_metadata(trivy_report)
        if os_meta is not None:
            scan = session.get(Scan, scan_uuid)
            if scan is not None:
                merged = dict(scan.scan_metadata or {})
                merged["os"] = os_meta
                scan.scan_metadata = merged
        session.commit()
    log.info("bomlens_findings_persisted", stage="persist", findings_persisted=inserted)
    return AppliedBomLensResult(
        result=result,
        sbom_path=sbom_path,
        sbom=persisted,
        components_left_out=left_out,
        findings_persisted=inserted,
        detections_persisted=detections_persisted,
        declared_but_empty=declared_but_empty,
    )


def bomlens_component_outcome(
    applied: AppliedBomLensResult, *, manifest_inventory: dict[str, Any] | None
) -> str:
    """``component_outcome`` for an applied run, for the executor's finalize step.

    Counts components the way the in-process ``_record_component_outcome``
    does (nested ones included) and lets the engine's ``no_components``
    finding decide an empty result
    (:func:`integrations.bomlens.records.component_outcome`).
    """
    count = sum(1 for _ in sbom_component_walk.iter_components(applied.sbom.get("components")))
    return records.component_outcome(
        component_count=count,
        manifest_inventory=manifest_inventory,
        engine_declared_but_empty=applied.declared_but_empty,
    )


__all__ = [
    "ENGINE_METADATA_KEY",
    "OS_CONTEXT_BOM_REF",
    "AppliedBomLensResult",
    "apply_bomlens_result",
    "bomlens_component_outcome",
    "engine_record",
    "sbom_for_persistence",
    "trivy_report_problem",
]
