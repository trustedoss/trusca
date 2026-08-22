# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
W1 (concurrency-scaling-plan-2026-08-22.md §4, W1 row): vocabulary
consistency for the default uvicorn worker count.

"설정하지 않으면 워커 수가 현행 4다. compose·차트·이미지 셋이 같은 값을 쓴다"
(unset -> 4; compose, chart, and image all agree). Before W1 the image CMD
baked 4 in with no override point at all; now that UVICORN_WORKERS is a real
env var, the three places that each state "4 is the default" (the
Dockerfile CMD's shell fallback, the production compose file's own fallback
for the same var, and the Helm chart's `values.yaml`) are three independent
strings that could drift apart exactly the way NOTES.txt and .env.example
drifted in W2. This file is the regression guard for that.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCKERFILE_PROD = REPO_ROOT / "apps" / "backend" / "Dockerfile.prod"
DOCKER_COMPOSE_PROD = REPO_ROOT / "docker-compose.yml"
VALUES_YAML = REPO_ROOT / "charts" / "trustedoss" / "values.yaml"

# Matches the shell-form fallback `${UVICORN_WORKERS:-4}` -- present
# verbatim in both the Dockerfile CMD and the compose file's own
# `UVICORN_WORKERS: ${UVICORN_WORKERS:-4}` line.
_SHELL_FALLBACK_RE = re.compile(r"\$\{UVICORN_WORKERS:-(\d+)\}")
_VALUES_YAML_RE = re.compile(r"^\s*uvicornWorkers:\s*(\d+)\s*$", re.MULTILINE)


def _default_from_dockerfile_cmd() -> int:
    source = DOCKERFILE_PROD.read_text()
    match = _SHELL_FALLBACK_RE.search(source)
    assert match, (
        "Dockerfile.prod's CMD no longer has a "
        "${UVICORN_WORKERS:-N} shell fallback -- the W1 knob regressed"
    )
    return int(match.group(1))


def _default_from_prod_compose() -> int:
    source = DOCKER_COMPOSE_PROD.read_text()
    match = _SHELL_FALLBACK_RE.search(source)
    assert match, (
        "docker-compose.yml no longer sets "
        "UVICORN_WORKERS: ${UVICORN_WORKERS:-N} in the shared backend-env anchor"
    )
    return int(match.group(1))


def _default_from_helm_values() -> int:
    source = VALUES_YAML.read_text()
    match = _VALUES_YAML_RE.search(source)
    assert match, "charts/trustedoss/values.yaml no longer sets backend.uvicornWorkers"
    return int(match.group(1))


def test_dockerfile_compose_and_chart_agree_on_the_default() -> None:
    """The literal '4' in three independently-editable files must match."""
    image_default = _default_from_dockerfile_cmd()
    compose_default = _default_from_prod_compose()
    chart_default = _default_from_helm_values()
    assert image_default == compose_default == chart_default, (
        f"UVICORN_WORKERS defaults drifted apart: "
        f"Dockerfile.prod={image_default}, docker-compose.yml={compose_default}, "
        f"values.yaml={chart_default}"
    )


def test_the_shared_default_is_four() -> None:
    """Pins the actual number, not just "the three agree with each other".

    A regression that lowers all three in lock-step (e.g. someone "fixing" a
    perceived inefficiency without reading the plan) would pass the equality
    check above but still break the W1 contract, which names 4 explicitly.
    """
    assert _default_from_dockerfile_cmd() == 4
    assert _default_from_prod_compose() == 4
    assert _default_from_helm_values() == 4


@pytest.fixture(autouse=True)
def _clean_env() -> Iterator[None]:
    saved = os.environ.get("UVICORN_WORKERS")
    os.environ.pop("UVICORN_WORKERS", None)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("UVICORN_WORKERS", None)
        else:
            os.environ["UVICORN_WORKERS"] = saved


def test_the_python_accessor_agrees_with_the_static_default_too() -> None:
    """core.config.uvicorn_workers() is a fourth place this number lives.

    Unlike the other three (parsed from source text, since none of them run
    Python), this one is a live call -- included here so a single test file
    documents every place "4" has to stay in sync.
    """
    from core.config import uvicorn_workers

    assert uvicorn_workers() == _default_from_dockerfile_cmd() == 4
