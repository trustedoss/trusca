# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The local check runner must cover exactly what CI's lint/typecheck jobs run.

``tools/local-ci/run.py`` names the same commands the workflow does, which is a
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
RUNNER = REPO_ROOT / "tools" / "local-ci" / "run.py"

#: The jobs this runner claims to reproduce. Test jobs are deliberately out of
#: scope: they need a database and a long time, and pretending otherwise is the
#: false-confidence this file exists to prevent.
COVERED_JOBS = ("lint", "typecheck")


def _load_runner() -> Any:
    """Import the runner by path; it lives outside the backend package."""
    spec = importlib.util.spec_from_file_location("_local_ci_run", RUNNER)
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


def test_every_ci_step_is_accounted_for() -> None:
    """A new CI check must not silently be absent from the local runner."""
    runner = _load_runner()
    accounted = {c.ci_step for c in runner.CHECKS} | set(runner.INSTALL_STEPS)

    missing = set(_workflow_run_steps()) - accounted
    assert not missing, (
        f"ci.yml runs these steps that tools/local-ci/run.py does not know "
        f"about: {sorted(missing)}. Add them to CHECKS, or to INSTALL_STEPS if "
        f"they only install dependencies. Leaving them out means the local "
        f"runner reports success without having run them."
    )


def test_the_runner_claims_nothing_ci_does_not_run() -> None:
    """The other direction: a step renamed in CI leaves a stale entry here,
    and a stale entry is a check nobody is actually getting."""
    runner = _load_runner()
    in_ci = set(_workflow_run_steps())

    stale = ({c.ci_step for c in runner.CHECKS} | set(runner.INSTALL_STEPS)) - in_ci
    assert not stale, (
        f"tools/local-ci/run.py names steps ci.yml no longer has: {sorted(stale)}"
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


def test_install_steps_are_only_installs() -> None:
    """INSTALL_STEPS is the one exemption from running locally, so it must not
    become a place to park a real check that nobody wants to run."""
    steps = _workflow_run_steps()
    runner = _load_runner()
    for name in runner.INSTALL_STEPS:
        script = steps.get(name, "")
        assert any(
            marker in script for marker in ("pip install", "npm ci", "git fetch")
        ), (
            f"{name!r} is exempted from the local run as an install step, but "
            f"its script does not look like one: {script.strip()[:120]!r}"
        )


@pytest.mark.parametrize("scope", ["backend", "frontend"])
def test_each_scope_has_checks(scope: str) -> None:
    """A scope that selects nothing would run zero commands and report success."""
    runner = _load_runner()
    assert [c for c in runner.CHECKS if c.scope == scope], (
        f"no checks are declared for scope {scope!r}, so `--scope {scope}` "
        f"would pass without running anything"
    )
