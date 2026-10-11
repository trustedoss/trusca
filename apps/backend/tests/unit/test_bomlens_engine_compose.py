# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The production compose wiring of the BomLens engine's Docker API.

The administrator guide (Dynamic scan executor, "Turn on the engine") promises
that the scan worker reaches Docker only through a filtered proxy, that the
proxy refuses exec, volumes, networks, swarm, system and build calls, that
nothing but the scan worker can reach it, and that it is off unless its
profile is started. Each promise is a line in docker-compose.yml that an edit
can drop without anything else failing, so each one is asserted here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.config import (
    bomlens_network,
    bomlens_stage1_read_only_root,
    bomlens_stage1_repositories,
    bomlens_stage1_timeout_seconds,
    bomlens_stage2_timeout_seconds,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _compose() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return document


def test_the_proxy_is_opt_in_pinned_and_refuses_everything_but_containers_and_images() -> None:
    proxy = _compose()["services"]["docker-socket-proxy"]
    assert proxy["profiles"] == ["bomlens-engine"]
    image = proxy["image"]
    assert ":" in image.rsplit("/", 1)[-1] and not image.endswith(":latest")
    env = {k: str(v) for k, v in proxy["environment"].items()}
    assert {k for k, v in env.items() if v == "1"} == {"CONTAINERS", "IMAGES", "POST", "DELETE"}
    for refused in ("EXEC", "VOLUMES", "NETWORKS", "SWARM", "SYSTEM", "BUILD", "AUTH"):
        assert env[refused] == "0", refused
    assert proxy["volumes"] == ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    assert proxy["cap_drop"] == ["ALL"]
    assert proxy["networks"] == ["docker-api"]
    assert "ports" not in proxy


def test_only_the_scan_worker_shares_the_proxy_network_and_it_has_no_route_out() -> None:
    document = _compose()
    network = document["networks"]["docker-api"]
    assert network["internal"] is True
    members = sorted(
        name
        for name, service in document["services"].items()
        if "docker-api" in (service or {}).get("networks", [])
    )
    assert members == ["docker-socket-proxy", "worker-scan"]


def test_no_service_but_the_proxy_and_the_edge_mounts_the_socket() -> None:
    """The worker never gets the raw socket; Traefik's read-only mount predates
    the engine and is for its own provider."""
    holders = sorted(
        name
        for name, service in _compose()["services"].items()
        if any("docker.sock" in str(v) for v in (service or {}).get("volumes", []))
    )
    assert holders == ["docker-socket-proxy", "traefik"]


def test_the_scan_worker_reaches_docker_through_the_proxy() -> None:
    worker = _compose()["services"]["worker-scan"]
    assert worker["environment"]["DOCKER_HOST"] == "tcp://docker-socket-proxy:2375"


def test_the_engine_settings_reach_the_worker_with_the_code_defaults(monkeypatch: Any) -> None:
    """Each key the guide lists is in the shared env anchor, and an unset .env
    gives the worker the same value as an unset variable gives the code
    (except the network, which compose sets to ``bridge`` on purpose)."""
    env = _compose()["x-backend-env"]
    for key in (
        "SCAN_ENGINE",
        "SCAN_BOMLENS_IMAGE",
        "SCAN_BOMLENS_DEEP_LICENSE_IMAGE",
        "SCAN_BOMLENS_NETWORK",
        "SCAN_BOMLENS_STAGE1_REPOSITORIES",
        "SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT",
        "SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS",
        "SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS",
        "SCAN_TRIVY_CACHE_VOLUME",
    ):
        assert key in env, key
    for key in (
        "SCAN_BOMLENS_NETWORK",
        "SCAN_BOMLENS_STAGE1_REPOSITORIES",
        "SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT",
        "SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS",
        "SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert env["SCAN_BOMLENS_NETWORK"] == "${SCAN_BOMLENS_NETWORK:-bridge}"
    assert bomlens_network() == ""
    assert env["SCAN_BOMLENS_STAGE1_REPOSITORIES"] == (
        "${SCAN_BOMLENS_STAGE1_REPOSITORIES:-" + ",".join(bomlens_stage1_repositories()) + "}"
    )
    assert env["SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT"] == (
        "${SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT:-"
        + str(bomlens_stage1_read_only_root()).lower()
        + "}"
    )
    assert env["SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS"] == (
        "${SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS:-" + str(bomlens_stage1_timeout_seconds()) + "}"
    )
    assert env["SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS"] == (
        "${SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS:-" + str(bomlens_stage2_timeout_seconds()) + "}"
    )
