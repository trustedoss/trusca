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
   whose matching failed still shows its inventory.
5. A vulnerability scan that did not produce a trusted result fails the scan
   (:class:`~integrations.bomlens.contract.BomLensVulnerabilityScanFailed`).
   An empty report is never persisted as "no vulnerabilities" in its place.

The caller maps every :class:`~integrations.bomlens.contract.BomLensError` to a
terminal scan failure; ``tasks.scan_source`` already does.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import structlog

from core.db import sync_session_scope
from integrations.bomlens import stages
from integrations.bomlens.contract import (
    ROLE_SBOM_CYCLONEDX,
    ROLE_VULNERABILITIES_TRIVY,
    BomLensContractError,
    BomLensRunFailed,
    BomLensVulnerabilityScanFailed,
    EngineResult,
    load_manifest,
    read_json_output,
)
from models import Scan
from services import scan_outcome
from services.vulnerability_matching import persist_trivy_findings
from tasks.scan_container import extract_os_metadata
from tasks.scan_source import _persist_artifact, persist_sbom_components

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


def _clip(value: str | None) -> str | None:
    return value[:_TEXT_MAX] if isinstance(value, str) else None


def engine_record(result: EngineResult) -> dict[str, Any]:
    """The run's provenance, as kept in ``scan_metadata[ENGINE_METADATA_KEY]``.

    Every step is listed with its status and reason, including the ones that
    map to no TRUSCA stage, so nothing the engine reported is lost in the
    translation. ``detail`` is left out: it carries tool messages (network
    addresses, file names) that do not belong in a row users can read.
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
            )
        # This run's answer replaces whatever an earlier run of the same scan
        # row left, in both directions.
        if skip_reason is not None:
            metadata[scan_outcome.SCANCODE_SKIP_KEY] = skip_reason
        else:
            metadata.pop(scan_outcome.SCANCODE_SKIP_KEY, None)
        metadata[ENGINE_METADATA_KEY] = engine_record(result)
        scan.scan_metadata = metadata
        session.commit()
    for outcome in outcomes:
        log.warning(
            "bomlens_stage_degraded",
            stage=outcome.stage,
            reason=outcome.reason,
            engine_reason=outcome.engine_reason,
            step=outcome.step,
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


def _first_failed_step(result: EngineResult) -> str | None:
    return next((s.id for s in result.steps if s.status == "failed"), None)


def apply_bomlens_result(
    *,
    scan_uuid: uuid.UUID,
    output_dir: Path,
    source_dir: Path | None = None,
) -> AppliedBomLensResult:
    """Persist the BomLens run in ``output_dir`` as scan ``scan_uuid``.

    ``source_dir`` is the scanned project root, passed through to
    ``persist_sbom_components`` for its npm lockfile scope enrichment; ``None``
    for an input with no source tree.

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
    sbom_path, sbom_document = read_json_output(output_dir, sbom_output)

    # Read the vulnerability result before writing anything else, so a report
    # that does not match its digest fails the scan before half of it lands.
    problem = stages.vulnerability_failure(result)
    trivy_path: Path | None = None
    trivy_report: dict[str, Any] | None = None
    trivy_sha256: str | None = None
    if problem is None:
        trivy_output = result.output(ROLE_VULNERABILITIES_TRIVY)
        if trivy_output is None:
            problem = "the run recorded no Trivy report"
        else:
            trivy_path, trivy_report = read_json_output(output_dir, trivy_output)
            trivy_sha256 = trivy_output.sha256
            # The engine reports a Trivy ScanError as a failed step; this is the
            # same check on the report itself, so a manifest that missed it
            # still cannot turn "the scan did not run" into "no findings".
            if trivy_report.get("ScanError"):
                problem = "the Trivy report carries a scan error"

    persisted, left_out = sbom_for_persistence(sbom_document)
    with sync_session_scope() as session:
        persist_sbom_components(session, scan_uuid=scan_uuid, sbom=persisted, source_dir=source_dir)
        session.commit()
    _persist_artifact(scan_uuid, kind="sbom_cyclonedx", path=sbom_path, sha256=sbom_output.sha256)
    log.info("bomlens_components_persisted", stage="persist", left_out=left_out)

    if problem is not None or trivy_report is None or trivy_path is None:
        log.error("bomlens_vulnerability_scan_failed", stage="persist", problem=problem)
        raise BomLensVulnerabilityScanFailed(problem or "the run recorded no Trivy report")

    _persist_artifact(scan_uuid, kind="trivy_sbom_report", path=trivy_path, sha256=trivy_sha256)
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
    )


__all__ = [
    "ENGINE_METADATA_KEY",
    "OS_CONTEXT_BOM_REF",
    "AppliedBomLensResult",
    "apply_bomlens_result",
    "engine_record",
    "sbom_for_persistence",
]
