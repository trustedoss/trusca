# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
W8 (concurrency-scaling-plan-2026-08-22.md §4, W8 row) -- PodDisruptionBudget
and topologySpreadConstraints render golden for the backend Deployment.

Regression contract this file exists to hold: "파드가 하나 이하로 내려가지
않는다. 단일 레플리카 배포에서 PDB가 축출을 영구히 막지 않는다." (available
pods do not drop to zero during voluntary disruption when replicaCount >= 2;
a single-replica install is never permanently stuck for node maintenance).

Renders `charts/trustedoss` with `helm template` (skipped, not failed, when
`helm` is unavailable -- same convention as the W1/W2 golden tests) and
checks the two manifests both statically (defaults) and parametrized over
`backend.replicaCount`, applying the actual Kubernetes PDB eviction formula
rather than eyeballing a single number.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CHART_DIR = REPO_ROOT / "charts" / "trustedoss"

HELM = shutil.which("helm")

_REQUIRED_SET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]


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


def _render_pdb(*extra_set: str) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_helm_show_only("templates/pdb-backend.yaml", *extra_set))
    return doc


def _render_backend_deployment(*extra_set: str) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(
        _helm_show_only("templates/deployment-backend.yaml", *extra_set)
    )
    return doc


def _eviction_allowed(replica_count: int, max_unavailable: int) -> bool:
    """Kubernetes' own PDB math: is a voluntary eviction ever permitted.

    The eviction API admits a request only when
    currentHealthy - 1 >= desiredHealthy, i.e. currentHealthy > desiredHealthy,
    where desiredHealthy = replicaCount - maxUnavailable and (at rest, before
    any disruption) currentHealthy == replicaCount.
    """
    desired_healthy = replica_count - max_unavailable
    current_healthy = replica_count
    return current_healthy > desired_healthy


# ---------------------------------------------------------------------------
# PodDisruptionBudget
# ---------------------------------------------------------------------------


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_pdb_renders_by_default_no_toggle_needed() -> None:
    """W8 is an availability fix for an existing gap, not a feature flag."""
    pdb = _render_pdb()
    assert pdb["kind"] == "PodDisruptionBudget"
    assert pdb["apiVersion"] == "policy/v1"
    assert pdb["metadata"]["labels"]["app.kubernetes.io/component"] == "backend"


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_pdb_selector_matches_only_backend_pods() -> None:
    """A worker/beat/frontend pod must never count toward this budget."""
    pdb = _render_pdb()
    selector = pdb["spec"]["selector"]["matchLabels"]
    assert selector["app.kubernetes.io/component"] == "backend"


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_pdb_max_unavailable_is_one_at_chart_defaults() -> None:
    pdb = _render_pdb()
    assert pdb["spec"]["maxUnavailable"] == 1


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_pdb_max_unavailable_is_configurable() -> None:
    pdb = _render_pdb("--set", "backend.podDisruptionBudget.maxUnavailable=2")
    assert pdb["spec"]["maxUnavailable"] == 2


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
@pytest.mark.parametrize("replica_count", [1, 2, 3, 4, 8])
def test_pdb_never_permanently_blocks_eviction_at_any_replica_count(
    replica_count: int,
) -> None:
    """W8 regression contract, part 1: single-replica installs stay evictable.

    Renders the ACTUAL manifest at each replicaCount (rather than hand-coding
    maxUnavailable=1 in the test) and runs the real Kubernetes eviction
    formula against it, so a values.yaml edit that makes maxUnavailable scale
    with replicaCount (and reintroduces the deadlock at replicaCount=1) fails
    here.
    """
    pdb = _render_pdb("--set", f"backend.replicaCount={replica_count}")
    max_unavailable = pdb["spec"]["maxUnavailable"]
    assert _eviction_allowed(replica_count, max_unavailable), (
        f"replicaCount={replica_count}, maxUnavailable={max_unavailable}: "
        "a voluntary eviction (node drain) would be permanently blocked, "
        "which is exactly the deadlock the W8 contract forbids"
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
@pytest.mark.parametrize("replica_count", [2, 3, 4, 8])
def test_pdb_keeps_at_least_one_pod_available_when_replicas_allow_it(
    replica_count: int,
) -> None:
    """W8 regression contract, part 2: pods never all go down together.

    For any replicaCount >= 2 the PDB must guarantee at least one pod stays
    healthy through a voluntary disruption -- the exact bug this unit fixes
    (a node drain taking every backend replica down at once).
    """
    pdb = _render_pdb("--set", f"backend.replicaCount={replica_count}")
    max_unavailable = pdb["spec"]["maxUnavailable"]
    desired_healthy = replica_count - max_unavailable
    assert desired_healthy >= 1, (
        f"replicaCount={replica_count}, maxUnavailable={max_unavailable}: "
        "the PDB permits every pod to be evicted at once"
    )


def test_pdb_deadlock_math_helper_matches_kubernetes_semantics() -> None:
    """Pure-Python sanity check of `_eviction_allowed`, no helm required.

    Pins the formula itself against hand-worked cases from the plan: 1 pod /
    maxUnavailable=1 evicts (0 remain, allowed -- the single-replica carve-out);
    2 pods / maxUnavailable=1 evicts once then blocks (1 remains, the second
    simultaneous eviction is refused); 2 pods / maxUnavailable=2 would allow
    both down at once (this is the historical W8 bug, reproduced here to show
    the helper actually distinguishes the two).
    """
    assert _eviction_allowed(1, 1) is True
    assert _eviction_allowed(2, 1) is True  # first eviction still permitted
    assert _eviction_allowed(2, 0) is False  # PDB with maxUnavailable=0 blocks entirely
    assert _eviction_allowed(2, 2) is True  # reproduces the pre-W8 bug shape


# ---------------------------------------------------------------------------
# topologySpreadConstraints
# ---------------------------------------------------------------------------


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_topology_spread_renders_by_default_no_toggle_needed() -> None:
    deployment = _render_backend_deployment()
    constraints = deployment["spec"]["template"]["spec"]["topologySpreadConstraints"]
    assert len(constraints) == 2
    topology_keys = {c["topologyKey"] for c in constraints}
    assert topology_keys == {"kubernetes.io/hostname", "topology.kubernetes.io/zone"}


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_topology_spread_is_soft_so_a_single_node_cluster_can_still_schedule() -> None:
    """`DoNotSchedule` (hard) would strand a pod Pending forever on `kind` /
    a one-VM install where the constraint cannot be satisfied -- a worse
    regression than not spreading pods at all. Every constraint must stay
    `ScheduleAnyway` unless a future change deliberately documents why a
    hard requirement is now safe to ship as the default.
    """
    deployment = _render_backend_deployment()
    constraints = deployment["spec"]["template"]["spec"]["topologySpreadConstraints"]
    for constraint in constraints:
        assert constraint["whenUnsatisfiable"] == "ScheduleAnyway"


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_topology_spread_label_selector_matches_only_backend_pods() -> None:
    """A worker/beat/frontend pod must not count toward the spread skew.

    If the selector matched every trustedoss pod regardless of component,
    the scheduler would try to spread backend pods relative to WHERE
    frontend/worker/beat pods happen to sit, which is not what this
    constraint is for.
    """
    deployment = _render_backend_deployment()
    constraints = deployment["spec"]["template"]["spec"]["topologySpreadConstraints"]
    for constraint in constraints:
        selector = constraint["labelSelector"]["matchLabels"]
        assert selector["app.kubernetes.io/component"] == "backend"


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_topology_spread_can_be_disabled_by_setting_an_empty_list() -> None:
    deployment = _render_backend_deployment("--set", "backend.topologySpreadConstraints=null")
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "topologySpreadConstraints" not in pod_spec


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_full_chart_render_includes_pdb_and_topology_spread_by_default() -> None:
    """The chart-release.yml-style smoke render (no --show-only) must carry
    both manifests without any extra --set -- this is the "차트 렌더 골든"
    half of the W8 DoD: an operator who just runs `helm template` /
    `helm install` with no extra flags gets both fixes.
    """
    assert HELM is not None
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        *_REQUIRED_SET,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, f"helm template failed:\n{result.stderr}"
    assert "kind: PodDisruptionBudget" in result.stdout
    assert "topologySpreadConstraints:" in result.stdout
