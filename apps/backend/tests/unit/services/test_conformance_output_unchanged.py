# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Byte-level baseline for the conformance verdict, across every fixture.

This exists for refactors that must not change what a user sees. The registry
evaluator is being generalised so a second baseline (the 2026 SBOM minimum
elements) can be added beside the G7 one, and a generalisation that quietly
alters a G7 verdict is a regression that no per-element test would necessarily
catch — each of them asserts its own element, none asserts the whole shape.

The captures under ``tests/fixtures/conformance_baseline/`` were taken before
that refactor began. Upstream did the same comparison for its own version of
this change but recorded it only in a commit message, so it cannot be re-run
today; keeping the captures in the tree is the point.

When a change is MEANT to move the output, re-capture deliberately and let the
diff be reviewed:

    python -m tests.fixtures.conformance_baseline.recapture

A diff here is not automatically a bug — it is a claim that needs an argument.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.sbom_conformance import evaluate

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_SOURCES = _FIXTURES / "sbom_ingest"
_BASELINE = _FIXTURES / "conformance_baseline"


def _cases() -> list[Path]:
    return sorted(_SOURCES.glob("*.json"))


def test_every_source_fixture_has_a_baseline() -> None:
    """A fixture added without a capture would be silently uncovered."""
    missing = [f.name for f in _cases() if not (_BASELINE / f"{f.stem}.json").is_file()]
    assert not missing, (
        "these SBOM fixtures have no conformance baseline capture: "
        f"{missing}. Run the recapture module to add them."
    )


@pytest.mark.parametrize("source", _cases(), ids=lambda p: p.stem)
def test_verdict_matches_the_captured_baseline(source: Path) -> None:
    captured = json.loads((_BASELINE / f"{source.stem}.json").read_text("utf-8"))
    actual = evaluate(source.read_bytes()).as_dict()

    # Compare the summary first: a failure here says WHAT moved before the
    # reader has to read a 65-element diff.
    summary_keys = [k for k in captured if k != "checks"]
    assert {k: actual[k] for k in summary_keys} == {
        k: captured[k] for k in summary_keys
    }, f"{source.stem}: verdict summary moved"

    captured_checks = {c["id"]: c for c in captured["checks"]}
    actual_checks = {c["id"]: c for c in actual["checks"]}
    assert set(actual_checks) == set(captured_checks), (
        f"{source.stem}: the set of checks changed — "
        f"added {sorted(set(actual_checks) - set(captured_checks))}, "
        f"dropped {sorted(set(captured_checks) - set(actual_checks))}"
    )
    for check_id, expected in captured_checks.items():
        assert actual_checks[check_id] == expected, f"{source.stem}: {check_id} moved"

    # Order is part of the shape: the panel renders checks in the order the
    # evaluator returns them, so a reordering IS a user-visible change.
    assert [c["id"] for c in actual["checks"]] == [
        c["id"] for c in captured["checks"]
    ], f"{source.stem}: check order changed"
