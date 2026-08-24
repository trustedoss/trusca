# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Guard: ``API_KEY_HMAC_SECRET`` (A5, ``core.config.api_key_hmac_secret``) is
actually wired into the four Helm workloads that run backend code, and the
chart-rendered Secret refuses to render a value derived from ``secretKey``
when the operator leaves ``env.secret.apiKeyHmacSecret`` blank.

Why this exists. ``tests/unit/test_prod_failclosed_secrets_compose_wiring.py``
covers the same defect class for Docker Compose (self-hosted install) but
explicitly scoped itself out of Helm ("a Helm-chart equivalent is a natural
follow-up but is out of scope for this guard"). A separate security-reviewer
follow-up on the A5 PR (concurrency-scaling-tracker.md §2, A5 Medium finding)
found that ``templates/secret.yaml`` silently derived
``API_KEY_HMAC_SECRET`` from ``secretKey`` (``sha256sum
"trustedoss-api-key-hmac-secret-v1:" + secretKey``) instead of failing the
render, the way ``secretKey`` itself already does. A derived value always
looks "explicitly set" to the backend's own ``APP_ENV != dev`` fail-closed
check, so that check could never fire against a chart-rendered Secret --
silently reopening the exact "one leaked secret exposes two credential
families" risk A5 set out to close. ``secret.yaml`` was changed to ``fail``
instead of deriving; this file is the regression guard for BOTH halves of
that fix:

1. the four Deployments (backend, worker-scan, worker-default, beat) render
   ``API_KEY_HMAC_SECRET`` sourced from the chart Secret when the value is
   set, and
2. the render FAILS with a clear, actionable message when it is unset --
   instead of silently succeeding with a derived value.
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

# The four Deployments that run backend code and therefore call
# core.config.api_key_hmac_secret() at request time (backend) or task time
# (worker-scan / worker-default / beat -- beat itself does not verify API
# keys, but it shares the same runtimeSecretEnv wiring in _helpers.tpl, so it
# is included here to guard that shared helper for all four callers at once).
DEPLOYMENT_TEMPLATES = {
    "backend": "templates/deployment-backend.yaml",
    "worker-scan": "templates/deployment-worker-scan.yaml",
    "worker-default": "templates/deployment-worker-default.yaml",
    "beat": "templates/deployment-beat.yaml",
}

# Set WITHOUT env.secret.apiKeyHmacSecret -- the render must fail against
# this set, and each per-workload render below adds apiKeyHmacSecret itself
# to prove the successful path.
_BASE_SET_NO_HMAC_SECRET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]

_HMAC_SECRET_VALUE = "ci-golden-hmac-key-abcdef0123456789"


def _helm_show_only(template: str, *extra_set: str) -> subprocess.CompletedProcess[str]:
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        "--show-only",
        template,
        *_BASE_SET_NO_HMAC_SECRET,
        *extra_set,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


def _render_deployment_with_hmac_secret(template: str) -> dict[str, Any]:
    result = _helm_show_only(
        template,
        "--set",
        f"env.secret.apiKeyHmacSecret={_HMAC_SECRET_VALUE}",
    )
    assert result.returncode == 0, (
        f"helm template failed with apiKeyHmacSecret set:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    doc: dict[str, Any] = yaml.safe_load(result.stdout)
    return doc


def _env_entry(
    deployment: dict[str, Any], container_name: str, var_name: str
) -> dict[str, Any] | None:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    for container in containers:
        if container["name"] != container_name:
            continue
        for entry in container.get("env", []):
            if entry["name"] == var_name:
                result: dict[str, Any] = entry
                return result
    return None


def _primary_container_name(deployment: dict[str, Any]) -> str:
    """First container in the pod spec -- every one of these four Deployments
    has exactly one application container (backend / worker / beat), unlike
    e.g. a sidecar-bearing Deployment elsewhere in the chart."""
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) >= 1
    name: str = containers[0]["name"]
    return name


# ---------------------------------------------------------------------------
# 1. API_KEY_HMAC_SECRET is wired into all four workloads when the value is set
# ---------------------------------------------------------------------------


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
@pytest.mark.parametrize("workload", sorted(DEPLOYMENT_TEMPLATES))
def test_api_key_hmac_secret_env_is_wired_into_each_workload(workload: str) -> None:
    """Every one of backend / worker-scan / worker-default / beat must carry
    an API_KEY_HMAC_SECRET env var sourced from the chart Secret's
    API_KEY_HMAC_SECRET key -- a container that never receives the var sees
    os.getenv(...) return None regardless of what the operator set, and the
    accessor's fail-closed RuntimeError fires at request/task time even
    though the deployment LOOKS fully configured.
    """
    deployment = _render_deployment_with_hmac_secret(DEPLOYMENT_TEMPLATES[workload])
    container_name = _primary_container_name(deployment)
    entry = _env_entry(deployment, container_name, "API_KEY_HMAC_SECRET")
    assert entry is not None, (
        f"{workload}: container {container_name!r} has no API_KEY_HMAC_SECRET "
        f"env var -- add it to trustedoss.runtimeSecretEnv (_helpers.tpl) and "
        f"confirm this Deployment includes that helper"
    )
    secret_ref = entry.get("valueFrom", {}).get("secretKeyRef", {})
    assert secret_ref.get("key") == "API_KEY_HMAC_SECRET", (
        f"{workload}: API_KEY_HMAC_SECRET env var is not sourced from the "
        f"chart Secret's API_KEY_HMAC_SECRET key (got: {entry!r})"
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_full_chart_render_includes_api_key_hmac_secret_for_all_workloads() -> None:
    """Same contract as the per-workload renders above, but over one
    no-`--show-only` render of the whole chart -- the "차트 렌더 골든" shape
    the other Helm guard tests in this directory already use, so a change
    that breaks only the full-chart composition (e.g. a helper included
    under the wrong indentation for one Deployment) is still caught.
    """
    assert HELM is not None
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        *_BASE_SET_NO_HMAC_SECRET,
        "--set",
        f"env.secret.apiKeyHmacSecret={_HMAC_SECRET_VALUE}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, f"helm template failed:\n{result.stderr}"
    occurrences = result.stdout.count("name: API_KEY_HMAC_SECRET")
    assert occurrences >= len(DEPLOYMENT_TEMPLATES), (
        f"expected API_KEY_HMAC_SECRET wired into all {len(DEPLOYMENT_TEMPLATES)} "
        f"workloads, found {occurrences} occurrences in the full render"
    )


# ---------------------------------------------------------------------------
# 2. The render FAILS (not: silently derives a value) when unset
# ---------------------------------------------------------------------------


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_render_fails_when_api_key_hmac_secret_is_unset() -> None:
    """The regression this whole file exists to prevent: an operator who
    leaves env.secret.apiKeyHmacSecret blank must get a failed release, not
    a Secret silently carrying a value derived from secretKey (which would
    make core.config.api_key_hmac_secret's fail-closed branch unreachable
    from a chart install, reopening the credential-family coupling A5 set
    out to close).
    """
    result = _helm_show_only("templates/secret.yaml")
    assert result.returncode != 0, (
        "helm template succeeded with env.secret.apiKeyHmacSecret unset -- "
        "expected the render to fail via the `fail` guard in secret.yaml"
    )
    assert "env.secret.apiKeyHmacSecret is required" in result.stderr, (
        f"render failed as expected but not with the apiKeyHmacSecret guard "
        f"message -- got:\n{result.stderr}"
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_render_fails_before_deriving_any_value_from_secret_key() -> None:
    """Same failure, asserted from the full-chart render (no --show-only) so
    a future change that moves the `fail` guard somewhere only reached via a
    specific --show-only path cannot silently reopen the gap.
    """
    assert HELM is not None
    cmd = [HELM, "template", "trustedoss-golden", str(CHART_DIR), *_BASE_SET_NO_HMAC_SECRET]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0, (
        "full chart render succeeded with env.secret.apiKeyHmacSecret unset -- "
        "the whole release must fail, not just the Secret template in isolation"
    )
    assert "env.secret.apiKeyHmacSecret is required" in result.stderr


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_existing_secret_bypasses_the_apikeyhmacsecret_requirement() -> None:
    """When env.secret.existingSecret is set, the chart renders NO Secret of
    its own (the operator's pre-created Secret is used verbatim), so the
    apiKeyHmacSecret `fail` guard -- which only applies to the
    chart-rendered Secret -- must not fire even though
    env.secret.apiKeyHmacSecret is left blank. Mirrors secretKey's existing
    behaviour for the same existingSecret path.

    Uses a full-chart render rather than `--show-only templates/secret.yaml`:
    when secret.yaml emits nothing at all, `--show-only` on that path errors
    with "could not find template ... in chart" (a Helm quirk for templates
    with zero output under a show-only filter), which would make this test
    indistinguishable from the failure this file exists to catch. The
    full-chart render succeeding, with secret-postgres.yaml as the only
    rendered Secret, is the actual contract: existingSecret suppresses the
    app Secret without suppressing the bundled-Postgres one.
    """
    assert HELM is not None
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        "--set",
        "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
        "--set",
        "postgres.auth.password=ci-golden-pw",
        "--set",
        "ingress.host=trustedoss.ci-golden.example.com",
        "--set",
        "env.secret.existingSecret=trustedoss-prod-secrets",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, (
        f"expected the full render to succeed when existingSecret is set, "
        f"even with apiKeyHmacSecret blank:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "# Source: trustedoss/templates/secret.yaml" not in result.stdout, (
        "templates/secret.yaml rendered a Secret even though existingSecret "
        "was set -- it should emit nothing on this path"
    )
