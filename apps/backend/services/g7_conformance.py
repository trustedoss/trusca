# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
G7 AI SBOM minimum-elements conformance — advisory checks for ML-BOM ingests.

Registry and semantics vendored from BomLens (sktelecom/bomlens, Apache-2.0,
Copyright 2026 SK Telecom Co., Ltd. — see THIRD_PARTY_NOTICES.md) from
``docker/lib/g7-registry.json`` **v2**
(sbom-tools#306) + ``validate-sbom.sh``'s ``g7_ai_checks()`` — faithful
Python port.

The vendored ``g7_registry.json`` (same directory) is the SINGLE SOURCE OF
TRUTH for element *metadata*: id, label, cluster, source, role. Its jq
expressions are NOT executed — each is hand-ported to Python (the original jq
is kept verbatim in every port's docstring so a registry refresh can be
diffed against the port). v2 defines two element shapes:

- ``cdxPath`` — a boolean presence expression over the whole SBOM, ported to
  ``_PREDICATES``.
- ``missingPath`` — per-model coverage (models cluster): the evaluator binds
  ``$models`` (the machine-learning-model components) once per run and each
  expression returns the names of the models MISSING the element, ported to
  ``_MISSING``. One unlicensed model in a multi-model supplier SBOM now warns
  with the offender listed, where v1's any-model semantics passed.

Elements with both null (``source == "na"``) carry no port and are surfaced
as "requires human review". Elements with an ``evidencePath`` have a matching
extractor in ``_EVIDENCE`` that pulls the actual satisfied values (clamped —
adversarial SBOMs must not flood the verdict row).

Three contract tests (tests/unit/services/test_g7_conformance.py) lock the
port against the registry: cdxPath element ids == ``_PREDICATES`` keys,
missingPath element ids == ``_MISSING`` keys, and evidence element ids ==
``_EVIDENCE`` keys — a registry refresh with a missed port fails the suite
immediately (CLAUDE.md §2 rule 2).

Scoring mirrors BomLens ``validate-sbom.sh`` g7_ai_checks(): predicate True →
pass "present" / False → warn "not present in the SBOM" / no port → warn
"requires human review (no automated source)". missingPath elements score
per-model: no models → warn "no machine-learning-model components", none
missing → pass "{t}/{t} model component(s)", otherwise → warn
"{t-m}/{t} model component(s)" with the offender names in ``Check.missing``.
Every check is advisory (``required=False``) — G7 defines no per-role
required matrix (see the registry ``note``), so these never move the core
conformance verdict (the aggregation in ``sbom_conformance.evaluate`` skips
cluster-tagged checks).

Defensive posture: predicates are pure ``dict.get`` chains with ``isinstance``
guards and must never raise on an adversarial SBOM; if one does anyway, the
evaluator catches it, logs a structlog WARNING, and records the element as
warn "evaluation error". Pure module — no DB / network / env.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import structlog

from services.registry_conformance import (
    RegistrySpec,
    evaluate_registry,
)
from services.registry_conformance import (
    iter_elements as _iter_registry_elements,
)
from services.registry_conformance import (
    load_registry as _load_registry_file,
)
from services.sbom_conformance import Check

log = structlog.get_logger("services.g7_conformance")

_REGISTRY_PATH = Path(__file__).resolve().parent / "g7_registry.json"

# What this baseline measures, and what it calls it. The evaluator is neutral;
# these are G7's own (registry_conformance.RegistrySpec).
_SUBJECT_LABEL = "model component"
_DETAIL_NO_MODELS = "no machine-learning-model components"

# v2 g7-model-openness prose fallback — jq:
# test("open[ _-]?(weight|architecture|data|training)";"i")
_OPENNESS_PROSE_RE = re.compile(
    r"open[ _-]?(weight|architecture|data|training)", re.IGNORECASE
)

# Registry recursion guard for the `..` (recursive descent) port. The ingest
# service already rejects documents nested deeper than 64; this evaluator may
# be handed other documents, so it carries its own ceiling.
_MAX_WALK_DEPTH = 64


# ---------------------------------------------------------------------------
# Registry loading (metadata single source of truth). Module-level cache is a
# static vendored file — not env (CLAUDE.md rule #11 concerns config only).
# ---------------------------------------------------------------------------
def load_registry() -> dict[str, Any]:
    """Parse and cache the vendored G7 registry JSON."""
    return _load_registry_file(_REGISTRY_PATH)


def iter_elements() -> list[tuple[str, dict[str, Any]]]:
    """All registry elements as ``(cluster_id, element)`` in document order."""
    return _iter_registry_elements(_REGISTRY_PATH)


def automated_element_ids() -> frozenset[str]:
    """Element ids the registry marks automatable (``cdxPath`` != null)."""
    return frozenset(
        _str(e.get("id"))
        for _, e in iter_elements()
        if e.get("cdxPath") is not None
    )


def missing_element_ids() -> frozenset[str]:
    """Element ids the registry scores per-model (``missingPath`` != null)."""
    return frozenset(
        _str(e.get("id"))
        for _, e in iter_elements()
        if e.get("missingPath") is not None
    )


def evidence_element_ids() -> frozenset[str]:
    """Element ids that carry an ``evidencePath`` in the registry."""
    return frozenset(
        _str(e.get("id")) for _, e in iter_elements() if e.get("evidencePath")
    )


# ---------------------------------------------------------------------------
# Type-guard helpers — every predicate goes through these so a hostile shape
# (scalar where an object/array is expected) degrades to "absent", never raises.
# ---------------------------------------------------------------------------
def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _metadata(doc: dict[str, Any]) -> dict[str, Any]:
    return _dict(doc.get("metadata"))


def _components(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in _list(doc.get("components")) if isinstance(c, dict)]


def _ml_components(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in _components(doc) if c.get("type") == "machine-learning-model"]


def _data_components(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in _components(doc) if c.get("type") == "data"]


def _tool_entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """jq: ``(.metadata.tools.components? // .metadata.tools // [])`` —
    CycloneDX 1.5+ object form (``tools.components``) or the legacy array.
    v2 (#306) added the ``?`` error suppression so a legacy array ``tools``
    no longer errors the expression into ``catch false``; this port always
    had the array-safe semantics (the ``isinstance`` branch below)."""
    tools = _metadata(doc).get("tools")
    entries: Any
    if isinstance(tools, dict):
        entries = tools.get("components")
    else:
        entries = tools
    return [t for t in _list(entries) if isinstance(t, dict)]


def _walk_objects(node: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Port of jq's ``..`` recursive descent, restricted to objects."""
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_objects(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item, depth + 1)


def _walk_strings(node: Any, depth: int = 0) -> Iterator[str]:
    """Port of jq's ``.. | strings`` — every string *value* anywhere in the
    document (jq's ``..`` emits values, never object keys), depth-guarded
    like :func:`_walk_objects`."""
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item, depth + 1)
    elif isinstance(node, str):
        yield node


# ---------------------------------------------------------------------------
# Predicates — one per ``cdxPath`` element. Each docstring carries the
# original jq ``cdxPath`` verbatim for diffability against a registry refresh.
# ---------------------------------------------------------------------------
def _p_meta_author(doc: dict[str, Any]) -> bool:
    """jq: ((.metadata.authors // []) | length > 0) or
    ((.metadata.supplier // null) != null) or
    ((.metadata.manufacturer // null) != null)"""
    md = _metadata(doc)
    return (
        len(_list(md.get("authors"))) > 0
        or md.get("supplier") is not None
        or md.get("manufacturer") is not None
    )


def _p_meta_version(doc: dict[str, Any]) -> bool:
    """jq: (.version // null) != null"""
    return doc.get("version") is not None


def _p_meta_format_name(doc: dict[str, Any]) -> bool:
    """jq: (.bomFormat // "") != \"\""""
    return _str(doc.get("bomFormat")) != ""


def _p_meta_format_version(doc: dict[str, Any]) -> bool:
    """jq: (.specVersion // "") != \"\""""
    return _str(doc.get("specVersion")) != ""


def _p_meta_signature(doc: dict[str, Any]) -> bool:
    """jq: (.signature // null) != null"""
    return doc.get("signature") is not None


def _p_meta_tool_name(doc: dict[str, Any]) -> bool:
    """jq: [ (.metadata.tools.components? // .metadata.tools // []) |
    if type=="array" then .[] else empty end |
    select((.name // "") != "") ] | length > 0"""
    return any(_str(t.get("name")) != "" for t in _tool_entries(doc))


def _p_meta_tool_version(doc: dict[str, Any]) -> bool:
    """jq: [ (.metadata.tools.components? // .metadata.tools // []) |
    if type=="array" then .[] else empty end |
    select((.version // "") != "") ] | length > 0"""
    return any(_str(t.get("version")) != "" for t in _tool_entries(doc))


def _p_meta_gen_context(doc: dict[str, Any]) -> bool:
    """jq: [ (.metadata.properties // [])[]? |
    select((.name // "") == "bomlens:generationContext") ] | length > 0"""
    return any(
        isinstance(p, dict) and _str(p.get("name")) == "bomlens:generationContext"
        for p in _list(_metadata(doc).get("properties"))
    )


def _p_meta_timestamp(doc: dict[str, Any]) -> bool:
    """jq: (.metadata.timestamp // "") != \"\""""
    return _str(_metadata(doc).get("timestamp")) != ""


def _p_meta_dependency(doc: dict[str, Any]) -> bool:
    """jq: ((.dependencies // []) | length) > 0"""
    return len(_list(doc.get("dependencies"))) > 0


def _p_slp_name(doc: dict[str, Any]) -> bool:
    """jq: ((.metadata.component // {}) |
    (.type != "machine-learning-model") and ((.name // "") != ""))"""
    component = _dict(_metadata(doc).get("component"))
    return (
        component.get("type") != "machine-learning-model"
        and _str(component.get("name")) != ""
    )


def _p_slp_components(doc: dict[str, Any]) -> bool:
    """jq: ((.components // []) | length) > 1"""
    return len(_list(doc.get("components"))) > 1


def _p_slp_producer(doc: dict[str, Any]) -> bool:
    """jq: ((.metadata.component.publisher // "") != "") or
    ((.metadata.supplier // null) != null)"""
    md = _metadata(doc)
    return (
        _str(_dict(md.get("component")).get("publisher")) != ""
        or md.get("supplier") is not None
    )


def _p_slp_version(doc: dict[str, Any]) -> bool:
    """jq: ((.metadata.component.version // "") != "")"""
    return _str(_dict(_metadata(doc).get("component")).get("version")) != ""


def _p_slp_timestamp(doc: dict[str, Any]) -> bool:
    """jq: (.metadata.timestamp // "") != \"\""""
    return _p_meta_timestamp(doc)


def _p_model_openness(doc: dict[str, Any]) -> bool:
    """jq: (([ $models[] | (.properties // [])[]? |
    select((.name // "") | startswith("openness:")) ] | length) > 0) or
    (([ .. | strings |
    select(test("open[ _-]?(weight|architecture|data|training)";"i")) ]
    | length) > 0)

    v2 (#306): openness:* properties (written by enrich-aibom.sh) OR a prose
    declaration anywhere in the SBOM — supplier SBOMs that state openness in
    text still count.
    """
    if any(
        isinstance(p, dict) and _str(p.get("name")).startswith("openness:")
        for c in _ml_components(doc)
        for p in _list(c.get("properties"))
    ):
        return True
    return any(_OPENNESS_PROSE_RE.search(s) for s in _walk_strings(doc))


def _dataset_entry_name(entry: Any) -> str:
    """jq map body: if type=="string" then . elif type=="object" then
    (.name // .ref // .componentData.name // "") else "" end
    (v2 added the ``.ref`` fallback for dataset entries that only carry a
    bom-ref pointer)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return (
            _str(entry.get("name"))
            or _str(entry.get("ref"))
            or _str(_dict(entry.get("componentData")).get("name"))
        )
    return ""


def _p_ds_name(doc: dict[str, Any]) -> bool:
    """jq: (([ .components[]? | select(.type=="data") ]) +
    ([ .. | objects | select(has("datasets")) | .datasets[]? ])) |
    map(if type=="string" then . elif type=="object" then
    (.name // .ref // .componentData.name // "") else "" end) |
    map(select(. != "")) | length > 0"""
    for c in _data_components(doc):
        if _dataset_entry_name(c):
            return True
    for obj in _walk_objects(doc):
        for entry in _list(obj.get("datasets")):
            if _dataset_entry_name(entry):
                return True
    return False


def _p_ds_description(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="data") |
    select((.description // "") != "") ] | length > 0"""
    return any(_str(c.get("description")) != "" for c in _data_components(doc))


def _p_ds_identifier(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="data") |
    select(((.["bom-ref"] // "") != "") or ((.purl // "") != "")) ] | length > 0"""
    return any(
        _str(c.get("bom-ref")) != "" or _str(c.get("purl")) != ""
        for c in _data_components(doc)
    )


def _p_ds_provenance(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="data") |
    select(((.componentData.governance // null) != null) or
    ((.properties // []) | map(.name // "") |
    any(test("provenance|source";"i")))) ] | length > 0"""
    for c in _data_components(doc):
        if _dict(c.get("componentData")).get("governance") is not None:
            return True
        for p in _list(c.get("properties")):
            if isinstance(p, dict) and re.search(
                r"provenance|source", _str(p.get("name")), re.IGNORECASE
            ):
                return True
    return False


def _p_ds_license(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="data") |
    select(((.licenses // []) | length) > 0) ] | length > 0"""
    return any(len(_list(c.get("licenses"))) > 0 for c in _data_components(doc))


def _p_infra_software(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="library" or .type=="application"
    or .type=="framework") ] | length > 0"""
    return any(
        c.get("type") in ("library", "application", "framework")
        for c in _components(doc)
    )


def _p_infra_hardware(doc: dict[str, Any]) -> bool:
    """jq: (([ .components[]? | select(.type=="device" or .type=="hardware") ]
    | length) > 0) or (([ .externalReferences[]? |
    select((.type // "") | test("bom|hardware";"i")) ] | length) > 0)"""
    if any(c.get("type") in ("device", "hardware") for c in _components(doc)):
        return True
    return any(
        isinstance(ref, dict)
        and re.search(r"bom|hardware", _str(ref.get("type")), re.IGNORECASE)
        for ref in _list(doc.get("externalReferences"))
    )


def _p_sec_vulns(doc: dict[str, Any]) -> bool:
    """jq: ((.vulnerabilities // []) | length) > 0"""
    return len(_list(doc.get("vulnerabilities"))) > 0


def _p_kpi_operational(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.modelCard.quantitativeAnalysis.performanceMetrics //
    .modelCard.modelParameters.performanceMetrics // null) != null)) ]
    | length > 0"""
    for c in _ml_components(doc):
        card = _dict(c.get("modelCard"))
        if (
            _dict(card.get("quantitativeAnalysis")).get("performanceMetrics")
            is not None
            or _dict(card.get("modelParameters")).get("performanceMetrics")
            is not None
        ):
            return True
    return False


# Element id → predicate. Key set MUST equal ``automated_element_ids()`` — a
# contract test locks this so a registry refresh cannot silently miss a port.
_PREDICATES: dict[str, Callable[[dict[str, Any]], bool | None]] = {
    "g7-meta-author": _p_meta_author,
    "g7-meta-version": _p_meta_version,
    "g7-meta-format-name": _p_meta_format_name,
    "g7-meta-format-version": _p_meta_format_version,
    "g7-meta-signature": _p_meta_signature,
    "g7-meta-tool-name": _p_meta_tool_name,
    "g7-meta-tool-version": _p_meta_tool_version,
    "g7-meta-gen-context": _p_meta_gen_context,
    "g7-meta-timestamp": _p_meta_timestamp,
    "g7-meta-dependency": _p_meta_dependency,
    "g7-slp-name": _p_slp_name,
    "g7-slp-components": _p_slp_components,
    "g7-slp-producer": _p_slp_producer,
    "g7-slp-version": _p_slp_version,
    "g7-slp-timestamp": _p_slp_timestamp,
    "g7-model-openness": _p_model_openness,
    "g7-ds-name": _p_ds_name,
    "g7-ds-description": _p_ds_description,
    "g7-ds-identifier": _p_ds_identifier,
    "g7-ds-provenance": _p_ds_provenance,
    "g7-ds-license": _p_ds_license,
    "g7-infra-software": _p_infra_software,
    "g7-infra-hardware": _p_infra_hardware,
    "g7-sec-vulns": _p_sec_vulns,
    "g7-kpi-operational": _p_kpi_operational,
}


# ---------------------------------------------------------------------------
# missingPath ports (registry v2, sbom-tools#306) — one per per-model-coverage
# element. Each takes the pre-bound ``$models`` array (the evaluator extracts
# the machine-learning-model components ONCE per run, like the BomLens jq
# program binds ``$models`` once) and returns the names of the models MISSING
# the element, in document order. Each docstring carries the original jq
# ``missingPath`` verbatim for diffability against a registry refresh.
# ---------------------------------------------------------------------------
def _missing_name(component: dict[str, Any]) -> str:
    """jq offender label: ``(.name // "(unnamed)")``.

    Defensive divergence: jq's ``//`` only falls through on null/false, so an
    empty-string or non-string ``name`` would be emitted verbatim; the port
    folds every empty / non-string name into ``"(unnamed)"`` so the missing
    list stays a useful ``list[str]``.
    """
    return _str(component.get("name")) or "(unnamed)"


def _m_model_name(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select((.name // "") == "") | (.name // "(unnamed)") ]"""
    return [_missing_name(c) for c in models if _str(c.get("name")) == ""]


def _m_model_id(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.purl // "") == "") and ((.cpe // "") == ""))
    | (.name // "(unnamed)") ]"""
    return [
        _missing_name(c)
        for c in models
        if _str(c.get("purl")) == "" and _str(c.get("cpe")) == ""
    ]


def _m_model_version(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select((.version // "") == "") | (.name // "(unnamed)") ]"""
    return [_missing_name(c) for c in models if _str(c.get("version")) == ""]


def _m_model_timestamp(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.properties // []) | map(.name // "") |
    any(test("timestamp|created";"i"))) | not) | (.name // "(unnamed)") ]"""
    return [
        _missing_name(c)
        for c in models
        if not any(
            isinstance(p, dict)
            and re.search(r"timestamp|created", _str(p.get("name")), re.IGNORECASE)
            for p in _list(c.get("properties"))
        )
    ]


def _m_model_producer(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.publisher // "") == "") and
    ((.supplier // null) == null) and ((.manufacturer // null) == null)) |
    (.name // "(unnamed)") ]"""
    return [
        _missing_name(c)
        for c in models
        if _str(c.get("publisher")) == ""
        and c.get("supplier") is None
        and c.get("manufacturer") is None
    ]


def _m_model_description(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select((.description // "") == "") |
    (.name // "(unnamed)") ]"""
    return [_missing_name(c) for c in models if _str(c.get("description")) == ""]


def _m_model_hash_value(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.hashes // []) | length) == 0) |
    (.name // "(unnamed)") ]"""
    return [_missing_name(c) for c in models if len(_list(c.get("hashes"))) == 0]


def _m_model_hash_alg(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(([.hashes[]? | select((.alg // "") != "")] |
    length) == 0) | (.name // "(unnamed)") ]"""
    return [
        _missing_name(c)
        for c in models
        if not any(
            isinstance(h, dict) and _str(h.get("alg")) != ""
            for h in _list(c.get("hashes"))
        )
    ]


def _m_model_card(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select((.modelCard.modelParameters // null) == null) |
    (.name // "(unnamed)") ]"""
    return [
        _missing_name(c)
        for c in models
        if _dict(c.get("modelCard")).get("modelParameters") is None
    ]


def _m_model_io(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.modelCard.modelParameters.inputs // null)
    == null) and ((.modelCard.modelParameters.outputs // null) == null)) |
    (.name // "(unnamed)") ]"""
    out: list[str] = []
    for c in models:
        params = _dict(_dict(c.get("modelCard")).get("modelParameters"))
        if params.get("inputs") is None and params.get("outputs") is None:
            out.append(_missing_name(c))
    return out


def _m_model_training(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.modelCard.modelParameters // {}) |
    (has("datasets") or has("modelArchitecture") or
    has("architectureFamily"))) | not) | (.name // "(unnamed)") ]"""
    out: list[str] = []
    for c in models:
        params = _dict(_dict(c.get("modelCard")).get("modelParameters"))
        if not (
            "datasets" in params
            or "modelArchitecture" in params
            or "architectureFamily" in params
        ):
            out.append(_missing_name(c))
    return out


def _m_model_license(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.licenses // []) | length) == 0) |
    (.name // "(unnamed)") ]"""
    return [_missing_name(c) for c in models if len(_list(c.get("licenses"))) == 0]


def _m_model_extref(models: list[dict[str, Any]]) -> list[str]:
    """jq: [ $models[] | select(((.externalReferences // []) | length) == 0) |
    (.name // "(unnamed)") ]"""
    return [
        _missing_name(c)
        for c in models
        if len(_list(c.get("externalReferences"))) == 0
    ]


# Element id → missingPath port. Key set MUST equal ``missing_element_ids()``
# (contract test) — a registry refresh cannot silently miss a port. The
# returned names are verbatim SBOM content: they go into ``Check.missing``,
# and ``Check.as_dict`` (the JSONB persist boundary) already passes every
# ``missing[]`` item through ``sanitize_jsonb_text`` (Phase A F-1), so no
# NUL / control-char cleaning is needed here — only the size clamp
# (:func:`_clamp_missing`).
_MISSING: dict[str, Callable[[list[dict[str, Any]]], list[str]]] = {
    "g7-model-name": _m_model_name,
    "g7-model-id": _m_model_id,
    "g7-model-version": _m_model_version,
    "g7-model-timestamp": _m_model_timestamp,
    "g7-model-producer": _m_model_producer,
    "g7-model-description": _m_model_description,
    "g7-model-hash-value": _m_model_hash_value,
    "g7-model-hash-alg": _m_model_hash_alg,
    "g7-model-card": _m_model_card,
    "g7-model-io": _m_model_io,
    "g7-model-training": _m_model_training,
    "g7-model-license": _m_model_license,
    "g7-model-extref": _m_model_extref,
}


# ---------------------------------------------------------------------------
# Evidence extractors — one per registry ``evidencePath``. Only invoked when
# the matching predicate is satisfied; output is clamped by ``_clamp_evidence``.
# ---------------------------------------------------------------------------
def _e_model_id(doc: dict[str, Any]) -> list[str]:
    """jq: [ $models[] | (.purl // .cpe) | select(. != null and . != "") ]
    | unique"""
    return [
        _str(c.get("purl")) or _str(c.get("cpe"))
        for c in _ml_components(doc)
        if _str(c.get("purl")) or _str(c.get("cpe"))
    ]


def _e_model_hash_alg(doc: dict[str, Any]) -> list[str]:
    """jq: [ $models[] | .hashes[]? | .alg | select(. != null and . != "") ]
    | unique"""
    return [
        _str(h.get("alg"))
        for c in _ml_components(doc)
        for h in _list(c.get("hashes"))
        if isinstance(h, dict) and _str(h.get("alg"))
    ]


def _e_model_card(doc: dict[str, Any]) -> list[str]:
    """jq: [ $models[] | .modelCard.modelParameters | select(. != null) |
    (.architectureFamily // .modelArchitecture // "documented") ] | unique

    Defensive divergence: in jq a non-object ``modelParameters`` (scalar)
    passes ``select(. != null)`` and then errors the WHOLE evidence expression
    (→ ``catch []``); the port skips just that component.
    """
    values: list[str] = []
    for c in _ml_components(doc):
        params = _dict(c.get("modelCard")).get("modelParameters")
        if params is None or not isinstance(params, dict):
            continue
        values.append(
            _str(params.get("architectureFamily"))
            or _str(params.get("modelArchitecture"))
            or "documented"
        )
    return values


def _e_model_license(doc: dict[str, Any]) -> list[str]:
    """jq: [ $models[] | .licenses[]? |
    (.license.id // .license.name // .expression) |
    select(. != null and . != "") ] | unique"""
    values: list[str] = []
    for c in _ml_components(doc):
        for entry in _list(c.get("licenses")):
            if not isinstance(entry, dict):
                continue
            lic = _dict(entry.get("license"))
            value = (
                _str(lic.get("id"))
                or _str(lic.get("name"))
                or _str(entry.get("expression"))
            )
            if value:
                values.append(value)
    return values


def _e_model_openness(doc: dict[str, Any]) -> list[str]:
    """jq: ([ $models[] | (.properties // [])[]? |
    select((.name // "") | startswith("openness:")) |
    "\\(.name)=\\(.value)" ] + [ .. | strings |
    select(test("open[ _-]?(weight|architecture|data|training)";"i")) ])
    | unique"""
    values = [
        f"{_str(p.get('name'))}={_str(p.get('value'))}"
        for c in _ml_components(doc)
        for p in _list(c.get("properties"))
        if isinstance(p, dict) and _str(p.get("name")).startswith("openness:")
    ]
    values.extend(s for s in _walk_strings(doc) if _OPENNESS_PROSE_RE.search(s))
    return values


def _e_ds_name(doc: dict[str, Any]) -> list[str]:
    """jq: (([ .components[]? | select(.type=="data") ]) + ([ .. | objects |
    select(has("datasets")) | .datasets[]? ])) | map(if type=="string" then .
    elif type=="object" then (.name // .ref // .componentData.name // "")
    else "" end) | map(select(. != "")) | unique"""
    values = [n for c in _data_components(doc) if (n := _dataset_entry_name(c))]
    for obj in _walk_objects(doc):
        for entry in _list(obj.get("datasets")):
            if name := _dataset_entry_name(entry):
                values.append(name)
    return values


# Element id → evidence extractor. Key set MUST equal ``evidence_element_ids()``
# (second contract test).
_EVIDENCE: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "g7-model-id": _e_model_id,
    "g7-model-hash-alg": _e_model_hash_alg,
    "g7-model-card": _e_model_card,
    "g7-model-license": _e_model_license,
    "g7-model-openness": _e_model_openness,
    "g7-ds-name": _e_ds_name,
}


# ---------------------------------------------------------------------------
# Baseline declaration + evaluator entry point.
# ---------------------------------------------------------------------------
#: What the G7 baseline is, stated as data for the neutral evaluator. Before
#: this existed, all four of these lived inside the evaluator: the caller
#: decided that G7 applied when a model component was present, the loop bound
#: the model components itself, the wording was a literal, and every element
#: was advisory by a literal too. A second baseline could not be expressed
#: while that was true.
G7_SPEC = RegistrySpec(
    name="g7",
    registry_path=_REGISTRY_PATH,
    predicates=_PREDICATES,
    missing=_MISSING,
    evidence=_EVIDENCE,
    subject=_ml_components,
    subject_label=_SUBJECT_LABEL,
    empty_subject_detail=_DETAIL_NO_MODELS,
    # G7 describes AI systems, so it is measured on documents that carry a
    # machine-learning-model component and skipped on the rest. Baselines that
    # apply to all software leave this alone.
    applies_when=lambda doc: bool(_ml_components(doc)),
    # The G7 text defines no per-role required matrix (see the registry note),
    # so every element is advisory and none moves the core verdict.
    default_required=False,
)


def evaluate_g7(doc: dict[str, Any]) -> list[Check]:
    """Evaluate every G7 element against ``doc`` (a parsed CycloneDX dict).

    Returns 51 advisory :class:`~services.sbom_conformance.Check` entries in
    registry order, all ``required=False`` and tagged with their cluster /
    source / role. Never raises on an adversarial document.

    The loop itself is :func:`services.registry_conformance.evaluate_registry`;
    what makes it G7 is :data:`G7_SPEC`.
    """
    return evaluate_registry(doc, G7_SPEC)
