"""BomLens result completeness in TRUSCA's own records (integrations.bomlens.records).

The persist path that writes these records is exercised against a real
database in ``tests/integration/scan/test_bomlens_result.py``. This file pins
the translation rules and the vocabulary they share with the in-process
pipeline (hardening rule 2).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.bomlens import contract, records
from integrations.license_fetcher.budget import EnrichmentBudget
from services import scan_outcome as so

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "bomlens"


def _manifest(case: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / case / contract.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    return data


def _registry(status: str, counts: dict[str, int] | None, reason: str | None = None) -> Any:
    document = copy.deepcopy(_manifest("source-ok"))
    step: dict[str, Any] = {
        "id": "license-registry",
        "status": status,
        "reason": reason,
        "stage": 2,
        "started_at": "2026-10-11T09:00:00Z",
        "ended_at": "2026-10-11T09:04:00.04Z",
    }
    if counts is not None:
        step["counts"] = counts
    document["steps"] = [step]
    return contract.parse_manifest(document)


# ---------------------------------------------------------------------------
# Vocabulary contracts
# ---------------------------------------------------------------------------


def test_the_registry_counts_bomlens_wrote_are_the_ones_this_reader_classifies() -> None:
    """Read from a real run, so a key a new image adds or renames shows up here."""
    [step] = [s for s in _manifest("source-deep-license")["steps"] if s["id"] == "license-registry"]
    assert set(step["counts"]) == set(records.REGISTRY_COUNT_KEYS)


def test_every_skip_reason_from_bomlens_is_one_trusca_records_and_labels() -> None:
    assert set(records.REGISTRY_NOT_LOOKED_UP.values()) <= set(so.LICENSE_LOOKUP_REASONS)
    assert set(records.REGISTRY_NOT_LOOKED_UP) <= set(records.REGISTRY_COUNT_KEYS)
    assert set(records.REGISTRY_LOOKUP_FAILURES) <= set(records.REGISTRY_COUNT_KEYS)


def test_the_in_process_reasons_bomlens_cannot_produce_are_the_known_gap() -> None:
    """BomLens has a time budget and no circuit breaker, so its records never
    say ``breaker_open`` (and so never ``both`` on their own). A BomLens release
    that adds a breaker must extend REGISTRY_NOT_LOOKED_UP; this pins the gap so
    that change is a decision, not a drift."""
    produced = set(records.REGISTRY_NOT_LOOKED_UP.values())
    assert set(so.LICENSE_LOOKUP_REASONS) - produced == {"breaker_open", "both"}


def test_the_record_has_the_in_process_fields() -> None:
    """Same keys as EnrichmentBudget.summary(), plus which engines contributed."""
    record = records.license_enrichment_record(
        _registry("degraded", {"candidates": 4, "budget_reached": 3}, "budget_exhausted")
    )
    assert record is not None
    in_process = EnrichmentBudget(budget_seconds=60, breaker_threshold=3).summary()
    assert set(record) == set(in_process) | {"engines"}


# ---------------------------------------------------------------------------
# license_enrichment
# ---------------------------------------------------------------------------


def test_lookups_cut_off_by_the_budget_are_not_looked_up() -> None:
    result = _registry(
        "degraded",
        {"candidates": 5, "filled": 1, "lookup_failed": 1, "budget_reached": 3},
        "budget_exhausted",
    )
    record = records.license_enrichment_record(result)
    assert record == {
        "budget_seconds": None,
        "spent_seconds": 240.0,
        "lookups": 2,
        "lookup_failures": 1,
        "not_looked_up": 3,
        "not_looked_up_reason": "budget_exhausted",
        "engines": ["bomlens"],
    }
    # The Overview notice reads it exactly as it reads an in-process record.
    assert so.license_lookup_gap({so.LICENSE_ENRICHMENT_KEY: record}) == (3, "budget_exhausted")


@pytest.mark.parametrize(
    ("status", "counts", "reason"),
    [
        # Every lookup was made: no record, as in process.
        ("ok", {"candidates": 3, "filled": 3, "budget_reached": 0}, None),
        # Failed lookups were made; they are not "not looked up".
        ("degraded", {"candidates": 3, "lookup_failed": 3}, "unavailable"),
        # The step did not run or reported no counts.
        ("disabled", None, None),
        ("failed", None, "failed"),
        ("degraded", None, "budget_exhausted"),
        # A negative count is not a count.
        ("degraded", {"candidates": 3, "budget_reached": -2}, "budget_exhausted"),
    ],
)
def test_no_record_when_nothing_was_skipped(
    status: str, counts: dict[str, int] | None, reason: str | None
) -> None:
    assert records.license_enrichment_record(_registry(status, counts, reason)) is None


def test_no_record_when_the_step_is_absent() -> None:
    document = copy.deepcopy(_manifest("source-ok"))
    document["steps"] = []
    assert records.license_enrichment_record(contract.parse_manifest(document)) is None


def _engine(skipped: int) -> dict[str, Any]:
    record = records.license_enrichment_record(
        _registry(
            "degraded",
            {"candidates": skipped + 1, "lookup_failed": 1, "budget_reached": skipped},
            "budget_exhausted",
        )
    )
    assert record is not None
    return record


def test_the_engine_record_stands_alone_when_the_persist_path_skipped_nothing() -> None:
    assert records.merge_license_enrichment(None, _engine(2)) == _engine(2)
    assert records.merge_license_enrichment({"not_looked_up": 0}, _engine(2)) == _engine(2)


def test_an_in_process_record_is_kept_when_the_engine_skipped_nothing() -> None:
    existing = {"not_looked_up": 4, "not_looked_up_reason": "breaker_open"}
    assert records.merge_license_enrichment(existing, None) == existing
    assert records.merge_license_enrichment("garbage", None) is None


@pytest.mark.parametrize(
    ("in_process_reason", "merged_reason"),
    [("budget_exhausted", "budget_exhausted"), ("breaker_open", "both"), ("both", "both")],
)
def test_both_layers_skipping_add_up(in_process_reason: str, merged_reason: str) -> None:
    existing = {
        "budget_seconds": 60.0,
        "spent_seconds": 60.4,
        "lookups": 10,
        "lookup_failures": 3,
        "not_looked_up": 4,
        "not_looked_up_reason": in_process_reason,
    }
    merged = records.merge_license_enrichment(existing, _engine(2))
    assert merged == {
        "budget_seconds": 60.0,
        "spent_seconds": 300.4,
        "lookups": 11,
        "lookup_failures": 4,
        "not_looked_up": 6,
        "not_looked_up_reason": merged_reason,
        "engines": ["inprocess", "bomlens"],
    }


# ---------------------------------------------------------------------------
# scope_filter
# ---------------------------------------------------------------------------


def _sbom(**properties: str) -> dict[str, Any]:
    return {
        "metadata": {"properties": [{"name": k, "value": v} for k, v in properties.items()]},
        "components": [],
    }


def test_the_captured_exclusion_is_recorded_beside_the_in_process_keys() -> None:
    sbom = json.loads(
        (FIXTURES / "source-deep-license" / "mixed_1.0.0_bom.json").read_text(encoding="utf-8")
    )
    record = records.scope_filter_record(sbom, kept=1)
    assert record is not None
    assert {"applied", "dropped", "kept", "dropped_refs"} <= set(record)
    assert record["dropped"] == {}
    assert "**/test/**" in record["excluded_paths"]
    assert "**/.github/workflows/**" in record["excluded_paths"]
    assert record["excluded_manifests"] == ["test/package.json"]
    assert record["excluded_manifests_more"] == 0


def test_the_more_suffix_is_a_count_and_the_lists_are_bounded() -> None:
    names = ", ".join(f"pkg{i}/test/package.json" for i in range(60))
    sbom = _sbom(
        **{
            records.EXCLUDED_PATHS_PROPERTY: "**/test/**, " + "x" * 500,
            records.EXCLUDED_MANIFESTS_PROPERTY: f"{names} (+7 more)",
        }
    )
    record = records.scope_filter_record(sbom, kept=0)
    assert record is not None
    assert record["excluded_paths"] == ["**/test/**", "x" * records.MAX_ENTRY_CHARS]
    assert len(record["excluded_manifests"]) == records.MAX_LISTED
    assert record["excluded_manifests"][-1] == "pkg49/test/package.json"
    # 10 over the cap here, 7 the engine already left off its own list.
    assert record["excluded_manifests_more"] == 17


@pytest.mark.parametrize(
    "sbom",
    [
        {},
        {"metadata": None},
        {"metadata": {"properties": "nope"}},
        {"metadata": {"properties": [None, {"name": 1, "value": "x"}]}},
        _sbom(**{records.EXCLUDED_PATHS_PROPERTY: " , "}),
        # Files without patterns: the exclusion's record is the patterns.
        _sbom(**{records.EXCLUDED_MANIFESTS_PROPERTY: "test/package.json"}),
    ],
)
def test_no_patterns_means_no_record(sbom: dict[str, Any]) -> None:
    assert records.scope_filter_record(sbom, kept=0) is None


# ---------------------------------------------------------------------------
# component_outcome
# ---------------------------------------------------------------------------


def test_the_captured_run_says_declared_but_empty_both_ways() -> None:
    """The manifest reason and the SBOM property, each on its own."""
    result = contract.parse_manifest(_manifest("source-no-components"))
    sbom = json.loads(
        (FIXTURES / "source-no-components" / "poetry_1.0.0_bom.json").read_text(encoding="utf-8")
    )
    assert records.declared_but_empty(result, sbom)
    assert records.declared_but_empty(result, {})
    without_reason = copy.deepcopy(_manifest("source-no-components"))
    for step in without_reason["steps"]:
        if step["id"] == "generate":
            step.update(status="ok", reason=None)
    assert records.declared_but_empty(contract.parse_manifest(without_reason), sbom)


def test_a_clean_run_did_not_declare_anything_it_missed() -> None:
    result = contract.parse_manifest(_manifest("source-ok"))
    sbom = json.loads((FIXTURES / "source-ok" / "node_1.0.0_bom.json").read_text(encoding="utf-8"))
    assert not records.declared_but_empty(result, sbom)


@pytest.mark.parametrize(
    ("count", "inventory", "engine", "expected"),
    [
        (3, None, False, so.COMPONENTS_FOUND),
        # Components win over any empty finding.
        (3, None, True, so.COMPONENTS_FOUND),
        # The engine's finding decides when TRUSCA could not look.
        (0, None, True, so.EMPTY_WITH_MANIFESTS),
        (0, {"count": 0}, True, so.EMPTY_WITH_MANIFESTS),
        # Otherwise TRUSCA's own inventory, as in process.
        (0, {"count": 2}, False, so.EMPTY_WITH_MANIFESTS),
        (0, None, False, so.EMPTY_NO_MANIFESTS),
    ],
)
def test_component_outcome(
    count: int, inventory: dict[str, Any] | None, engine: bool, expected: str
) -> None:
    assert (
        records.component_outcome(
            component_count=count,
            manifest_inventory=inventory,
            engine_declared_but_empty=engine,
        )
        == expected
    )
