"""
Unit tests for the AI usage-scenario license verdicts, gap #28.

The axis exists because one model license gives different answers depending on
what you do with the model. These tests pin that behaviour at the boundary that
decides it: which registry entry a license string matches, and which of that
entry's conditions bind the scenario in play.

Fixtures are the real AI-BOMs already in the ingest suite rather than minimal
hand-written documents, so a model with several licenses, a dataset with none,
and a document whose dependency edges matter all appear at their real density.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.ai_risk_assessment import (
    AI_VERDICT_RANK,
    CAUTION,
    CONDITIONAL,
    OK,
    REVIEW,
    USAGE_SCENARIOS,
    assess,
    extract_subjects,
    match_term,
    verdict_for,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sbom_ingest"


def _doc(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return document


# ---------------------------------------------------------------------------
# Registry matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "expected_key"),
    [
        ("CC-BY-NC-4.0", "cc-non-commercial"),
        ("cc_by_nc_4.0", "cc-non-commercial"),
        ("Apache-2.0", "permissive"),
        ("LLAMA-2-Community-License", "llama-community"),
        ("AGPL-3.0", "agpl"),
        ("CC-BY-SA-4.0", "cc-share-alike"),
    ],
)
def test_match_term_finds_the_expected_family(declared: str, expected_key: str) -> None:
    """The separator classes a document might use all normalise to one form."""
    term = match_term(declared)
    assert term is not None
    assert term["key"] == expected_key


def test_unknown_license_matches_nothing() -> None:
    """A license nobody classified is not quietly folded into a known family."""
    assert match_term("Widgets Corp Proprietary Terms v3") is None


def test_specific_family_wins_over_the_generic_one() -> None:
    """NonCommercial is decided before plain Creative Commons.

    Both entries' regexes match ``cc by nc 4.0``. The registry's file order is
    what keeps the restrictive reading from being shadowed by the permissive
    one, so this asserts the order, not just the result.
    """
    assert match_term("CC-BY-NC-4.0")["key"] == "cc-non-commercial"  # type: ignore[index]
    assert match_term("CC-BY-4.0")["key"] == "cc-attribution"  # type: ignore[index]


def test_exact_id_beats_a_regex_from_an_earlier_entry() -> None:
    """``ids`` are consulted across the whole registry before any regex runs."""
    term = match_term("openrail")
    assert term is not None
    assert term["key"] == "openrail"


# ---------------------------------------------------------------------------
# Scenario narrowing
# ---------------------------------------------------------------------------


def test_worst_of_rank_puts_review_above_conditional() -> None:
    """Not knowing outranks a known obligation somebody has already read."""
    assert AI_VERDICT_RANK[CAUTION] > AI_VERDICT_RANK[REVIEW]
    assert AI_VERDICT_RANK[REVIEW] > AI_VERDICT_RANK[CONDITIONAL]
    assert AI_VERDICT_RANK[CONDITIONAL] > AI_VERDICT_RANK[OK]


@pytest.mark.parametrize("scenario", [None, *USAGE_SCENARIOS])
def test_share_alike_binds_only_where_it_distributes(scenario: str | None) -> None:
    """Share-alike obligations trigger on distribution, not on running a model.

    Internal use and outputs-only carry none of its conditions, so the term
    reads ``ok`` for them; every other reading keeps the obligation.
    """
    term = match_term("CC-BY-SA-4.0")
    assert term is not None
    verdict = verdict_for(term, scenario)
    if scenario in ("internal", "outputs-only"):
        assert verdict == OK
    else:
        assert verdict == CONDITIONAL


def test_scenario_override_wins_over_the_condition_calculation() -> None:
    """NonCommercial for internal use is a judgement call, not a condition count.

    Its ``non-commercial-only`` condition binds every scenario, so the condition
    path would answer ``caution`` for internal use too. The registry's explicit
    per-scenario verdict says ``conditional`` instead, whether in-company use
    counts as commercial depends on the purpose. The override has to win.
    """
    term = match_term("CC-BY-NC-4.0")
    assert term is not None
    assert verdict_for(term, None) == CAUTION
    assert verdict_for(term, "internal") == CONDITIONAL
    assert verdict_for(term, "redistribute") == CAUTION


def test_unrecognised_scenario_falls_back_to_the_full_terms() -> None:
    """A bad value must widen to the conservative reading, never narrow."""
    subjects = extract_subjects(_doc("aibom-review-flags-1_7.json"))
    strict = assess(subjects, scenario=None)
    bogus = assess(subjects, scenario="on-the-moon")
    assert strict is not None and bogus is not None
    assert bogus.scenario is None
    assert bogus.verdict == strict.verdict


# ---------------------------------------------------------------------------
# Subject extraction
# ---------------------------------------------------------------------------


def test_extract_reads_models_and_datasets() -> None:
    subjects = extract_subjects(_doc("aibom-datasets-1_7.json"))
    assert subjects is not None
    assert [m["name"] for m in subjects["models"]] == [
        "distilbert-base-uncased-finetuned-sst-2-english"
    ]
    assert {d["name"] for d in subjects["datasets"]} == {
        "stanfordnlp/sst2",
        "internal/eval-holdout",
    }
    assert subjects["models"][0]["depends_on"]


def test_extract_returns_none_without_a_model() -> None:
    """A plain dependency SBOM has no opinion to store."""
    assert extract_subjects(_doc("realistic.cdx.json")) is None


def test_extract_reads_a_model_that_is_the_document_subject() -> None:
    """An ML-BOM whose subject is the model must not count as zero models.

    ``metadata.component`` is a component too. Reading only ``components[]``
    is what makes a document *about* a model report that it contains none -
    the same narrow reading tracked as issue #53 on the G7 evaluator.
    """
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "component": {
                "type": "machine-learning-model",
                "bom-ref": "model-subject",
                "name": "subject-model",
                "licenses": [{"license": {"id": "CC-BY-NC-4.0"}}],
            }
        },
        "components": [],
    }
    subjects = extract_subjects(doc)
    assert subjects is not None
    assert [m["name"] for m in subjects["models"]] == ["subject-model"]


def test_extract_stores_no_verdict() -> None:
    """Only facts are persisted: a stored verdict would go stale on a setting."""
    subjects = extract_subjects(_doc("aibom-review-flags-1_7.json"))
    assert subjects is not None
    serialised = json.dumps(subjects)
    for token in ("verdict", "caution", "conditional", "scenario"):
        assert token not in serialised


# ---------------------------------------------------------------------------
# Document-level assessment
# ---------------------------------------------------------------------------


def test_assess_returns_none_without_subjects() -> None:
    assert assess(None, scenario=None) is None
    assert assess({"models": [], "datasets": []}, scenario=None) is None


def test_missing_license_reads_review_not_ok() -> None:
    """A dataset with no license declared is unresolved, not clean."""
    subjects = extract_subjects(_doc("aibom-datasets-1_7.json"))
    result = assess(subjects, scenario="internal")
    assert result is not None
    holdout = next(d for d in result.datasets if d.name == "internal/eval-holdout")
    assert holdout.verdict == REVIEW


def test_model_verdict_folds_in_its_declared_datasets() -> None:
    """A model is no cleaner than the data it was trained on.

    The fixture's model is Apache-2.0 (ok on its own) and depends on a
    share-alike dataset, so for redistribution the model reads ``conditional``
    through the dataset axis alone.
    """
    subjects = extract_subjects(_doc("aibom-datasets-1_7.json"))
    result = assess(subjects, scenario="redistribute")
    assert result is not None
    model = result.models[0]
    assert model.dataset_verdict == CONDITIONAL
    assert model.verdict == CONDITIONAL
    # Its own license is still the permissive one; the fold is what moved it.
    assert [r.term_key for r in model.reasons] == ["permissive"]


def test_dataset_edges_are_not_guessed() -> None:
    """A model with no dependency edges gets no dataset axis at all.

    The fixture carries a NonCommercial dataset and two models, none of which
    declares an edge. Inferring one would let an unrelated dataset decide a
    model's verdict.
    """
    subjects = extract_subjects(_doc("aibom-review-flags-1_7.json"))
    result = assess(subjects, scenario=None)
    assert result is not None
    assert all(m.dataset_verdict is None for m in result.models)
    assert all(m.dataset_refs == () for m in result.models)


def test_scenario_changes_the_verdict_for_the_same_document() -> None:
    """The whole point of the axis: same facts, different intended use.

    The NonCommercial dataset is the mover, ``conditional`` for internal use
    and ``caution`` once the work is redistributed.
    """
    subjects = extract_subjects(_doc("aibom-review-flags-1_7.json"))
    internal = assess(subjects, scenario="internal")
    redistribute = assess(subjects, scenario="redistribute")
    assert internal is not None and redistribute is not None
    assert internal.verdict == CONDITIONAL
    assert redistribute.verdict == CAUTION


def test_reasons_quote_the_declared_license_verbatim() -> None:
    """A reviewer argues with what the document said, not with our normalisation."""
    subjects = extract_subjects(_doc("aibom-review-flags-1_7.json"))
    result = assess(subjects, scenario=None)
    assert result is not None
    llama = next(m for m in result.models if m.name == "Llama-2-7b")
    assert [r.license for r in llama.reasons] == ["LLAMA-2-Community-License"]
    assert llama.reasons[0].summary
    assert llama.reasons[0].summary_ko


def test_extract_cleans_strings_headed_into_jsonb() -> None:
    """A hostile document must not be able to sink the conformance persist.

    Postgres cannot store NUL in JSONB, and a lone surrogate is not encodable
    to UTF-8 at all. Either one reaching the column aborts the whole write -
    taking the conformance verdict, which is the row's reason to exist, down
    with a value this axis merely happened to collect.
    """
    doc = {
        "components": [
            {
                "type": "machine-learning-model",
                "bom-ref": "pkg:huggingface/acme/model\x00",
                "name": "model\x00\x1b\ud800",
                "licenses": [{"license": {"id": "Apache-2.0\x00\x1b[31m"}}],
            }
        ]
    }
    subjects = extract_subjects(doc)
    assert subjects is not None
    serialised = json.dumps(subjects)
    assert "\x00" not in serialised
    assert "\ud800" not in serialised
    # It still encodes, which is the property the persist actually needs.
    serialised.encode("utf-8")
    assert subjects["models"][0]["name"] == "model"


def test_subject_count_is_bounded() -> None:
    """A document with thousands of subjects cannot make the column unbounded."""
    doc = {
        "components": [
            {
                "type": "machine-learning-model",
                "bom-ref": f"model-{i}",
                "name": f"model-{i}",
                "licenses": [],
            }
            for i in range(500)
        ]
    }
    subjects = extract_subjects(doc)
    assert subjects is not None
    assert len(subjects["models"]) == 200
    assert subjects["truncated"] is True
