# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Guidance join for the 2026 minimum elements — contracts and join behaviour."""

from __future__ import annotations

from typing import Any

from services import cisa_conformance as cisa
from services import cisa_guidance as guidance
from services import registry_conformance as rc


def _element_ids() -> set[str]:
    return {str(e.get("id")) for _, e in rc.iter_elements(cisa.CISA_SPEC.registry_path)}


def test_guidance_keys_are_all_real_elements() -> None:
    """A fragment or note keyed to an id no registry declares is dead text that
    no reader will ever see, and nothing else would report it."""
    unknown = (guidance.fragment_ids() | guidance.review_ids()) - _element_ids()
    assert not unknown, f"guidance for unknown elements: {sorted(unknown)}"


def test_every_element_without_an_automated_source_carries_a_review_note() -> None:
    """If a scan cannot settle an element, the report has to say what a person
    does instead — otherwise the row is a dead end for the reader."""
    na_elements = {
        str(e.get("id"))
        for _, e in rc.iter_elements(cisa.CISA_SPEC.registry_path)
        if e.get("source") == "na"
    }
    assert na_elements <= guidance.review_ids(), sorted(na_elements - guidance.review_ids())


def test_no_element_carries_both_a_fragment_and_a_note() -> None:
    """One panel per row: a row either tells you what to write or tells you
    what to go and establish."""
    assert not (guidance.fragment_ids() & guidance.review_ids())


def test_review_notes_are_bilingual() -> None:
    """Fragments carry CycloneDX field names and are not localized; notes are
    prose a person reads, and a Korean reader gets the Korean one."""
    data = guidance._guidance()["review"]
    missing = [k for k, v in data.items() if not str(v.get("how_ko", ""))]
    assert not missing, f"review notes without Korean: {missing}"


def test_the_signature_element_carries_a_note_despite_having_a_source() -> None:
    """The row a reader most needs the note on. Its source is 'declared', not
    'na', so a rule that only annotated source-'na' rows would skip it — and a
    reader whose supplier signed detached would read 'not present' as unsigned.
    """
    assert "cisa-sbom-author-signature" in guidance.review_ids()


def _check(check_id: str, status: str) -> dict[str, Any]:
    return {"id": check_id, "status": status, "label": "x", "detail": ""}


def test_guidance_attaches_to_rows_that_are_not_a_pass() -> None:
    rows = guidance.attach_guidance(
        [
            _check("cisa-sbom-author", "warn"),
            _check("cisa-coverage", "warn"),
            _check("cisa-sbom-author-signature", "warn"),
        ]
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id["cisa-sbom-author"]["guidance"]["snippet"]
    assert "review" not in by_id["cisa-sbom-author"]
    assert by_id["cisa-coverage"]["review"]["how"]
    assert by_id["cisa-coverage"]["review"]["how_ko"]
    assert by_id["cisa-sbom-author-signature"]["review"]["how"]


def test_a_passing_row_gets_neither() -> None:
    rows = guidance.attach_guidance([_check("cisa-sbom-author", "pass")])
    assert "guidance" not in rows[0]
    assert "review" not in rows[0]


def test_the_join_does_not_mutate_its_input() -> None:
    """The caller may be holding the raw JSONB row."""
    original = _check("cisa-sbom-author", "warn")
    guidance.attach_guidance([original])
    assert set(original) == {"id", "status", "label", "detail"}


def test_an_unmapped_check_passes_through_untouched() -> None:
    rows = guidance.attach_guidance([_check("purl", "fail")])
    assert set(rows[0]) == {"id", "status", "label", "detail"}
