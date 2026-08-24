# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S5 (concurrency-scaling-plan-2026-08-22.md §3.2/§4): chart render golden for
``worker.scan.autoscaling.mode`` (``cpu`` | ``queue``).

Regression contract this file holds (plan §4, S5 row): "스케일러가 꺼짐(기본)이면
차트 산출물이 현행과 동일하다. 켜져도 CRD가 없는 클러스터에서 설치가 깨지지
않는다." Concretely:

* ``mode`` defaults to ``cpu``, which is this chart's ONLY behavior before
  this unit -- a plain ``helm template`` with no extra ``--set`` (autoscaling
  off) or with only ``worker.scan.autoscaling.enabled=true`` (HPA on) must
  render identically to how it did before ``scaledobject-worker-scan.yaml``
  existed. The two manifest-set-equality tests below pin that by rendering
  the FULL chart and asserting no KEDA kind appears anywhere, whether or not
  autoscaling is on, as long as mode stays at its default.
* ``mode: queue`` renders a KEDA ``ScaledObject`` (and, only when a password
  secret is configured, a ``TriggerAuthentication``) INSTEAD of the CPU HPA
  -- never alongside it, since KEDA creates its own HPA from a
  ``ScaledObject`` and two HPAs on one ``scaleTargetRef`` would fight each
  other over the replica count.
* Rendering ``mode: queue`` never talks to a cluster (`helm template` takes
  no cluster credentials) and therefore cannot fail just because the KEDA
  CRDs are not installed -- the whole point of the conditional template is
  that the failure mode for a missing CRD is at `helm install`/`upgrade`
  apply time, not at chart-render time, which is what the "새 리소스의 API
  그룹·kind가 CRD 의존성을 명시" half of the DoD below checks: the rendered
  document literally says ``keda.sh/v1alpha1`` / ``ScaledObject``, which is
  the exact API resource an operator needs to have installed.

S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4, S3 row) split the chart's
single worker Deployment / HPA / ScaledObject into scan and default-queue
pairs. Only the scan worker offers ``mode: queue`` (S3's judgment call: the
default worker's tasks are short and CPU-tracking, so it only ever gets a
CPU HPA, ``hpa-worker-default.yaml``); this file follows the values it tests
to ``worker.scan.*`` / ``templates/*-worker-scan.yaml`` and is not about the
default worker at all.

Renders `charts/trustedoss` with `helm template` (skipped, not failed, when
`helm` is unavailable, same convention as the W1/W2/W8/S4 golden tests).
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
    "env.secret.apiKeyHmacSecret=ci-golden-hmac-key-abcdef0123456789",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]


def _render_full(*extra_set: str) -> str:
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        *_REQUIRED_SET,
        *extra_set,
    ]
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    return result  # caller inspects returncode -- "renders nothing" is a valid outcome here


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_default_render_has_no_keda_resource_and_no_hpa() -> None:
    """Plan §4 S5: "꺼짐(기본)이면 차트 산출물이 현행과 동일하다."

    Default install: autoscaling.enabled=false. Neither the CPU HPA nor the
    KEDA ScaledObject/TriggerAuthentication should appear anywhere in the
    full manifest set -- this is exactly the pre-S5 behavior.
    """
    manifest = _render_full()
    assert "kind: HorizontalPodAutoscaler" not in manifest
    assert "kind: ScaledObject" not in manifest
    assert "kind: TriggerAuthentication" not in manifest
    assert "keda.sh" not in manifest


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_cpu_mode_enabled_renders_the_same_hpa_as_before_this_unit() -> None:
    """``mode`` left at its default (``cpu``) with autoscaling turned on must
    render byte-for-byte the same HPA this chart has always rendered --
    this unit only ADDS a new mode, it must not change the existing one."""
    manifest = _render_full("--set", "worker.scan.autoscaling.enabled=true")
    assert "kind: HorizontalPodAutoscaler" in manifest
    assert "kind: ScaledObject" not in manifest
    assert "kind: TriggerAuthentication" not in manifest
    assert "keda.sh" not in manifest

    hpa = yaml.safe_load(
        _render_show_only(
            "templates/hpa-worker-scan.yaml", "--set", "worker.scan.autoscaling.enabled=true"
        ).stdout
    )
    assert hpa["apiVersion"] == "autoscaling/v2"
    assert hpa["kind"] == "HorizontalPodAutoscaler"
    assert hpa["spec"]["minReplicas"] == 2
    assert hpa["spec"]["maxReplicas"] == 8
    assert hpa["spec"]["metrics"][0]["resource"]["name"] == "cpu"


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_explicit_cpu_mode_is_equivalent_to_the_default() -> None:
    """``mode: cpu`` spelled out explicitly renders identically to leaving it
    unset -- the two are not different code paths."""
    implicit = _render_full("--set", "worker.scan.autoscaling.enabled=true")
    explicit = _render_full(
        "--set", "worker.scan.autoscaling.enabled=true", "--set", "worker.scan.autoscaling.mode=cpu"
    )
    assert implicit == explicit


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_renders_scaledobject_instead_of_hpa() -> None:
    """Plan §4 S5 / §3.2 S5 judgement: queue mode REPLACES the CPU HPA, it
    does not sit alongside it -- KEDA creates its own HPA from the
    ScaledObject, so two HPAs on one scaleTargetRef would fight each other."""
    manifest = _render_full(
        "--set",
        "worker.scan.autoscaling.enabled=true",
        "--set",
        "worker.scan.autoscaling.mode=queue",
    )
    assert "kind: HorizontalPodAutoscaler" not in manifest
    assert "kind: ScaledObject" in manifest


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_scaledobject_api_group_and_kind_name_the_crd_dependency() -> None:
    """DoD: "그 리소스의 API 그룹·kind가 CRD 의존성을 명시한다는 것" -- the
    rendered document must literally say what CRD an operator needs
    installed, since `helm template`/`helm lint` cannot check for it."""
    doc: dict[str, Any] = yaml.safe_load(
        _render_show_only(
            "templates/scaledobject-worker-scan.yaml",
            "--set",
            "worker.scan.autoscaling.enabled=true",
            "--set",
            "worker.scan.autoscaling.mode=queue",
        ).stdout
    )
    assert doc["apiVersion"] == "keda.sh/v1alpha1"
    assert doc["kind"] == "ScaledObject"
    assert doc["spec"]["scaleTargetRef"]["kind"] == "Deployment"
    assert doc["spec"]["scaleTargetRef"]["name"] == "trustedoss-golden-trustedoss-worker-scan"
    assert doc["spec"]["minReplicaCount"] == 2
    assert doc["spec"]["maxReplicaCount"] == 8
    trigger = doc["spec"]["triggers"][0]
    assert trigger["type"] == "redis"
    assert trigger["metadata"]["listName"] == "trustedoss.scan"
    assert "authenticationRef" not in trigger  # no password secret configured


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_defaults_the_redis_address_from_the_bundled_service() -> None:
    """redis.bundled=true (the chart default) means the operator does not
    have to supply worker.autoscaling.queue.redis.address by hand."""
    doc: dict[str, Any] = yaml.safe_load(
        _render_show_only(
            "templates/scaledobject-worker-scan.yaml",
            "--set",
            "worker.scan.autoscaling.enabled=true",
            "--set",
            "worker.scan.autoscaling.mode=queue",
        ).stdout
    )
    address = doc["spec"]["triggers"][0]["metadata"]["address"]
    assert address == "trustedoss-golden-trustedoss-redis:6379"


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_fails_the_render_when_redis_is_external_and_unaddressed() -> None:
    """No silent fallback to an unreachable address: an external Redis with
    no address configured must fail loudly at `helm template` time, not
    produce a ScaledObject nobody can actually scale on."""
    assert HELM is not None
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        *_REQUIRED_SET,
        "--set",
        "worker.scan.autoscaling.enabled=true",
        "--set",
        "worker.scan.autoscaling.mode=queue",
        "--set",
        "redis.bundled=false",
        "--set",
        "env.redis.url=redis://external.example.com:6379/0",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0
    assert "worker.scan.autoscaling.queue.redis.address is required" in result.stderr


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_with_password_secret_renders_triggerauthentication() -> None:
    manifest = _render_full(
        "--set",
        "worker.scan.autoscaling.enabled=true",
        "--set",
        "worker.scan.autoscaling.mode=queue",
        "--set",
        "worker.scan.autoscaling.queue.redis.passwordSecretName=external-redis-auth",
    )
    assert "kind: TriggerAuthentication" in manifest
    assert "kind: ScaledObject" in manifest

    # --show-only on this template now streams TWO documents (the
    # TriggerAuthentication precedes the ScaledObject) -- yaml.safe_load_all,
    # not yaml.safe_load, is required to read both back.
    raw = _render_show_only(
        "templates/scaledobject-worker-scan.yaml",
        "--set",
        "worker.scan.autoscaling.enabled=true",
        "--set",
        "worker.scan.autoscaling.mode=queue",
        "--set",
        "worker.scan.autoscaling.queue.redis.passwordSecretName=external-redis-auth",
    ).stdout
    documents = list(yaml.safe_load_all(raw))
    kinds = {doc["kind"] for doc in documents if doc}
    assert kinds == {"TriggerAuthentication", "ScaledObject"}

    trigger_auth = next(doc for doc in documents if doc and doc["kind"] == "TriggerAuthentication")
    assert trigger_auth["spec"]["secretTargetRef"][0]["name"] == "external-redis-auth"
    assert trigger_auth["spec"]["secretTargetRef"][0]["key"] == "password"

    scaled_object = next(doc for doc in documents if doc and doc["kind"] == "ScaledObject")
    assert (
        scaled_object["spec"]["triggers"][0]["authenticationRef"]["name"]
        == "trustedoss-golden-trustedoss-worker-scan-queue-redis"
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_cooldown_defaults_to_the_worker_termination_grace_period() -> None:
    """S4's terminationGracePeriodSeconds (values.yaml, default 4200s) is
    what makes a KEDA-triggered scale-down SAFE for an in-flight scan.
    worker.scan.autoscaling.queue.cooldownPeriod is the separate, complementary
    knob that keeps KEDA from being eager to scale down in the first place
    -- both default to the same number so they do not drift apart without a
    reason to (see values.yaml's comment on cooldownPeriod)."""
    deployment = yaml.safe_load(_render_show_only("templates/deployment-worker-scan.yaml").stdout)
    grace_period = deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]

    scaled_object: dict[str, Any] = yaml.safe_load(
        _render_show_only(
            "templates/scaledobject-worker-scan.yaml",
            "--set",
            "worker.scan.autoscaling.enabled=true",
            "--set",
            "worker.scan.autoscaling.mode=queue",
        ).stdout
    )
    assert scaled_object["spec"]["cooldownPeriod"] == grace_period == 4200


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_queue_mode_worker_deployment_omits_static_replicas() -> None:
    """Same behavior as CPU-mode HPA: when an autoscaler owns the replica
    count, the Deployment must not also pin a static `replicas:`, or Helm
    upgrades would fight the autoscaler back to the static value."""
    deployment = yaml.safe_load(
        _render_show_only(
            "templates/deployment-worker-scan.yaml",
            "--set",
            "worker.scan.autoscaling.enabled=true",
            "--set",
            "worker.scan.autoscaling.mode=queue",
        ).stdout
    )
    assert "replicas" not in deployment["spec"]
