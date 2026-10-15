"""The BomLens engine runner: what each container gets, and when it runs.

The containers are played by :class:`tests._bomlens_engine.FakeDocker`; the
plan each test runs is the one the published image wrote
(``tests/fixtures/bomlens-plan/``). Every assertion is on the ``docker run``
argument list the runner built, which is what the Docker daemon would get.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.bomlens import runner as runner_mod
from integrations.bomlens.plan import PROXY_ENV_NAMES, BomLensPlanRejected
from integrations.bomlens.runner import (
    BindMounts,
    BomLensEngineError,
    BomLensRunner,
    EngineContainerStillRunning,
    EngineLayout,
    NamedVolumeMounts,
    confined_path,
    is_loopback_proxy,
    pinned_image,
    resolve_images,
    trivy_db_max_age_days,
)
from tests._bomlens_engine import (
    Behaviour,
    FakeDocker,
    Run,
    captured_images,
    write_plan,
    write_sbom,
)

OUTPUT_FILE = "app_1.0_bom.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    scan_dir = root / "scan"
    (scan_dir / "source" / "repo").mkdir(parents=True)
    (scan_dir / "source" / "repo" / "package.json").write_text("{}")
    trivy = tmp_path / "trivy" / "db"
    trivy.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy"))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE", captured_images())
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "trusca-scan-egress")
    monkeypatch.setenv("SECRET_KEY", "worker-secret-that-must-not-leak-0123456789")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("GOPROXY", "https://proxy.golang.org")
    # A developer's or runner's own proxy and refresh settings must not leak
    # into the exact environments the tests below assert.
    for name in (
        *PROXY_ENV_NAMES,
        "TRIVY_DB_REFRESH_HOURS",
        "BOMLENS_TRIVY_DB_MAX_AGE_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    return scan_dir


def _runner(workspace: Path, docker: FakeDocker) -> BomLensRunner:
    return BomLensRunner(
        scan_uuid=uuid.UUID(int=7),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source" / "repo"),
        images=resolve_images(deep_license=False),
        project_name="app",
        project_version="1.0",
        fetch_license=False,
        docker=docker,
    )


def _output_file() -> str:
    import json

    from tests._bomlens_engine import PLAN_FIXTURES

    return str(
        json.loads((PLAN_FIXTURES / "node-ok" / "bomlens-plan.json").read_text())["output_file"]
    )


def _happy_docker() -> FakeDocker:
    return (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(effect=write_sbom(_output_file())))
    )


def _env_names(run: Run) -> set[str]:
    return set(run.env)


# ---------------------------------------------------------------------------
# The normal sequence
# ---------------------------------------------------------------------------


def test_plan_then_stage_1_then_stage_2_then_ownership(workspace: Path) -> None:
    docker = _happy_docker()
    out = _runner(workspace, docker).run()
    assert [r.label for r in docker.runs] == ["plan", "stage1", "stage2", "chown"]
    assert out == confined_path(workspace / "bomlens" / "out", workspace.parent)
    assert (out / runner_mod.STAGE1_LOG_NAME).exists()
    # Every container was removed after it exited.
    assert sorted(docker.removed) == sorted(docker.containers)


def test_every_container_is_hardened(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    for run in docker.runs:
        assert run.option_values("--cap-drop") == ["ALL"], run.label
        assert "no-new-privileges" in run.option_values("--security-opt"), run.label
        assert run.option_values("--pids-limit"), run.label
        assert run.option_values("--memory") == run.option_values("--memory-swap"), run.label
        assert run.option_values("--cpus"), run.label
        assert "SYS_ADMIN" not in run.option_values("--cap-add"), run.label
        for word in ("--privileged", "--device", "--pid", "--ipc", "--userns", "--volumes-from"):
            assert word not in run.argv, (run.label, word)
        assert "trusca.role=bomlens-engine" in run.option_values("--label")


def test_plan_and_stage_2_have_a_read_only_root_and_no_network(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    for label in ("plan", "stage2"):
        (run,) = docker.runs_of(label)
        assert run.has_flag("--read-only"), label
        assert run.option_values("--network") == ["none"], label


def test_stage_1_gets_the_registry_network_and_a_read_only_root(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert stage1.option_values("--network") == ["trusca-scan-egress"]
    assert stage1.has_flag("--read-only")


def test_stage_1_read_only_root_can_be_turned_off(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT", "false")
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert not stage1.has_flag("--read-only")
    (stage2,) = docker.runs_of("stage2")
    assert stage2.has_flag("--read-only")


def test_stage_1_runs_the_plan_image_pinned_by_its_digest(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert "@sha256:" in stage1.image
    assert stage1.image.startswith("ghcr.io/cdxgen/")
    assert stage1.image in docker.pulled


def test_every_host_path_is_inside_the_scan_workspace(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    root = os.path.realpath(workspace)
    trivy = os.path.realpath(os.environ["TRIVY_CACHE_DIR"])
    for run in docker.runs:
        for target, fields in run.mounts.items():
            src = fields["src"]
            if target == runner_mod.TRIVY_DB_TARGET:
                assert src == trivy and fields.get("readonly") == "true"
                continue
            assert src.startswith(root + os.sep), (run.label, target, src)


def test_the_source_is_writable_only_in_stage_1(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    by_label = {r.label: r for r in docker.runs}
    assert by_label["plan"].mounts["/src"].get("readonly") == "true"
    assert "readonly" not in by_label["stage1"].mounts["/app"]
    assert by_label["stage2"].mounts["/src"].get("readonly") == "true"


def test_plan_files_are_mounted_from_a_copy_no_container_writes(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    prep = stage1.mounts["/tmp/build-prep.sh"]
    assert prep["readonly"] == "true"
    assert Path(prep["src"]).parent == confined_path(
        workspace / "bomlens" / "plan-files", workspace.parent
    )
    writable = {
        f["src"]
        for r in docker.runs
        if r.label != "chown"
        for f in r.mounts.values()
        if "readonly" not in f
    }
    assert not any(src.startswith(str(Path(prep["src"]).parent)) for src in writable)


def test_stage_1_mounts_only_the_roles_trusca_provides(workspace: Path) -> None:
    """The captured plan lists two caches and six settings files (contract
    1.3.0), all optional. TRUSCA provides none of them, so stage 1 gets the
    source, output, plan files, guard state and its own scratch, and nothing at
    ``/tmp/sbomhome/.m2`` or under ``/bomlens-settings``."""
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert set(stage1.mounts) == {
        "/app",
        "/out",
        "/tmp/build-prep.sh",
        "/tmp/identify-conda.py",
        "/bomlens-state",
        runner_mod.SCRATCH_TARGET,
    }


def test_the_worker_environment_never_reaches_a_container(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    for run in docker.runs:
        assert "SECRET_KEY" not in run.env, run.label
        assert "DATABASE_URL" not in run.env, run.label
        assert not any("worker-secret" in v for v in run.env.values()), run.label


def test_stage_1_env_is_the_plan_env_plus_allowed_passthrough_and_trusca_values(
    workspace: Path,
) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert _env_names(stage1) == {
        "HOME",
        "MAVEN_OPTS",
        "FETCH_LICENSE",
        "PROJECT_NAME",
        "PROJECT_VERSION",
        "GOPROXY",
        "HOST_UID",
        "HOST_GID",
        "BOMLENS_GUARD_ID",
    }
    assert stage1.env["GOPROXY"] == "https://proxy.golang.org"
    assert stage1.env["BOMLENS_GUARD_ID"] == f"trusca-{uuid.UUID(int=7).hex}"
    assert stage1.env["HOST_UID"] == str(os.getuid())


def test_stage_2_gets_stage_1_results_and_reads_trivy_offline(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    env = stage2.env
    assert env["BOMLENS_STAGE1_EXIT"] == "0"
    assert env["BOMLENS_STAGE1_OOM"] == "false"
    assert env["BOMLENS_STAGE1_LOG"] == runner_mod.STAGE1_LOG_NAME
    assert env["GENERATE_SECURITY"] == "true"
    assert env["SECURITY_ENRICH"] == "false"
    assert env["TRIVY_CACHE_DIR"] == runner_mod.TRIVY_DB_TARGET
    assert env["TRIVY_SKIP_DB_UPDATE"] == "true"
    assert env["MODE"] == "POSTPROCESS"
    image, digest = captured_images().split("@")
    assert (env["BOMLENS_IMAGE_REF"], env["BOMLENS_IMAGE_DIGEST"]) == (image, digest)
    assert stage2.image == captured_images()


def test_the_plan_is_told_which_stage_2_image_trusca_runs(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_DEEP_LICENSE_IMAGE", captured_images())
    monkeypatch.setenv(
        "SCAN_BOMLENS_IMAGE",
        "ghcr.io/sktelecom/bomlens:sha-0000000@sha256:" + "1" * 64,
    )
    docker = _happy_docker()
    runner = BomLensRunner(
        scan_uuid=uuid.UUID(int=7),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source" / "repo"),
        images=resolve_images(deep_license=True),
        project_name="app",
        project_version="1.0",
        fetch_license=False,
        docker=docker,
    )
    runner.run()
    (plan,) = docker.runs_of("plan")
    (stage2,) = docker.runs_of("stage2")
    assert plan.image.endswith("@sha256:" + "1" * 64)
    assert (
        plan.env["BOMLENS_IMAGE_REF"] + "@" + plan.env["BOMLENS_IMAGE_DIGEST"] == captured_images()
    )
    assert stage2.image == captured_images()
    assert stage2.env["DEEP_LICENSE"] == "true"


# ---------------------------------------------------------------------------
# Stage 1 killed: restore, then stage 2 anyway
# ---------------------------------------------------------------------------


def test_an_oom_killed_stage_1_is_restored_and_stage_2_still_runs(workspace: Path) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(oom=True, output=b"Killed\n"))
    )
    runner = _runner(workspace, docker)
    runner.run()
    assert [r.label for r in docker.runs] == ["plan", "stage1", "restore", "stage2", "chown"]
    (stage1,) = docker.runs_of("stage1")
    (restore,) = docker.runs_of("restore")
    assert restore.env["BOMLENS_GUARD_RESTORE_ONLY"] == "1"
    assert restore.env["BOMLENS_GUARD_ID"] == stage1.env["BOMLENS_GUARD_ID"]
    assert (restore.image, restore.args) == (stage1.image, stage1.args)
    assert restore.mounts == stage1.mounts
    assert restore.option_values("--network") == ["none"]
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_STAGE1_OOM"] == "true"
    assert stage2.env["BOMLENS_STAGE1_EXIT"] == "137"
    assert runner.report.stage1_oom is True
    assert runner.report.restored is True
    log = (workspace / "bomlens" / "out" / runner_mod.STAGE1_LOG_NAME).read_bytes()
    assert log == b"Killed\n"


def test_a_stage_1_past_its_time_limit_is_killed_restored_and_followed(
    workspace: Path,
) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(hang=True))
    )
    runner = _runner(workspace, docker)
    runner.run()
    stage1_id = next(cid for cid, c in docker.containers.items() if c.run.label == "stage1")
    assert stage1_id in docker.killed
    assert [r.label for r in docker.runs] == ["plan", "stage1", "restore", "stage2", "chown"]
    assert runner.report.stage1_timed_out is True
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_STAGE1_EXIT"] == "137"


def test_a_stage_1_that_failed_normally_is_not_restored(workspace: Path) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(exit_code=1))
    )
    _runner(workspace, docker).run()
    assert [r.label for r in docker.runs] == ["plan", "stage1", "stage2", "chown"]
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_STAGE1_EXIT"] == "1"


def test_a_failed_restore_is_reported_and_stage_2_still_runs(workspace: Path) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(oom=True))
        .behave("restore", Behaviour(exit_code=2))
    )
    runner = _runner(workspace, docker)
    runner.run()
    assert runner.report.restored is False
    assert docker.runs_of("stage2")


# ---------------------------------------------------------------------------
# A container that will not stop, an interrupted wait
# ---------------------------------------------------------------------------


def test_a_container_still_running_after_kill_stops_the_run_before_any_read(
    workspace: Path,
) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(stuck=True))
    )
    with pytest.raises(EngineContainerStillRunning):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage2")
    stage1_id = next(cid for cid, c in docker.containers.items() if c.run.label == "stage1")
    assert stage1_id in docker.killed
    assert stage1_id in docker.removed


def test_an_interrupted_wait_still_removes_the_container(workspace: Path) -> None:
    class SoftTimeLimit(Exception):
        pass

    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(raise_in_logs=SoftTimeLimit()))
    )
    with pytest.raises(SoftTimeLimit):
        _runner(workspace, docker).run()
    stage1_id = next(cid for cid, c in docker.containers.items() if c.run.label == "stage1")
    assert stage1_id in docker.removed


def test_a_container_that_failed_to_be_created_is_removed_by_name(workspace: Path) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(fail_start=True))
    )
    with pytest.raises(BomLensEngineError, match="could not be created"):
        _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    name = stage1.option_values("--name")[0]
    assert name in docker.removed


def test_containers_left_by_an_earlier_attempt_are_removed_first(workspace: Path) -> None:
    docker = _happy_docker()
    docker.leftovers = ["old-1", "old-2"]
    _runner(workspace, docker).run()
    assert docker.removed[:2] == ["old-1", "old-2"]


def test_a_failed_plan_run_stops_the_scan(workspace: Path) -> None:
    docker = FakeDocker().behave("plan", Behaviour(exit_code=1))
    with pytest.raises(BomLensEngineError, match="MODE=PLAN exited 1"):
        _runner(workspace, docker).run()
    assert [r.label for r in docker.runs] == ["plan", "chown"]


def test_a_plan_run_that_wrote_no_plan_is_refused(workspace: Path) -> None:
    docker = FakeDocker().behave("plan", Behaviour())
    with pytest.raises(BomLensPlanRejected, match="wrote no plan file"):
        _runner(workspace, docker).run()


# ---------------------------------------------------------------------------
# Unsupported plan
# ---------------------------------------------------------------------------


def test_an_unsupported_plan_runs_stage_2_alone(workspace: Path) -> None:
    docker = FakeDocker().behave("plan", Behaviour(effect=write_plan("android-unsupported")))
    runner = _runner(workspace, docker)
    runner.run()
    assert [r.label for r in docker.runs] == ["plan", "stage2", "chown"]
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_STAGE1_EXIT"] == "127"
    assert stage2.env["MODE"] == "POSTPROCESS"
    assert stage2.option_values("--network") == ["none"]
    assert stage2.has_flag("--read-only")
    note = (workspace / "bomlens" / "out" / runner_mod.STAGE1_LOG_NAME).read_text()
    assert "android-sdk-image" in note
    assert runner.report.plan_status == "unsupported"


# ---------------------------------------------------------------------------
# What the plan may not choose
# ---------------------------------------------------------------------------


def test_a_stage_1_image_outside_the_allowed_repositories_is_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", "registry.example.com/mirror/")
    docker = _happy_docker()
    with pytest.raises(BomLensEngineError, match="not from an allowed repository"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_a_prefix_without_a_slash_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import bomlens_stage1_repositories

    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", "ghcr.io/cdxgen,docker.io/library/")
    assert bomlens_stage1_repositories() == ("docker.io/library/",)


def test_a_plan_naming_another_stage_2_image_is_refused(workspace: Path) -> None:
    docker = FakeDocker().behave(
        "plan",
        Behaviour(
            effect=write_plan(
                "node-ok", image="ghcr.io/evil/bomlens:1.0", digest="sha256:" + "2" * 64
            )
        ),
    )
    with pytest.raises(BomLensEngineError, match="is not the configured BomLens image"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


@pytest.mark.parametrize("network", ["", "host", "none", "container:abc", "-x"])
def test_a_registry_stage_without_an_allowed_network_is_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, network: str
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", network)
    docker = _happy_docker()
    with pytest.raises(BomLensEngineError, match="SCAN_BOMLENS_NETWORK"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_the_worker_s_own_network_is_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "trusca_default")
    docker = _happy_docker()
    docker.networks = frozenset({"trusca_default"})
    with pytest.raises(BomLensEngineError, match="worker's own network"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_a_symlinked_plan_file_is_refused_before_stage_1(workspace: Path) -> None:
    secret = workspace.parent.parent / "host-secret"
    secret.write_text("host file")

    def plan_with_link(run: Run) -> None:
        write_plan("node-ok")(run)
        target = run.host("/host-output") / "bomlens-plan" / "build-prep.sh"
        target.unlink()
        target.symlink_to(secret)

    docker = FakeDocker().behave("plan", Behaviour(effect=plan_with_link))
    with pytest.raises(BomLensEngineError, match="symbolic link"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_a_symlinked_output_folder_is_refused(workspace: Path) -> None:
    elsewhere = workspace.parent.parent / "elsewhere"
    elsewhere.mkdir()
    (workspace / "bomlens").mkdir()
    (workspace / "bomlens" / "out").symlink_to(elsewhere)
    docker = _happy_docker()
    with pytest.raises(BomLensEngineError, match="symbolic link"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_a_source_outside_the_workspace_is_refused(tmp_path: Path, workspace: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    docker = _happy_docker()
    runner = BomLensRunner(
        scan_uuid=uuid.UUID(int=7),
        layout=EngineLayout(scan_dir=workspace, source=outside),
        images=resolve_images(deep_license=False),
        project_name="app",
        project_version="1.0",
        fetch_license=False,
        docker=docker,
    )
    with pytest.raises(BomLensEngineError, match="outside the scan workspace"):
        runner.run()
    assert docker.runs == []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("ghcr.io/sktelecom/bomlens:sha-d8d3323", "is not pinned by a digest"),
        ("ghcr.io/sktelecom/bomlens@sha256:" + "a" * 64, "has no tag"),
        ("ghcr.io/sktelecom/bomlens:latest@sha256:" + "a" * 64, "is not a pinned image"),
        ("ghcr.io/sktelecom/bomlens:1.0@sha256:abc", "is not a pinned image"),
    ],
)
def test_an_engine_image_that_is_not_pinned_is_refused(value: str, fragment: str) -> None:
    """Each case is refused by its own check (the message says which), so one
    check cannot hide the absence of another."""
    with pytest.raises(BomLensEngineError, match=fragment):
        pinned_image(value, "SCAN_BOMLENS_IMAGE")


def test_the_default_images_are_pinned() -> None:
    from core.config import BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT, BOMLENS_IMAGE_DEFAULT

    for value in (BOMLENS_IMAGE_DEFAULT, BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT):
        image = pinned_image(value, "default")
        assert image.digest != "sha256:" + "0" * 64


def test_named_volume_mounts_use_a_subpath_of_the_workspace_volume(tmp_path: Path) -> None:
    mount_root = tmp_path / "workspace"
    (mount_root / "scan" / "out").mkdir(parents=True)
    style = NamedVolumeMounts(
        volume="trusca_workspace", volume_mount=mount_root, trivy_volume="trusca_trivy-cache"
    )
    host = Path(os.path.realpath(mount_root / "scan" / "out"))
    args = style.mount_args(host, "/out", read_only=False)
    assert args == ["--mount", "type=volume,src=trusca_workspace,dst=/out,volume-subpath=scan/out"]
    assert style.mount_args(host, "/out", read_only=True, no_copy=True) == [
        "--mount",
        "type=volume,src=trusca_workspace,dst=/out,volume-subpath=scan/out,readonly,volume-nocopy",
    ]
    # Nothing of an image belongs in the worker's database volume, whoever runs.
    assert style.trivy_db_args() == [
        "--mount",
        "type=volume,src=trusca_trivy-cache,dst=/trivy-db,readonly,volume-nocopy",
    ]
    with pytest.raises(BomLensEngineError, match="SCAN_WORKSPACE_MOUNT"):
        style.mount_args(Path(os.path.realpath(tmp_path)), "/out", read_only=False)


def test_named_volume_without_a_trivy_volume_is_refused(tmp_path: Path) -> None:
    style = NamedVolumeMounts(volume="v", volume_mount=tmp_path, trivy_volume="")
    with pytest.raises(BomLensEngineError, match="SCAN_TRIVY_CACHE_VOLUME"):
        style.trivy_db_args()


def test_a_mount_path_with_a_comma_is_refused(tmp_path: Path) -> None:
    style = BindMounts(trivy_cache=tmp_path)
    with pytest.raises(BomLensEngineError, match="cannot carry"):
        style.mount_args(Path("/w/a,dst=/etc"), "/out", read_only=False)


@pytest.mark.parametrize("strategy", ["volumes_from", "anything"])
def test_strategies_the_engine_cannot_use_are_refused(
    monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", strategy)
    with pytest.raises(BomLensEngineError, match="not usable by the BomLens engine"):
        runner_mod.mount_style()


def test_the_named_strategy_needs_the_workspace_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.delenv("SCAN_WORKSPACE_VOLUME", raising=False)
    with pytest.raises(BomLensEngineError, match="SCAN_WORKSPACE_VOLUME"):
        runner_mod.mount_style()


def test_confined_path_refuses_a_link_that_points_back_inside(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(BomLensEngineError, match="symbolic link"):
        confined_path(tmp_path / "link", tmp_path)


def test_confined_path_refuses_dot_dot(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with pytest.raises(BomLensEngineError, match="outside the scan workspace"):
        confined_path(tmp_path / "a" / ".." / ".." / "etc", tmp_path)


def test_the_docker_cli_settles_and_inspects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real CLI adapter: ``wait`` timing out reads as "not exited"."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        if argv[1] == "wait":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(
                argv, 0, b'{"Running": false, "ExitCode": 137, "OOMKilled": true}', b""
            )
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli = runner_mod.DockerCli()
    assert cli.wait("c1", 1) is None
    state = cli.state("c1")
    assert (state.running, state.exit_code, state.oom_killed) == (False, 137, True)


# ---------------------------------------------------------------------------
# Non-shipped trees follow TRUSCA's scope filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("include", [True, False])
def test_non_shipped_trees_follow_trusca_s_setting_not_the_worker_env(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, include: bool
) -> None:
    # A stray worker variable must not decide it in either direction.
    monkeypatch.setenv("BOMLENS_INCLUDE_NON_SHIPPED", "false" if include else "true")
    docker = _happy_docker()
    runner = BomLensRunner(
        scan_uuid=uuid.UUID(int=7),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source" / "repo"),
        images=resolve_images(deep_license=False),
        project_name="app",
        project_version="1.0",
        fetch_license=False,
        include_non_shipped=include,
        docker=docker,
    )
    runner.run()
    for label in ("stage1", "stage2"):
        (run,) = docker.runs_of(label)
        values = [
            v
            for k, v in run.options
            if k == "--env" and v is not None and v.startswith("BOMLENS_INCLUDE_NON_SHIPPED=")
        ]
        assert values == (["BOMLENS_INCLUDE_NON_SHIPPED=true"] if include else []), label


# ---------------------------------------------------------------------------
# What the engine containers can reach of the workspace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage1", [Behaviour(), Behaviour(oom=True)], ids=["clean", "restored"])
def test_engine_containers_get_their_folders_never_a_parent(
    workspace: Path, stage1: Behaviour
) -> None:
    """The output folder is mounted, its parent is not: the adapter's verified
    copies sit beside it, where no engine container can write."""
    if stage1.effect is None and not stage1.oom:
        stage1 = Behaviour(effect=write_sbom(_output_file()))
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", stage1)
    )
    runner = _runner(workspace, docker)
    runner.run()
    root = workspace.parent
    forbidden = {
        str(confined_path(workspace, root)),
        str(confined_path(workspace / "bomlens", root)),
        str(confined_path(workspace / "source", root)),
    }
    verified = str(runner.verified_dir())
    for run in docker.runs:
        sources = {f["src"] for f in run.mounts.values()}
        # Neither inside the verified folder nor above it.
        assert not any(
            src == verified or src.startswith(verified + "/") or verified.startswith(src + "/")
            for src in sources
        ), run.label
        if run.label == "chown":
            continue
        assert not sources & forbidden, (run.label, sources & forbidden)
    out = str(confined_path(workspace / "bomlens" / "out", root))
    (stage2,) = docker.runs_of("stage2")
    assert stage2.mounts["/host-output"]["src"] == out


def test_the_verified_folder_is_fresh_and_never_a_link(workspace: Path) -> None:
    docker = _happy_docker()
    runner = _runner(workspace, docker)
    runner.run()
    elsewhere = workspace.parent.parent / "elsewhere-verified"
    elsewhere.mkdir()
    (workspace / "bomlens-verified").symlink_to(elsewhere)
    with pytest.raises(BomLensEngineError, match="symbolic link"):
        runner.verified_dir()
    (workspace / "bomlens-verified").unlink()
    (workspace / "bomlens-verified").mkdir()
    (workspace / "bomlens-verified" / "stale.json").write_text("{}")
    fresh = runner.verified_dir()
    assert list(fresh.iterdir()) == []


# ---------------------------------------------------------------------------
# Contract 1.2.0: the trivy-db role, the database age, the proxy
# ---------------------------------------------------------------------------


def _plan_without_trivy_role(run: Run) -> None:
    """A plan from an image before BomLens #203: no ``trivy-db`` mount."""
    import json

    write_plan("node-ok")(run)
    path = run.host("/host-output") / "bomlens-plan.json"
    document = json.loads(path.read_text())
    stage = next(s for s in document["stages"] if s["id"] == "postprocess")
    before = len(stage["mounts"])
    stage["mounts"] = [m for m in stage["mounts"] if m["role"] != "trivy-db"]
    assert len(stage["mounts"]) == before - 1
    path.write_text(json.dumps(document))


def _trivy_mounts(run: Run) -> dict[str, dict[str, str]]:
    return {t: f for t, f in run.mounts.items() if t.startswith(runner_mod.TRIVY_DB_TARGET)}


@pytest.mark.parametrize("plan_has_role", [True, False], ids=["role-in-plan", "earlier-plan"])
def test_stage_2_reads_the_worker_trivy_database_through_the_role(
    workspace: Path, plan_has_role: bool
) -> None:
    """The plan's ``trivy-db`` role is connected to the worker's database,
    read-only; a plan without the role (an earlier image) gets the same
    mount, since stage 2 cannot scan without a database."""
    effect = write_plan("node-ok") if plan_has_role else _plan_without_trivy_role
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=effect))
        .behave("stage1", Behaviour(effect=write_sbom(_output_file())))
    )
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    trivy = os.path.realpath(os.environ["TRIVY_CACHE_DIR"])
    assert _trivy_mounts(stage2) == {
        "/trivy-db": {"type": "bind", "src": trivy, "dst": "/trivy-db", "readonly": "true"}
    }
    assert stage2.env["TRIVY_CACHE_DIR"] == "/trivy-db"
    for label in ("plan", "stage1"):
        (run,) = docker.runs_of(label)
        assert _trivy_mounts(run) == {}, label


@pytest.mark.parametrize(
    ("hours", "days"),
    [
        (None, 16),
        ("168", 16),
        ("24", 4),
        ("25", 6),
        ("1", 4),
        ("0", 4),
        ("-48", 4),
        ("junk", 16),
        ("336", 30),
        ("337", 30),
        ("720", 30),
        # Past a float's range: integer arithmetic, no OverflowError.
        ("9" * 400, 30),
        # Past Python's int() digit limit: read as junk, the default.
        ("9" * 5000, 16),
    ],
)
def test_the_database_age_follows_the_refresh_interval(
    monkeypatch: pytest.MonkeyPatch, hours: str | None, days: int
) -> None:
    """ceil(TRIVY_DB_REFRESH_HOURS / 24) * 2 + 2, at most 30, read per call."""
    if hours is None:
        monkeypatch.delenv("TRIVY_DB_REFRESH_HOURS", raising=False)
    else:
        monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", hours)
    assert trivy_db_max_age_days() == days


@pytest.mark.parametrize(("hours", "capped"), [("336", False), ("337", True), ("9" * 400, True)])
def test_a_capped_database_age_is_logged(
    monkeypatch: pytest.MonkeyPatch, hours: str, capped: bool
) -> None:
    import structlog

    monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", hours)
    with structlog.testing.capture_logs() as captured:
        assert trivy_db_max_age_days() == runner_mod.TRIVY_DB_MAX_AGE_LIMIT_DAYS
    events = [e for e in captured if e["event"] == "bomlens_trivy_db_max_age_capped"]
    assert [e["log_level"] for e in events] == (["warning"] if capped else [])


def test_stage_2_is_told_the_database_age_trusca_allows(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The value comes from TRUSCA's refresh interval, read at run time, and
    never from a variable of the same name in the worker's environment."""
    monkeypatch.setenv("BOMLENS_TRIVY_DB_MAX_AGE_DAYS", "365")
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"] == "16"
    assert [a for a in stage2.argv if a.startswith("BOMLENS_TRIVY_DB_MAX_AGE_DAYS")] == [
        "BOMLENS_TRIVY_DB_MAX_AGE_DAYS=16"
    ]

    monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", "24")
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"] == "4"
    # Not even offered by name: the de-duplication in docker_run_argv would
    # hide a worker value here, so the pass-through is checked on its own.
    from integrations.bomlens.plan import load_plan
    from tests._bomlens_engine import PLAN_FIXTURES

    runner = _runner(workspace, _happy_docker())
    stage = runner.stage2_for(load_plan(PLAN_FIXTURES / "node-ok"))
    assert "BOMLENS_TRIVY_DB_MAX_AGE_DAYS" in stage.env_passthrough
    assert "BOMLENS_TRIVY_DB_MAX_AGE_DAYS" not in dict(runner._passthrough(stage))
    for label in ("plan", "stage1"):
        (run,) = docker.runs_of(label)
        assert "BOMLENS_TRIVY_DB_MAX_AGE_DAYS" not in run.env, label


def test_stage_1_gets_the_proxy_and_stage_2_does_not(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:8080")
    monkeypatch.setenv("https_proxy", "http://proxy.corp.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,.corp.example")
    # A local authenticating proxy on the worker's own loopback address.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("http_proxy", "localhost:3128")
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage1,) = docker.runs_of("stage1")
    assert {n: v for n, v in stage1.env.items() if n.lower().endswith("_proxy")} == {
        "HTTPS_PROXY": "http://proxy.corp.example:8080",
        "https_proxy": "http://proxy.corp.example:8080",
        "NO_PROXY": "localhost,127.0.0.1,.corp.example",
    }
    for label in ("plan", "stage2"):
        (run,) = docker.runs_of(label)
        assert not {n for n in run.env if n.lower().endswith("_proxy")}, label


def test_values_from_the_worker_environment_never_reach_a_command_line(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy URL can carry credentials, and a command line is visible to
    every process on the host. Values that come from the worker's environment
    go to the docker CLI's own environment and are named only (``--env
    NAME``); the container still gets them."""
    secret = "http://scan-user:s3cr3t-pa55@proxy.corp.example:8080"
    goproxy = "https://goproxy-user:t0ken@goproxy.corp.example"
    monkeypatch.setenv("HTTPS_PROXY", secret)
    monkeypatch.setenv("GOPROXY", goproxy)
    docker = _happy_docker()
    _runner(workspace, docker).run()
    for run in docker.runs:
        joined = "\n".join(run.argv)
        assert "s3cr3t-pa55" not in joined, run.label
        assert "t0ken" not in joined, run.label
    (stage1,) = docker.runs_of("stage1")
    assert stage1.by_name == {"HTTPS_PROXY", "GOPROXY"}
    assert stage1.option_values("--env").count("HTTPS_PROXY") == 1
    assert stage1.env["HTTPS_PROXY"] == secret
    assert docker.process_envs[docker.runs.index(stage1)] == {
        "HTTPS_PROXY": secret,
        "GOPROXY": goproxy,
    }
    # Containers that take nothing from the worker's environment give the
    # CLI no extra environment either.
    for run, extra in zip(docker.runs, docker.process_envs, strict=True):
        if run.label != "stage1":
            assert extra == {}, run.label


def test_the_docker_cli_gets_the_named_values_in_its_own_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["argv"], captured["env"] = argv, kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, b"c1\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    cli = runner_mod.DockerCli()
    secret = {"HTTPS_PROXY": "http://u:p@h:1"}
    assert cli.create(["--env", "HTTPS_PROXY", "img"], env=secret) == "c1"
    assert captured["argv"] == ["docker", "create", "--env", "HTTPS_PROXY", "img"]
    assert captured["env"]["HTTPS_PROXY"] == "http://u:p@h:1"
    assert captured["env"]["PATH"] == "/usr/bin:/bin"
    cli.create(["img"])
    assert captured["env"] is None


def test_stage_2_sets_each_trusca_value_exactly_once(workspace: Path) -> None:
    docker = _happy_docker()
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    names = [v.split("=", 1)[0] for v in stage2.option_values("--env")]
    for name, value in (
        ("SECURITY_ENRICH", "false"),
        ("GENERATE_SECURITY", "true"),
        ("TRIVY_OFFLINE_SCAN", "true"),
        ("BOMLENS_TRIVY_DB_MAX_AGE_DAYS", "16"),
    ):
        assert names.count(name) == 1, name
        assert stage2.env[name] == value, name
    assert len(names) == len(set(names))


def test_a_name_set_twice_keeps_the_last_value_and_trusca_wins_over_a_named_one(
    tmp_path: Path,
) -> None:
    from integrations.bomlens.runner import ContainerSpec, docker_run_argv

    spec = ContainerSpec(
        name="trusca-x-stage2",
        image="img@sha256:" + "a" * 64,
        user=None,
        entrypoint=None,
        args=(),
        workdir=None,
        mounts=(),
        extra_mount_args=(),
        env=(("SECURITY_ENRICH", "true"), ("MODE", "POSTPROCESS"), ("SECURITY_ENRICH", "false")),
        secret_env=(("SECURITY_ENRICH", "from-worker"), ("HTTPS_PROXY", "http://p:1")),
        network="none",
        read_only=True,
    )
    argv = docker_run_argv(spec, BindMounts(trivy_cache=tmp_path), "ab", 0)
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert envs == ["MODE=POSTPROCESS", "SECURITY_ENRICH=false", "HTTPS_PROXY"]
    assert spec.container_env()[1] == {"HTTPS_PROXY": "http://p:1"}


@pytest.mark.parametrize(
    ("value", "loopback"),
    [
        ("http://127.0.0.1:3128", True),
        ("127.0.0.1:3128", True),
        ("http://127.8.9.10", True),
        ("http://user:secret@127.0.0.1:3128", True),
        ("http://localhost:3128", True),
        ("localhost", True),
        ("socks5://proxy.localhost:1080", True),
        ("http://[::1]:3128", True),
        (" http://LOCALHOST:3128 ", True),
        ("http://proxy.corp.example:8080", False),
        ("http://10.0.0.5:3128", False),
        ("http://localhost.corp.example:3128", False),
        ("http://127.0.0.1.nip.io:3128", False),
        ("http://[::ffff:10.0.0.1]:3128", False),
        ("http://[bad", False),
        ("", False),
        # A trailing dot, the legacy IPv4 spellings, the unspecified address.
        ("http://localhost.:3128", True),
        ("http://127.0.0.1.:3128", True),
        ("http://127.1:3128", True),
        ("http://2130706433:3128", True),
        ("http://0x7f000001:3128", True),
        ("http://0177.0.0.1:3128", True),
        ("http://0.0.0.0:3128", True),
        ("http://[::]:3128", True),
        ("http://[::ffff:127.0.0.1]:3128", True),
        ("http://0x0a000001:3128", False),
        ("http://167772161:3128", False),
        ("http://deadbeef:3128", False),
        ("http://proxy.corp.example.:3128", False),
    ],
)
def test_a_proxy_on_the_loopback_address_is_recognised(value: str, loopback: bool) -> None:
    assert is_loopback_proxy(value) is loopback


def test_an_ipv4_mapped_loopback_is_recognised_on_an_older_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before Python 3.13 (and some 3.12 patch releases), ``::ffff:127.0.0.1``
    did not read as loopback. The runner unwraps the IPv4 address itself, so
    the answer does not depend on the interpreter's patch level."""
    import ipaddress

    monkeypatch.setattr(
        ipaddress.IPv6Address,
        "is_loopback",
        property(lambda self: int(self) == 1),
    )
    assert not ipaddress.ip_address("::ffff:127.0.0.1").is_loopback
    assert is_loopback_proxy("http://[::ffff:127.0.0.1]:3128") is True
    assert is_loopback_proxy("http://[::ffff:10.0.0.1]:3128") is False


# ---------------------------------------------------------------------------
# Stage 2 runs with TRUSCA's environment, never the plan's
# ---------------------------------------------------------------------------


def _write_plan_with_stage_2_env(**env: str) -> Any:
    """PLAN effect: the captured plan with stage 2's ``env`` changed. Every
    name is on the stage 2 allow list, so the reader keeps the values."""
    import json

    from tests._bomlens_engine import PLAN_FIXTURES

    def effect(run: Run) -> None:
        write_plan("node-ok")(run)
        document = json.loads((PLAN_FIXTURES / "node-ok" / "bomlens-plan.json").read_text())
        stage = next(s for s in document["stages"] if s["id"] == "postprocess")
        stage["env"].update(env)
        (run.host("/host-output") / "bomlens-plan.json").write_text(json.dumps(document))

    return effect


def test_stage_2_never_takes_values_from_the_plan_env(workspace: Path) -> None:
    """The plan is untrusted. Allow-listed stage 2 names only pass the
    reader's checks; the container gets TRUSCA's own values (security review
    of the 1.3.0 bump, L1)."""
    hostile = {"CYCLONEDX_SPEC_VERSION": "1.2", "PROJECT_NAME": "evil", "SECURITY_ENRICH": "true"}
    from integrations.bomlens.plan import STAGE_ENV_ALLOWED

    # Allow-listed, so the reader keeps them and only the runner can drop them.
    assert set(hostile) <= STAGE_ENV_ALLOWED[2]
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=_write_plan_with_stage_2_env(**hostile)))
        .behave("stage1", Behaviour(effect=write_sbom(_output_file())))
    )
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    assert "CYCLONEDX_SPEC_VERSION" not in stage2.env
    # TRUSCA's project name, the one stage 1 got too.
    (stage1,) = docker.runs_of("stage1")
    assert stage2.env["PROJECT_NAME"] == stage1.env["PROJECT_NAME"] != "evil"
    assert stage2.env["SECURITY_ENRICH"] == "false"
    assert not any(v in ("evil", "1.2") for v in stage2.env.values())


def test_only_the_provided_roles_get_a_host_path(tmp_path: Path) -> None:
    """``plan.py`` lets a role sit under TRUSCA's ``/tmp`` only when it is not
    in ``PROVIDED_ROLES``, because the runner never mounts it. That holds only
    while the two agree: a role the runner starts mounting must join the set."""
    from integrations.bomlens.plan import MOUNT_ROLES, PROVIDED_ROLES, TRIVY_DB_ROLE, Mount

    mounted = {
        role
        for role in MOUNT_ROLES
        if BomLensRunner._mount_host(
            Mount(
                role=role,
                target="/x",
                mode="ro",
                source="bomlens-plan/f" if role == "plan-file" else None,
            ),
            source=tmp_path / "src",
            output=tmp_path / "out",
            plan_files={"bomlens-plan/f": tmp_path / "f"},
            guard=tmp_path / "guard",
        )
        is not None
    }
    # The Trivy database is mounted by run_stage2 itself, not through here.
    assert mounted | {TRIVY_DB_ROLE} == PROVIDED_ROLES
