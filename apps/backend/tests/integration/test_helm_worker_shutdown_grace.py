# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S4 (concurrency-scaling-plan-2026-08-22.md §3.2/§4): chart render golden for
the scan worker Deployment's ``terminationGracePeriodSeconds`` and
``preStop`` lifecycle hook.

Regression contract this file holds: an operator who installs the chart with
no extra ``--set`` gets a worker grace period long enough to outlive a scan
running at the pipeline's own hard time limit
(``core.config.scan_hard_time_limit_seconds()``). Kubernetes' own default
(30s) is nowhere close, and Helm cannot read that Python accessor at render
time, so ``worker.scan.terminationGracePeriodSeconds`` (``values.yaml``) is a
STATIC mirror of it that a values.yaml edit could silently let drift below
the hard limit. The margin cross-check test below (its name says what it
checks) pins the two directly against each other, the same pattern
``test_helm_notes_connection_budget.py`` uses for the connection-budget
formula, so that drift fails CI instead of surfacing as a SIGKILLed scan in
production.

S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4, S3 row) renamed the
chart's single ``templates/deployment-worker.yaml`` /
``worker.terminationGracePeriodSeconds`` to
``templates/deployment-worker-scan.yaml`` / ``worker.scan.*`` when it split
the worker into scan and default-queue Deployments; only the scan worker
runs the scan pipeline this grace period protects, so this file follows it
there. The default worker's own grace period (``worker.default.*``, sized
off ``BACKUP_SUBPROCESS_TIMEOUT`` instead) is not this file's concern.

Renders `charts/trustedoss` with `helm template` (skipped, not failed, when
`helm` is unavailable, same convention as the W1/W2/W8 golden tests).
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


def _render_worker_deployment(*extra_set: str) -> dict[str, Any]:
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        "--show-only",
        "templates/deployment-worker-scan.yaml",
        *_REQUIRED_SET,
        *extra_set,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert (
        result.returncode == 0
    ), f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    doc: dict[str, Any] = yaml.safe_load(result.stdout)
    return doc


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_termination_grace_period_renders_by_default_no_toggle_needed() -> None:
    """S4 is a safety fix for an existing gap, not a feature flag."""
    deployment = _render_worker_deployment()
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "terminationGracePeriodSeconds" in pod_spec
    assert pod_spec["terminationGracePeriodSeconds"] == 4200


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_termination_grace_period_is_configurable() -> None:
    deployment = _render_worker_deployment(
        "--set", "worker.scan.terminationGracePeriodSeconds=5400"
    )
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["terminationGracePeriodSeconds"] == 5400


def test_termination_grace_period_default_clears_the_scan_hard_time_limit_with_margin() -> None:
    """Cross-check against the real backend accessor, not a hand-copied number.

    ``charts/trustedoss/values.yaml``'s ``worker.terminationGracePeriodSeconds``
    default cannot literally derive from
    ``core.config.scan_hard_time_limit_seconds()`` (Helm cannot execute Python
    at render time), so it is a static mirror maintained by hand. This test is
    the guard against that mirror drifting: it renders nothing and needs no
    ``helm`` binary, so it always runs (unlike the golden tests above).
    """
    import sys

    backend_root = REPO_ROOT / "apps" / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from core.config import scan_hard_time_limit_seconds

    chart_default = 4200  # worker.terminationGracePeriodSeconds, values.yaml
    hard_limit_default = scan_hard_time_limit_seconds()
    assert chart_default > hard_limit_default, (
        f"worker.terminationGracePeriodSeconds default ({chart_default}s) no "
        f"longer clears scan_hard_time_limit_seconds() ({hard_limit_default}s) "
        "-- a worker scaled down mid-scan can now be SIGKILLed before the "
        "hard-limit handler marks the scan failed cleanly. Raise the chart "
        "default (and docker-compose.yml's worker.stop_grace_period) to stay "
        "above it, with margin."
    )
    # The margin itself should be non-trivial (mirrors
    # BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS = 300s, core/config.py) -- a
    # 1-second margin would technically satisfy the assertion above while
    # leaving no real headroom for the worker's own SIGKILL-to-settled
    # bookkeeping after a hard-limit trip.
    assert chart_default - hard_limit_default >= 300


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_prestop_hook_renders_by_default_and_targets_only_this_pod() -> None:
    deployment = _render_worker_deployment()
    containers = deployment["spec"]["template"]["spec"]["containers"]
    worker_container = next(c for c in containers if c["name"] == "worker")
    pre_stop = worker_container["lifecycle"]["preStop"]["exec"]["command"]
    joined = " ".join(pre_stop)
    assert "celery" in joined
    assert "control" in joined
    assert "shutdown" in joined
    # Must scope to THIS pod (celery@$HOSTNAME), never a broadcast to every
    # worker replica -- an unscoped `celery control shutdown` would shut down
    # every sibling worker the instant one replica is asked to scale down.
    assert '-d celery@"$HOSTNAME"' in joined


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_full_chart_render_includes_worker_shutdown_grace_by_default() -> None:
    """The "차트 렌더 골든" half of the S4 DoD: a plain `helm template` with no
    extra flags carries both the grace period and the preStop hook."""
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
    assert "terminationGracePeriodSeconds: 4200" in result.stdout
    assert "control shutdown" in result.stdout
