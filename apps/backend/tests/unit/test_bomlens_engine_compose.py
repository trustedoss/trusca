# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The production compose wiring of the BomLens engine's Docker API.

The administrator guide (Dynamic scan executor, "Turn on the engine") promises
that only the engine worker reaches Docker, and only through a filtered proxy,
that the proxy refuses exec, volumes, networks, swarm, system and build calls,
that nothing but the engine worker can reach it, and that both start with the
stack (the BomLens engine is the default). The demo box holds them back behind
a profile and keeps the in-process engine. Each promise is a line in
docker-compose.yml that an edit can drop without anything else failing, so
each one is asserted here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from core.config import (
    bomlens_image_scan_timeout_seconds,
    bomlens_network,
    bomlens_stage1_image_prefix,
    bomlens_stage1_read_only_root,
    bomlens_stage1_repositories,
    bomlens_stage1_timeout_seconds,
    bomlens_stage2_timeout_seconds,
    scan_container_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _compose() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return document


def test_the_proxy_is_always_started_pinned_and_refuses_all_but_containers_and_images() -> None:
    proxy = _compose()["services"]["docker-socket-proxy"]
    assert "profiles" not in proxy
    image = proxy["image"]
    ref, _, digest = image.partition("@")
    assert ":" in ref.rsplit("/", 1)[-1] and not ref.endswith(":latest")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), image
    env = {k: str(v) for k, v in proxy["environment"].items()}
    assert {k for k, v in env.items() if v == "1"} == {"CONTAINERS", "IMAGES", "POST", "DELETE"}
    for refused in ("EXEC", "VOLUMES", "NETWORKS", "SWARM", "SYSTEM", "BUILD", "AUTH"):
        assert env[refused] == "0", refused
    assert proxy["volumes"] == ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    assert proxy["cap_drop"] == ["ALL"]
    assert proxy["networks"] == ["docker-api"]
    assert "ports" not in proxy


def test_only_the_engine_worker_shares_the_proxy_network_and_it_has_no_route_out() -> None:
    document = _compose()
    network = document["networks"]["docker-api"]
    assert network["internal"] is True
    members = sorted(
        name
        for name, service in document["services"].items()
        if "docker-api" in (service or {}).get("networks", [])
    )
    assert members == ["docker-socket-proxy", "worker-engine"]


def test_no_service_but_the_proxy_and_the_edge_mounts_the_socket() -> None:
    """The worker never gets the raw socket; Traefik's read-only mount predates
    the engine and is for its own provider."""
    holders = sorted(
        name
        for name, service in _compose()["services"].items()
        if any("docker.sock" in str(v) for v in (service or {}).get("volumes", []))
    )
    assert holders == ["docker-socket-proxy", "traefik"]


def test_the_engine_worker_reaches_docker_through_the_proxy() -> None:
    services = _compose()["services"]
    assert services["worker-engine"]["environment"]["DOCKER_HOST"] == (
        "tcp://docker-socket-proxy:2375"
    )
    # The in-process scan worker has no Docker access at all.
    assert "DOCKER_HOST" not in services["worker-scan"]["environment"]
    assert services["worker-scan"]["networks"] == ["trustedoss"]


def test_the_egress_network_is_external_and_named_as_the_default() -> None:
    document = _compose()
    network = document["networks"]["scan-egress"]
    assert network == {"name": "trustedoss-scan-egress", "external": True}
    assert document["x-backend-env"]["SCAN_BOMLENS_NETWORK"] == (
        "${SCAN_BOMLENS_NETWORK:-trustedoss-scan-egress}"
    )


def test_the_engine_settings_reach_the_worker_with_the_code_defaults(monkeypatch: Any) -> None:
    """Each key the guide lists is in the shared env anchor, and an unset .env
    gives the worker the same value as an unset variable gives the code
    (except the network, which compose sets to its egress network, and the
    engine choice, which compose moves to the BomLens engine)."""
    env = _compose()["x-backend-env"]
    for key in (
        "SCAN_ENGINE",
        "SCAN_CONTAINER_ENGINE",
        "SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS",
        "SCAN_BOMLENS_IMAGE",
        "SCAN_BOMLENS_DEEP_LICENSE_IMAGE",
        "SCAN_BOMLENS_NETWORK",
        "SCAN_BOMLENS_STAGE1_REPOSITORIES",
        "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX",
        "SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT",
        "SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS",
        "SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS",
        "SCAN_TRIVY_CACHE_VOLUME",
    ):
        assert key in env, key
    for key in (
        "SCAN_BOMLENS_NETWORK",
        "SCAN_BOMLENS_STAGE1_REPOSITORIES",
        "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX",
        "SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT",
        "SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS",
        "SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert bomlens_network() == ""
    # Empty in compose, so the code decides: ghcr.io/cdxgen/, or the image
    # prefix alone when one is set. A compose default would keep ghcr.io
    # allowed next to a mirror.
    assert env["SCAN_BOMLENS_STAGE1_REPOSITORIES"] == "${SCAN_BOMLENS_STAGE1_REPOSITORIES:-}"
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", "")
    assert bomlens_stage1_repositories() == ("ghcr.io/cdxgen/",)
    assert bomlens_stage1_repositories("registry.example/m/") == ("registry.example/m/",)
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_REPOSITORIES")
    assert env["SCAN_BOMLENS_STAGE1_IMAGE_PREFIX"] == "${SCAN_BOMLENS_STAGE1_IMAGE_PREFIX:-}"
    assert bomlens_stage1_image_prefix() == ""
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
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("SCAN_CONTAINER_ENGINE", raising=False)
    assert env["SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS"] == (
        "${SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS:-" + str(bomlens_image_scan_timeout_seconds()) + "}"
    )
    # The one setting where compose is deliberately not the code's default: the
    # code stays `inprocess` (a dev stack, a test, a Helm install), compose moves to
    # the engine, and the Helm chart sets its own (env.scan.containerEngine).
    assert scan_container_engine() == "inprocess"
    assert env["SCAN_CONTAINER_ENGINE"] == "${SCAN_CONTAINER_ENGINE:-bomlens}"


def test_the_engine_is_the_default_and_its_workers_start_with_the_stack() -> None:
    document = _compose()
    env = document["x-backend-env"]
    assert env["SCAN_ENGINE"] == "${SCAN_ENGINE:-bomlens}"
    assert env["SCAN_CONTAINER_ENGINE"] == "${SCAN_CONTAINER_ENGINE:-bomlens}"
    # A scan queued for the engine waits for this worker, so it is never optional.
    assert "profiles" not in document["services"]["worker-engine"]
    assert "profiles" not in document["services"]["docker-socket-proxy"]


def test_the_demo_box_keeps_the_in_process_engine_and_holds_the_engine_services_back() -> None:
    demo = yaml.safe_load((REPO_ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8"))
    scan_env = demo["x-demo-scan-env"]
    assert scan_env["SCAN_ENGINE"] == "inprocess"
    assert scan_env["SCAN_CONTAINER_ENGINE"] == "inprocess"
    # Every service that decides which queue a scan goes to reads that anchor.
    for name in ("backend", "worker-scan", "worker-default", "beat"):
        assert demo["services"][name]["environment"]["SCAN_ENGINE"] == "inprocess", name
    assert demo["services"]["worker-engine"]["profiles"] == ["bomlens-engine"]
    assert demo["services"]["docker-socket-proxy"]["profiles"] == ["bomlens-engine"]
