# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""BomLens steps in TRUSCA's stage vocabulary.

A BomLens run records one entry per step (``integrations.bomlens.contract``).
TRUSCA records two things per scan in ``scan_metadata`` (U3-B):
``stage_timings`` (when each stage that ran started and ended) and
``stage_outcomes`` (stages that finished worse than a normal run, with a
reason). This module decides, per BomLens step, which TRUSCA stage it is and
whether its result is a degradation.

Rules, each one a decision rather than a translation:

* ``ok`` and ``disabled`` are not degradations. A step an operator turned off
  is a choice, the same rule ``stage_outcomes`` already follows.
* ``skipped`` with ``no_input`` is not a degradation: there was nothing for the
  step to read (no signing key, no source tree). ``skipped`` for any other
  reason is: the step was asked for and could not run (BomLens #190 makes the
  engine's own ``run.status`` say the same).
* ``degraded`` and ``failed`` are degradations.
* ``scan-security`` is not degradable. A vulnerability scan that did not
  produce a result fails the TRUSCA scan, as a failed ``trivy sbom`` does in
  the in-process pipeline; :func:`vulnerability_failure` reports it.
* Steps TRUSCA does its own way (end-of-life and malicious-package flags,
  notices, risk reports, the file viewer's snapshot) or that only apply to
  input kinds TRUSCA does not open yet (AI models) map to nothing and are
  recorded only in the engine record, never as a TRUSCA stage.
* A step id this reader does not know is left out with a warning: contract
  minor versions add step ids and a reader ignores what it does not know.
* A reason this reader does not know (a contract minor version adds reasons,
  as BomLens #194 added ``no_components``, now known) is never read as success. The step counts as a
  degradation whatever its status says, except ``disabled``; TRUSCA records it
  under the catch-all ``failed`` so it is labelled and counted, and keeps the
  engine's reason verbatim beside it (``engine_reason``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

import structlog

from integrations.bomlens.contract import STEP_REASONS, EngineResult, Step, clip
from services import scan_outcome

log = structlog.get_logger("integrations.bomlens.stages")

#: The BomLens step whose failure fails the TRUSCA scan.
VULNERABILITY_STEP: Final = "scan-security"

#: The BomLens step that detects licences in the project's own files.
DEEP_LICENSE_STEP: Final = "deep-license"

#: BomLens step id -> TRUSCA degradable stage, or ``None`` for a step TRUSCA
#: does not report as a stage of its own. Every BomLens step of contract 1.0
#: is listed, so an unlisted id is one a later minor version added.
STEP_STAGE: Final[dict[str, str | None]] = {
    "generate": "generate",
    "vendored": "scanoss",
    "suggest-vendored": None,
    "cocoapods": "cocoapods",
    # Fail-closed in BomLens: when it fails the run fails, so it never reaches
    # TRUSCA as a degradation of a run that succeeded.
    "stamp-metadata": None,
    "docmeta": "document_metadata",
    "license-registry": "enrich",
    "normalize": "normalize",
    "enrich-cpe": "enrich",
    "enrich-os-context": "enrich",
    "enrich-distro-supplier": "enrich",
    "enrich-maven-cpe": "enrich",
    "enrich-github-cpe": "enrich",
    "enrich-interpreter-cpe": "enrich",
    "enrich-eol": None,
    "enrich-malicious": None,
    "enrich-staleness": None,
    "declared-sources": None,
    "declared-service": None,
    "assess-ai-risk": None,
    "resolve-purl": "enrich",
    "describe-input": None,
    "conformance": None,
    "deep-license": "scancode",
    "source-file-tree": None,
    "source-snapshot": None,
    "generate-notice": None,
    VULNERABILITY_STEP: None,
    "generate-risk-report": None,
    "model-security": None,
    "verify-weights": None,
    "enrich-aibom": None,
    "sign": "sign",
}

#: Where each step's time is accounted in ``stage_timings``. SBOM generation
#: keeps the ``cdxgen`` slot the progress view already names, licence
#: detection keeps ``scancode`` and vulnerability matching keeps ``trivy``;
#: everything else the engine does after generation is one ``postprocess``
#: window, so the number of stages a reader sees does not grow with BomLens'.
TIMING_STAGE: Final[dict[str, str]] = {
    "generate": "cdxgen",
    DEEP_LICENSE_STEP: "scancode",
    VULNERABILITY_STEP: "trivy",
}
POSTPROCESS_TIMING_STAGE: Final = "postprocess"

#: How bad a step's status is, for picking one entry when several BomLens
#: steps fold into the same TRUSCA stage (the six ``enrich-*`` steps).
_SEVERITY: Final = {"failed": 3, "degraded": 2, "skipped": 1}


@dataclass(frozen=True)
class StageOutcome:
    """A TRUSCA degradation derived from one BomLens step."""

    stage: str
    reason: str
    step: str
    status: str
    #: The engine's own reason, kept when it is not one TRUSCA knows.
    engine_reason: str | None = None


def _reason_known(step: Step) -> bool:
    return step.reason is None or step.reason in STEP_REASONS


def is_degradation(step: Step) -> bool:
    """Whether ``step`` finished worse than a normal run (module rules)."""
    if step.status in ("degraded", "failed"):
        return True
    if step.status == "disabled":
        return False
    if not _reason_known(step):
        # A reason from a later contract minor: never read as a clean step.
        return True
    return step.status == "skipped" and step.reason != "no_input"


def _trusca_reason(step: Step) -> str:
    reason = step.reason
    if reason in scan_outcome.STAGE_REASONS:
        return str(reason)
    if reason is not None:
        log.warning("bomlens_step_reason_unknown", step=clip(step.id), reason=clip(reason))
    return "failed"


def stage_outcomes(result: EngineResult) -> list[StageOutcome]:
    """The degradations to record, at most one per TRUSCA stage.

    When several steps fold into one stage, the worst status wins and the
    first step recorded wins a tie, the same rule the engine uses when one step
    id is recorded twice.
    """
    chosen: dict[str, tuple[int, StageOutcome]] = {}
    for step in result.steps:
        if step.id not in STEP_STAGE:
            log.warning("bomlens_step_unknown", step=clip(step.id), status=step.status)
            continue
        stage = STEP_STAGE[step.id]
        if stage is None or not is_degradation(step):
            continue
        severity = _SEVERITY.get(step.status, 0)
        current = chosen.get(stage)
        if current is None or severity > current[0]:
            chosen[stage] = (
                severity,
                StageOutcome(
                    stage=stage,
                    reason=_trusca_reason(step),
                    step=step.id,
                    status=step.status,
                    engine_reason=(
                        step.reason
                        if step.reason is not None and step.reason not in scan_outcome.STAGE_REASONS
                        else None
                    ),
                ),
            )
    return [entry for _, entry in chosen.values()]


def stage_windows(result: EngineResult) -> dict[str, tuple[datetime, datetime]]:
    """``{timing stage: (started, ended)}`` for the steps that actually ran.

    A step that was turned off or skipped did no work and gets no window, so it
    cannot pull a stage's average toward zero.
    """
    windows: dict[str, tuple[datetime, datetime]] = {}
    for step in result.steps:
        if step.status in ("disabled", "skipped"):
            continue
        stage = TIMING_STAGE.get(step.id, POSTPROCESS_TIMING_STAGE)
        started, ended = step.started_at, max(step.ended_at, step.started_at)
        if stage in windows:
            first, last = windows[stage]
            windows[stage] = (min(first, started), max(last, ended))
        else:
            windows[stage] = (started, ended)
    return windows


def vulnerability_failure(result: EngineResult) -> str | None:
    """Why the vulnerability scan cannot be trusted, or ``None`` when it ran.

    ``scan-security`` must be recorded as ``ok`` with no reason. Anything else
    (failed, skipped because Trivy was missing, turned off, or not recorded at
    all) means the run has no vulnerability result, and "no vulnerabilities"
    must not be persisted in its place.

    Every entry counts: a manifest that lists the step twice passes only when
    both are clean. And ``ok`` with a reason attached is not clean; the step is
    not degradable, so a qualified ok is read as a failure rather than guessed
    at.
    """
    entries = [s for s in result.steps if s.id == VULNERABILITY_STEP]
    if not entries:
        return "the vulnerability scan was not recorded"
    for step in entries:
        if step.status == "ok" and step.reason is None:
            continue
        reason = f" ({clip(step.reason)})" if step.reason else ""
        return f"the vulnerability scan {step.status}{reason}"
    return None


__all__ = [
    "DEEP_LICENSE_STEP",
    "POSTPROCESS_TIMING_STAGE",
    "STEP_STAGE",
    "TIMING_STAGE",
    "VULNERABILITY_STEP",
    "StageOutcome",
    "is_degradation",
    "stage_outcomes",
    "stage_windows",
    "vulnerability_failure",
]
