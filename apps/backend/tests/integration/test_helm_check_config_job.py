# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Guard: the config-preflight Job (job-check-config.yaml, #430) actually runs
where it is meant to, gets the env it needs, and has its own ServiceAccount.

Three ways this could quietly stop doing its job without any Python test
noticing: (1) its hook-weight drifts to run at or after job-migrate.yaml's
"-5", so a bad config surfaces after alembic has already connected rather
than before; (2) its ServiceAccount reference points at a name
serviceaccounts.yaml never renders, which fails the release at apply time
with a K8s-level error that says nothing about configuration; (3) it stops
receiving DATABASE_URL / REDIS_URL / SECRET_KEY / API_KEY_HMAC_SECRET, so
`core.config`'s accessors would raise for the wrong reason (missing var, not
a bad one) and the Job would "pass" by looking broken in an unrelated way.
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

_BASE_SET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "env.secret.apiKeyHmacSecret=ci-golden-hmac-key-abcdef0123456789",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]


def _helm_show_only_all(template: str) -> list[dict[str, Any]]:
    """Render one template file, returning every YAML document in it.

    Some templates (serviceaccounts.yaml) render one document per item in a
    `range` and join them with `---`; `yaml.safe_load` raises on more than
    one document, so this always uses `safe_load_all`.
    """
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    cmd = [
        HELM,
        "template",
        "trustedoss-golden",
        str(CHART_DIR),
        "--show-only",
        template,
        *_BASE_SET,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, (
        f"helm template failed for {template}:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _helm_show_only(template: str) -> dict[str, Any]:
    """Render a template file that is expected to produce exactly one document."""
    docs = _helm_show_only_all(template)
    assert len(docs) == 1, f"{template} rendered {len(docs)} documents, expected 1"
    return docs[0]


def _env_names(job: dict[str, Any]) -> set[str]:
    container = job["spec"]["template"]["spec"]["containers"][0]
    return {entry["name"] for entry in container.get("env", [])}


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_check_config_job_runs_before_migrate() -> None:
    check_config = _helm_show_only("templates/job-check-config.yaml")
    migrate = _helm_show_only("templates/job-migrate.yaml")

    check_config_weight = int(
        check_config["metadata"]["annotations"]["helm.sh/hook-weight"]
    )
    migrate_weight = int(migrate["metadata"]["annotations"]["helm.sh/hook-weight"])

    assert check_config_weight < migrate_weight, (
        "job-check-config.yaml must run BEFORE job-migrate.yaml: a bad "
        "config value should fail the release before alembic even connects, "
        "not after"
    )
    assert set(check_config["metadata"]["annotations"]["helm.sh/hook"].split(",")) == {
        "pre-install",
        "pre-upgrade",
    }


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_check_config_job_runs_the_check_config_module() -> None:
    check_config = _helm_show_only("templates/job-check-config.yaml")
    container = check_config["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "scripts.check_config"]
    # The backend image, not the worker image - core.config's dependencies
    # ship there and this needs nothing else.
    assert "trusca-backend" in container["image"]
    assert "trusca-backend-worker" not in container["image"]


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_check_config_job_has_a_dedicated_service_account() -> None:
    check_config = _helm_show_only("templates/job-check-config.yaml")
    accounts = _helm_show_only_all("templates/serviceaccounts.yaml")

    referenced = check_config["spec"]["template"]["spec"]["serviceAccountName"]
    rendered_names = {doc["metadata"]["name"] for doc in accounts}

    assert referenced in rendered_names, (
        f"job-check-config.yaml references ServiceAccount {referenced!r}, "
        f"which serviceaccounts.yaml does not render ({rendered_names}) - "
        "the release would fail at apply time with a K8s-level error, not a "
        "configuration one"
    )


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_check_config_job_gets_the_same_env_the_backend_deployment_gets() -> None:
    """Missing an env var here makes an accessor raise for the wrong reason
    (unset, not invalid) - this pins the set the backend Deployment's own
    wiring test suite (test_helm_api_key_hmac_secret_wiring.py) already
    guards per-variable, so a regression in either shows up in both."""
    check_config = _helm_show_only("templates/job-check-config.yaml")
    required = {
        "DATABASE_URL",
        "DATABASE_URL_APP",
        "REDIS_URL",
        "SECRET_KEY",
        "API_KEY_HMAC_SECRET",
    }
    missing = required - _env_names(check_config)
    assert not missing, f"job-check-config.yaml is missing env var(s): {missing}"
