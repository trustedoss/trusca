# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The local check runner must cover exactly what CI's lint/typecheck jobs run.

``tools/static-checks/run.py`` names the same commands the workflow does, which is a
second copy of that list. The point of the runner is that somebody can trust it
instead of deciding per change what to run, and a copy that has quietly fallen
behind is worse than no runner: it answers the question wrongly rather than
leaving it open.

So the two are compared here, in both directions, keyed by the workflow's own
step names.

Parsed as YAML rather than matched with a regex. A workflow is structured data,
and reading structure with a pattern is how a step spread over several lines,
or a name containing an expression, silently stops being seen.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RUNNER = REPO_ROOT / "tools" / "static-checks" / "run.py"

#: The jobs this runner claims to reproduce.
COVERED_JOBS = ("lint", "typecheck", "shellcheck")

#: Every other job in ci.yml, and why it is not reproduced locally. Listing the
#: reason rather than only the name is the point: a job left out silently is an
#: accident, a job left out with a sentence is a decision. The test below
#: asserts these two lists together account for EVERY job, so adding a job to
#: ci.yml fails until somebody says which side it belongs on.
NOT_COVERED_JOBS = {
    "test": "needs a database and roughly 25 minutes; reproducing it is the "
    "false confidence this tool exists to avoid",
    "coverage-gate": "consumes the coverage artifacts the test job uploads, so "
    "it cannot run without that job",
    "e2e": "needs Playwright browsers and the whole stack running",
    "postgres-init-l1": "needs a Postgres container to assert the role "
    "provisioning contract",
    "image-scan": "builds and scans the worker image",
    "frontend-bundle-audit": "needs a production bundle built first",
    "docs-build": "builds the Docusaurus site in two languages",
    "nightly-alert": "has no run steps; it opens an issue on scheduled failures",
}


def _load_runner() -> Any:
    """Import the runner by path; it lives outside the backend package."""
    spec = importlib.util.spec_from_file_location("_static_checks_run", RUNNER)
    assert spec is not None and spec.loader is not None, f"cannot import {RUNNER}"
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves its own module by name, and
    # a module missing from sys.modules makes that lookup return None.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _workflow_run_steps() -> dict[str, str]:
    """``{step name: run script}`` for every run-step in the covered jobs."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps: dict[str, str] = {}
    for job_id in COVERED_JOBS:
        assert job_id in workflow["jobs"], (
            f"ci.yml no longer has a '{job_id}' job; this contract needs updating"
        )
        for step in workflow["jobs"][job_id].get("steps", []):
            if "run" not in step:
                continue
            name = step.get("name")
            assert name, (
                f"a run-step in job '{job_id}' has no name; this contract keys "
                f"on step names, so every run-step needs one"
            )
            steps[name] = step["run"]
    return steps


def test_every_ci_job_is_classified() -> None:
    """A new job must not be silently out of scope.

    The step-level contracts below only look inside COVERED_JOBS, so a third
    static-check job added to ci.yml would be outside every comparison and
    nothing would say so. Somebody has to decide where a new job belongs, and
    this is what makes them.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    in_ci = set(workflow["jobs"])
    classified = set(COVERED_JOBS) | set(NOT_COVERED_JOBS)

    unclassified = in_ci - classified
    assert not unclassified, (
        f"ci.yml has jobs this contract does not classify: {sorted(unclassified)}. "
        f"Add each to COVERED_JOBS (and to the runner), or to NOT_COVERED_JOBS "
        f"with the reason it cannot run locally."
    )
    stale = classified - in_ci
    assert not stale, (
        f"this contract classifies jobs ci.yml no longer has: {sorted(stale)}"
    )


def test_every_excluded_job_gives_a_reason() -> None:
    """An exclusion list without reasons becomes a place to drop things."""
    for job, reason in NOT_COVERED_JOBS.items():
        assert len(reason.split()) >= 5, (
            f"the reason {job!r} is out of scope is too short to be a reason: "
            f"{reason!r}"
        )


def test_every_ci_step_is_accounted_for() -> None:
    """A new CI check must not silently be absent from the local runner."""
    runner = _load_runner()
    accounted = {c.ci_step for c in runner.CHECKS} | set(runner.STEPS_NOT_RUN_LOCALLY)

    missing = set(_workflow_run_steps()) - accounted
    assert not missing, (
        f"ci.yml runs these steps that tools/static-checks/run.py does not know "
        f"about: {sorted(missing)}. Add them to CHECKS, or to INSTALL_STEPS if "
        f"they only install dependencies. Leaving them out means the local "
        f"runner reports success without having run them."
    )


def test_the_runner_claims_nothing_ci_does_not_run() -> None:
    """The other direction: a step renamed in CI leaves a stale entry here,
    and a stale entry is a check nobody is actually getting."""
    runner = _load_runner()
    in_ci = set(_workflow_run_steps())

    stale = ({c.ci_step for c in runner.CHECKS} | set(runner.STEPS_NOT_RUN_LOCALLY)) - in_ci
    assert not stale, (
        f"tools/static-checks/run.py names steps ci.yml no longer has: {sorted(stale)}"
    )


def test_each_local_command_matches_the_ci_command() -> None:
    """Names matching is not enough: a step could keep its name and change what
    it runs, and the local runner would keep running the old command."""
    runner = _load_runner()
    steps = _workflow_run_steps()

    mismatched: list[str] = []
    for check in runner.CHECKS:
        token = check.ci_command_token
        assert token, f"{check.ci_step} declares no ci_command_token to compare"
        if token not in steps.get(check.ci_step, ""):
            mismatched.append(f"{check.ci_step}: ci.yml no longer runs {token!r}")
    assert not mismatched, mismatched


def test_every_skipped_step_gives_a_reason() -> None:
    """STEPS_NOT_RUN_LOCALLY is the only exemption from running, so it must not
    become somewhere to park a real check nobody wants to wait for."""
    runner = _load_runner()
    for step, reason in runner.STEPS_NOT_RUN_LOCALLY.items():
        assert len(reason.split()) >= 5, (
            f"the reason {step!r} is not run locally is too short to be a "
            f"reason: {reason!r}"
        )


def test_no_linting_step_is_exempted_from_running() -> None:
    """The exemption is for installs and setup. A step whose script invokes a
    linter or a typechecker is a check, and parking it here would silence it
    while leaving the coverage test satisfied."""
    steps = _workflow_run_steps()
    runner = _load_runner()
    smells = ("ruff", "mypy", "eslint", "shellcheck --severity", "lint.mjs")
    for name in runner.STEPS_NOT_RUN_LOCALLY:
        script = steps.get(name, "")
        found = [s for s in smells if s in script]
        assert not found, (
            f"{name!r} is exempted from the local run but its script invokes "
            f"{found}, which makes it a check and not setup"
        )


@pytest.mark.parametrize("scope", ["backend", "frontend"])
def test_each_scope_has_checks(scope: str) -> None:
    """A scope that selects nothing would run zero commands and report success."""
    runner = _load_runner()
    assert [c for c in runner.CHECKS if c.scope == scope], (
        f"no checks are declared for scope {scope!r}, so `--scope {scope}` "
        f"would pass without running anything"
    )
