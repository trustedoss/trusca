#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Run CI's STATIC checks: the lint, typecheck and shellcheck jobs (ER62).

Why this exists
---------------
There was no single way to run what CI runs, so everybody decided per change
what was worth running. Deciding by the shape of the change is how a test-only
edit skips mypy and the type error is found by CI instead, and that judgement
has to be made correctly every time for the habit to hold.

Two properties matter more than convenience:

1. **It cannot drift from CI quietly.** The table below is keyed by the
   workflow's own step names, and ``tests/unit/test_static_checks_match_ci.py``
   asserts every ``run:`` step in the covered jobs appears here. A new CI step,
   or a whole new job, fails that test rather than being silently absent from
   the local run.

2. **It never reports success for something it did not run.** A missing tool
   is a FAILURE, not a skip. A run that quietly covered half the checks and
   ended green would be worse than no runner at all: it would replace an
   explicit decision with a false belief.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: CI steps that are deliberately not run here, each with the reason. Recorded
#: rather than omitted so the contract test still sees them: a step missing
#: from both this map and CHECKS fails, which is what stops a real check being
#: dropped by accident. Carrying a reason is what keeps this from becoming a
#: quiet place to park something inconvenient.
STEPS_NOT_RUN_LOCALLY: dict[str, str] = {
    "Install backend dev deps": "the local toolchain is the developer's, not "
    "something this should install over",
    "Install frontend deps": "same; a missing node_modules is reported as a "
    "precondition with the npm ci remedy instead",
    "Install shellcheck": "installs an apt package; reported as a missing tool "
    "instead when it is absent",
    "Show shellcheck version": "prints a version for the CI log and asserts "
    "nothing",
    "Fetch the base branch for the em-dash diff": "deepens the CI clone; a "
    "local checkout already has the history the diff needs",
}


@dataclass(frozen=True)
class Check:
    """One CI step, and how to run it here."""

    #: The workflow step's ``name:``. The link between this table and CI.
    ci_step: str
    scope: str  # "backend" | "frontend"
    argv: list[str]
    cwd: Path = REPO_ROOT
    #: A token that must appear in the workflow step's ``run:`` text, so the
    #: command is compared and not just the step's existence.
    ci_command_token: str = ""
    #: Executables that must be on PATH.
    needs: list[str] = field(default_factory=list)
    #: Paths that must exist, with what to do when they do not. CI installs
    #: dependencies in its own step; locally they are the developer's, and a
    #: git worktree in particular starts without node_modules. Reported as a
    #: precondition rather than left to surface as a confusing tool error.
    needs_paths: list[tuple[Path, str]] = field(default_factory=list)


BACKEND = REPO_ROOT / "apps" / "backend"
FRONTEND = REPO_ROOT / "apps" / "frontend"

#: Every npm-driven check needs these installed first.
_NPM_DEPS = [(FRONTEND / "node_modules", "run `npm ci` in apps/frontend")]

CHECKS: list[Check] = [
    Check("Run ruff", "backend", ["ruff", "check", "."], BACKEND, "ruff check .", ["ruff"]),
    Check(
        "Run ai-review selftest",
        "backend",
        [sys.executable, "tools/ai-review/selftest.py"],
        REPO_ROOT,
        "tools/ai-review/selftest.py",
    ),
    Check("Run mypy", "backend", ["mypy", "."], BACKEND, "mypy .", ["mypy"]),
    Check(
        "Run tsc",
        "frontend",
        ["npm", "run", "typecheck"],
        FRONTEND,
        "npm run typecheck",
        ["npm"],
        _NPM_DEPS,
    ),
    Check(
        "Run eslint",
        "frontend",
        ["npm", "run", "lint"],
        FRONTEND,
        "npm run lint",
        ["npm"],
        _NPM_DEPS,
    ),
    Check(
        "Run i18n drift check",
        "frontend",
        ["npm", "run", "i18n:check"],
        FRONTEND,
        "npm run i18n:check",
        ["npm"],
        _NPM_DEPS,
    ),
    Check(
        "Run design-token lint",
        "frontend",
        ["npm", "run", "token:lint"],
        FRONTEND,
        "npm run token:lint",
        ["npm"],
        _NPM_DEPS,
    ),
    Check(
        "Run error-copy lint",
        "frontend",
        ["npm", "run", "problem:lint"],
        FRONTEND,
        "npm run problem:lint",
        ["npm"],
        _NPM_DEPS,
    ),
    Check(
        "Run Korean style lint",
        "frontend",
        ["node", "tools/ko-style/lint.mjs", "--all", "--fail-on", "S2"],
        REPO_ROOT,
        "tools/ko-style/lint.mjs",
        ["node"],
    ),
    Check(
        "Run em-dash lint",
        "frontend",
        ["node", "tools/em-dash/lint.mjs"],
        REPO_ROOT,
        "tools/em-dash/lint.mjs",
        ["node"],
    ),
    Check(
        "Run license-header selftest",
        "frontend",
        ["node", "tools/license-header/selftest.mjs"],
        REPO_ROOT,
        "tools/license-header/selftest.mjs",
        ["node"],
    ),
    Check(
        "Run license-header gate",
        "frontend",
        ["node", "tools/license-header/lint.mjs", "--all"],
        REPO_ROOT,
        "tools/license-header/lint.mjs",
        ["node"],
    ),
    Check(
        "Run release-refs selftest",
        "frontend",
        ["node", "tools/release-refs/selftest.mjs"],
        REPO_ROOT,
        "tools/release-refs/selftest.mjs",
        ["node"],
    ),
    Check(
        "Run release-refs gate",
        "frontend",
        ["node", "tools/release-refs/lint.mjs"],
        REPO_ROOT,
        "tools/release-refs/lint.mjs",
        ["node"],
    ),
    Check(
        "Run shellcheck (severity=warning)",
        "scripts",
        [
            "sh",
            "-c",
            "shellcheck --severity=warning scripts/*.sh deploy/hetzner/*.sh",
        ],
        REPO_ROOT,
        "shellcheck --severity=warning",
        ["shellcheck"],
    ),
]


def _run_one(check: Check) -> tuple[str, str]:
    """Run one check. Returns ``(outcome, detail)``; outcome is a status word."""
    absent = [remedy for path, remedy in check.needs_paths if not path.exists()]
    if absent:
        return "NOT SET UP", "; ".join(absent)
    missing = [tool for tool in check.needs if shutil.which(tool) is None]
    if missing:
        # NOT a skip. A tool that is not installed means this check did not
        # happen, and the summary has to say so in a way that fails the run.
        return "MISSING TOOL", f"{', '.join(missing)} not on PATH"
    print(f"\n\033[1m--- {check.ci_step} ({check.scope}) ---\033[0m", flush=True)
    result = subprocess.run(check.argv, cwd=check.cwd)
    return ("ok", "") if result.returncode == 0 else ("FAILED", f"exit {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=["all", "backend", "frontend", "scripts"],
        default="all",
        help="which scope to run; 'all' is every static check, not every CI job",
    )
    args = parser.parse_args()

    selected = [c for c in CHECKS if args.scope in ("all", c.scope)]
    results: list[tuple[Check, str, str]] = []
    for check in selected:
        outcome, detail = _run_one(check)
        results.append((check, outcome, detail))

    print("\n\033[1m=== summary ===\033[0m")
    for check, outcome, detail in results:
        mark = "ok  " if outcome == "ok" else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{mark}] {check.scope:8} {check.ci_step}{suffix}")

    not_ok = [(c, o, d) for c, o, d in results if o != "ok"]
    if not_ok:
        # The last line is the one people read. It says what did not run as
        # well as what failed, so "I ran the checks" is never true of a run
        # that quietly covered less than it looks like it did.
        print(
            f"\n\033[31m{len(not_ok)} of {len(results)} checks did not pass "
            f"({args.scope} scope). NOT the same as CI passing.\033[0m"
        )
        return 1
    print(f"\n\033[32mall {len(results)} checks passed ({args.scope} scope)\033[0m")
    if args.scope != "all":
        print(
            f"\033[33mnote: scope was '{args.scope}', so this is NOT everything "
            f"CI runs.\033[0m"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
