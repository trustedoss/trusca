# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""SCAN_BOMLENS_STAGE1_IMAGE_PREFIX: stage 1's cdxgen images from a mirror.

The runner hands the setting to ``MODE=PLAN`` as BomLens's
``CDXGEN_IMAGE_PREFIX``, and the plan then names its stage 1 image under it
(``fixtures/bomlens-plan/node-mirror-prefix`` is the published image's own
answer). Same harness as ``test_runner.py``: containers are played by
:class:`tests._bomlens_engine.FakeDocker`, and the PLAN container copies the
capture the real image writes for the env the runner gave it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from core.config import bomlens_stage1_repositories
from integrations.bomlens.contract import BomLensError
from integrations.bomlens.runner import (
    BomLensEngineError,
    BomLensRunner,
    EngineLayout,
    resolve_images,
    stage1_image_prefix,
)
from tests._bomlens_engine import (
    NO_DIGEST,
    PLAN_FIXTURES,
    Behaviour,
    Effect,
    FakeDocker,
    captured_images,
    plan_as_published,
    write_plan,
    write_sbom,
)

MIRROR = "registry.example/mirror/cdxgen/"


def _stage1(case: str) -> dict[str, str]:
    document = json.loads((PLAN_FIXTURES / case / "bomlens-plan.json").read_text())
    generate = next(s for s in document["stages"] if s["id"] == "generate")
    return {"image": generate["image"], "digest": generate["image_digest"]}


PUBLISHED = _stage1("node-ok")
MIRRORED = _stage1("node-mirror-prefix")
OUTPUT_FILE = json.loads((PLAN_FIXTURES / "node-ok" / "bomlens-plan.json").read_text())[
    "output_file"
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    scan_dir = root / "scan"
    (scan_dir / "source" / "repo").mkdir(parents=True)
    (scan_dir / "source" / "repo" / "package.json").write_text("{}")
    (tmp_path / "trivy" / "db").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy"))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE", captured_images())
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "trustedoss-scan-egress")
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", raising=False)
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", raising=False)
    return scan_dir


def _docker(plan_effect: Effect | None = None) -> FakeDocker:
    return (
        FakeDocker()
        .behave("plan", Behaviour(effect=plan_effect or plan_as_published()))
        .behave("stage1", Behaviour(effect=write_sbom(OUTPUT_FILE)))
    )


def _runner(workspace: Path, docker: FakeDocker) -> BomLensRunner:
    return BomLensRunner(
        scan_uuid=uuid.UUID(int=11),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source" / "repo"),
        images=resolve_images(deep_license=False),
        project_name="app",
        project_version="1.0",
        fetch_license=False,
        docker=docker,
    )


# ---------------------------------------------------------------------------
# The captures: the published image puts the prefix on the name, not the digest
# ---------------------------------------------------------------------------


def test_the_mirror_capture_differs_from_the_plain_one_only_in_the_registry() -> None:
    assert PUBLISHED["image"].startswith("ghcr.io/cdxgen/")
    assert MIRRORED["image"] == MIRROR + PUBLISHED["image"].removeprefix("ghcr.io/cdxgen/")
    assert MIRRORED["digest"] == PUBLISHED["digest"]


# ---------------------------------------------------------------------------
# The setting reaches MODE=PLAN, and stage 1 runs the mirrored image
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [MIRROR, MIRROR.rstrip("/"), f"  {MIRROR}  "])
def test_the_prefix_reaches_the_plan_and_stage_1_pulls_from_the_mirror(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", value)
    docker = _docker()
    runner = _runner(workspace, docker)
    runner.run()

    (plan,) = docker.runs_of("plan")
    assert plan.env["CDXGEN_IMAGE_PREFIX"] == MIRROR
    (stage1,) = docker.runs_of("stage1")
    expected = f"{MIRRORED['image']}@{MIRRORED['digest']}"
    assert stage1.image == expected
    assert expected in docker.pulled
    assert not any(ref.startswith("ghcr.io/cdxgen/") for ref in docker.pulled)
    assert runner.report.stage1_image == expected


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_prefix_the_plan_gets_no_variable_and_stage_1_is_the_published_image(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", value)
    docker = _docker()
    _runner(workspace, docker).run()

    (plan,) = docker.runs_of("plan")
    assert "CDXGEN_IMAGE_PREFIX" not in plan.env
    assert "CDXGEN_IMAGE_PREFIX" not in plan.by_name
    (stage1,) = docker.runs_of("stage1")
    assert stage1.image == f"{PUBLISHED['image']}@{PUBLISHED['digest']}"


# ---------------------------------------------------------------------------
# The value's shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("registry.example/mirror/cdxgen/", "registry.example/mirror/cdxgen/"),
        ("registry.example/mirror/cdxgen", "registry.example/mirror/cdxgen/"),
        ("localhost:5000/cdxgen/", "localhost:5000/cdxgen/"),
        ("proxy.example/ghcr-proxy/cdxgen", "proxy.example/ghcr-proxy/cdxgen/"),
        ("localhost/cdxgen", "localhost/cdxgen/"),
        ("10.0.0.5:5000/a_b/c-d/e.f/", "10.0.0.5:5000/a_b/c-d/e.f/"),
        ("ghcr.io/cdxgen/", "ghcr.io/cdxgen/"),
    ],
)
def test_a_registry_path_is_accepted_and_ends_in_a_slash(value: str, expected: str) -> None:
    assert stage1_image_prefix(value) == expected


def test_an_empty_prefix_is_not_set() -> None:
    assert stage1_image_prefix("") is None


@pytest.mark.parametrize(
    "value",
    [
        "https://registry.example/mirror/cdxgen/",
        "docker://registry.example/mirror/",
        "//registry.example/mirror/",
        "/registry.example/mirror/",
        "registry.example//mirror/",
        "registry.example/mirror//",
        "registry.example/mirror/../other/",
        "registry.example/mirror/cdxgen:13.2.0",
        "registry.example/mirror@sha256:" + "0" * 64,
        "registry.example/mirror cdxgen/",
        "registry.example/mirror/\tcdxgen/",
        "registry.example/mirror/\n",
        "registry.example/mirror/;id",
        "registry.example/mirror/$(id)/",
        "registry.example/mirror/`id`/",
        "registry.example/mirror/|sh",
        "registry.example/mirror/&&id/",
        "registry.example/mirror/'x'/",
        "registry.example/mirror/*/",
        "Registry.Example/mirror/",
        "registry.example/Mirror/",
        "registry.example/-mirror/",
        "registry.example/mirror-/",
        "-registry.example/mirror/",
        "registry.example:port/mirror/",
        "registry.example:123456/mirror/",
        "mirror/cdxgen/",
        "cdxgen",
        # A host alone would admit every image in the registry.
        "localhost",
        "localhost/",
        "docker.io",
        "reg.example:5000",
        "reg.example:5000/",
        "registry.example/",
        "user:pass@registry.example/mirror/",
        "registry.example/" + "a" * 200 + "/",
    ],
)
def test_anything_but_a_registry_path_is_refused(value: str) -> None:
    with pytest.raises(BomLensEngineError, match="SCAN_BOMLENS_STAGE1_IMAGE_PREFIX") as caught:
        stage1_image_prefix(value)
    message = str(caught.value)
    assert "is not in the right form" in message
    # The rule, never the value (it may carry credentials): every bad value
    # gets the same message.
    with pytest.raises(BomLensEngineError) as other:
        stage1_image_prefix("zz-distinct-bad-value")
    assert message == str(other.value)
    assert "zz-distinct" not in message


@pytest.mark.parametrize(
    "value", ["u:p@reg.example/x", "https://u:secret-token@reg.example/mirror/cdxgen/"]
)
def test_credentials_in_a_bad_prefix_stay_out_of_the_error(value: str) -> None:
    with pytest.raises(BomLensEngineError) as caught:
        stage1_image_prefix(value)
    for part in ("u:p", "secret-token", "@reg.example"):
        assert part not in str(caught.value)


def test_a_bad_prefix_refuses_the_run_before_any_container(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", "https://registry.example/mirror/")
    with pytest.raises(BomLensEngineError, match="SCAN_BOMLENS_STAGE1_IMAGE_PREFIX"):
        resolve_images(deep_license=False)


# ---------------------------------------------------------------------------
# The allowed repositories follow the prefix
# ---------------------------------------------------------------------------


def test_the_prefix_is_allowed_without_being_listed_and_replaces_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", raising=False)
    assert bomlens_stage1_repositories() == ("ghcr.io/cdxgen/",)
    assert bomlens_stage1_repositories(MIRROR) == (MIRROR,)
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", "other.example/cdxgen/")
    assert bomlens_stage1_repositories(MIRROR) == ("other.example/cdxgen/", MIRROR)
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", f"other.example/cdxgen/,{MIRROR}")
    assert bomlens_stage1_repositories(MIRROR) == ("other.example/cdxgen/", MIRROR)
    # A list of nothing usable still admits nothing without a prefix, as before.
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", "ghcr.io/cdxgen")
    assert bomlens_stage1_repositories() == ()


def test_with_a_mirror_a_plan_naming_ghcr_is_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", MIRROR)
    docker = _docker(write_plan("node-ok"))
    with pytest.raises(BomLensEngineError, match="not from an allowed repository"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")
    assert not any(ref.startswith("ghcr.io/cdxgen/") for ref in docker.pulled)


def test_a_listed_repository_stays_allowed_next_to_the_prefix(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", MIRROR)
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", "ghcr.io/cdxgen/")
    docker = _docker(write_plan("node-ok"))
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert stage1.image.startswith("ghcr.io/cdxgen/")


_NAME = PUBLISHED["image"].removeprefix("ghcr.io/cdxgen/")


@pytest.mark.parametrize(
    "image",
    [
        f"registry.example/mirror/cdxgen-evil/{_NAME}",
        f"registry.example/mirror/cdxgenx/{_NAME}",
        f"registry.example/mirror/{_NAME}",
        f"registry.example.evil.example/mirror/cdxgen/{_NAME}",
        f"evil.example/registry.example/mirror/cdxgen/{_NAME}",
        f"registry.example:5000/mirror/cdxgen/{_NAME}",
        f"ghcr.io/cdxgen/{_NAME}",
        # The prefix path itself is not under the prefix.
        "registry.example/mirror/cdxgen:13.2.0",
    ],
)
def test_a_plan_image_outside_the_prefix_is_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, image: str
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", MIRROR)
    docker = _docker(write_plan("node-mirror-prefix", stage1_image=image))
    with pytest.raises(BomLensEngineError, match="not from an allowed repository"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")
    assert docker.pulled == [captured_images()]


def test_without_a_prefix_the_default_path_itself_is_not_under_it(workspace: Path) -> None:
    docker = _docker(write_plan("node-ok", stage1_image="ghcr.io/cdxgen:13.2.0"))
    with pytest.raises(BomLensEngineError, match="not from an allowed repository"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_a_mirrored_image_without_its_digest_is_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", MIRROR)
    docker = _docker(write_plan("node-mirror-prefix", stage1_digest=NO_DIGEST))
    with pytest.raises(BomLensError, match="not pinned by a sha256 digest"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")
