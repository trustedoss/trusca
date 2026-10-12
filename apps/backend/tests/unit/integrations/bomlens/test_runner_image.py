"""The BomLens runner's container image scan (``MODE=IMAGE``, SCAN_CONTAINER_ENGINE=bomlens).

Every assertion is on the ``docker run`` argument list the runner built (or
on the reference it hands over), which is what the Docker daemon and the
BomLens image would get. The container itself is played by
:class:`tests._bomlens_engine.FakeDocker`.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from integrations.bomlens import runner as runner_mod
from integrations.bomlens.plan import PROXY_ENV_NAMES
from integrations.bomlens.runner import (
    BomLensEngineError,
    BomLensRunner,
    ContainerSpec,
    EngineLayout,
    RunReport,
    docker_run_argv,
    image_target,
    resolve_images,
)
from tests._bomlens_engine import Behaviour, FakeDocker, captured_images, write_result


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    scan_dir = root / "scan"
    scan_dir.mkdir(parents=True)
    trivy = tmp_path / "trivy" / "db"
    trivy.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy"))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE", captured_images())
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "trusca-scan-egress")
    monkeypatch.setenv("SECRET_KEY", "worker-secret-that-must-not-leak-0123456789")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("SYFT_REGISTRY_AUTH_PASSWORD", "worker-env-registry-password")
    monkeypatch.setenv("TRIVY_PASSWORD", "worker-env-trivy-password")
    for name in (*PROXY_ENV_NAMES, "TRIVY_DB_REFRESH_HOURS"):
        monkeypatch.delenv(name, raising=False)
    return scan_dir


def _runner(workspace: Path, docker: FakeDocker) -> BomLensRunner:
    return BomLensRunner(
        scan_uuid=uuid.UUID(int=9),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source"),
        images=resolve_images(deep_license=False),
        project_name="image",
        project_version="1.0.0",
        fetch_license=False,
        docker=docker,
    )


# ---------------------------------------------------------------------------
# The reference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "alpine",
        "alpine:3.19.0",
        "library/alpine:3.19.0",
        "myorg/app",
        "docker.io/library/alpine:3.19.0",
        "ghcr.io/org/team/app:1.2.3",
        "registry.example:5000/app:v1_2-3",
        "localhost:5000/app",
        "localhost/app:1",
        "alpine@sha256:" + "a" * 64,
        "alpine:3.19.0@sha256:" + "0123456789abcdef" * 4,
        "my-org/my_app__x/sub.part:Tag_1.0-rc",
        # Valid image names that are also syft scheme words: the registry
        # scheme in front is what keeps syft from reading a folder, and makes
        # "registry:alpine" the image "registry" with the tag "alpine".
        "dir:x",
        "registry:alpine",
    ],
)
def test_a_registry_reference_is_handed_over_with_the_registry_scheme(reference: str) -> None:
    assert image_target(reference) == f"registry:{reference}"


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "   ",
        "docker-archive:/w/image.tar",
        "oci-archive:/w/image.tar",
        "oci-dir:/w/layout",
        "dir:/",
        "file:/etc/passwd",
        "docker://alpine",
        "https://registry.example/app:1",
        "user:token@registry.example/app:1",
        "registry.example/App:1",
        "MyOrg/app",
        "alpine:3.19 --privileged",
        "alpine\n:3.19",
        "-alpine",
        "alpine:-3",
        "alpine@sha256:short",
        "alpine@md5:" + "a" * 32,
        "../alpine",
        "alpine/",
        "a" * 600,
    ],
)
def test_anything_else_is_refused_without_echoing_it(reference: str) -> None:
    with pytest.raises(BomLensEngineError, match="not a registry image reference") as raised:
        image_target(reference)
    if reference.strip():
        assert reference not in str(raised.value)


@pytest.mark.parametrize(
    ("host", "internal"),
    [
        ("localhost", True),
        ("localhost:5000", True),
        ("LOCALHOST", True),
        ("registry.localhost", True),
        ("127.0.0.1", True),
        ("127.1:5000", True),
        ("0x7f000001", True),
        ("10.1.2.3:443", True),
        ("169.254.169.254", True),
        ("[::1]:5000", True),
        ("[fe80::1]", True),
        ("8.8.8.8", True),
        ("docker.io", False),
        ("ghcr.io", False),
        ("registry.example:5000", False),
        ("localhost.example.com", False),
        ("deadbeef.example", False),
        ("abc", False),
        ("", False),
    ],
)
def test_an_address_or_the_local_machine_is_an_internal_registry(
    host: str, internal: bool
) -> None:
    from tasks.scan_container_bomlens import is_internal_registry

    assert is_internal_registry(host) is internal


def test_surrounding_space_is_dropped_not_passed_on() -> None:
    assert image_target(" alpine:3.19 ") == "registry:alpine:3.19"


# ---------------------------------------------------------------------------
# The container
# ---------------------------------------------------------------------------


def test_the_image_container_runs_hardened_with_three_mounts(workspace: Path) -> None:
    docker = FakeDocker().behave("image", Behaviour(effect=write_result("image-registry-alpine")))
    runner = _runner(workspace, docker)

    output = runner.run_image("registry:alpine:3.19.0")

    assert output == runner_mod.confined_path(workspace / "bomlens" / "out", workspace.parent)
    (run,) = docker.runs
    assert run.label == "image"
    assert run.image == captured_images()
    assert docker.pulled == [captured_images()]
    assert run.option_values("--user") == [f"{os.getuid()}:{os.getgid()}"]
    assert run.has_flag("--read-only")
    assert run.option_values("--cap-add") == []
    assert run.option_values("--cap-drop") == ["ALL"]
    assert run.option_values("--network") == ["trusca-scan-egress"]
    assert run.option_values("--workdir") == ["/host-output"]
    assert set(run.mounts) == {"/host-output", "/tmp", "/trivy-db"}
    assert run.mounts["/trivy-db"]["readonly"] == "true"
    assert "readonly" not in run.mounts["/host-output"]
    assert run.env["MODE"] == "IMAGE"
    assert run.env["TARGET_IMAGE"] == "registry:alpine:3.19.0"
    assert run.env["UPLOAD_ENABLED"] == "false"
    assert run.env["DEEP_LICENSE"] == "false"
    assert run.env["TRIVY_OFFLINE_SCAN"] == "true"
    # Nothing from the worker's environment: no secret, no registry login
    # (the engine path passes none), no database address.
    joined = " ".join(run.argv)
    for leaked in (
        "worker-secret-that-must-not-leak",
        "postgresql",
        "worker-env-registry-password",
        "worker-env-trivy-password",
    ):
        assert leaked not in joined
        assert leaked not in str(docker.process_envs)
    assert not any(n.startswith(("SYFT_", "DOCKER_")) for n in run.env)
    assert "TRIVY_PASSWORD" not in run.env
    # Removed after it exited.
    assert docker.removed == list(docker.containers)
    report = runner.report.as_metadata()
    assert report == {
        "mode": "image",
        "image_target": "registry:alpine:3.19.0",
        "engine_image": captured_images(),
        "exit_code": 0,
        "timed_out": False,
    }


def test_a_proxy_is_passed_by_name_and_a_loopback_one_not_at_all(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://u:proxy-pass-1@proxy.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    docker = FakeDocker().behave("image", Behaviour(effect=write_result("image-registry-alpine")))

    _runner(workspace, docker).run_image("registry:alpine")

    (run,) = docker.runs
    assert run.by_name == frozenset({"HTTPS_PROXY"})
    assert "HTTP_PROXY" not in run.env
    assert not any("proxy-pass-1" in arg for arg in run.argv)


def test_a_container_past_its_time_limit_is_killed_and_the_run_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS", "60")
    docker = FakeDocker().behave("image", Behaviour(hang=True))
    runner = _runner(workspace, docker)

    with pytest.raises(BomLensEngineError, match="stopped at its time limit"):
        runner.run_image("registry:alpine")

    assert docker.killed == list(docker.containers)
    assert docker.removed == list(docker.containers)
    assert runner.report.image_timed_out is True


def test_leftovers_of_an_earlier_attempt_are_removed_first(workspace: Path) -> None:
    docker = FakeDocker().behave("image", Behaviour(effect=write_result("image-registry-alpine")))
    docker.leftovers = ["old-1"]

    _runner(workspace, docker).run_image("registry:alpine")

    assert docker.removed[0] == "old-1"


def test_an_old_docker_api_is_refused_before_anything_runs(workspace: Path) -> None:
    docker = FakeDocker(api_version="1.44")

    with pytest.raises(BomLensEngineError, match="Docker Engine 26"):
        _runner(workspace, docker).run_image("registry:alpine")
    assert docker.runs == []


def test_dropping_all_capabilities_removes_every_added_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_SIDECAR_CAP_ADD", "CHOWN,SETUID")
    monkeypatch.setenv("SCAN_SIDECAR_CAP_DROP", "NET_RAW")
    spec = ContainerSpec(
        name="n",
        image="i@sha256:" + "a" * 64,
        user="1000:1000",
        entrypoint=None,
        args=(),
        workdir=None,
        mounts=(),
        extra_mount_args=(),
        env=(),
        network="none",
        read_only=True,
        drop_all_caps=True,
    )

    class _NoMounts:
        def mount_args(self, *_a: object, **_k: object) -> list[str]:
            return []

        def trivy_db_args(self) -> list[str]:
            return []

    argv = docker_run_argv(spec, _NoMounts(), "ab", 1)
    assert "--cap-add" not in argv
    drops = [argv[i + 1] for i, a in enumerate(argv) if a == "--cap-drop"]
    assert "ALL" in drops
    # The rest of the hardening stays.
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "--pids-limit" in argv

    # Without the flag the sidecar's minimal set is added back, as before.
    from dataclasses import replace

    default = docker_run_argv(replace(spec, drop_all_caps=False), _NoMounts(), "ab", 1)
    assert [default[i + 1] for i, a in enumerate(default) if a == "--cap-add"] == [
        "CHOWN",
        "SETUID",
    ]


def test_a_source_run_report_is_unchanged() -> None:
    """The image fields do not leak into a source scan's record."""
    keys = set(RunReport().as_metadata())
    assert "mode" not in keys and "image_target" not in keys
    assert "plan_status" in keys


# ---------------------------------------------------------------------------
# The administrator guide says what the code does (hardening rule 4)
# ---------------------------------------------------------------------------

_DOCS = Path(__file__).resolve().parents[6] / "docs-site"
_GUIDES = (
    _DOCS / "docs" / "admin-guide" / "dynamic-scan-executor.md",
    _DOCS
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "admin-guide"
    / "dynamic-scan-executor.md",
)


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_quotes_the_private_registry_refusal_as_the_scan_reports_it(
    guide: Path,
) -> None:
    from tasks.scan_container_bomlens import credential_refusal, internal_registry_refusal

    text = guide.read_text(encoding="utf-8")
    assert credential_refusal("<host>") in text
    assert internal_registry_refusal("<host>") in text


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_gives_the_image_scan_settings_their_code_defaults(
    guide: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.config import bomlens_image_scan_timeout_seconds, scan_container_engine

    monkeypatch.delenv("SCAN_CONTAINER_ENGINE", raising=False)
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS", raising=False)
    rows = {
        line.split("|")[1].strip(): line.split("|")[2].strip()
        for line in guide.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `SCAN_")
    }
    assert rows["`SCAN_CONTAINER_ENGINE`"] == f"`{scan_container_engine()}`"
    assert rows["`SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS`"] == (
        f"`{bomlens_image_scan_timeout_seconds()}`"
    )
