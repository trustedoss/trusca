# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Contract for the registry-neutral evaluator and the registries it reads.

Two things are pinned here.

The SHAPE every registry must have, so that a baseline added later is held to
the same structure the evaluator assumes rather than discovering the mismatch
as a missing label on a user's screen.

The BEHAVIOUR the spec fields are supposed to give a baseline: that a spec can
declare what it measures and what it calls it, that a failing applies_when
evaluates rather than silently dropping the baseline, and that a predicate
which raises degrades to one warn row instead of losing the verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services import registry_conformance as rc
from services.cisa_conformance import CISA_SPEC
from services.g7_conformance import G7_SPEC

_SERVICES = Path(__file__).resolve().parents[3] / "services"

#: Every registry the evaluator reads. A new one added without being listed
#: here is caught by test_every_registry_is_under_contract.
REGISTRIES = [G7_SPEC.registry_path, CISA_SPEC.registry_path]


def test_every_registry_is_under_contract() -> None:
    """A registry file that no spec points at is either dead or unguarded."""
    on_disk = {p.name for p in _SERVICES.glob("*_registry.json")}
    covered = {p.name for p in REGISTRIES}
    assert on_disk == covered, (
        f"registry files not under contract: {sorted(on_disk - covered)}; "
        f"contracted files that do not exist: {sorted(covered - on_disk)}"
    )


@pytest.mark.parametrize("path", REGISTRIES, ids=lambda p: p.name)
def test_registry_shape(path: Path) -> None:
    registry = rc.load_registry(path)
    assert isinstance(registry.get("clusters"), list), "clusters must be a list"

    seen: set[str] = set()
    for cluster_id, element in rc.iter_elements(path):
        assert cluster_id, f"{path.name}: an element sits in an unnamed cluster"
        element_id = element.get("id")
        label = element.get("label")
        assert isinstance(element_id, str) and element_id, (
            f"{path.name}: element in cluster {cluster_id} has no id"
        )
        assert isinstance(label, str) and label, f"{path.name}: {element_id} has no label"
        assert element_id not in seen, f"{path.name}: duplicate element id {element_id}"
        seen.add(element_id)

        # `required` is optional and defaults per spec, but if a registry
        # states it, the evaluator reads it as a bool — a string "false" would
        # silently make an element mandatory.
        if "required" in element:
            assert isinstance(element["required"], bool), (
                f"{path.name}: {element_id} declares a non-boolean `required`"
            )


def _spec(**overrides: Any) -> rc.RegistrySpec:
    base: dict[str, Any] = {
        "name": "test",
        "registry_path": G7_SPEC.registry_path,
        "predicates": {},
    }
    return rc.RegistrySpec(**{**base, "predicates": {}, **overrides})


def test_a_spec_names_what_it_measures() -> None:
    """The wording follows the spec, not a literal in the evaluator."""
    doc = {"components": [{"type": "widget", "name": "a"}]}
    spec = _spec(
        missing={"g7-model-name": lambda subjects: []},
        subject=lambda d: [c for c in d["components"] if c["type"] == "widget"],
        subject_label="widget",
    )
    rows = {c.id: c for c in rc.evaluate_registry(doc, spec)}
    assert rows["g7-model-name"].detail == "1/1 widget(s)"


def test_an_empty_subject_uses_the_spec_wording() -> None:
    spec = _spec(
        missing={"g7-model-name": lambda subjects: []},
        subject=lambda _doc: [],
        empty_subject_detail="nothing of the kind here",
    )
    rows = {c.id: c for c in rc.evaluate_registry({}, spec)}
    assert rows["g7-model-name"].status == "warn"
    assert rows["g7-model-name"].detail == "nothing of the kind here"


def test_default_required_is_applied_to_every_element() -> None:
    """A baseline can be mandatory in full without touching the registry."""
    rows = rc.evaluate_registry({}, _spec(default_required=True))
    assert rows and all(c.required for c in rows)
    assert not any(c.required for c in rc.evaluate_registry({}, _spec()))


def test_a_failing_applies_when_evaluates_rather_than_disappearing() -> None:
    def boom(_doc: dict[str, Any]) -> bool:
        raise RuntimeError("upstream shape changed")

    assert rc.applies({}, _spec(applies_when=boom)) is True
    assert rc.applies({}, _spec(applies_when=lambda _doc: False)) is False


def test_a_raising_predicate_costs_one_row_not_the_verdict() -> None:
    def boom(_doc: dict[str, Any]) -> bool:
        raise RuntimeError("hostile shape")

    rows = {c.id: c for c in rc.evaluate_registry({}, _spec(predicates={"g7-meta-author": boom}))}
    assert rows["g7-meta-author"].status == "warn"
    assert rows["g7-meta-author"].detail == rc.DETAIL_ERROR
    # Every other element still produced a row.
    assert len(rows) == len(rc.iter_elements(G7_SPEC.registry_path))


def test_g7_declares_itself_rather_than_being_special_cased() -> None:
    """The properties that used to be hardcoded are readable off the spec."""
    assert G7_SPEC.subject_label == "model component"
    assert G7_SPEC.empty_subject_detail == "no machine-learning-model components"
    assert G7_SPEC.default_required is False
    assert G7_SPEC.applies_when(
        {"components": [{"type": "machine-learning-model", "name": "m"}]}
    )
    assert not G7_SPEC.applies_when({"components": [{"type": "library", "name": "l"}]})
