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

import pytest

from integrations.bomlens import runner as runner_mod
from integrations.bomlens.plan import BomLensPlanRejected
from integrations.bomlens.runner import (
    BindMounts,
    BomLensEngineError,
    BomLensRunner,
    EngineContainerStillRunning,
    EngineLayout,
    NamedVolumeMounts,
    confined_path,
    pinned_image,
    resolve_images,
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
        "ghcr.io/haksungjang/bomlens:sha-0000000@sha256:" + "1" * 64,
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


def test_a_container_that_failed_to_start_is_removed_by_name(workspace: Path) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(fail_start=True))
    )
    with pytest.raises(BomLensEngineError, match="did not start"):
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
        ("ghcr.io/haksungjang/bomlens:sha-d8d3323", "is not pinned by a digest"),
        ("ghcr.io/haksungjang/bomlens@sha256:" + "a" * 64, "has no tag"),
        ("ghcr.io/haksungjang/bomlens:latest@sha256:" + "a" * 64, "is not a pinned image"),
        ("ghcr.io/haksungjang/bomlens:1.0@sha256:abc", "is not a pinned image"),
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
    assert style.trivy_db_args() == [
        "--mount",
        "type=volume,src=trusca_trivy-cache,dst=/trivy-db,readonly",
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
