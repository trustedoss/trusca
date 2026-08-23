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
one) and reads back the numbers the ConfigMap AND the backend Deployment
actually carry into every pod, cross-checking them against
`core.connection_budget`, the same oracle `.env.example`'s worked examples
derive from. UVICORN_WORKERS lives on the backend Deployment specifically
(W1: it is a real per-container knob, not a value worker/beat pods need),
while the two remaining fleet-shape hints stay on the shared ConfigMap (W2:
still informational, since no env var lets a container learn its own
replica count), so this file renders both templates and merges what they
carry.

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
`envFrom` (`--show-only templates/configmap-env.yaml`) plus the backend
Deployment's own `env:` entry for UVICORN_WORKERS
(`--show-only templates/deployment-backend.yaml`).

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
# The backend Deployment's `env:` is a YAML list (`- name: X` / `value: "N"`
# on the next line), not the flat `KEY: "value"` map configmap-env.yaml
# renders as, so it needs its own pattern.
_ENV_LIST_ITEM_RE = re.compile(r'-\s*name:\s*([A-Z][A-Z0-9_]*)\s*\n\s*value:\s*"?(-?\d+)"?')


def _helm_show_only(template: str, *extra_set: str) -> str:
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        "--show-only",
        template,
        *_REQUIRED_SET,
        *extra_set,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert (
        result.returncode == 0
    ), f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return result.stdout


def _render_env(*extra_set: str) -> dict[str, int]:
    """Render the shared ConfigMap AND the backend Deployment's own `env:`.

    No cluster, no kubeconfig: `helm template` renders purely from the chart
    + values, which is exactly what a render golden test wants. Merging the
    two is safe -- W1 moved UVICORN_WORKERS off the ConfigMap onto the
    backend Deployment specifically, so the key sets do not collide.
    """
    configmap_out = _helm_show_only("templates/configmap-env.yaml", *extra_set)
    backend_out = _helm_show_only("templates/deployment-backend.yaml", *extra_set)
    env = dict(_ENV_LINE_RE.findall(configmap_out))
    env.update(_ENV_LIST_ITEM_RE.findall(backend_out))
    return {key: int(value) for key, value in env.items()}


def _budget_from_env(env: dict[str, int], *, max_connections: int = 100) -> ConnectionBudget:
    return ConnectionBudget(
        name="charts/trustedoss (rendered)",
        backend_replicas=env["CONN_BUDGET_BACKEND_REPLICAS"],
        uvicorn_workers=env["UVICORN_WORKERS"],
        pool_size=env["DB_POOL_SIZE"],
        max_overflow=env["DB_MAX_OVERFLOW"],
        worker_replicas=env["CONN_BUDGET_WORKER_REPLICAS"],
        sync_pool_size=env["DB_SYNC_POOL_SIZE"],
        sync_max_overflow=env["DB_SYNC_MAX_OVERFLOW"],
        max_connections=max_connections,
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_rendered_env_matches_backend_and_worker_replica_counts() -> None:
    """The W1 knob and the W2 hints track the SAME counts NOTES.txt reads.

    `UVICORN_WORKERS` (backend Deployment) and `CONN_BUDGET_BACKEND_REPLICAS`
    / `CONN_BUDGET_WORKER_REPLICAS` (shared ConfigMap) are wired from
    `.Values.backend.uvicornWorkers` / `.Values.backend.replicaCount` /
    (post-S3) `.Values.worker.scan.replicaCount` + `.Values.worker.default.replicaCount`
    -- the same values NOTES.txt's formula multiplies. Confirms that wiring
    survives a real render at the chart's own defaults.
    """
    env = _render_env()
    assert env["CONN_BUDGET_BACKEND_REPLICAS"] == 2  # values.yaml backend.replicaCount
    # S3: worker.scan.replicaCount(2) + worker.default.replicaCount(1) = 3.
    assert env["CONN_BUDGET_WORKER_REPLICAS"] == 3
    assert env["UVICORN_WORKERS"] == 4  # values.yaml backend.uvicornWorkers


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_rendered_env_matches_the_python_oracle_at_chart_defaults() -> None:
    """The rendered pool + fleet-shape env vars agree with core.connection_budget.

    Builds the ConnectionBudget straight from what `helm template` actually
    rendered -- not from a hand-copied fixture -- so a values.yaml edit that
    changes the numbers without a matching test update fails here.
    """
    env = _render_env()
    budget = _budget_from_env(env)

    # Same numbers test_connection_budget.py's HELM_DEFAULT fixture encodes
    # by hand; this is the render-time confirmation that fixture is accurate.
    #
    # S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4): the chart's single
    # `worker` Deployment split into `worker.scan` (replicaCount 2, unchanged
    # from the pre-split default) and `worker.default` (replicaCount 1, new).
    # CONN_BUDGET_WORKER_REPLICAS (configmap-env.yaml) now renders their SUM
    # (3), not just the scan worker's count, since core.connection_budget's
    # ConnectionBudget treats "worker replicas" as one uniform pool: every
    # replica of either kind opens the same DB_SYNC_POOL_SIZE +
    # DB_SYNC_MAX_OVERFLOW connections from the same shared ConfigMap.
    assert budget.backend_replicas == 2
    assert budget.uvicorn_workers == 4
    assert budget.pool_size == 5
    assert budget.max_overflow == 3
    assert budget.worker_replicas == 3  # worker.scan(2) + worker.default(1)
    assert budget.sync_pool_size == 3
    assert budget.sync_max_overflow == 3
    assert budget.backend_conns == 64  # 2 x 4 x (5 + 3)
    assert budget.worker_conns == 18  # 3 x (3 + 3)
    assert budget.beat_conns == 6  # 1 x (3 + 3)
    assert budget.total_connections == 88
    assert not budget.over_budget


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_rendered_env_reflects_replicacount_overrides() -> None:
    """Scaling `--set backend.replicaCount=N` changes the rendered hints too.

    Mirrors the arithmetic NOTES.txt's WARNING branch (and main.py's
    boot-time check) key off: scaling past default max_connections=100 is
    something the rendered manifests must be ABLE to reflect, since that is
    what an operator's real `helm upgrade --set ...` would ship to every pod.

    S3: `worker.replicaCount` no longer exists (split into `worker.scan.*` /
    `worker.default.*`), so this overrides both halves: 4 + 2 = 6, the same
    combined worker-replica total the pre-split test pinned, so the expected
    numbers below are unchanged from before the split.
    """
    env = _render_env(
        "--set",
        "backend.replicaCount=4",
        "--set",
        "worker.scan.replicaCount=4",
        "--set",
        "worker.default.replicaCount=2",
    )
    budget = _budget_from_env(env)
    assert budget.backend_replicas == 4
    assert budget.worker_replicas == 6
    assert budget.total_connections == 4 * 4 * 8 + 6 * 6 + 6  # 128 + 36 + 6 = 170
    assert budget.over_budget


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_rendered_uvicorn_workers_is_backend_only_not_on_the_shared_configmap() -> None:
    """W1: UVICORN_WORKERS is backend-specific, unlike the other pool knobs.

    worker/beat pods never run uvicorn, so this must NOT leak onto the
    shared `-env` ConfigMap they also mount -- confirms deployment-backend.yaml
    carries it as its own `env:` entry rather than configmap-env.yaml. Checks
    the PARSED key set, not a raw substring search: configmap-env.yaml's own
    comments mention "UVICORN_WORKERS" by name (explaining why it moved), and
    a plain `in` check on the raw YAML would trip on that prose, not on an
    actual env entry.
    """
    configmap_out = _helm_show_only("templates/configmap-env.yaml")
    configmap_keys = dict(_ENV_LINE_RE.findall(configmap_out))
    assert "UVICORN_WORKERS" not in configmap_keys

    backend_out = _helm_show_only("templates/deployment-backend.yaml")
    backend_keys = dict(_ENV_LIST_ITEM_RE.findall(backend_out))
    assert "UVICORN_WORKERS" in backend_keys


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
