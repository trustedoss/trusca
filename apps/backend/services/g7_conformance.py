"""
G7 AI SBOM minimum-elements conformance — advisory checks for ML-BOM ingests.

Registry and semantics vendored from BomLens (sktelecom/sbom-tools,
Apache-2.0, Copyright SK Telecom) ``docker/lib/g7-registry.json`` +
``validate-sbom.sh``'s ``g7_ai_checks()`` — faithful Python port.

The vendored ``g7_registry.json`` (same directory) is the SINGLE SOURCE OF
TRUTH for element *metadata*: id, label, cluster, source, role. Its ``cdxPath``
jq expressions are NOT executed — each is hand-ported to a Python predicate in
``_PREDICATES`` (the original jq is kept verbatim in every predicate's
docstring so a registry refresh can be diffed against the port). Elements whose
``cdxPath`` is null (``source == "na"``) carry no predicate and are surfaced as
"requires human review". Elements with an ``evidencePath`` have a matching
extractor in ``_EVIDENCE`` that pulls the actual satisfied values (clamped —
adversarial SBOMs must not flood the verdict row).

Two contract tests (tests/unit/services/test_g7_conformance.py) lock the port
against the registry: automated element ids == ``_PREDICATES`` keys and
evidence element ids == ``_EVIDENCE`` keys — a registry refresh with a missed
port fails the suite immediately (CLAUDE.md §2 rule 2).

Scoring mirrors BomLens ``validate-sbom.sh`` g7_ai_checks(): predicate True →
pass "present" / False → warn "not present in the SBOM" / no predicate → warn
"requires human review (no automated source)". Every check is advisory
(``required=False``) — G7 defines no per-role required matrix (see the registry
``note``), so these never move the core conformance verdict (the aggregation in
``sbom_conformance.evaluate`` skips cluster-tagged checks).

Defensive posture: predicates are pure ``dict.get`` chains with ``isinstance``
guards and must never raise on an adversarial SBOM; if one does anyway, the
evaluator catches it, logs a structlog WARNING, and records the element as
warn "evaluation error". Pure module — no DB / network / env.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from services.sbom_conformance import Check, sanitize_jsonb_text

log = structlog.get_logger("services.g7_conformance")

_REGISTRY_PATH = Path(__file__).resolve().parent / "g7_registry.json"

# Detail strings — BomLens validate-sbom.sh g7_ai_checks() wording, verbatim.
_DETAIL_PRESENT = "present"
_DETAIL_ABSENT = "not present in the SBOM"
_DETAIL_REVIEW = "requires human review (no automated source)"
_DETAIL_ERROR = "evaluation error"

# Evidence clamp — an adversarial SBOM must not balloon the persisted verdict
# (checks live in a JSONB column): at most 8 items, each cut to 200 chars.
_EVIDENCE_MAX_ITEMS = 8
_EVIDENCE_MAX_CHARS = 200

# Registry recursion guard for the `..` (recursive descent) port. The ingest
# service already rejects documents nested deeper than 64; this evaluator may
# be handed other documents, so it carries its own ceiling.
_MAX_WALK_DEPTH = 64


# ---------------------------------------------------------------------------
# Registry loading (metadata single source of truth). Module-level cache is a
# static vendored file — not env (CLAUDE.md rule #11 concerns config only).
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    """Parse and cache the vendored G7 registry JSON."""
    with _REGISTRY_PATH.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):  # pragma: no cover — vendored file
        raise ValueError("g7_registry.json must be a JSON object")
    return loaded


def iter_elements() -> list[tuple[str, dict[str, Any]]]:
    """All registry elements as ``(cluster_id, element)`` in document order."""
    out: list[tuple[str, dict[str, Any]]] = []
    for cluster in _list(load_registry().get("clusters")):
        if not isinstance(cluster, dict):
            continue
        cluster_id = _str(cluster.get("id"))
        for element in _list(cluster.get("elements")):
            if isinstance(element, dict):
                out.append((cluster_id, element))
    return out


def automated_element_ids() -> frozenset[str]:
    """Element ids the registry marks automatable (``cdxPath`` != null)."""
    return frozenset(
        _str(e.get("id"))
        for _, e in iter_elements()
        if e.get("cdxPath") is not None
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
    """jq: ``(.metadata.tools.components // .metadata.tools // [])`` —
    CycloneDX 1.5+ object form (``tools.components``) or the legacy array."""
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


# ---------------------------------------------------------------------------
# Predicates — one per automated element. Each docstring carries the original
# jq ``cdxPath`` verbatim for diffability against a registry refresh.
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
    """jq: [ (.metadata.tools.components // .metadata.tools // []) |
    if type=="array" then .[] else empty end |
    select((.name // "") != "") ] | length > 0"""
    return any(_str(t.get("name")) != "" for t in _tool_entries(doc))


def _p_meta_tool_version(doc: dict[str, Any]) -> bool:
    """jq: [ (.metadata.tools.components // .metadata.tools // []) |
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


def _p_model_name(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select((.name // "") != "") ] | length > 0"""
    return any(_str(c.get("name")) != "" for c in _ml_components(doc))


def _p_model_id(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.purl // "") != "") or ((.cpe // "") != "")) ] | length > 0"""
    return any(
        _str(c.get("purl")) != "" or _str(c.get("cpe")) != ""
        for c in _ml_components(doc)
    )


def _p_model_version(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select((.version // "") != "") ] | length > 0"""
    return any(_str(c.get("version")) != "" for c in _ml_components(doc))


def _p_model_timestamp(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    (.properties // [])[]? |
    select((.name // "") | test("timestamp|created";"i")) ] | length > 0"""
    return any(
        isinstance(p, dict)
        and re.search(r"timestamp|created", _str(p.get("name")), re.IGNORECASE)
        for c in _ml_components(doc)
        for p in _list(c.get("properties"))
    )


def _p_model_producer(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.publisher // "") != "") or ((.supplier // null) != null)
    or ((.manufacturer // null) != null)) ] | length > 0"""
    return any(
        _str(c.get("publisher")) != ""
        or c.get("supplier") is not None
        or c.get("manufacturer") is not None
        for c in _ml_components(doc)
    )


def _p_model_description(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select((.description // "") != "") ] | length > 0"""
    return any(_str(c.get("description")) != "" for c in _ml_components(doc))


def _p_model_hash_value(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.hashes // []) | length) > 0) ] | length > 0"""
    return any(len(_list(c.get("hashes"))) > 0 for c in _ml_components(doc))


def _p_model_hash_alg(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    .hashes[]? | select((.alg // "") != "") ] | length > 0"""
    return any(
        isinstance(h, dict) and _str(h.get("alg")) != ""
        for c in _ml_components(doc)
        for h in _list(c.get("hashes"))
    )


def _p_model_card(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select((.modelCard.modelParameters // null) != null) ] | length > 0"""
    return any(
        _dict(c.get("modelCard")).get("modelParameters") is not None
        for c in _ml_components(doc)
    )


def _p_model_io(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.modelCard.modelParameters.inputs // null) != null) or
    ((.modelCard.modelParameters.outputs // null) != null)) ] | length > 0"""
    for c in _ml_components(doc):
        params = _dict(_dict(c.get("modelCard")).get("modelParameters"))
        if params.get("inputs") is not None or params.get("outputs") is not None:
            return True
    return False


def _p_model_training(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    (.modelCard.modelParameters // {}) |
    select(has("datasets") or has("modelArchitecture") or
    has("architectureFamily")) ] | length > 0"""
    for c in _ml_components(doc):
        params = _dict(_dict(c.get("modelCard")).get("modelParameters"))
        if (
            "datasets" in params
            or "modelArchitecture" in params
            or "architectureFamily" in params
        ):
            return True
    return False


def _p_model_license(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.licenses // []) | length) > 0) ] | length > 0"""
    return any(len(_list(c.get("licenses"))) > 0 for c in _ml_components(doc))


def _p_model_openness(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    (.properties // [])[]? |
    select((.name // "") | startswith("openness:")) ] | length > 0"""
    return any(
        isinstance(p, dict) and _str(p.get("name")).startswith("openness:")
        for c in _ml_components(doc)
        for p in _list(c.get("properties"))
    )


def _p_model_extref(doc: dict[str, Any]) -> bool:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    select(((.externalReferences // []) | length) > 0) ] | length > 0"""
    return any(
        len(_list(c.get("externalReferences"))) > 0 for c in _ml_components(doc)
    )


def _dataset_entry_name(entry: Any) -> str:
    """jq map body: if type=="string" then . elif type=="object" then
    (.name // .componentData.name // "") else "" end"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return _str(entry.get("name")) or _str(
            _dict(entry.get("componentData")).get("name")
        )
    return ""


def _p_ds_name(doc: dict[str, Any]) -> bool:
    """jq: (([ .components[]? | select(.type=="data") ]) +
    ([ .. | objects | select(has("datasets")) | .datasets[]? ])) |
    map(if type=="string" then . elif type=="object" then
    (.name // .componentData.name // "") else "" end) |
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
    "g7-model-name": _p_model_name,
    "g7-model-id": _p_model_id,
    "g7-model-version": _p_model_version,
    "g7-model-timestamp": _p_model_timestamp,
    "g7-model-producer": _p_model_producer,
    "g7-model-description": _p_model_description,
    "g7-model-hash-value": _p_model_hash_value,
    "g7-model-hash-alg": _p_model_hash_alg,
    "g7-model-card": _p_model_card,
    "g7-model-io": _p_model_io,
    "g7-model-training": _p_model_training,
    "g7-model-license": _p_model_license,
    "g7-model-openness": _p_model_openness,
    "g7-model-extref": _p_model_extref,
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
# Evidence extractors — one per registry ``evidencePath``. Only invoked when
# the matching predicate is satisfied; output is clamped by ``_clamp_evidence``.
# ---------------------------------------------------------------------------
def _e_model_id(doc: dict[str, Any]) -> list[str]:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    (.purl // .cpe) | select(. != null and . != "") ] | unique"""
    return [
        _str(c.get("purl")) or _str(c.get("cpe"))
        for c in _ml_components(doc)
        if _str(c.get("purl")) or _str(c.get("cpe"))
    ]


def _e_model_hash_alg(doc: dict[str, Any]) -> list[str]:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    .hashes[]? | .alg | select(. != null and . != "") ] | unique"""
    return [
        _str(h.get("alg"))
        for c in _ml_components(doc)
        for h in _list(c.get("hashes"))
        if isinstance(h, dict) and _str(h.get("alg"))
    ]


def _e_model_license(doc: dict[str, Any]) -> list[str]:
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    .licenses[]? | (.license.id // .license.name // .expression) |
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
    """jq: [ .components[]? | select(.type=="machine-learning-model") |
    (.properties // [])[]? | select((.name // "") | startswith("openness:")) |
    "\\(.name)=\\(.value)" ] | unique"""
    return [
        f"{_str(p.get('name'))}={_str(p.get('value'))}"
        for c in _ml_components(doc)
        for p in _list(c.get("properties"))
        if isinstance(p, dict) and _str(p.get("name")).startswith("openness:")
    ]


# Element id → evidence extractor. Key set MUST equal ``evidence_element_ids()``
# (second contract test).
_EVIDENCE: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "g7-model-id": _e_model_id,
    "g7-model-hash-alg": _e_model_hash_alg,
    "g7-model-license": _e_model_license,
    "g7-model-openness": _e_model_openness,
}


def _clamp_evidence(values: list[str]) -> list[str]:
    """jq ``unique`` (sorted, de-duplicated) + adversarial clamps.

    Each item is truncated to ``_EVIDENCE_MAX_CHARS`` BEFORE the dedupe/sort
    set is built (a flood of huge values never materialises in memory), then
    passed through :func:`~services.sbom_conformance.sanitize_jsonb_text` —
    evidence is verbatim SBOM content, and an embedded NUL would abort the
    verdict's JSONB persist with a Postgres ``DataError``. Finally capped at
    ``_EVIDENCE_MAX_ITEMS`` items. ``Check.as_dict`` re-sanitises defensively,
    but cleaning here keeps the in-memory ``Check.evidence`` safe for any
    consumer.
    """
    prepared: set[str] = set()
    for value in values:
        if not value:
            continue
        cleaned = sanitize_jsonb_text(value[:_EVIDENCE_MAX_CHARS])
        if cleaned:
            prepared.add(cleaned)
    return sorted(prepared)[:_EVIDENCE_MAX_ITEMS]


# ---------------------------------------------------------------------------
# Evaluator.
# ---------------------------------------------------------------------------
def evaluate_g7(doc: dict[str, Any]) -> list[Check]:
    """Evaluate every G7 element against ``doc`` (a parsed CycloneDX dict).

    Returns 51 advisory :class:`~services.sbom_conformance.Check` entries in
    registry order, all ``required=False`` and tagged with their cluster /
    source / role. Never raises on an adversarial document.
    """
    checks: list[Check] = []
    for cluster_id, element in iter_elements():
        element_id = _str(element.get("id"))
        label = _str(element.get("label"))
        source = _str(element.get("source")) or None
        role = _str(element.get("role")) or None

        predicate = _PREDICATES.get(element_id)
        evidence: list[str] | None = None
        if predicate is None:
            # cdxPath null (source == "na") — no automated source.
            status, detail = "warn", _DETAIL_REVIEW
        else:
            try:
                satisfied = bool(predicate(doc))
            except Exception:
                # Predicates are written to never raise; this is the last-line
                # defence against a hostile SBOM shape a guard missed.
                log.warning(
                    "g7_predicate_error", element_id=element_id, exc_info=True
                )
                status, detail = "warn", _DETAIL_ERROR
            else:
                if satisfied:
                    status, detail = "pass", _DETAIL_PRESENT
                    extractor = _EVIDENCE.get(element_id)
                    if extractor is not None:
                        try:
                            evidence = _clamp_evidence(extractor(doc))
                        except Exception:
                            log.warning(
                                "g7_evidence_error",
                                element_id=element_id,
                                exc_info=True,
                            )
                            evidence = None
                else:
                    status, detail = "warn", _DETAIL_ABSENT

        checks.append(
            Check(
                id=element_id,
                label=label,
                required=False,
                status=status,
                detail=detail,
                cluster=cluster_id,
                source=source,
                role=role,
                evidence=evidence,
            )
        )
    return checks
