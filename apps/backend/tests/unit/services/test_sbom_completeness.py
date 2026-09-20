"""What an exported SBOM may claim about its own completeness (U3-C): the rules."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services import sbom_completeness as sc
from services import scan_outcome as so


def _meta(**extra: Any) -> dict[str, Any]:
    """Metadata of a scan recorded by U3-B: it has a stage_timings entry."""
    return {so.STAGE_TIMINGS_KEY: {"fetch": {}}, **extra}


def _assess(**kw: Any) -> sc.Completeness:
    base: dict[str, Any] = {
        "scan_kind": "source",
        "metadata": _meta(),
        "component_count": 5,
        "edge_count": 4,
    }
    base.update(kw)
    return sc.assess(**base)


def test_a_recorded_scan_with_no_failure_and_a_graph_is_complete() -> None:
    result = _assess()
    assert (result.assemblies, result.dependencies, result.basis) == (
        sc.COMPLETE,
        sc.COMPLETE,
        "no_known_gap",
    )


# Spelled out, not read from the module: a test that iterates the constant under
# test agrees with any value the constant is changed to.
@pytest.mark.parametrize("stage", ["prep", "cocoapods"])
def test_a_degraded_inventory_stage_makes_both_incomplete(stage: str) -> None:
    meta = so.add_stage_outcome(_meta(), stage=stage, reason="failed")
    result = _assess(metadata=meta)
    assert (result.assemblies, result.dependencies) == (sc.INCOMPLETE, sc.INCOMPLETE)
    assert result.basis == f"stage_degraded:{stage}"


@pytest.mark.parametrize(
    "stage",
    [
        "scope_filter",
        "document_metadata",
        "sign",
        "attest",
        "scancode",
        "detected_licenses",
        "approvals",
        "scanoss",
        "preserve",
        "reachability",
    ],
)
def test_a_stage_that_cannot_drop_components_does_not_count(stage: str) -> None:
    """Signing, licenses and the scope filter (which only adds) leave the claim alone."""
    meta = so.add_stage_outcome(_meta(), stage=stage, reason="failed")
    assert _assess(metadata=meta).assemblies == sc.COMPLETE


def test_a_scan_without_the_stage_record_is_unknown_not_complete() -> None:
    """No stage_timings means stage_outcomes being absent proves nothing."""
    result = _assess(metadata={"detected_env": "node"})
    assert (result.assemblies, result.dependencies, result.basis) == (
        sc.UNKNOWN,
        sc.UNKNOWN,
        "not_recorded",
    )
    assert _assess(metadata=None).assemblies == sc.UNKNOWN


def test_an_ingested_document_is_unknown() -> None:
    result = _assess(scan_kind="sbom")
    assert (result.assemblies, result.basis) == (sc.UNKNOWN, "ingested_document")


def test_removed_components_make_a_filtered_export_incomplete() -> None:
    result = _assess(excluded_count=2)
    assert (result.assemblies, result.dependencies, result.basis) == (
        sc.INCOMPLETE,
        sc.INCOMPLETE,
        "profile_filter",
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (so.EMPTY_WITH_MANIFESTS, sc.INCOMPLETE),
        (so.EMPTY_NO_MANIFESTS, sc.UNKNOWN),
        (so.COMPONENTS_FOUND, sc.COMPLETE),
    ],
)
def test_the_component_outcome_is_read(outcome: str, expected: str) -> None:
    meta = _meta(**{so.METADATA_KEY: outcome})
    assert _assess(metadata=meta).assemblies == expected


def test_listed_components_with_no_graph_leave_the_edges_unknown() -> None:
    result = _assess(edge_count=0)
    assert (result.assemblies, result.dependencies, result.basis) == (
        sc.COMPLETE,
        sc.UNKNOWN,
        "no_graph",
    )


def test_a_single_component_needs_no_edges() -> None:
    assert _assess(component_count=1, edge_count=0).dependencies == sc.COMPLETE


def test_the_inventory_stages_are_recorded_stages() -> None:
    """The set this module reads must exist in the vocabulary U3-B writes, and the
    two lists above must between them cover every recorded stage."""
    assert set(sc.INVENTORY_STAGES) == {"prep", "cocoapods"}
    assert set(sc.INVENTORY_STAGES) <= set(so.DEGRADABLE_STAGES)


# ---------------------------------------------------------------------------
# The document builder: filtered exports must not leave a reference dangling
# ---------------------------------------------------------------------------


def _row(cv: uuid.UUID, *, direct: bool = False) -> dict[str, Any]:
    return {
        "component_version_id": cv,
        "name": "n",
        "version": "1",
        "purl": f"pkg:npm/n@{cv.hex[:6]}",
        "direct": direct,
    }


def test_edges_to_a_removed_component_are_dropped_and_the_export_says_incomplete() -> None:
    from datetime import UTC, datetime

    from models import Project, Scan
    from services.sbom_export import _build_cyclonedx_doc, _ProfileMarker

    kept_a, kept_b, removed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    project = Project(id=uuid.uuid4(), name="p")
    scan = Scan(id=uuid.uuid4(), kind="source", scan_metadata=_meta())
    doc = _build_cyclonedx_doc(
        project=project,
        scan=scan,
        rows=[_row(kept_a, direct=True), _row(kept_b)],
        licenses_by_cv={},
        vuln_rows=[],
        now=datetime(2026, 9, 21, tzinfo=UTC),
        profile=_ProfileMarker(profile="policy-filtered", annotations={}, excluded_count=1),
        edges=[(kept_a, kept_b), (kept_a, removed), (removed, kept_b)],
    )

    by_ref = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    assert by_ref[str(kept_a)] == [str(kept_b)]
    assert by_ref[str(kept_b)] == []
    assert str(removed) not in by_ref
    assert {c["aggregate"] for c in doc["compositions"]} == {sc.INCOMPLETE}


def test_an_export_with_no_scan_carries_neither_graph_nor_composition() -> None:
    from datetime import UTC, datetime

    from models import Project
    from services.sbom_export import _build_cyclonedx_doc

    doc = _build_cyclonedx_doc(
        project=Project(id=uuid.uuid4(), name="p"),
        scan=None,
        rows=[],
        licenses_by_cv={},
        vuln_rows=[],
        now=datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert "dependencies" not in doc and "compositions" not in doc
