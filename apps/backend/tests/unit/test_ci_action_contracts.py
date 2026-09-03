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
        ref = re.search(r"steps\.([a-z0-9_-]+)\.outputs\.([a-z0-9_]+)", str(spec.get("value", "")))
        assert ref, f"output {name!r} does not reference a step output"
        step_id, key = ref.group(1), ref.group(2)
        assert step_id in steps, f"output {name!r} references unknown step {step_id!r}"
        if f"{key}=" not in (steps[step_id].get("run") or ""):
            unwritten.append(f"{name} -> steps.{step_id}.outputs.{key}")

    assert not unwritten, (
        "these outputs are declared but their step never writes the key, so they "
        f"resolve to an empty string: {unwritten}"
    )


# ---------------------------------------------------------------------------
# Release workflow — the version the image states about itself.
# ---------------------------------------------------------------------------
_RELEASE_REL = Path(".github") / "workflows" / "release.yml"


def _release_workflow() -> dict:
    root = _repo_root()
    if root is None or not (root / _RELEASE_REL).is_file():
        pytest.skip("release.yml not present in this checkout")
    parsed = yaml.safe_load((root / _RELEASE_REL).read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "release.yml did not parse to a mapping"
    return parsed


def _meta_step(job: dict) -> dict:
    for step in job["steps"]:
        if step.get("id") == "meta":
            assert isinstance(step, dict)
            return step
    raise AssertionError("no step with id 'meta' in this job")


def test_injected_version_is_bare_semver() -> None:
    """The build job must resolve `outputs.version` through a semver pattern.

    ``docker/metadata-action`` falls back to the ref name when no ``tags``
    pattern is given, and the ref here is a ``vX.Y.Z`` git tag — so the build
    job injected ``TRUSTEDOSS_VERSION=v0.21.0`` while the image it was building
    got tagged ``0.21.0``. That value is not decoration: it is the tool version
    of every SBOM the deployment emits, the SLSA provenance, and the About
    screen, all of which are read as SemVer, where a leading ``v`` is not part
    of the grammar.

    Deleting the pattern restores the old behaviour silently — the workflow
    still runs, the images still publish, and only the strings inside them are
    wrong. Hence a guard rather than a comment.
    """
    workflow = _release_workflow()
    build_meta = _meta_step(workflow["jobs"]["build"])
    tags = build_meta.get("with", {}).get("tags", "")
    assert "pattern={{version}}" in tags, (
        "the build job's metadata-action has no {{version}} semver pattern, so "
        "steps.meta.outputs.version falls back to the ref name and "
        "TRUSTEDOSS_VERSION regains its 'v' prefix"
    )


def test_build_and_merge_agree_on_the_version_pattern() -> None:
    """Both jobs must derive the version the same way.

    The merge job's ``{{version}}`` is what becomes the published image tag; the
    build job's is what the image says it is. They are two computations of one
    number, and the defect this guards against was exactly them disagreeing.
    """
    workflow = _release_workflow()
    build_tags = _meta_step(workflow["jobs"]["build"])["with"]["tags"]
    merge_tags = _meta_step(workflow["jobs"]["merge"])["with"]["tags"]

    def version_lines(tags: str) -> list[str]:
        return [ln.strip() for ln in tags.splitlines() if "pattern={{version}}" in ln]

    assert version_lines(build_tags) == version_lines(merge_tags), (
        "build and merge derive {{version}} differently; the image would state "
        "a version other than the tag it is published under"
    )


# ---------------------------------------------------------------------------
# The component-outcome vocabulary, across all three places that name it
# ---------------------------------------------------------------------------
#
# `component_outcome` travels from the backend, through the gate response, into
# the action's shell, and is described in the docs a user reads. Four places
# spelling the same three values is exactly the shape hardening rule 2 exists
# for: each is green on its own while the set they agree on has quietly split,
# and the failure surfaces as an empty-scan warning that never prints because
# the action is matching a value the backend stopped emitting.


def _action_gate_step_script() -> str:
    action = _load_action()
    for step in action["runs"]["steps"]:
        if step.get("id") == "gate":
            return str(step.get("run", ""))
    raise AssertionError("action.yml has no step with id 'gate'")


def _case_blocks(script: str, *, subject: str) -> list[set[str]]:
    """The branch labels of each ``case ... esac`` block on ``subject``.

    Keyed on the variable being matched, because the gate step now branches on
    more than one vocabulary. Collecting every block regardless of subject
    would make each vocabulary's test demand the other's labels.
    """
    blocks: list[set[str]] = []
    current: set[str] | None = None
    wanted = f'case "${{{subject}}}" in'
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("case "):
            current = set() if stripped == wanted else None
            continue
        if stripped == "esac":
            if current is not None:
                blocks.append(current)
            current = None
            continue
        if current is None or not stripped.endswith(")"):
            continue
        for raw in stripped[:-1].split("|"):
            label = raw.strip()
            if re.fullmatch(r"[a-z_]+", label):
                current.add(label)
    return blocks


def test_component_outcome_vocabulary_is_the_same_everywhere() -> None:
    """Backend, action and docs must name the same three outcomes."""
    from services.scan_outcome import COMPONENT_OUTCOME_VALUES

    backend = set(COMPONENT_OUTCOME_VALUES)
    assert backend == {
        "components_found",
        "empty_no_manifests",
        "empty_with_manifests",
    }, "the published vocabulary changed; update every consumer below with it"

    # The two empty values are the ones the action branches on. Requiring the
    # ordinary value there too would be wrong: there is nothing to say about it.
    empty_values = backend - {"components_found"}

    # Per `case` block, not "does the string appear anywhere in the step".
    # The step has two of them, one writing the job-summary row and one
    # emitting the warning annotation, and a value has to be handled by both.
    # Checking the step as a whole passes on a typo in either, because the
    # other block still spells the value correctly, which is the exact drift
    # this guards: one branch stops matching and silently prints nothing.
    blocks = _case_blocks(_action_gate_step_script(), subject="outcome")
    assert len(blocks) >= 2, (
        "expected the gate step to branch on the outcome twice (summary row "
        f"and warning annotation); found {len(blocks)} case block(s)"
    )
    for index, block in enumerate(blocks):
        missing = empty_values - block
        assert not missing, (
            f"case block {index} in action.yml's gate step has no branch for "
            f"{sorted(missing)}, so a scan with that outcome goes unreported "
            "there"
        )

    doc = _doc_text()
    for value in backend:
        assert value in doc, (
            f"{value!r} is not described in github-actions.md, so a user "
            "reading the output has no way to interpret it"
        )


def test_the_gate_schema_and_the_classifier_agree() -> None:
    """The API description must enumerate what the classifier can produce.

    The description is what a consumer reads to decide which values to handle,
    so a value the classifier emits and the schema never names is a value
    nobody will branch on.
    """
    from schemas.policy_gate import GateResultResponse
    from services.scan_outcome import COMPONENT_OUTCOME_VALUES

    described = GateResultResponse.model_fields["component_outcome"].description or ""
    missing = [value for value in COMPONENT_OUTCOME_VALUES if value not in described]
    assert not missing, f"the gate response describes component_outcome without naming {missing}"


# ---------------------------------------------------------------------------
# The EPSS gate-outcome vocabulary, across the same four places
# ---------------------------------------------------------------------------
#
# Same shape as the component-outcome contract above and for the same reason.
# `epss_outcome` is what tells a reader that `epss_gate_count=0` means the axis
# judged nothing rather than that nothing was risky, so a value the backend
# emits and the action does not branch on is a build that silently prints an
# all-clear, which is the exact defect this vocabulary was added to close.


def test_epss_gate_outcome_vocabulary_is_the_same_everywhere() -> None:
    """Backend, action and docs must name the same four outcomes."""
    from services.epss_gate_outcome import EPSS_GATE_OUTCOME_VALUES

    backend = set(EPSS_GATE_OUTCOME_VALUES)
    assert backend == {
        "not_configured",
        "evaluated",
        "partial",
        "no_data",
    }, "the published vocabulary changed; update every consumer below with it"

    # The two the action branches on. `not_configured` and `evaluated` need no
    # branch: there is nothing to caveat about an axis that was off by choice
    # or that saw every score.
    caveated = backend - {"not_configured", "evaluated"}

    # Per `case` block, not "does the string appear in the step": the step
    # branches twice, once for the job-summary row and once for the warning
    # annotation, and a value has to be handled by both. Checking the step as a
    # whole passes on a typo in either.
    blocks = _case_blocks(_action_gate_step_script(), subject="epss_outcome")
    assert len(blocks) >= 2, (
        "expected the gate step to branch on the EPSS outcome twice (summary "
        f"row and warning annotation); found {len(blocks)} case block(s)"
    )
    for index, block in enumerate(blocks):
        missing = caveated - block
        assert not missing, (
            f"case block {index} in action.yml's gate step has no branch for "
            f"{sorted(missing)}, so a scan with that outcome goes unreported "
            "there"
        )

    doc = _doc_text()
    for value in backend:
        assert value in doc, (
            f"{value!r} is not described in github-actions.md, so a user "
            "reading the output has no way to interpret it"
        )


def test_the_gate_schema_describes_every_epss_outcome() -> None:
    """A value the classifier emits and the schema never names is unbranchable."""
    from schemas.policy_gate import GateResultResponse
    from services.epss_gate_outcome import EPSS_GATE_OUTCOME_VALUES

    described = GateResultResponse.model_fields["epss_outcome"].description or ""
    missing = [v for v in EPSS_GATE_OUTCOME_VALUES if v not in described]
    assert not missing, f"the gate response describes epss_outcome without naming {missing}"


def test_the_gate_schema_describes_every_missing_data_policy() -> None:
    """Same for the policy vocabulary: an unnamed value is one nobody sets."""
    from schemas.policy_gate import GateResultResponse
    from services.epss_gate_outcome import EPSS_ON_MISSING_DATA_VALUES

    described = (
        GateResultResponse.model_fields["epss_on_missing_data"].description or ""
    )
    missing = [v for v in EPSS_ON_MISSING_DATA_VALUES if v not in described]
    assert not missing, (
        f"the gate response describes epss_on_missing_data without naming {missing}"
    )
