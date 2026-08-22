# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
W2: Helm chart render golden for the connection budget
(concurrency-scaling-plan-2026-08-22.md §4, W2 row: ".env.example / NOTES.txt
/ chart all derive from the same function").

This is the "golden" half of the W2 DoD ("차트 렌더 골든 테스트에도 예산 관련
값을 포함시킨다"): it actually renders `charts/trustedoss` with `helm template`
(skipped, not failed, when the binary is unavailable, matching this repo's
convention of skipif-ing on a missing real external tool rather than mocking
one) and reads back the numbers the ConfigMap actually carries into every
pod, cross-checking them against `core.connection_budget`, the same oracle
`.env.example`'s worked examples derive from.

Why `helm template` and not `helm install --dry-run` / `helm install
--dry-run=client`: NOTES.txt is an install-time artifact, so it is tempting
to reach for `helm install --dry-run` to read it back. That does not work
cluster-free in Helm 3.x -- verified against the exact version this repo
pins (3.16.3, matching .github/workflows/ci.yml's `azure/setup-helm`
step and chart-release.yml): even `--dry-run=client`, whose own --help text
says "it will not attempt cluster connections", still performs a hard
`GET /version` preflight and fails with "Kubernetes cluster unreachable" on
a runner with no kubeconfig. `helm template`, by contrast, has never needed
a cluster in any Helm version -- it just cannot print NOTES.txt (Helm
excludes that one file from the manifest set on purpose). So this file reads
the SAME numbers a different way: the ConfigMap every workload mounts via
`envFrom`, rendered with `--show-only templates/configmap-env.yaml`.

test_notes_template_source_multiplies_backend_by_uvicorn_workers below
covers the actual historical bug (NOTES.txt's backend line silently
dropping the uvicorn-worker multiplier) by asserting on NOTES.txt's own Go
template source text, since that specific arithmetic cannot be exercised by
`helm template` (the .txt file it lives in is never rendered by that
command) and executing it via `helm install` is the cluster-dependent path
this file avoids.
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
NOTES_PATH = CHART_DIR / "templates" / "NOTES.txt"

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

_ENV_LINE_RE = re.compile(r'^\s*([A-Z][A-Z0-9_]*):\s*"?(-?\d+)"?\s*$', re.MULTILINE)


def _render_configmap_env(*extra_set: str) -> dict[str, int]:
    """Render `templates/configmap-env.yaml` and parse its integer-valued keys.

    No cluster, no kubeconfig: `helm template` renders purely from the chart
    + values, which is exactly what a render golden test wants.
    """
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        "--show-only",
        "templates/configmap-env.yaml",
        *_REQUIRED_SET,
        *extra_set,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert (
        result.returncode == 0
    ), f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return {key: int(value) for key, value in _ENV_LINE_RE.findall(result.stdout)}


def _budget_from_configmap(env: dict[str, int], *, max_connections: int = 100) -> ConnectionBudget:
    return ConnectionBudget(
        name="charts/trustedoss configmap-env.yaml (rendered)",
        backend_replicas=env["CONN_BUDGET_BACKEND_REPLICAS"],
        uvicorn_workers=env["CONN_BUDGET_UVICORN_WORKERS"],
        pool_size=env["DB_POOL_SIZE"],
        max_overflow=env["DB_MAX_OVERFLOW"],
        worker_replicas=env["CONN_BUDGET_WORKER_REPLICAS"],
        sync_pool_size=env["DB_SYNC_POOL_SIZE"],
        sync_max_overflow=env["DB_SYNC_MAX_OVERFLOW"],
        max_connections=max_connections,
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_configmap_conn_budget_hints_match_backend_and_worker_replica_counts() -> None:
    """The W2 fleet-shape hints track the SAME replica counts NOTES.txt reads.

    `CONN_BUDGET_BACKEND_REPLICAS` / `CONN_BUDGET_WORKER_REPLICAS` are wired
    from `.Values.backend.replicaCount` / `.Values.worker.replicaCount` in
    configmap-env.yaml -- the same values NOTES.txt's formula multiplies.
    Confirms that wiring survives a real render at the chart's own defaults.
    """
    env = _render_configmap_env()
    assert env["CONN_BUDGET_BACKEND_REPLICAS"] == 2  # values.yaml backend.replicaCount
    assert env["CONN_BUDGET_WORKER_REPLICAS"] == 2  # values.yaml worker.replicaCount
    assert env["CONN_BUDGET_UVICORN_WORKERS"] == 4  # values.yaml backend.uvicornWorkers


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_configmap_matches_the_python_oracle_at_chart_defaults() -> None:
    """The rendered pool + fleet-shape env vars agree with core.connection_budget.

    Builds the ConnectionBudget straight from what `helm template` actually
    put in the ConfigMap -- not from a hand-copied fixture -- so a values.yaml
    edit that changes the numbers without a matching test update fails here.
    """
    env = _render_configmap_env()
    budget = _budget_from_configmap(env)

    # Same numbers test_connection_budget.py's HELM_DEFAULT fixture encodes
    # by hand; this is the render-time confirmation that fixture is accurate.
    assert budget.backend_replicas == 2
    assert budget.uvicorn_workers == 4
    assert budget.pool_size == 5
    assert budget.max_overflow == 3
    assert budget.worker_replicas == 2
    assert budget.sync_pool_size == 3
    assert budget.sync_max_overflow == 3
    assert budget.backend_conns == 64  # 2 x 4 x (5 + 3)
    assert budget.worker_conns == 12  # 2 x (3 + 3)
    assert budget.beat_conns == 6  # 1 x (3 + 3)
    assert budget.total_connections == 82
    assert not budget.over_budget


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_configmap_reflects_replicacount_overrides() -> None:
    """Scaling `--set backend.replicaCount=N` changes the rendered hint too.

    Mirrors the arithmetic NOTES.txt's WARNING branch (and main.py's
    boot-time check) key off: scaling past default max_connections=100 is
    something the rendered ConfigMap must be ABLE to reflect, since that is
    what an operator's real `helm upgrade --set ...` would ship to every pod.
    """
    env = _render_configmap_env("--set", "backend.replicaCount=4", "--set", "worker.replicaCount=6")
    budget = _budget_from_configmap(env)
    assert budget.backend_replicas == 4
    assert budget.worker_replicas == 6
    assert budget.total_connections == 4 * 4 * 8 + 6 * 6 + 6  # 128 + 36 + 6 = 170
    assert budget.over_budget


def test_notes_template_source_multiplies_backend_by_uvicorn_workers() -> None:
    """Guards the actual historical bug directly on NOTES.txt's source text.

    Before W2, the backend line was `replicaCount x (size + maxOverflow)` --
    missing `uvicornWorkers` entirely, silently answering 4x too small at the
    chart's own defaults. This does not need `helm` at all: it reads the
    template file and asserts its Go-template arithmetic actually multiplies
    by `.Values.backend.uvicornWorkers` before printing the backend line, the
    same way `.env.example`'s copy of the formula already did.
    """
    source = NOTES_PATH.read_text()
    assert "backend.uvicornWorkers" in source, (
        "NOTES.txt no longer references backend.uvicornWorkers at all -- "
        "the connection-budget formula regressed to the pre-W2 shape"
    )
    # The $backendConns assignment is the one line responsible for the
    # historical bug; pin its shape so a future edit cannot quietly drop the
    # uvicorn-worker factor while leaving the variable name (and this file's
    # weaker "mentions uvicornWorkers somewhere" check above) intact.
    backend_conns_line = next(
        line for line in source.splitlines() if "$backendConns" in line and ":=" in line
    )
    assert (
        "backend.uvicornWorkers" in backend_conns_line
    ), f"$backendConns is computed without backend.uvicornWorkers: {backend_conns_line!r}"
    assert (
        "backend.replicaCount" in backend_conns_line
    ), f"$backendConns is computed without backend.replicaCount: {backend_conns_line!r}"
