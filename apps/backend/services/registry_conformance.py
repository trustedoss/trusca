# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Registry-neutral evaluator for declarative conformance baselines.

A baseline is a registry of elements (clusters of named data fields) plus the
predicates that decide whether an SBOM satisfies each one. ``g7_conformance``
was the first, and its evaluator had four of its own properties written into
the code rather than declared:

  * whether the baseline applied at all was decided by the CALLER, which
    tested the document for a machine-learning-model component;
  * what was measured was fixed to those model components;
  * the wording said "model component(s)" and "no machine-learning-model
    components";
  * every element was advisory, as a literal.

A second baseline cannot be expressed while those four live in the program, so
they move here as :class:`RegistrySpec` fields, each defaulting to what the G7
evaluator did. Upstream (sktelecom/bomlens#639) made the same move by putting
the four in the registry JSON, which works there because its evaluator runs the
registry's jq expressions directly. TRUSCA ports each expression to a Python
predicate instead — the registry JSON is our single source of truth for element
METADATA, not for evaluation — so the declaration lives in a spec object beside
the predicate tables rather than in the vendored JSON. The vendored files stay
byte-comparable to upstream on the parts that are shared.

Adding a baseline is therefore: a registry JSON, a predicate table, and a spec.
No change here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from services.sbom_conformance import Check, sanitize_jsonb_text

log = structlog.get_logger("services.registry_conformance")

# Detail strings — BomLens validate-sbom.sh g7_ai_checks() wording, verbatim.
# The empty-subject wording is NOT here: it names what a baseline measures, so
# each one states its own (RegistrySpec.empty_subject_detail).
DETAIL_PRESENT = "present"
DETAIL_ABSENT = "not present in the SBOM"
DETAIL_REVIEW = "requires human review (no automated source)"
DETAIL_ERROR = "evaluation error"

# Evidence clamp — an adversarial SBOM must not balloon the persisted verdict
# (checks live in a JSONB column): at most 8 items, each cut to 200 chars.
# ``Check.missing`` reuses the same caps (BomLens caps at MISSING_CAP=50 with
# no per-item truncation; we clamp tighter — same JSONB posture as evidence).
EVIDENCE_MAX_ITEMS = 8
EVIDENCE_MAX_CHARS = 200


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class RegistrySpec:
    """Everything a baseline declares about itself.

    ``subject`` and ``subject_label`` travel together: the first picks the
    components a per-subject element is measured over, the second names them in
    the detail line a reader sees. ``applies_when`` decides whether the
    baseline is evaluated at all — a baseline that measures every SBOM says so
    by leaving the default, rather than by the caller knowing not to gate it.
    """

    name: str
    registry_path: Path
    predicates: Mapping[str, Callable[[dict[str, Any]], bool | None]]
    missing: Mapping[str, Callable[[list[dict[str, Any]]], list[str]]] = field(
        default_factory=dict
    )
    evidence: Mapping[str, Callable[[dict[str, Any]], list[str]]] = field(
        default_factory=dict
    )
    #: Components a per-subject element is measured over. The default measures
    #: nothing, which is right for a baseline with no per-subject elements.
    subject: Callable[[dict[str, Any]], list[dict[str, Any]]] = lambda _doc: []
    subject_label: str = "component"
    empty_subject_detail: str = "no components"
    #: Whether this baseline applies to the document at all. Default: always.
    applies_when: Callable[[dict[str, Any]], bool] = lambda _doc: True
    #: Element-level ``required`` default. Every baseline shipped so far is
    #: advisory in full; an element may override with its own ``required`` key
    #: once a baseline arrives that draws the line.
    default_required: bool = False


@lru_cache(maxsize=8)
def load_registry(path: Path) -> dict[str, Any]:
    """Parse and cache a registry JSON. Static repo asset — caching is safe."""
    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):  # pragma: no cover — vendored file
        raise ValueError(f"{path.name} must be a JSON object")
    return loaded


def iter_elements(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """All elements of a registry as ``(cluster_id, element)``, document order."""
    out: list[tuple[str, dict[str, Any]]] = []
    for cluster in _as_list(load_registry(path).get("clusters")):
        if not isinstance(cluster, dict):
            continue
        cluster_id = _as_str(cluster.get("id"))
        for element in _as_list(cluster.get("elements")):
            if isinstance(element, dict):
                out.append((cluster_id, element))
    return out


def clamp_evidence(values: list[str]) -> list[str]:
    """jq ``unique`` (sorted, de-duplicated) + adversarial clamps.

    Each item is truncated to ``EVIDENCE_MAX_CHARS`` BEFORE the dedupe/sort set
    is built (a flood of huge values never materialises in memory), then passed
    through :func:`~services.sbom_conformance.sanitize_jsonb_text` — evidence is
    verbatim SBOM content, and an embedded NUL would abort the verdict's JSONB
    persist with a Postgres ``DataError``. Finally capped at
    ``EVIDENCE_MAX_ITEMS`` items. ``Check.as_dict`` re-sanitises defensively,
    but cleaning here keeps the in-memory ``Check.evidence`` safe for any
    consumer.
    """
    prepared: set[str] = set()
    for value in values:
        if not value:
            continue
        cleaned = sanitize_jsonb_text(value[:EVIDENCE_MAX_CHARS])
        if cleaned:
            prepared.add(cleaned)
    return sorted(prepared)[:EVIDENCE_MAX_ITEMS]


def clamp_missing(names: list[str]) -> list[str]:
    """Clamp a missingPath offender list for the verdict row.

    BomLens fold: ``missing:(._missing[0:$cap])`` — document order, no dedupe
    (MISSING_CAP=50, no per-item truncation). The port keeps the order but
    clamps tighter, reusing the evidence caps (8 items × 200 chars), because
    ``Check.missing`` persists into the same JSONB column and subject names are
    verbatim SBOM content. NUL / control-char cleaning is deliberately NOT done
    here — ``Check.as_dict`` (the JSONB persist boundary) already runs every
    ``missing[]`` item through ``sanitize_jsonb_text``.
    """
    return [n[:EVIDENCE_MAX_CHARS] for n in names if n][:EVIDENCE_MAX_ITEMS]


def applies(doc: dict[str, Any], spec: RegistrySpec) -> bool:
    """Whether ``spec`` should be evaluated against ``doc``.

    Only a clean False means the baseline does not apply. If the test itself
    blows up, the baseline is evaluated anyway — a broken applies_when that
    silently dropped a whole baseline would leave a document scored against
    fewer elements than a reader believes, with nothing on the screen saying
    so. Evaluating lets the failure land where someone will see it, and every
    element is individually defended below.
    """
    try:
        return bool(spec.applies_when(doc))
    except Exception:
        log.warning("conformance_applies_error", registry=spec.name, exc_info=True)
        return True


def _extract_evidence(
    element_id: str, doc: dict[str, Any], spec: RegistrySpec
) -> list[str] | None:
    """Run the element's evidence extractor (if any), clamped; never raises."""
    extractor = spec.evidence.get(element_id)
    if extractor is None:
        return None
    try:
        return clamp_evidence(extractor(doc))
    except Exception:
        log.warning(
            "conformance_evidence_error",
            registry=spec.name,
            element_id=element_id,
            exc_info=True,
        )
        return None


def evaluate_registry(doc: dict[str, Any], spec: RegistrySpec) -> list[Check]:
    """Evaluate every element of ``spec`` against ``doc`` (parsed CycloneDX).

    Returns one :class:`~services.sbom_conformance.Check` per registry element,
    in registry order, tagged with its cluster / source / role. Never raises on
    an adversarial document.

    Evidence is extracted on pass only — a deliberate divergence from the
    BomLens fold (which computes ``_ev`` unconditionally): a warn row's partial
    evidence would change the persisted shape of existing rows for no consumer,
    and the offender list (``missing``) already tells the story.
    """
    checks: list[Check] = []
    # Subjects — bound once per run, like the BomLens jq program binds $models.
    subjects = spec.subject(doc)
    for cluster_id, element in iter_elements(spec.registry_path):
        element_id = _as_str(element.get("id"))
        label = _as_str(element.get("label"))
        source = _as_str(element.get("source")) or None
        role = _as_str(element.get("role")) or None
        required = bool(element.get("required", spec.default_required))

        missing_fn = spec.missing.get(element_id)
        predicate = spec.predicates.get(element_id)
        evidence: list[str] | None = None
        missing: list[str] = []
        if missing_fn is not None:
            # missingPath — per-subject coverage.
            total = len(subjects)
            try:
                absent = missing_fn(subjects)
            except Exception:
                # missingPath ports are written to never raise; this is the
                # last-line defence against a hostile shape a guard missed.
                log.warning(
                    "conformance_missing_error",
                    registry=spec.name,
                    element_id=element_id,
                    exc_info=True,
                )
                status, detail = "warn", DETAIL_ERROR
            else:
                if total == 0:
                    status, detail = "warn", spec.empty_subject_detail
                elif not absent:
                    # BomLens fold wording: "\($t)/\($t) <subject>(s)".
                    status = "pass"
                    detail = f"{total}/{total} {spec.subject_label}(s)"
                    evidence = _extract_evidence(element_id, doc, spec)
                else:
                    status = "warn"
                    detail = (
                        f"{total - len(absent)}/{total} {spec.subject_label}(s)"
                    )
                    missing = clamp_missing(absent)
        elif predicate is None:
            # cdxPath AND missingPath null (source == "na") — no automated
            # source.
            status, detail = "warn", DETAIL_REVIEW
        else:
            try:
                satisfied = bool(predicate(doc))
            except Exception:
                # Predicates are written to never raise; this is the last-line
                # defence against a hostile SBOM shape a guard missed.
                log.warning(
                    "conformance_predicate_error",
                    registry=spec.name,
                    element_id=element_id,
                    exc_info=True,
                )
                status, detail = "warn", DETAIL_ERROR
            else:
                if satisfied:
                    status, detail = "pass", DETAIL_PRESENT
                    evidence = _extract_evidence(element_id, doc, spec)
                else:
                    status, detail = "warn", DETAIL_ABSENT

        checks.append(
            Check(
                id=element_id,
                label=label,
                required=required,
                status=status,
                detail=detail,
                missing=missing,
                cluster=cluster_id,
                source=source,
                role=role,
                evidence=evidence,
            )
        )
    return checks
