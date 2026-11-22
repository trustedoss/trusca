# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The execution plans the published BomLens image writes, read by the runner package.

The package checks every plan before it starts a container (``bomlens_runner.plan``).
What it checks is its own to test; these tests keep what TRUSCA needs to know:
that the plans captured from the pinned image still read, that the package's
vocabulary is the contract's (``engine-plan-1.json``), and that a plan asking
for something TRUSCA must not run is refused. Each refusal is a mutation of a
captured plan, so the test fails when the check goes, not when the plan changes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from bomlens_runner import PlanRejected, load_plan
from bomlens_runner import plan as package_plan
from bomlens_runner.plan import parse_plan

PLAN_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "bomlens-plan"


def _captured(case: str = "node-ok") -> dict[str, Any]:
    document: dict[str, Any] = json.loads((PLAN_DIR / case / "bomlens-plan.json").read_text())
    return document


def _stage(document: dict[str, Any], stage_id: str) -> dict[str, Any]:
    return next(s for s in document["stages"] if s["id"] == stage_id)


def _mount(stage: dict[str, Any], role: str) -> dict[str, Any]:
    return next(m for m in stage["mounts"] if m["role"] == role)


def _mutated(mutate: Any) -> dict[str, Any]:
    document = _captured()
    changed = copy.deepcopy(document)
    mutate(changed)
    assert changed != document, "the mutation did not change the plan"
    return changed


def _refused(document: dict[str, Any], fragment: str) -> None:
    with pytest.raises(PlanRejected) as caught:
        parse_plan(document)
    assert fragment in str(caught.value)


# ---------------------------------------------------------------------------
# The captured plans read as BomLens wrote them
# ---------------------------------------------------------------------------


def test_the_captured_npm_plan_reads_as_two_pinned_stages() -> None:
    plan = load_plan(PLAN_DIR / "node-ok")
    assert plan.supported
    assert plan.detected_ecosystem == "node"
    generate, postprocess = plan.stages
    assert (generate.id, generate.stage) == ("generate", 1)
    assert (postprocess.id, postprocess.stage) == ("postprocess", 2)
    assert generate.image.startswith("ghcr.io/cdxgen/")
    assert generate.image_digest.startswith("sha256:")
    assert generate.network_required is True
    assert postprocess.network_required is False


@pytest.mark.parametrize("case", ["node-ok", "node-mirror-prefix"])
def test_every_captured_supported_plan_reads(case: str) -> None:
    plan = parse_plan(_captured(case))
    assert plan.supported and len(plan.stages) == 2


def test_the_captured_android_plan_is_unsupported_with_its_reason_and_no_stage() -> None:
    plan = parse_plan(_captured("android-unsupported"))
    assert not plan.supported
    assert plan.reason == "android-sdk-image"
    assert plan.stages == ()


# ---------------------------------------------------------------------------
# One vocabulary in two places (hardening rule 2)
# ---------------------------------------------------------------------------


def test_the_package_vocabulary_is_the_contract_schemas() -> None:
    """The package's enums are the ones the plan schema BomLens publishes lists. A
    BomLens upgrade that adds a mount role or a status fails here, not as a plan
    that is refused in production."""
    schema = json.loads((PLAN_DIR / "engine-plan-1.json").read_text())
    props = schema["properties"]
    stage_props = props["stages"]["items"]["properties"]
    mount_props = stage_props["mounts"]["items"]["properties"]
    assert set(props["status"]["enum"]) == set(package_plan.PLAN_STATUSES)
    assert set(props["reason"]["enum"]) - {None} == set(package_plan.PLAN_REASONS)
    assert set(stage_props["id"]["enum"]) == {i for i, _ in package_plan.STAGE_ORDER}
    assert set(mount_props["role"]["enum"]) == set(package_plan.MOUNT_ROLES)
    assert set(mount_props["mode"]["enum"]) == {"ro", "rw"}
    assert props["contract"]["properties"]["name"]["const"] == package_plan.CONTRACT_NAME


# ---------------------------------------------------------------------------
# Plans TRUSCA must not run
# ---------------------------------------------------------------------------


def test_a_host_path_on_a_folder_mount_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "source")["source"] = "/etc"

    _refused(_mutated(mutate), "only a plan file mount may name a source")


@pytest.mark.parametrize("source", ["../../../etc/passwd", "/etc/shadow", "bomlens-plan/../x"])
def test_a_plan_file_outside_the_plan_folder_is_refused(source: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        next(m for m in _stage(d, "generate")["mounts"] if m["role"] == "plan-file")["source"] = (
            source
        )

    _refused(_mutated(mutate), "plan file is not under bomlens-plan/")


@pytest.mark.parametrize(
    "target", ["/var/run/docker.sock", "/run/docker.sock", "/etc", "/proc/1", "/"]
)
def test_a_mount_over_a_system_path_or_the_docker_socket_is_refused(target: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "output")["target"] = target

    with pytest.raises(PlanRejected, match="mount target"):
        parse_plan(_mutated(mutate))


@pytest.mark.parametrize(
    "role",
    ["maven-settings", "maven-repository", "gradle-cache", "gradle-wrapper", "cache-gradle"],
)
def test_a_required_mount_of_a_role_the_caller_provides_is_refused(role: str) -> None:
    """TRUSCA provides none of the caches and settings folders, so a plan that
    needs one cannot be run."""

    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), role)["optional"] = False

    _refused(_mutated(mutate), "must be optional")


@pytest.mark.parametrize("role", ["maven-settings", "gradle-cache", "cache-maven"])
def test_a_cache_or_settings_mount_in_stage_2_is_refused(role: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        mount = dict(_mount(_stage(d, "generate"), role))
        _stage(d, "postprocess")["mounts"].append(mount)

    _refused(_mutated(mutate), "not allowed in stage 2")


def test_the_trivy_database_is_read_only_and_only_in_stage_2() -> None:
    def writable(d: dict[str, Any]) -> None:
        _mount(_stage(d, "postprocess"), "trivy-db")["mode"] = "rw"

    _refused(_mutated(writable), "must be read-only")

    def in_stage_1(d: dict[str, Any]) -> None:
        _stage(d, "generate")["mounts"].append(dict(_mount(_stage(d, "postprocess"), "trivy-db")))

    _refused(_mutated(in_stage_1), "not allowed in stage 1")

    def elsewhere(d: dict[str, Any]) -> None:
        _mount(_stage(d, "postprocess"), "trivy-db")["target"] = "/data"

    _refused(_mutated(elsewhere), "/trivy-db")


def test_stage_2_may_not_write_the_source() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "postprocess"), "source")["mode"] = "rw"

    _refused(_mutated(mutate), "stage 2 source must be read-only")


def test_a_stage_that_runs_something_other_than_a_shell_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["entrypoint"] = ["/usr/bin/curl"]

    _refused(_mutated(mutate), "entrypoint is not a shell")


def test_a_stage_that_runs_as_a_named_user_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["user"] = "root"

    _refused(_mutated(mutate), "numeric uid")


def test_stage_2_with_other_values_than_the_postprocess_ones_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "postprocess")["env"]["UPLOAD_ENABLED"] = "true"

    _refused(_mutated(mutate), "stage 2 env does not hold its required values")


def test_an_env_name_or_pass_through_the_package_does_not_know_is_dropped_not_run() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["env"]["LD_PRELOAD"] = "/tmp/x.so"
        _stage(d, "generate")["env_passthrough"].append("DATABASE_URL")

    plan = parse_plan(_mutated(mutate))
    assert set(plan.dropped_env) >= {"LD_PRELOAD", "DATABASE_URL"}
    generate = plan.stage(1)
    assert "LD_PRELOAD" not in generate.env
    assert "DATABASE_URL" not in generate.env_passthrough


def test_a_plan_whose_stage_1_image_is_not_pinned_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["image_digest"] = None

    _refused(_mutated(mutate), "not pinned by digest")


def test_the_stage_1_tag_latest_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage = _stage(d, "generate")
        stage["image"] = stage["image"].rsplit(":", 1)[0] + ":latest"

    _refused(_mutated(mutate), "latest")
