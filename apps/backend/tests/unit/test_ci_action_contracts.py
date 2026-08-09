# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Contract tests between the GitHub action and the docs that describe it.

The action's inputs and outputs are a public interface: users write
``steps.sca.outputs.<name>`` into their own workflows, and the only place that
interface is written down is ``docs-site/docs/ci-integration/github-actions.md``.
Nothing linked the two, and they drifted in both directions at once — the docs
promised an ``epss-gate-count`` output the action never emitted (a workflow
reading it got an empty string, silently), while the action emitted a
``malicious-component-count`` the docs never mentioned, so nobody knew it was
there to read.

CLAUDE.md hardening rule 4 ("documentation is the oracle") asks for a guard on
every documented contract. These are that guard: the outputs table and the
inputs table must each name exactly what ``action.yml`` declares, so adding one
without the other fails here rather than in a user's pipeline.

The gate-step assertion covers the other half of the same drift — an output can
be *declared* while the shell step never writes the value it points at, which
also yields an empty string at use time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML ships no stubs

_ACTION_REL = Path("actions") / "scan" / "action.yml"
_DOC_REL = Path("docs-site") / "docs" / "ci-integration" / "github-actions.md"


def _repo_root() -> Path | None:
    """Walk up to the checkout root, or None when only the backend is mounted.

    CI runs pytest from ``apps/backend`` inside a full checkout, so the walk
    finds the root. A container that mounts ``apps/backend`` alone (the local
    convenience path) has no root to find — those runs skip rather than fail on
    files that were never there to read.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / _ACTION_REL).is_file():
            return parent
    return None


def _load_action() -> dict:
    root = _repo_root()
    if root is None:
        pytest.skip("repository root not reachable — backend-only checkout")
    parsed = yaml.safe_load((root / _ACTION_REL).read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "action.yml did not parse to a mapping"
    return parsed


def _doc_text() -> str:
    root = _repo_root()
    if root is None:
        pytest.skip("repository root not reachable — backend-only checkout")
    doc = root / _DOC_REL
    if not doc.is_file():
        pytest.skip(f"{doc} not present in this checkout")
    return doc.read_text(encoding="utf-8")


def _table_keys(markdown: str, heading: str) -> set[str]:
    """Collect the first column of the markdown table under *heading*.

    Cells are written as `` `name` `` — we take the backticked token so a
    descriptive column never leaks into the key set.
    """
    section = markdown.split(f"\n## {heading}\n", 1)
    assert len(section) == 2, f"no '## {heading}' section in {_DOC_REL.name}"
    body = section[1].split("\n## ", 1)[0]

    keys: set[str] = set()
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1].strip()
        match = re.fullmatch(r"`([a-z0-9-]+)`", first_cell)
        if match:
            keys.add(match.group(1))
    return keys


def test_documented_outputs_match_the_action() -> None:
    declared = set(_load_action().get("outputs") or {})
    documented = _table_keys(_doc_text(), "Outputs")

    assert declared == documented, (
        "the action's outputs and the docs' Outputs table disagree — "
        f"only in action.yml: {sorted(declared - documented)}; "
        f"only in the docs: {sorted(documented - declared)}"
    )


def test_documented_inputs_match_the_action() -> None:
    declared = set(_load_action().get("inputs") or {})
    documented = _table_keys(_doc_text(), "Inputs")

    assert declared == documented, (
        "the action's inputs and the docs' Inputs table disagree — "
        f"only in action.yml: {sorted(declared - documented)}; "
        f"only in the docs: {sorted(documented - declared)}"
    )


def test_every_output_is_actually_written_by_its_step() -> None:
    """A declared output whose step never writes the key resolves to "".

    ``value: ${{ steps.gate.outputs.critical_cve_count }}`` only carries data if
    the gate step also appends ``critical_cve_count=...`` to ``$GITHUB_OUTPUT``.
    Declaring one without the other is invisible in YAML and silent at runtime.
    """
    action = _load_action()
    steps = {s.get("id"): s for s in action["runs"]["steps"] if s.get("id")}

    unwritten: list[str] = []
    for name, spec in (action.get("outputs") or {}).items():
        ref = re.search(
            r"steps\.([a-z0-9_-]+)\.outputs\.([a-z0-9_]+)", str(spec.get("value", ""))
        )
        assert ref, f"output {name!r} does not reference a step output"
        step_id, key = ref.group(1), ref.group(2)
        assert step_id in steps, f"output {name!r} references unknown step {step_id!r}"
        if f"{key}=" not in (steps[step_id].get("run") or ""):
            unwritten.append(f"{name} -> steps.{step_id}.outputs.{key}")

    assert not unwritten, (
        "these outputs are declared but their step never writes the key, so they "
        f"resolve to an empty string: {unwritten}"
    )
