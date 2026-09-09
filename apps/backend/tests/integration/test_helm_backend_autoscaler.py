# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
#431: chart render golden for ``backend.autoscaling``.

CPU-only, same shape as ``worker.default.autoscaling`` (hpa-worker-default.yaml)
-- there is no queue-depth mode here, unlike ``worker.scan.autoscaling``
(test_helm_worker_queue_autoscaler.py), since the backend is a stateless
FastAPI tier whose CPU draw tracks its request load reasonably well.

Regression contract: off (default) renders no HorizontalPodAutoscaler and a
static ``replicas:`` on the backend Deployment; on renders the HPA with the
configured min/max/target and the Deployment omits ``replicas:`` so the HPA
owns the count (same pattern deployment-worker-default.yaml already has).

Renders `charts/trustedoss` with `helm template` (skipped, not failed, when
`helm` is unavailable, same convention as the other chart render goldens).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CHART_DIR = REPO_ROOT / "charts" / "trustedoss"

HELM = shutil.which("helm")

_REQUIRED_SET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "env.secret.apiKeyHmacSecret=ci-golden-hmac-key-abcdef0123456789",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]


def _render_full(*extra_set: str) -> str:
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [HELM, "template", "trustedoss-golden", str(CHART_DIR), *_REQUIRED_SET, *extra_set]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert (
        result.returncode == 0
    ), f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return result.stdout


def _render_show_only(template: str, *extra_set: str) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_default_render_has_no_backend_hpa_and_static_replicas() -> None:
    manifest = _render_full()
    assert "kind: HorizontalPodAutoscaler" not in manifest

    deployment = yaml.safe_load(_render_show_only("templates/deployment-backend.yaml").stdout)
    assert deployment["spec"]["replicas"] == 2  # values.yaml backend.replicaCount


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_enabled_renders_the_hpa_and_deployment_omits_replicas() -> None:
    extra = ("--set", "backend.autoscaling.enabled=true")
    manifest = _render_full(*extra)
    assert "kind: HorizontalPodAutoscaler" in manifest

    hpa = yaml.safe_load(_render_show_only("templates/hpa-backend.yaml", *extra).stdout)
    assert hpa["apiVersion"] == "autoscaling/v2"
    assert hpa["spec"]["scaleTargetRef"]["kind"] == "Deployment"
    assert hpa["spec"]["scaleTargetRef"]["name"] == "trustedoss-golden-trustedoss-backend"
    assert hpa["spec"]["minReplicas"] == 2  # values.yaml backend.autoscaling.minReplicas
    assert hpa["spec"]["maxReplicas"] == 6  # values.yaml backend.autoscaling.maxReplicas
    metric = hpa["spec"]["metrics"][0]
    assert metric["type"] == "Resource"
    assert metric["resource"]["name"] == "cpu"
    assert metric["resource"]["target"]["averageUtilization"] == 75

    deployment = yaml.safe_load(
        _render_show_only("templates/deployment-backend.yaml", *extra).stdout
    )
    assert "replicas" not in deployment["spec"]


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_enabled_with_overrides_reflects_them() -> None:
    extra = (
        "--set",
        "backend.autoscaling.enabled=true",
        "--set",
        "backend.autoscaling.minReplicas=3",
        "--set",
        "backend.autoscaling.maxReplicas=10",
        "--set",
        "backend.autoscaling.targetCPUUtilizationPercentage=60",
    )
    hpa = yaml.safe_load(_render_show_only("templates/hpa-backend.yaml", *extra).stdout)
    assert hpa["spec"]["minReplicas"] == 3
    assert hpa["spec"]["maxReplicas"] == 10
    assert hpa["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 60
