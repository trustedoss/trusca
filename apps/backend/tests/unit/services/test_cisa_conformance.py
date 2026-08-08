# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""2026 SBOM minimum elements — port contracts and per-element behaviour.

The contracts mirror the G7 ones (CLAUDE.md hardening rule 2): the same
vocabulary lives in the registry JSON and in the predicate tables, so a
registry refresh with a missed port has to fail here rather than surface as a
silently unscored element.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import cisa_conformance as cisa
from services import registry_conformance as rc

_REGISTRY = cisa.CISA_SPEC.registry_path


def _elements() -> dict[str, tuple[str, dict[str, Any]]]:
    return {str(e.get("id")): (cid, e) for cid, e in rc.iter_elements(_REGISTRY)}


def _rows(doc: dict[str, Any]) -> dict[str, Any]:
    return {c.id: c for c in cisa.evaluate_cisa(doc)}


# ---------------------------------------------------------------------------
# Registry ↔ port contracts.
# ---------------------------------------------------------------------------
def test_the_baseline_carries_seventeen_fields_and_six_practices() -> None:
    """The counts the guidance states. A registry that drifts from them is
    either mis-ported or tracking a different document."""
    clusters: dict[str, int] = {}
    for cluster_id, _ in rc.iter_elements(_REGISTRY):
        clusters[cluster_id] = clusters.get(cluster_id, 0) + 1
    assert clusters == {
        "cisa-metadata": 9,
        "cisa-component": 8,
        "cisa-practices": 6,
    }
    # 17 data fields (9 SBOM-level + 8 component-level) + 6 practices.
    assert sum(clusters.values()) == 23


def test_every_cdx_path_element_has_a_predicate() -> None:
    declared = {
        eid for eid, (_, e) in _elements().items() if e.get("cdxPath") is not None
    }
    assert declared == set(cisa._PREDICATES)


def test_every_missing_path_element_has_a_port() -> None:
    declared = {
        eid for eid, (_, e) in _elements().items() if e.get("missingPath") is not None
    }
    assert declared == set(cisa._MISSING)


def test_elements_without_an_automated_source_have_no_port() -> None:
    """`source: na` means a person has to establish it — a port would be a lie."""
    for eid, (_, element) in _elements().items():
        if element.get("source") == "na":
            assert eid not in cisa._PREDICATES, eid
            assert eid not in cisa._MISSING, eid
            assert element.get("cdxPath") is None
            assert element.get("missingPath") is None


def test_the_four_organisational_practices_are_human_review() -> None:
    """Five of six practices describe how an organisation operates; the two the
    guidance lets a tool answer are format detection and the unknowns
    statement."""
    rows = _rows({"bomFormat": "CycloneDX"})
    review = {eid for eid, row in rows.items() if row.source == "na"}
    assert review == {
        "cisa-coverage",
        "cisa-accommodation-of-updates",
        "cisa-distribution-and-delivery",
        "cisa-frequency",
    }


def test_every_element_is_advisory() -> None:
    assert not any(row.required for row in _rows({}).values())


def test_every_element_carries_a_korean_label() -> None:
    """A baseline added later must ship label_ko with its elements, or a Korean
    reader gets 23 English rows inside a Korean report."""
    missing = [
        eid for eid, (_, e) in _elements().items() if not str(e.get("label_ko", ""))
    ]
    assert not missing, f"elements without a Korean label: {missing}"


# ---------------------------------------------------------------------------
# Document-level elements.
# ---------------------------------------------------------------------------
def test_generation_context_reads_lifecycles() -> None:
    assert _rows({"metadata": {"lifecycles": [{"phase": "pre-build"}]}})[
        "cisa-sbom-generation-context"
    ].status == "pass"
    assert _rows({"metadata": {}})["cisa-sbom-generation-context"].status == "warn"


def test_tool_version_requires_every_tool_to_carry_one() -> None:
    both = {"metadata": {"tools": [{"name": "a", "version": "1"}, {"name": "b", "version": "2"}]}}
    one = {"metadata": {"tools": [{"name": "a", "version": "1"}, {"name": "b"}]}}
    assert _rows(both)["cisa-sbom-tool-version"].status == "pass"
    assert _rows(one)["cisa-sbom-tool-version"].status == "warn"


def test_tool_entries_accept_both_cyclonedx_shapes() -> None:
    legacy = {"metadata": {"tools": [{"name": "a", "version": "1"}]}}
    modern = {"metadata": {"tools": {"components": [{"name": "a", "version": "1"}]}}}
    for doc in (legacy, modern):
        assert _rows(doc)["cisa-sbom-tool-name"].status == "pass"
        assert _rows(doc)["cisa-sbom-tool-version"].status == "pass"


@pytest.mark.parametrize("suffix", ["trusca:undeclared-fields", "bomlens:undeclared-fields"])
def test_the_unknowns_statement_is_read_whoever_wrote_it(suffix: str) -> None:
    """Our own exports and a supplier document from the sibling tool both state
    this the same way; recognising only our spelling would score an honest
    declaration as a gap."""
    doc = {"metadata": {"properties": [{"name": suffix, "value": "unknown to the author"}]}}
    assert _rows(doc)["cisa-explicit-unknowns"].status == "pass"


def test_an_empty_unknowns_statement_does_not_count() -> None:
    doc = {"metadata": {"properties": [{"name": "trusca:undeclared-fields", "value": ""}]}}
    assert _rows(doc)["cisa-explicit-unknowns"].status == "warn"


def test_a_detached_signature_is_invisible_to_this_element() -> None:
    """Recorded so the behaviour is deliberate: TRUSCA signs detached, so this
    element cannot pass on our own exports and the guidance file carries the
    note that says so."""
    assert _rows({"bomFormat": "CycloneDX"})["cisa-sbom-author-signature"].status == "warn"
    assert _rows({"signature": {"algorithm": "ES256"}})[
        "cisa-sbom-author-signature"
    ].status == "pass"


# ---------------------------------------------------------------------------
# Per-subject coverage.
# ---------------------------------------------------------------------------
def test_the_target_component_is_measured_with_the_rest() -> None:
    """The guidance describes its fields over the target component AND the
    enumerated ones; measuring only the second lets an unnamed root pass."""
    doc = {
        "metadata": {"component": {"type": "application"}},
        "components": [{"name": "lib", "version": "1.0"}],
    }
    row = _rows(doc)["cisa-component-name"]
    assert row.status == "warn"
    assert row.detail == "1/2 component(s)"


def test_an_identifier_may_be_a_purl_a_cpe_a_hash_or_a_swhid() -> None:
    """Wider than the submission criteria's PURL requirement, on purpose."""
    for identified in (
        {"name": "a", "purl": "pkg:npm/a@1"},
        {"name": "a", "cpe": "cpe:2.3:a:v:a:1:*:*:*:*:*:*:*"},
        {"name": "a", "hashes": [{"alg": "SHA-256", "content": "ab"}]},
        {"name": "a", "swhid": "swh:1:cnt:deadbeef"},
    ):
        assert _rows({"components": [identified]})["cisa-component-identifiers"].status == "pass"
    assert _rows({"components": [{"name": "a"}]})["cisa-component-identifiers"].status == "warn"


def test_a_version_marked_unestablished_is_not_reported_as_missing() -> None:
    """That marking IS the statement the guidance asks for; listing it here
    would report the same fact twice."""
    marked = {
        "components": [
            {
                "name": "a",
                "properties": [
                    {"name": "trusca:evidenceGrade", "value": "unestablished"}
                ],
            }
        ]
    }
    plain = {"components": [{"name": "a"}]}
    assert _rows(marked)["cisa-component-version"].status == "pass"
    assert _rows(plain)["cisa-component-version"].status == "warn"


def test_hash_algorithm_is_only_asked_of_components_that_carry_a_hash() -> None:
    no_hash = {"components": [{"name": "a"}]}
    good = {"components": [{"name": "a", "hashes": [{"alg": "SHA-256", "content": "ab"}]}]}
    unrecomputable = {"components": [{"name": "a", "hashes": [{"alg": "", "content": "ab"}]}]}
    unknown_alg = {"components": [{"name": "a", "hashes": [{"alg": "moon-dust", "content": "ab"}]}]}
    assert _rows(no_hash)["cisa-component-hash-algorithm"].status == "pass"
    assert _rows(good)["cisa-component-hash-algorithm"].status == "pass"
    assert _rows(unrecomputable)["cisa-component-hash-algorithm"].status == "warn"
    assert _rows(unknown_alg)["cisa-component-hash-algorithm"].status == "warn"


def test_an_empty_document_warns_with_the_registry_wording() -> None:
    row = _rows({})["cisa-component-name"]
    assert row.status == "warn"
    assert row.detail == "no components"


def test_a_hostile_shape_never_raises() -> None:
    """Scalars where objects are expected, on every field the ports touch."""
    hostile: dict[str, Any] = {
        "metadata": "nope",
        "components": ["nope", {"name": [], "hashes": "nope", "properties": 7}],
        "dependencies": "nope",
        "version": None,
    }
    rows = cisa.evaluate_cisa(hostile)
    assert len(rows) == 23
    assert all(row.detail != rc.DETAIL_ERROR for row in rows)
