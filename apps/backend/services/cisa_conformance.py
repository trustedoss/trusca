# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""2026 SBOM minimum elements — advisory checks for every CycloneDX document.

The 2026 Minimum Elements for a Software Bill of Materials (v2.1, 2026-07-29),
published by CISA, the NSA and the FBI with fifteen international partners,
replace the NTIA minimum elements of 2021 rather than amending them. Seventeen
data fields and six practices, and unlike the G7 AI baseline they apply to all
software — so this registry states no condition and is measured on every
CycloneDX SBOM.

Registry and check semantics vendored from BomLens (sktelecom/bomlens,
Apache-2.0, Copyright 2026 SK Telecom Co., Ltd. — see THIRD_PARTY_NOTICES.md),
``docker/lib/cisa-registry.json``. As with the G7 port, the registry's jq is
kept verbatim in each predicate's docstring so a refresh can be diffed against
the port, and ``cisa_registry.json`` is the single source of truth for element
metadata.

Two things differ from the G7 baseline and are worth stating plainly.

WHAT IS MEASURED is every component: the target component (``metadata.component``
— the thing the SBOM is about) plus every enumerated component. The guidance
describes its data fields over both, and measuring only the second lets an
unnamed, unidentified root pass without mention.

WHAT COUNTS AS IDENTIFYING a component is wider here than under TRUSCA's own
submission criteria (``sbom_conformance``): a PURL, a CPE, a SWHID, or an
intrinsic identifier such as a hash. A firmware file entry carrying only a hash
is identified under this baseline while the submission criteria still ask for a
PURL, and the report says both rather than only the stricter one.

Every element is advisory and none moves the pass/fail verdict, which stays
with the submission criteria. See the registry ``note`` for why promoting them
would tell a reader nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services.registry_conformance import RegistrySpec, evaluate_registry
from services.sbom_conformance import Check

_REGISTRY_PATH = Path(__file__).resolve().parent / "cisa_registry.json"

_SUBJECT_LABEL = "component"
_DETAIL_NO_SUBJECTS = "no components"

#: Hash algorithm names a recipient can actually recompute with. The element
#: asks for the algorithm beside the digest, and a digest labelled with nothing
#: — or with something no reader can act on — cannot be recomputed, which is
#: the whole point of recording it.
_KNOWN_HASH_ALG = re.compile(
    r"^(md5|sha-?1|sha-?(224|256|384|512)|sha3-(256|384|512)"
    r"|blake2b-(256|384|512)|blake3)$",
    re.IGNORECASE,
)

#: Property-name suffixes, matched without their namespace on purpose. TRUSCA
#: writes ``trusca:*`` on the SBOMs it exports; a document produced by the
#: sibling upstream tool carries ``bomlens:*``. Both state the same fact, and a
#: baseline that only recognised our own spelling would score a supplier's
#: honest declaration as a gap.
_EVIDENCE_GRADE_SUFFIX = "evidenceGrade"
_UNDECLARED_FIELDS_SUFFIX = "undeclared-fields"


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


def subjects(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """jq: ``[.metadata.component // empty] + [.components[]?]``

    The target component first, then the enumerated ones. A document with no
    components at all yields an empty list and every per-subject element warns
    with the registry's empty-subject wording.
    """
    out: list[dict[str, Any]] = []
    target = _metadata(doc).get("component")
    if isinstance(target, dict):
        out.append(target)
    out.extend(c for c in _list(doc.get("components")) if isinstance(c, dict))
    return out


def _tool_entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """jq: the array form, or ``tools.components + tools.services``."""
    tools = _metadata(doc).get("tools")
    if isinstance(tools, list):
        return [t for t in tools if isinstance(t, dict)]
    as_dict = _dict(tools)
    entries = _list(as_dict.get("components")) + _list(as_dict.get("services"))
    return [t for t in entries if isinstance(t, dict)]


def _properties(component: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in _list(component.get("properties")) if isinstance(p, dict)]


def _name_of(component: dict[str, Any]) -> str:
    return _str(component.get("name")) or "(unnamed)"


# ---------------------------------------------------------------------------
# Document-level predicates (registry ``cdxPath``).
# ---------------------------------------------------------------------------
def _p_sbom_author(doc: dict[str, Any]) -> bool:
    """jq: ``((.metadata.authors // []) | length > 0) or
    ((.metadata.supplier // null) != null) or
    ((.metadata.manufacturer // null) != null)``"""
    meta = _metadata(doc)
    return bool(
        _list(meta.get("authors"))
        or meta.get("supplier") is not None
        or meta.get("manufacturer") is not None
    )


def _p_sbom_author_signature(doc: dict[str, Any]) -> bool:
    """jq: ``(.signature // null) != null``

    An enveloped signature only. TRUSCA signs detached (cosign, a file beside
    the SBOM), which this element cannot see — the guidance file carries the
    review note that says so, so a reader does not take "not present" to mean
    the supplier did not sign.
    """
    return doc.get("signature") is not None


def _p_sbom_format_name(doc: dict[str, Any]) -> bool:
    """jq: ``(.bomFormat // "") != ""``"""
    return bool(_str(doc.get("bomFormat")))


def _p_sbom_format_version(doc: dict[str, Any]) -> bool:
    """jq: ``(.specVersion // "") != ""``"""
    return bool(_str(doc.get("specVersion")))


def _p_sbom_generation_context(doc: dict[str, Any]) -> bool:
    """jq: ``[ (.metadata.lifecycles // [])[] |
    select(((.phase // "") != "") or ((.name // "") != "")) ] | length > 0``"""
    return any(
        _str(entry.get("phase")) or _str(entry.get("name"))
        for entry in _list(_metadata(doc).get("lifecycles"))
        if isinstance(entry, dict)
    )


def _p_sbom_timestamp(doc: dict[str, Any]) -> bool:
    """jq: ``(.metadata.timestamp // "") != ""``"""
    return bool(_str(_metadata(doc).get("timestamp")))


def _p_sbom_tool_name(doc: dict[str, Any]) -> bool:
    """jq: ``[ <tools> | select((.name // "") != "") ] | length > 0``"""
    return any(_str(tool.get("name")) for tool in _tool_entries(doc))


def _p_sbom_tool_version(doc: dict[str, Any]) -> bool:
    """jq: ``<tools> as $t | ($t | length > 0) and
    ([ $t[] | select((.version // "") == "") ] | length == 0)``

    Every recorded tool must carry a version, not just one of them: a document
    naming three tools and versioning one has not answered the element.
    """
    tools = _tool_entries(doc)
    return bool(tools) and all(_str(tool.get("version")) for tool in tools)


def _p_sbom_version(doc: dict[str, Any]) -> bool:
    """jq: ``(.version // null) != null``"""
    return doc.get("version") is not None


def _p_dependency_relationship(doc: dict[str, Any]) -> bool:
    """jq: ``[ .dependencies[]? | .dependsOn[]? ] | length > 0``"""
    return any(
        _list(entry.get("dependsOn"))
        for entry in _list(doc.get("dependencies"))
        if isinstance(entry, dict)
    )


def _p_machine_processable(doc: dict[str, Any]) -> bool:
    """jq: ``((.bomFormat // "") != "") or ((.spdxVersion // "") != "")``"""
    return bool(_str(doc.get("bomFormat")) or _str(doc.get("spdxVersion")))


def _p_explicit_unknowns(doc: dict[str, Any]) -> bool:
    """jq: ``[ (.metadata.properties // [])[] |
    select(((.name // "") | endswith("undeclared-fields")) and
           ((.value // "") != "")) ] | length > 0``

    The guidance asks an author to distinguish a value it could not establish
    from one it is withholding. A scan only ever produces the first kind, so
    the statement is made once for the whole document rather than repeated on
    every empty field — the claim is identical for all of them, and on a large
    SBOM the per-field form would add thousands of properties saying the same
    thing.
    """
    return any(
        _str(prop.get("name")).endswith(_UNDECLARED_FIELDS_SUFFIX)
        and _str(prop.get("value"))
        for prop in _list(_metadata(doc).get("properties"))
        if isinstance(prop, dict)
    )


# ---------------------------------------------------------------------------
# Per-subject coverage (registry ``missingPath``) — each returns the names of
# subjects MISSING the element.
# ---------------------------------------------------------------------------
def _m_component_name(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select((.name // "") == "") |
    (.purl // "(unnamed)") ]``"""
    return [
        _str(c.get("purl")) or "(unnamed)" for c in items if not _str(c.get("name"))
    ]


def _m_component_version(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select((.version // "") == "") |
    select([ (.properties // [])[] |
             select((.name // "") | endswith("evidenceGrade")) ] | length == 0) |
    (.name // "(unnamed)") ]``

    A component explicitly marked as having no established version is not
    counted as missing the field: that marking IS the statement the guidance
    asks for, and listing it here would report the same fact twice.
    """
    out: list[str] = []
    for component in items:
        if _str(component.get("version")):
            continue
        if any(
            _str(prop.get("name")).endswith(_EVIDENCE_GRADE_SUFFIX)
            for prop in _properties(component)
        ):
            continue
        out.append(_name_of(component))
    return out


def _m_component_producer(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select(((.authors // []) | length == 0) and
    ((.publisher // "") == "") and ((.supplier // {}) | length == 0) and
    ((.manufacturer // {}) | length == 0)) | (.name // "(unnamed)") ]``"""
    return [
        _name_of(c)
        for c in items
        if not _list(c.get("authors"))
        and not _str(c.get("publisher"))
        and not _dict(c.get("supplier"))
        and not _dict(c.get("manufacturer"))
    ]


def _m_component_identifiers(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select(((.purl // "") == "") and
    ((.cpe // "") == "") and ((.hashes // []) | length == 0) and
    ((.swhid // "") == "")) | (.name // "(unnamed)") ]``

    Wider than the submission criteria's PURL requirement on purpose — see the
    module docstring.
    """
    return [
        _name_of(c)
        for c in items
        if not _str(c.get("purl"))
        and not _str(c.get("cpe"))
        and not _list(c.get("hashes"))
        and not _str(c.get("swhid"))
    ]


def _m_component_license(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select((.licenses // []) | length == 0) |
    (.name // "(unnamed)") ]``"""
    return [_name_of(c) for c in items if not _list(c.get("licenses"))]


def _m_component_hash_value(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select([ (.hashes // [])[] |
    select((.content // "") != "") ] | length == 0) | (.name // "(unnamed)") ]``"""
    return [
        _name_of(c)
        for c in items
        if not any(
            _str(h.get("content")) for h in _list(c.get("hashes")) if isinstance(h, dict)
        )
    ]


def _m_component_hash_algorithm(items: list[dict[str, Any]]) -> list[str]:
    """jq: ``[ $subjects[] | select((.hashes // []) | length > 0) |
    select(<count of recognised alg> != <count of hashes>) |
    (.name // "(unnamed)") ]``

    Only components that carry a hash are asked the question, and every one of
    their digests must name an algorithm a reader can recompute with.
    """
    out: list[str] = []
    for component in items:
        hashes = [h for h in _list(component.get("hashes")) if isinstance(h, dict)]
        if not hashes:
            continue
        if not all(_KNOWN_HASH_ALG.match(_str(h.get("alg"))) for h in hashes):
            out.append(_name_of(component))
    return out


_PREDICATES = {
    "cisa-sbom-author": _p_sbom_author,
    "cisa-sbom-author-signature": _p_sbom_author_signature,
    "cisa-sbom-format-name": _p_sbom_format_name,
    "cisa-sbom-format-version": _p_sbom_format_version,
    "cisa-sbom-generation-context": _p_sbom_generation_context,
    "cisa-sbom-timestamp": _p_sbom_timestamp,
    "cisa-sbom-tool-name": _p_sbom_tool_name,
    "cisa-sbom-tool-version": _p_sbom_tool_version,
    "cisa-sbom-version": _p_sbom_version,
    "cisa-component-dependency-relationship": _p_dependency_relationship,
    "cisa-machine-processable-data": _p_machine_processable,
    "cisa-explicit-unknowns": _p_explicit_unknowns,
}

_MISSING = {
    "cisa-component-name": _m_component_name,
    "cisa-component-version": _m_component_version,
    "cisa-component-producer": _m_component_producer,
    "cisa-component-identifiers": _m_component_identifiers,
    "cisa-component-license": _m_component_license,
    "cisa-component-hash-value": _m_component_hash_value,
    "cisa-component-hash-algorithm": _m_component_hash_algorithm,
}


#: The 2026 baseline states no condition — it applies to all software, so it
#: leaves ``applies_when`` at its default and is measured on every CycloneDX
#: document.
CISA_SPEC = RegistrySpec(
    name="cisa-2026",
    registry_path=_REGISTRY_PATH,
    predicates=_PREDICATES,
    missing=_MISSING,
    subject=subjects,
    subject_label=_SUBJECT_LABEL,
    empty_subject_detail=_DETAIL_NO_SUBJECTS,
    default_required=False,
)


def evaluate_cisa(doc: dict[str, Any]) -> list[Check]:
    """Evaluate the 23 elements of the 2026 baseline against ``doc``."""
    return evaluate_registry(doc, CISA_SPEC)
