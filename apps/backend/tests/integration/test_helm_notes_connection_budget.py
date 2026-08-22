# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
W2: Helm chart render golden. NOTES.txt's connection-budget section must
agree with `core.connection_budget`, the shared formula oracle
(concurrency-scaling-plan-2026-08-22.md §4, W2 row: ".env.example / NOTES.txt
/ chart all derive from the same function").

This is the "golden" half of the W2 DoD ("차트 렌더 골든 테스트에도 예산 관련
값을 포함시킨다") -- it actually renders the chart with `helm` (skipped, not
failed, when the binary is unavailable, matching this repo's convention of
skipif-ing on a missing real external tool rather than mocking one) and reads
back the numbers NOTES.txt computed in Go template arithmetic, rather than
re-deriving them from values.yaml the way test_connection_budget.py's
HELM_DEFAULT fixture does. The bug W2 fixes (NOTES.txt's backend line
missing the uvicorn-worker multiplier, answering 4x too small) would have
been caught by this exact comparison.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core.connection_budget import ConnectionBudget

REPO_ROOT = Path(__file__).resolve().parents[4]
CHART_DIR = REPO_ROOT / "charts" / "trustedoss"

HELM = shutil.which("helm")

# Values required for the chart to render at all (same placeholders
# .github/workflows/chart-release.yml's smoke-render step uses).
_REQUIRED_SET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]

_BUDGET_LINE_RE = {
    "backend": re.compile(r"^\s*backend\s*=.*=\s*(\d+)\s*$", re.MULTILINE),
    "worker": re.compile(r"^\s*worker\s*=.*=\s*(\d+)\s*$", re.MULTILINE),
    "beat": re.compile(r"^\s*beat\s*=.*=\s*(\d+)\s*$", re.MULTILINE),
    "total": re.compile(r"^\s*TOTAL\s*=\s*(\d+)\s", re.MULTILINE),
}


def _render_notes(*extra_set: str) -> str:
    """Run `helm install --dry-run=client` and return the NOTES: section text.

    `helm template` does not print NOTES.txt (it is an install-time concept).
    `helm install --dry-run` (bare, no value) DOES print NOTES but still
    reaches for a live cluster to validate/discover API capabilities against
    -- on a CI runner with no cluster configured that surfaces as "Kubernetes
    cluster unreachable" rather than a render result, which is what this test
    hit before switching to `--dry-run=client`: Helm 3.13+'s client-only mode
    skips every API-server round trip (discovery, capability checks) and
    renders + prints NOTES from local templates alone, which is exactly what
    a render golden test wants (no cluster, no kubeconfig, ever).
    """
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "install",
        "trustedoss-golden",
        str(CHART_DIR),
        "--dry-run=client",
        *_REQUIRED_SET,
        *extra_set,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert (
        result.returncode == 0
    ), f"helm install --dry-run=client failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    marker = "NOTES:"
    assert marker in result.stdout, f"no NOTES: section in helm output:\n{result.stdout}"
    return result.stdout.split(marker, 1)[1]


def _budget_lines(notes: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for key, pattern in _BUDGET_LINE_RE.items():
        match = pattern.search(notes)
        assert match, f"could not find a {key!r} connection-budget line in NOTES.txt:\n{notes}"
        values[key] = int(match.group(1))
    return values


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_notes_backend_line_includes_the_uvicorn_worker_multiplier() -> None:
    """The regression this whole file exists for.

    Before W2, NOTES.txt's backend line was `replicaCount x (size +
    maxOverflow)`, missing `uvicornWorkers` entirely. At the chart's own
    defaults (replicaCount=2, uvicornWorkers=4) that under-counts by exactly
    4x: 2 x (5+3) = 16 (the old, wrong answer) vs. the correct 2 x 4 x (5+3)
    = 64. Asserting the multiplied value (not just "some number") is what
    would have caught the original bug.
    """
    notes = _render_notes()
    lines = _budget_lines(notes)
    assert lines["backend"] == 64, (
        f"NOTES.txt backend line = {lines['backend']}; expected 64 "
        "(replicaCount=2 x uvicornWorkers=4 x (size=5 + maxOverflow=3)). "
        "16 would mean the uvicorn-worker multiplier regressed out again."
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_notes_matches_the_python_oracle_at_chart_defaults() -> None:
    """NOTES.txt's Go-template arithmetic and core.connection_budget agree.

    Builds the ConnectionBudget the same way test_connection_budget.py's
    HELM_DEFAULT fixture does (from values.yaml's documented defaults) and
    compares every line NOTES.txt prints against it.
    """
    notes = _render_notes()
    lines = _budget_lines(notes)

    budget = ConnectionBudget(
        name="charts/trustedoss values.yaml (rendered)",
        backend_replicas=2,
        uvicorn_workers=4,
        pool_size=5,
        max_overflow=3,
        worker_replicas=2,
        sync_pool_size=3,
        sync_max_overflow=3,
        max_connections=100,
    )
    assert lines["backend"] == budget.backend_conns
    assert lines["worker"] == budget.worker_conns
    assert lines["beat"] == budget.beat_conns
    assert lines["total"] == budget.total_connections
    assert not budget.over_budget
    assert "WARNING: the connection budget" not in notes


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_notes_warns_when_scaled_past_the_default_postgres_ceiling() -> None:
    """Scaling replicaCount past budget surfaces the operator-facing WARNING.

    Mirrors main.py's boot-time check (core.connection_budget.over_budget)
    at the Helm-template level: an operator running `helm template` /
    `helm install --dry-run` before ever starting a pod should see the same
    verdict the running backend would log.
    """
    notes = _render_notes("--set", "backend.replicaCount=4", "--set", "worker.replicaCount=6")
    lines = _budget_lines(notes)

    budget = ConnectionBudget(
        name="charts/trustedoss values.yaml (scaled)",
        backend_replicas=4,
        uvicorn_workers=4,
        pool_size=5,
        max_overflow=3,
        worker_replicas=6,
        sync_pool_size=3,
        sync_max_overflow=3,
        max_connections=100,
    )
    assert lines["total"] == budget.total_connections
    assert budget.over_budget
    assert "WARNING: the connection budget" in notes
