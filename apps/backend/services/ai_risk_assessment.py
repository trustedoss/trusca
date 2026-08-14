# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Usage-scenario license verdicts for AI models and datasets, gap #28.

Answers a question neither the policy axis nor the outbound-conflict axis asks:
*this SBOM describes a model; may we use it the way we intend to?* A model
license often binds nothing for internal experimentation, binds obligations
when the model ships inside a product, and binds more again on redistribution.
One license, three answers, and the axis that can tell them apart is the
intended use.

Hand-ported from the license and dataset axes of BomLens's
``docker/lib/assess-ai-risk.sh``, with the terms themselves vendored as data in
``services/ai_risk_knowledge.json`` (see THIRD_PARTY_NOTICES.md). Rules-as-data
for the same reason as ``license_conflict``: every verdict carries the sentence
that justifies it, and that sentence is what a reviewer argues with.

Advisory, never a determination
-------------------------------
No verdict here reaches a build gate or an approval workflow. It is a reading
aid, and the registry's disclaimer travels with it to every screen that prints
one.

Two axes, not four
------------------
Upstream also judges model file security (``bomlens:hf:scan:*``) and dataset tag
signals, both read from properties that BomLens's own collectors stamp. TRUSCA
does not produce those properties, so an axis built on them would be empty for
every SBOM its users actually upload. The registry keeps their data so the axes
can be added later without re-vendoring.

Unknown is not safe
-------------------
A license the registry does not recognise is ``review``, never a guess, and the
worst-of rank puts ``review`` above ``conditional``: not knowing outranks a
known obligation somebody has already read. Only ``caution`` sits higher.

What is stored and what is computed
-----------------------------------
:func:`extract_subjects` records what the document says, which models and
datasets it carries, the license strings on them, which datasets each model
depends on. That is fixed at ingest. :func:`assess` turns those facts into
verdicts against a scenario, and runs on read, because the scenario is a
project setting that changes whenever an operator changes it. A stored verdict
would be stale the moment it did.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import structlog

log = structlog.get_logger("ai.risk")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

OK: Final = "ok"
CONDITIONAL: Final = "conditional"
REVIEW: Final = "review"
CAUTION: Final = "caution"

AI_VERDICT_VALUES: Final[tuple[str, ...]] = (OK, CONDITIONAL, REVIEW, CAUTION)

# Worst-of ordering. ``review`` above ``conditional`` is the load-bearing part:
# a license nobody has classified must never fold away behind one that has been.
AI_VERDICT_RANK: Final[dict[str, int]] = {
    OK: 0,
    CONDITIONAL: 1,
    REVIEW: 2,
    CAUTION: 3,
}

# The intended use of the model, chosen per project. Order is the wire order the
# settings form renders.
USAGE_SCENARIOS: Final[tuple[str, ...]] = (
    "internal",
    "product",
    "redistribute",
    "outputs-only",
)

MODEL_TYPE: Final = "machine-learning-model"
DATA_TYPE: Final = "data"

# A malformed document should not be able to make one JSONB column unbounded.
# Real ML-BOMs carry a handful of models and tens of datasets; this is a
# packaging bound, not a modelling one.
_MAX_SUBJECTS: Final = 200
_MAX_LICENSES_PER_SUBJECT: Final = 20
_MAX_TEXT_LEN: Final = 512

_KNOWLEDGE_PATH: Final[Path] = Path(__file__).with_name("ai_risk_knowledge.json")


# ---------------------------------------------------------------------------
# Registry access
# ---------------------------------------------------------------------------

_KNOWLEDGE_CACHE: dict[str, Any] | None = None


def _knowledge() -> dict[str, Any]:
    """The vendored registry, parsed once, but only a SUCCESSFUL parse is kept.

    Same degradation contract as ``license_conflict._rules``: a missing or
    malformed file yields "no terms", every license falls to ``review``, and the
    failure is logged rather than raised. Caching only success keeps a transient
    read error from becoming a process-lifetime outage.
    """
    global _KNOWLEDGE_CACHE
    if _KNOWLEDGE_CACHE is not None:
        return _KNOWLEDGE_CACHE
    try:
        document: dict[str, Any] = json.loads(_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
        log.error("ai_risk.knowledge_unreadable", error=str(exc))
        return {}
    _KNOWLEDGE_CACHE = document
    return document


def _terms() -> list[dict[str, Any]]:
    terms = _knowledge().get("licenseTerms")
    if not isinstance(terms, list):
        return []
    return [t for t in terms if isinstance(t, dict)]


def disclaimer() -> tuple[str, str]:
    """The registry's (English, Korean) disclaimer. Empty strings if unreadable."""
    block = _knowledge().get("disclaimer")
    if not isinstance(block, dict):
        return ("", "")
    return (str(block.get("en") or ""), str(block.get("ko") or ""))


def condition_labels() -> dict[str, dict[str, str]]:
    """``{condition_id: {"en": ..., "ko": ...}}`` for the conditions terms cite."""
    labels = _knowledge().get("conditionLabels")
    if not isinstance(labels, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in labels.items():
        if isinstance(value, dict):
            out[str(key)] = {
                "en": str(value.get("en") or ""),
                "ko": str(value.get("ko") or ""),
            }
    return out


# ---------------------------------------------------------------------------
# License string matching
# ---------------------------------------------------------------------------

# The registry's match contract: lowercase, then runs of space / dot /
# underscore / slash / dash collapse to one space. Deliberately NOT
# ``license_normalize._SEP_RE``, which also folds commas; that module maps
# free-text names onto SPDX ids, a different contract, and the registry's
# regexes were written against this one.
_SEP_RE: Final = re.compile(r"[ ._/-]+")


def _norm(value: str) -> str:
    return _SEP_RE.sub(" ", value.lower()).strip()


@lru_cache(maxsize=64)
def _compiled(pattern: str) -> re.Pattern[str] | None:
    """Compile a registry regex once. A bad pattern disables that entry only."""
    try:
        return re.compile(pattern)
    except re.error as exc:  # pragma: no cover - data error
        log.error("ai_risk.bad_match_pattern", pattern=pattern, error=str(exc))
        return None


def match_term(license_string: str) -> dict[str, Any] | None:
    """The registry entry for *license_string*, or None if nothing matches.

    Exact ``ids`` first across the whole registry, then the ``match`` regexes in
    file order. Both passes matter: the ids give a spelling-exact answer, and
    file order is why ``cc-by-nc`` is decided before ``cc-by``, ``agpl`` before
    ``gpl``, and the Llama family before the generic community entry.

    No match is not a failure mode to paper over; the caller reads it as
    ``review``.
    """
    normalized = _norm(license_string)
    if not normalized:
        return None
    terms = _terms()
    for term in terms:
        ids = term.get("ids")
        if isinstance(ids, list) and any(_norm(str(i)) == normalized for i in ids):
            return term
    for term in terms:
        pattern = term.get("match")
        if not isinstance(pattern, str) or not pattern:
            continue
        compiled = _compiled(pattern)
        if compiled is not None and compiled.search(normalized):
            return term
    return None


def verdict_for(term: dict[str, Any], scenario: str | None) -> str:
    """The term's verdict, narrowed to *scenario* when one is set.

    Two narrowings, in order:

    1. An explicit ``scenarioVerdicts`` entry wins outright. It exists for cases
       the conditions cannot express, NonCommercial reads ``conditional`` for
       internal use not because fewer conditions bind, but because whether
       in-company use counts as commercial is a judgement call.
    2. Otherwise a ``conditional`` term none of whose conditions bind this
       scenario reads ``ok`` for it. Share-alike obligations that trigger on
       distribution say nothing about a model that never leaves the building.

    With no scenario the full terms apply, which is the conservative reading and
    the default.
    """
    base = str(term.get("verdict") or REVIEW)
    if base not in AI_VERDICT_RANK:
        return REVIEW
    if scenario is None:
        return base
    overrides = term.get("scenarioVerdicts")
    if isinstance(overrides, dict):
        override = overrides.get(scenario)
        if isinstance(override, str) and override in AI_VERDICT_RANK:
            return override
    if base != CONDITIONAL:
        return base
    if not _binding_conditions(term, scenario):
        return OK
    return base


def _binding_conditions(term: dict[str, Any], scenario: str | None) -> tuple[str, ...]:
    """Condition ids that bind *scenario* (all of them when scenario is None)."""
    conditions = term.get("conditions")
    if not isinstance(conditions, list):
        return ()
    out: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        cid = condition.get("id")
        if not isinstance(cid, str) or not cid:
            continue
        applies = condition.get("appliesTo")
        if scenario is None or (isinstance(applies, list) and scenario in applies):
            out.append(cid)
    return tuple(out)


def _worst(verdicts: list[str]) -> str:
    """Worst-of fold. Empty folds to ``review``: nothing judged is not clean."""
    if not verdicts:
        return REVIEW
    return max(verdicts, key=lambda v: AI_VERDICT_RANK.get(v, AI_VERDICT_RANK[REVIEW]))


# ---------------------------------------------------------------------------
# Document facts (written at ingest)
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """Clip and clean an SBOM-supplied string on its way into JSONB.

    The document is untrusted. A NUL byte, a lone surrogate, or a CR in a model
    name would abort the whole conformance persist with a Postgres ``DataError``
    and take the verdict down with it. That is the failure mode
    ``sanitize_jsonb_text`` was written for on the ``checks`` column, reached
    here through a different field.
    """
    if not isinstance(value, str):
        return ""
    from services.sbom_conformance import sanitize_jsonb_text

    return sanitize_jsonb_text(value[:_MAX_TEXT_LEN])


def _license_strings(component: dict[str, Any]) -> list[str]:
    """License strings as the document spells them.

    ``licenses[].license.id``, falling back to ``.license.name`` and then to
    ``.expression``: the three shapes CycloneDX allows, in the order upstream
    reads them. Kept verbatim rather than normalised to an SPDX id: the registry
    matches on names too, and the reason shown to a reviewer should quote what
    the document actually said.
    """
    entries = component.get("licenses")
    if not isinstance(entries, list):
        return []
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("license")
        raw = ""
        if isinstance(inner, dict):
            raw = _text(inner.get("id")) or _text(inner.get("name"))
        if not raw:
            raw = _text(entry.get("expression"))
        if raw:
            out.append(raw)
        if len(out) >= _MAX_LICENSES_PER_SUBJECT:
            break
    return out


def _subject_key(component: dict[str, Any]) -> str:
    return _text(component.get("bom-ref")) or _text(component.get("name"))


def _candidates(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """``metadata.component`` followed by ``components[]``.

    Shared with the G7 evaluator's subject binding
    (``sbom_conformance.document_components``): both axes ask which components
    a document describes, and two answers to that question drift.
    """
    from services.sbom_conformance import document_components

    return document_components(doc)


def _dependency_edges(doc: dict[str, Any]) -> dict[str, list[str]]:
    edges: dict[str, list[str]] = {}
    entries = doc.get("dependencies")
    if not isinstance(entries, list):
        return edges
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ref = _text(entry.get("ref"))
        if not ref:
            continue
        depends = entry.get("dependsOn")
        if not isinstance(depends, list):
            continue
        edges.setdefault(ref, []).extend(
            _text(d) for d in depends if isinstance(d, str) and d
        )
    return edges


def extract_subjects(doc: dict[str, Any]) -> dict[str, Any] | None:
    """What the document says about its models and datasets, or None.

    None means "nothing to assess", no model component anywhere. A dataset-only
    document is not assessed either: the question this axis answers is about
    using a model.

    The result is the JSONB payload persisted alongside the conformance verdict.
    It holds facts only: no verdict, no registry key, nothing that depends on
    the scenario or on the registry version. Both of those can change after
    ingest, and a stored verdict would not notice.
    """
    candidates = _candidates(doc)
    models: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    edges = _dependency_edges(doc)
    truncated = False

    for component in candidates:
        ctype = component.get("type")
        if ctype not in (MODEL_TYPE, DATA_TYPE):
            continue
        if len(models) + len(datasets) >= _MAX_SUBJECTS:
            truncated = True
            break
        key = _subject_key(component)
        record: dict[str, Any] = {
            "bom_ref": key,
            "name": _text(component.get("name")) or "(unnamed)",
            "licenses": _license_strings(component),
        }
        if ctype == MODEL_TYPE:
            record["depends_on"] = edges.get(key, [])
            models.append(record)
        else:
            datasets.append(record)

    if not models:
        return None
    payload: dict[str, Any] = {"models": models, "datasets": datasets}
    if truncated:
        payload["truncated"] = True
    return payload


# ---------------------------------------------------------------------------
# Verdicts (computed on read)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseReason:
    """One declared license and what the registry says about it."""

    license: str
    term_key: str | None
    term_name: str | None
    verdict: str
    summary: str
    summary_ko: str
    conditions: tuple[str, ...]
    source_url: str | None


@dataclass(frozen=True)
class SubjectVerdict:
    """A model or dataset, its verdict, and the reasons behind it."""

    bom_ref: str
    name: str
    verdict: str
    reasons: tuple[LicenseReason, ...]
    # Models only: the datasets this model depends on, folded into its verdict.
    dataset_refs: tuple[str, ...] = ()
    dataset_verdict: str | None = None


@dataclass(frozen=True)
class AiRiskAssessment:
    """Every subject in one document, judged against one scenario."""

    scenario: str | None
    verdict: str
    models: tuple[SubjectVerdict, ...]
    datasets: tuple[SubjectVerdict, ...]


_NO_LICENSE_SUMMARY: Final = (
    "The component declares no license, so there are no terms to read. "
    "Someone has to establish what applies."
)
_NO_LICENSE_SUMMARY_KO: Final = (
    "컴포넌트에 라이선스가 선언되어 있지 않아 읽을 조건이 없습니다. "
    "무엇이 적용되는지 사람이 확인해야 합니다."
)
_UNKNOWN_SUMMARY: Final = (
    "This license is not in the registry. It is not assumed to be permissive."
)
_UNKNOWN_SUMMARY_KO: Final = (
    "레지스트리에 없는 라이선스입니다. 허용적이라고 가정하지 않습니다."
)


def _reasons_for(licenses: list[str], scenario: str | None) -> tuple[LicenseReason, ...]:
    if not licenses:
        return (
            LicenseReason(
                license="",
                term_key=None,
                term_name=None,
                verdict=REVIEW,
                summary=_NO_LICENSE_SUMMARY,
                summary_ko=_NO_LICENSE_SUMMARY_KO,
                conditions=(),
                source_url=None,
            ),
        )
    out: list[LicenseReason] = []
    for declared in licenses:
        term = match_term(declared)
        if term is None:
            out.append(
                LicenseReason(
                    license=declared,
                    term_key=None,
                    term_name=None,
                    verdict=REVIEW,
                    summary=_UNKNOWN_SUMMARY,
                    summary_ko=_UNKNOWN_SUMMARY_KO,
                    conditions=(),
                    source_url=None,
                )
            )
            continue
        out.append(
            LicenseReason(
                license=declared,
                term_key=str(term.get("key") or ""),
                term_name=str(term.get("name") or ""),
                verdict=verdict_for(term, scenario),
                summary=str(term.get("summary") or ""),
                summary_ko=str(term.get("summary_ko") or ""),
                conditions=_binding_conditions(term, scenario),
                source_url=str(term.get("sourceUrl")) if term.get("sourceUrl") else None,
            )
        )
    return tuple(out)


def _judge_subject(record: dict[str, Any], scenario: str | None) -> SubjectVerdict:
    licenses = record.get("licenses")
    reasons = _reasons_for(
        [str(x) for x in licenses] if isinstance(licenses, list) else [], scenario
    )
    return SubjectVerdict(
        bom_ref=str(record.get("bom_ref") or ""),
        name=str(record.get("name") or "(unnamed)"),
        verdict=_worst([r.verdict for r in reasons]),
        reasons=reasons,
    )


def assess(subjects: dict[str, Any] | None, *, scenario: str | None) -> AiRiskAssessment | None:
    """Judge the stored document facts against *scenario*.

    A model's verdict folds its own licenses with the verdicts of the datasets
    it declares a dependency on. Only declared edges count: a model with no
    ``dependsOn`` entries gets no dataset axis rather than a guess at which of
    the document's datasets were its training data. Keying on bom-ref is what
    keeps one model's dataset out of another's verdict in a multi-model
    document.

    Returns None when there is nothing to judge, which the API surfaces as an
    absent assessment rather than a clean one.
    """
    if not subjects:
        return None
    if scenario is not None and scenario not in USAGE_SCENARIOS:
        # An unrecognised value is dropped, not guessed at: judging against the
        # full terms is the conservative reading.
        log.warning("ai_risk.unknown_scenario", scenario=scenario)
        scenario = None

    raw_models = subjects.get("models")
    raw_datasets = subjects.get("datasets")
    model_records = (
        [m for m in raw_models if isinstance(m, dict)] if isinstance(raw_models, list) else []
    )
    dataset_records = (
        [d for d in raw_datasets if isinstance(d, dict)]
        if isinstance(raw_datasets, list)
        else []
    )
    if not model_records:
        return None

    datasets = tuple(_judge_subject(d, scenario) for d in dataset_records)
    by_ref = {d.bom_ref: d for d in datasets if d.bom_ref}

    models: list[SubjectVerdict] = []
    for record in model_records:
        own = _judge_subject(record, scenario)
        depends = record.get("depends_on")
        refs = tuple(
            str(r) for r in depends if isinstance(r, str) and str(r) in by_ref
        ) if isinstance(depends, list) else ()
        dataset_verdict = _worst([by_ref[r].verdict for r in refs]) if refs else None
        folded = (
            _worst([own.verdict, dataset_verdict]) if dataset_verdict is not None else own.verdict
        )
        models.append(
            SubjectVerdict(
                bom_ref=own.bom_ref,
                name=own.name,
                verdict=folded,
                reasons=own.reasons,
                dataset_refs=refs,
                dataset_verdict=dataset_verdict,
            )
        )

    overall = _worst([m.verdict for m in models] + [d.verdict for d in datasets])
    return AiRiskAssessment(
        scenario=scenario,
        verdict=overall,
        models=tuple(models),
        datasets=datasets,
    )


__all__ = [
    "AI_VERDICT_RANK",
    "AI_VERDICT_VALUES",
    "CAUTION",
    "CONDITIONAL",
    "OK",
    "REVIEW",
    "USAGE_SCENARIOS",
    "AiRiskAssessment",
    "LicenseReason",
    "SubjectVerdict",
    "assess",
    "condition_labels",
    "disclaimer",
    "extract_subjects",
    "match_term",
    "verdict_for",
]
