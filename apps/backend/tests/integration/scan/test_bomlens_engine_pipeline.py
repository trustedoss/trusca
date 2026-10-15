"""SCAN_ENGINE=bomlens end to end: the runner, the adapter and the scan row.

The containers are played by :class:`tests._bomlens_engine.FakeDocker`, whose
effects copy real BomLens output (``tests/fixtures/bomlens/``) into the
folders the runner mounted. Everything after the containers is real: the
adapter writes through the persist paths into the database, and each test
reads the rows back in a separate session, so a write that is never committed
cannot pass.

The last test runs the published images on a real Docker daemon. It is local
only (``BOMLENS_ENGINE_DOCKER_TEST=1``): CI has no BomLens image pull budget
and no way to share a host folder with the daemon.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from integrations.bomlens import runner as runner_mod
from models import Scan
from services import scan_outcome as so
from tests._bomlens_engine import (
    Behaviour,
    FakeDocker,
    captured_images,
    plan_as_published,
    write_plan,
    write_result,
    write_sbom,
)
from tests.integration.scan.test_bomlens_result import (
    _component_count,
    _finding_ids,
    _metadata,
    _seed_running_source_scan,
)

pytestmark = pytest.mark.integration

GOLDEN_NODE = Path(__file__).resolve().parents[2] / "e2e" / "golden" / "fixtures" / "node"
GOLDEN_GRADLE = GOLDEN_NODE.parent / "gradle"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    from tests._db_required import migrate_to_head

    migrate_to_head()


@pytest.fixture(autouse=True)
def _engine_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    trivy = tmp_path / "trivy" / "db"
    trivy.mkdir(parents=True)
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy"))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE", captured_images())
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "bridge")
    monkeypatch.setenv("SCANCODE_ENABLED", "false")
    return root


def _install(monkeypatch: pytest.MonkeyPatch, docker: FakeDocker) -> None:
    monkeypatch.setattr(runner_mod, "DockerCli", lambda: docker)
    monkeypatch.setattr("tasks.scan_source_bomlens.shutil.which", lambda _name: "/usr/bin/docker")


def _workspace(root: Path, scan_id: uuid.UUID, source: Path = GOLDEN_NODE) -> tuple[Path, Path]:
    workspace = root / str(scan_id)
    project_root = workspace / "source" / "repo"
    shutil.copytree(source, project_root)
    return workspace, project_root


def _run_pipeline(root: Path, scan_id: uuid.UUID, source: Path = GOLDEN_NODE) -> None:
    from tasks.scan_source_bomlens import run_bomlens_pipeline

    workspace, project_root = _workspace(root, scan_id, source)
    run_bomlens_pipeline(
        scan_uuid=scan_id,
        project_id=_project_id(scan_id),
        workspace=workspace,
        source_dir=workspace / "source",
        project_root=project_root,
        manifest_inventory=None,
    )


def _project_id(scan_id: uuid.UUID) -> uuid.UUID:
    from core.db import sync_session_scope

    with sync_session_scope() as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        return scan.project_id


def _scan(session: Session, scan_id: uuid.UUID) -> Scan:
    session.expire_all()
    scan = session.get(Scan, scan_id)
    assert scan is not None
    return scan


# ---------------------------------------------------------------------------
# Lifecycle: stage 1 OOM -> restore -> stage 2 fallback -> persisted
# ---------------------------------------------------------------------------


def test_oom_restore_fallback_then_the_scan_is_persisted_as_degraded(
    monkeypatch: pytest.MonkeyPatch, _engine_env: Path, sync_session: Session
) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(oom=True, output=b"Killed\n"))
        # The real BomLens output of a run whose generate step fell back to
        # the manifest-only reader (fixtures/bomlens/PROVENANCE.md).
        .behave("stage2", Behaviour(effect=write_result("source-degraded")))
    )
    _install(monkeypatch, docker)
    scan_id = _seed_running_source_scan()

    _run_pipeline(_engine_env, scan_id)

    assert [r.label for r in docker.runs] == ["plan", "stage1", "restore", "stage2", "chown"]
    (stage2,) = docker.runs_of("stage2")
    assert stage2.env["BOMLENS_STAGE1_OOM"] == "true"
    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100
    meta = _metadata(sync_session, scan_id)
    run = meta["scan_engine_run"]
    assert (run["stage1_oom"], run["source_restored"], run["plan_status"]) == (True, True, "ok")
    assert "generate" in {entry["stage"] for entry in so.degraded_stages(meta)}
    assert meta["scan_engine"]["run_status"] == "succeeded_degraded"
    assert _component_count(sync_session, scan_id) > 0


# ---------------------------------------------------------------------------
# The task routes SCAN_ENGINE=bomlens to the engine
# ---------------------------------------------------------------------------


def _clean_docker() -> FakeDocker:
    return (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(effect=write_sbom("app_1.0_bom.json")))
        .behave("stage2", Behaviour(effect=write_result("source-ok")))
    )


def test_the_engine_task_runs_the_engine(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """The engine task decides the engine, whatever this worker's SCAN_ENGINE."""
    from tasks.scan_source import scan_source_engine_task

    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("SCAN_ENGINE", "inprocess")
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    scan_id = _seed_running_source_scan()

    result = scan_source_engine_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    assert [r.label for r in docker.runs] == ["plan", "stage1", "stage2", "chown"]
    assert _component_count(sync_session, scan_id) == 1
    assert _finding_ids(sync_session, scan_id) == [
        "CVE-2025-13465",
        "CVE-2026-2950",
        "CVE-2026-4800",
    ]
    # The engine's containers were all removed, and the workspace with them.
    assert sorted(docker.removed) == sorted(docker.containers)
    assert not (Path(os.environ["WORKSPACE_HOST_PATH"]) / str(scan_id)).exists()


def test_the_in_process_task_never_touches_docker(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """Even on a worker whose SCAN_ENGINE says bomlens: the in-process task
    never runs the engine (only the engine worker may reach the Docker API)."""
    from tasks.scan_source import scan_source_task
    from tests.integration.scan.test_scan_source_pipeline_mock import _stub_trivy_empty

    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("SCAN_ENGINE", "bomlens")
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    _stub_trivy_empty(monkeypatch)
    scan_id = _seed_running_source_scan()

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback
    assert _scan(sync_session, scan_id).status == "succeeded"
    assert docker.runs == []


# ---------------------------------------------------------------------------
# SCAN_BOMLENS_STAGE1_IMAGE_PREFIX: stage 1 from a registry mirror
# ---------------------------------------------------------------------------

MIRROR = "registry.example/mirror/cdxgen/"


def test_the_engine_task_runs_stage_1_from_the_configured_mirror(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """The task reads the setting, PLAN gets it, and stage 1 runs the image the
    published image names for it (``fixtures/bomlens-plan/node-mirror-prefix``)."""
    from tasks.scan_source import scan_source_engine_task

    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=plan_as_published()))
        .behave("stage1", Behaviour(effect=write_sbom("app_1.0_bom.json")))
        .behave("stage2", Behaviour(effect=write_result("source-ok")))
    )
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", MIRROR.rstrip("/"))
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", raising=False)
    scan_id = _seed_running_source_scan()

    result = scan_source_engine_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    (plan,) = docker.runs_of("plan")
    assert plan.env["CDXGEN_IMAGE_PREFIX"] == MIRROR
    (stage1,) = docker.runs_of("stage1")
    assert stage1.image.startswith(MIRROR + "cdxgen-alpine-node24:13.3.0@sha256:")
    meta = _metadata(sync_session, scan_id)
    assert meta["scan_engine_run"]["stage1_image"] == stage1.image


@pytest.mark.parametrize(
    "value", ["https://registry.example/mirror/", "u:p@reg.example/x", "localhost:5000"]
)
def test_a_malformed_mirror_prefix_fails_the_scan_before_any_container(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    from tasks.scan_source import scan_source_engine_task

    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", value)
    scan_id = _seed_running_source_scan()

    scan_source_engine_task.apply(args=[str(scan_id)])

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    assert scan.error_message is not None
    assert "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX" in scan.error_message
    assert "is not in the right form" in scan.error_message
    # The value (credentials, in the second case) is in neither the scan's
    # error nor the worker's log.
    logged = capsys.readouterr()
    for text in (scan.error_message, logged.out, logged.err, caplog.text):
        assert value not in text
        assert "u:p" not in text
    # The capture did see the failure being logged, with its message.
    failure = next(
        line for line in logged.out.splitlines() if "scan_source_engine_failed" in line
    )
    assert "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX is not in the right form" in failure
    assert docker.runs == []
    assert _component_count(sync_session, scan_id) == 0


@pytest.mark.parametrize(
    ("docker", "fragment"),
    [
        (
            FakeDocker()
            .behave("plan", Behaviour(effect=write_plan("node-ok")))
            .behave("stage1", Behaviour(stuck=True)),
            "still running after it was killed",
        ),
        (
            FakeDocker()
            .behave("plan", Behaviour(effect=write_plan("node-ok")))
            .behave("stage1", Behaviour(effect=write_sbom("app_1.0_bom.json")))
            .behave("stage2", Behaviour(exit_code=1, effect=write_result("source-run-failed"))),
            "the BomLens run failed",
        ),
        (FakeDocker().behave("plan", Behaviour(exit_code=2)), "MODE=PLAN exited 2"),
        (
            FakeDocker()
            .behave("plan", Behaviour(effect=write_plan("node-ok")))
            .behave("stage1", Behaviour(effect=write_sbom("app_1.0_bom.json")))
            .behave(
                "stage2",
                Behaviour(
                    exit_code=1, output=b"engine-result.sh: line 255: out: Permission denied\n"
                ),
            ),
            "stage 2 exited 1 without writing a result manifest; "
            "last output: engine-result.sh: line 255: out: Permission denied",
        ),
    ],
    ids=["container-will-not-stop", "engine-run-failed", "plan-failed", "no-manifest"],
)
def test_an_engine_failure_fails_the_scan_with_a_message_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    _engine_env: Path,
    docker: FakeDocker,
    fragment: str,
) -> None:
    from tasks.scan_source import scan_source_engine_task

    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    scan_id = _seed_running_source_scan()

    scan_source_engine_task.apply(args=[str(scan_id)])

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    assert scan.error_message is not None
    assert scan.error_message.startswith("scan engine: ")
    assert fragment in scan.error_message
    assert str(_engine_env) not in scan.error_message
    assert _component_count(sync_session, scan_id) == 0


def test_a_run_that_left_no_result_still_records_which_engine_ran(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, _engine_env: Path
) -> None:
    """Lifecycle: an earlier run's full engine record on the same row is
    replaced, so a failed run never reads as the one before it."""
    from tasks.scan_source import scan_source_engine_task

    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(effect=write_sbom("app_1.0_bom.json")))
        .behave("stage2", Behaviour(exit_code=1))
    )
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    scan_id = _seed_running_source_scan()
    from core.db import sync_session_scope

    with sync_session_scope() as session:
        row = session.get(Scan, scan_id)
        assert row is not None
        row.scan_metadata = {"scan_engine": {"name": "bomlens", "run_status": "succeeded"}}
        session.commit()

    scan_source_engine_task.apply(args=[str(scan_id)])

    scan = _scan(sync_session, scan_id)
    assert scan.status == "failed"
    assert "stage 2 exited 1 without writing a result manifest" in (scan.error_message or "")
    meta = _metadata(sync_session, scan_id)
    image = runner_mod.pinned_image(captured_images(), "image")
    assert meta["scan_engine"] == {
        "name": "bomlens",
        "image": image.ref,
        "image_digest": image.digest,
        "run_status": None,
    }
    assert meta["scan_engine_run"]["stage2_exit_code"] == 1


def test_an_unsupported_plan_still_ends_in_a_degraded_success(
    monkeypatch: pytest.MonkeyPatch, _engine_env: Path, sync_session: Session
) -> None:
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("android-unsupported")))
        .behave("stage2", Behaviour(effect=write_result("source-degraded")))
    )
    _install(monkeypatch, docker)
    scan_id = _seed_running_source_scan()

    _run_pipeline(_engine_env, scan_id)

    assert [r.label for r in docker.runs] == ["plan", "stage2", "chown"]
    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded"
    meta = _metadata(sync_session, scan_id)
    assert meta["scan_engine_run"]["plan_status"] == "unsupported"
    assert meta["scan_engine_run"]["plan_reason"] == "android-sdk-image"
    assert "generate" in {entry["stage"] for entry in so.degraded_stages(meta)}


def test_progress_only_moves_forward(
    monkeypatch: pytest.MonkeyPatch, _engine_env: Path, sync_session: Session
) -> None:
    published: list[tuple[str, int]] = []

    def record(scan_uuid: Any, *, step: str, percent: int) -> None:  # noqa: ARG001
        published.append((step, percent))

    monkeypatch.setattr("tasks._scan_pipeline.publish_progress", record)
    _install(monkeypatch, _clean_docker())
    scan_id = _seed_running_source_scan()

    _run_pipeline(_engine_env, scan_id)

    steps = [s for s, _ in published]
    assert steps[:4] == ["prep", "cdxgen", "scancode", "sign"]
    percents = [p for _, p in published]
    assert percents == sorted(percents), published


# ---------------------------------------------------------------------------
# The real images on a real Docker daemon (local only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.getenv("BOMLENS_ENGINE_DOCKER_TEST") != "1",
    reason="runs the published BomLens images on a local Docker daemon "
    "(set BOMLENS_ENGINE_DOCKER_TEST=1 and BOMLENS_ENGINE_DOCKER_DIR to a folder the daemon sees)",
)
def test_the_published_images_scan_an_npm_project(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    host_dir = Path(os.environ["BOMLENS_ENGINE_DOCKER_DIR"]) / uuid.uuid4().hex
    host_dir.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(host_dir))
    monkeypatch.setenv(
        "TRIVY_CACHE_DIR",
        os.getenv("BOMLENS_ENGINE_TRIVY_CACHE", str(Path.home() / ".cache" / "trivy")),
    )
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE")
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", os.getenv("BOMLENS_ENGINE_NETWORK", "bridge"))
    # This test process is not a container, so it has no networks of its own
    # for the runner to compare; in compose it is worker-engine and does.
    monkeypatch.setattr(runner_mod.DockerCli, "own_networks", lambda self: frozenset())
    scan_id = _seed_running_source_scan()

    _run_pipeline(host_dir, scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    assert _component_count(sync_session, scan_id) == 1
    assert "CVE-2025-13465" in _finding_ids(sync_session, scan_id)
    meta = _metadata(sync_session, scan_id)
    assert meta["scan_engine"]["run_status"] == "succeeded"
    assert meta["scan_engine_run"]["stage1_exit_code"] == 0


@pytest.mark.skipif(
    os.getenv("BOMLENS_ENGINE_DOCKER_TEST") != "1" or not os.getenv("BOMLENS_ENGINE_STAGE1_MIRROR"),
    reason="runs the published BomLens images with stage 1 from a registry mirror "
    "(BOMLENS_ENGINE_STAGE1_MIRROR, holding cdxgen-alpine-node24 copied as the "
    "offline-install guide says)",
)
def test_the_published_images_take_stage_1_from_a_mirror(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    mirror = os.environ["BOMLENS_ENGINE_STAGE1_MIRROR"]
    host_dir = Path(os.environ["BOMLENS_ENGINE_DOCKER_DIR"]) / uuid.uuid4().hex
    host_dir.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(host_dir))
    monkeypatch.setenv(
        "TRIVY_CACHE_DIR",
        os.getenv("BOMLENS_ENGINE_TRIVY_CACHE", str(Path.home() / ".cache" / "trivy")),
    )
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE")
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_REPOSITORIES", raising=False)
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", mirror)
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", os.getenv("BOMLENS_ENGINE_NETWORK", "bridge"))
    monkeypatch.setattr(runner_mod.DockerCli, "own_networks", lambda self: frozenset())
    scan_id = _seed_running_source_scan()

    _run_pipeline(host_dir, scan_id)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    meta = _metadata(sync_session, scan_id)
    assert meta["scan_engine_run"]["stage1_image"].startswith(
        mirror.rstrip("/") + "/cdxgen-alpine-node24:13.3.0@sha256:"
    )
    assert meta["scan_engine_run"]["stage1_exit_code"] == 0
    assert _component_count(sync_session, scan_id) == 1


@pytest.mark.skipif(
    os.getenv("BOMLENS_ENGINE_DOCKER_TEST") != "1",
    reason="runs the published BomLens images on a local Docker daemon "
    "(set BOMLENS_ENGINE_DOCKER_TEST=1 and BOMLENS_ENGINE_DOCKER_DIR to a folder the daemon sees)",
)
def test_the_published_images_resolve_a_gradle_project_under_a_read_only_root(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """The runner gives stage 1 a read-only root. Before BomLens #209 Gradle
    failed there at once (it could not write the account's ~/.gradle) and a
    Gradle project came back with no component, ``generate`` degraded
    (``no_components``)."""
    host_dir = Path(os.environ["BOMLENS_ENGINE_DOCKER_DIR"]) / uuid.uuid4().hex
    host_dir.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(host_dir))
    monkeypatch.setenv(
        "TRIVY_CACHE_DIR",
        os.getenv("BOMLENS_ENGINE_TRIVY_CACHE", str(Path.home() / ".cache" / "trivy")),
    )
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE")
    monkeypatch.delenv("SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT", raising=False)
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", os.getenv("BOMLENS_ENGINE_NETWORK", "bridge"))
    monkeypatch.setattr(runner_mod.DockerCli, "own_networks", lambda self: frozenset())
    scan_id = _seed_running_source_scan()

    _run_pipeline(host_dir, scan_id, GOLDEN_GRADLE)

    scan = _scan(sync_session, scan_id)
    assert scan.status == "succeeded", scan.error_message
    meta = _metadata(sync_session, scan_id)
    assert meta["scan_engine_run"]["stage1_exit_code"] == 0
    assert so.STAGE_OUTCOMES_KEY not in meta or "generate" not in meta[so.STAGE_OUTCOMES_KEY]
    assert _component_count(sync_session, scan_id) > 0


def _docker(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *argv], capture_output=True, text=True, check=check)


@pytest.mark.skipif(
    os.getenv("BOMLENS_ENGINE_DOCKER_TEST") != "1",
    reason="needs a local Docker daemon (set BOMLENS_ENGINE_DOCKER_TEST=1)",
)
def test_root_owned_engine_files_become_deletable_by_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ownership reset on a named volume, where ownership is the daemon's own.

    A root container leaves a 0700 folder with a file in the engine scratch
    space and in the source tree, as stage 1 does. After the reset nothing
    under the scan is owned by anyone but the worker's user, and that user
    can delete the lot (security review on #522). A bind mount cannot show
    this on a Mac: its file sharing reports every file as root's.
    """
    uid, gid = os.getuid(), os.getgid()
    volume = f"trusca-chown-test-{uuid.uuid4().hex[:12]}"
    root = tmp_path / "ws"
    scan_dir = root / "scan"
    for folder in ("bomlens/tmp-stage1/sbomhome", "source/repo"):
        (scan_dir / folder).mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", volume)
    monkeypatch.setenv("SCAN_WORKSPACE_MOUNT", str(root))
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE", raising=False)
    _docker("volume", "create", volume)
    try:
        _docker(
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{volume}:/v",
            "alpine:3.19.0",
            "sh",
            "-c",
            "mkdir -p /v/scan/bomlens/tmp-stage1/sbomhome /v/scan/source/repo"
            " && touch /v/scan/bomlens/tmp-stage1/sbomhome/cache /v/scan/source/repo/Gemfile.lock"
            " && chmod 700 /v/scan/bomlens/tmp-stage1/sbomhome"
            # The folders the worker itself created are the worker's.
            f" && chown {uid}:{gid} /v/scan /v/scan/source /v/scan/source/repo"
            " /v/scan/bomlens /v/scan/bomlens/tmp-stage1",
        )
        before = _docker(
            "run",
            "--rm",
            "-v",
            f"{volume}:/v:ro",
            "alpine:3.19.0",
            "find",
            "/v/scan",
            "!",
            "-user",
            str(uid),
        ).stdout.split()
        assert "/v/scan/bomlens/tmp-stage1/sbomhome/cache" in before

        runner = runner_mod.BomLensRunner(
            scan_uuid=uuid.uuid4(),
            layout=runner_mod.EngineLayout(scan_dir=scan_dir, source=scan_dir / "source" / "repo"),
            images=runner_mod.resolve_images(deep_license=False),
            project_name="chown-test",
            project_version="1",
            fetch_license=False,
        )
        runner.reclaim_ownership()

        after = _docker(
            "run",
            "--rm",
            "-v",
            f"{volume}:/v:ro",
            "alpine:3.19.0",
            "find",
            "/v/scan/bomlens",
            "/v/scan/source/repo",
            "!",
            "-user",
            str(uid),
        ).stdout.split()
        assert after == []
        removed = _docker(
            "run",
            "--rm",
            "--user",
            f"{uid}:{gid}",
            "-v",
            f"{volume}:/v",
            "alpine:3.19.0",
            "rm",
            "-rf",
            "/v/scan/bomlens",
            "/v/scan/source/repo",
            check=False,
        )
        assert removed.returncode == 0, removed.stderr
    finally:
        _docker("volume", "rm", "-f", volume, check=False)


@pytest.mark.parametrize(
    ("master", "non_deployable", "expected"),
    [("true", "true", False), ("true", "false", True), ("false", "true", True)],
)
def test_the_scope_filter_setting_reaches_the_engine(
    monkeypatch: pytest.MonkeyPatch,
    _engine_env: Path,
    master: str,
    non_deployable: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("SCAN_SCOPE_FILTER_ENABLED", master)
    monkeypatch.setenv("SCAN_SCOPE_FILTER_NON_DEPLOYABLE_ENABLED", non_deployable)
    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed_running_source_scan()

    _run_pipeline(_engine_env, scan_id)

    for label in ("stage1", "stage2"):
        (run,) = docker.runs_of(label)
        assert (run.env.get("BOMLENS_INCLUDE_NON_SHIPPED") == "true") is expected, label


def test_verified_copies_live_outside_every_engine_mount(
    monkeypatch: pytest.MonkeyPatch, _engine_env: Path, sync_session: Session
) -> None:
    from sqlalchemy import select

    from models import ScanArtifact

    docker = _clean_docker()
    _install(monkeypatch, docker)
    scan_id = _seed_running_source_scan()

    _run_pipeline(_engine_env, scan_id)

    paths = [
        Path(p)
        for p in sync_session.execute(
            select(ScanArtifact.storage_path).where(
                ScanArtifact.scan_id == scan_id,
                ScanArtifact.kind.in_(("sbom_cyclonedx", "trivy_sbom_report")),
            )
        ).scalars()
    ]
    assert len(paths) == 2
    assert {p.parent.name for p in paths} == {"bomlens-verified"}
    mounted = {f["src"] for r in docker.runs for f in r.mounts.values()}
    assert not any(str(p.parent) == m or str(p).startswith(m + "/") for p in paths for m in mounted)


def test_the_engine_s_empty_finding_decides_the_component_outcome(
    monkeypatch: pytest.MonkeyPatch, _engine_env: Path, sync_session: Session
) -> None:
    """The captured run declared dependencies and resolved none. TRUSCA's own
    inventory is not given here, so only the engine's finding can make the
    outcome say "manifests but no components"."""
    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(effect=write_sbom("app_1.0_bom.json")))
        .behave("stage2", Behaviour(effect=write_result("source-no-components")))
    )
    _install(monkeypatch, docker)
    scan_id = _seed_running_source_scan()

    _run_pipeline(_engine_env, scan_id)

    meta = _metadata(sync_session, scan_id)
    assert meta[so.METADATA_KEY] == so.EMPTY_WITH_MANIFESTS


def test_a_scan_cancelled_mid_run_stops_without_writing_over_it(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """worker-engine takes no revoke; the pipeline sees the cancellation at
    its next stage and leaves the row as the cancel left it."""
    from tasks.scan_source import scan_source_engine_task

    scan_id = _seed_running_source_scan()

    def cancel_during_stage1(run: Any) -> None:
        write_sbom("app_1.0_bom.json")(run)
        from core.db import sync_session_scope

        with sync_session_scope() as session:
            scan = session.get(Scan, scan_id)
            assert scan is not None
            scan.status = "cancelled"
            scan.error_message = "cancelled by admin"
            session.commit()

    docker = (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan("node-ok")))
        .behave("stage1", Behaviour(effect=cancel_during_stage1))
        .behave("stage2", Behaviour(effect=write_result("source-ok")))
    )
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")

    result = scan_source_engine_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == ("cancelled", "cancelled by admin")
    assert not docker.runs_of("stage2")
    assert _component_count(sync_session, scan_id) == 0


# ---------------------------------------------------------------------------
# Third review (N-M2, N-L4): an ended scan stays ended
# ---------------------------------------------------------------------------


def _set(scan_id: uuid.UUID, **values: Any) -> None:
    from core.db import sync_session_scope

    with sync_session_scope() as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        for key, value in values.items():
            setattr(scan, key, value)
        session.commit()


@pytest.mark.parametrize("task_name", ["scan_source_engine_task", "scan_source_task"])
@pytest.mark.parametrize("ended", ["cancelled", "failed", "succeeded"])
def test_a_scan_that_ended_while_queued_is_not_run(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, task_name: str, ended: str
) -> None:
    """Cancelled while it waited in the queue: worker-engine never got the
    revoke, so the task arrives anyway. It must not restart the scan, and no
    engine container may start. Same rule for the in-process task."""
    import tasks.scan_source as scan_source

    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    scan_id = _seed_running_source_scan()
    _set(scan_id, status=ended, error_message="ended before the task ran")

    result = getattr(scan_source, task_name).apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == (ended, "ended before the task ran")
    assert docker.runs == []


def test_a_queued_scan_still_runs(monkeypatch: pytest.MonkeyPatch, sync_session: Session) -> None:
    from tasks.scan_source import scan_source_engine_task

    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    scan_id = _seed_running_source_scan()
    _set(scan_id, status="queued")

    scan_source_engine_task.apply(args=[str(scan_id)])

    assert _scan(sync_session, scan_id).status == "succeeded"
    assert docker.runs_of("stage2")


def test_an_engine_error_after_a_cancel_does_not_overwrite_it(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """The reaper removed the containers of a cancelled scan mid-stage and the
    runner raised; the failure is not recorded over the cancellation."""
    from tasks.scan_source import scan_source_engine_task

    scan_id = _seed_running_source_scan()

    def cancel(run: Any) -> None:
        del run
        _set(scan_id, status="cancelled", error_message="cancelled by admin")

    docker = FakeDocker().behave("plan", Behaviour(exit_code=137, effect=cancel))
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")

    result = scan_source_engine_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == ("cancelled", "cancelled by admin")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("running", "failed"),
        ("queued", "failed"),
        ("cancelled", "cancelled"),
        ("succeeded", "succeeded"),
    ],
)
def test_a_failure_is_recorded_only_over_queued_or_running(
    sync_session: Session, before: str, after: str
) -> None:
    """The shared writer every pipeline uses, in-process ones included."""
    from tasks._scan_pipeline import record_terminal_failure

    scan_id = _seed_running_source_scan()
    _set(scan_id, status=before, error_message=None)

    record_terminal_failure(scan_id, "boom")

    scan = _scan(sync_session, scan_id)
    assert scan.status == after
    assert scan.error_message == ("boom" if after == "failed" else None)


@pytest.mark.parametrize("writer", ["shared", "container"])
@pytest.mark.parametrize("before", ["running", "queued", "cancelled", "failed", "succeeded"])
def test_success_is_recorded_only_over_running(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session, writer: str, before: str
) -> None:
    """Both success writers (the shared one every source and SBOM pipeline
    uses, and the container pipeline's) against every state: only a running
    scan becomes succeeded, and nothing is published for the others."""
    import tasks._scan_pipeline as shared
    import tasks.scan_container as container

    published: list[str] = []
    for module in (shared, container):
        monkeypatch.setattr(
            module, "publish_progress", lambda _id, *, step, percent: published.append(step)
        )
    scan_id = _seed_running_source_scan()
    _set(scan_id, status=before, error_message=f"was {before}")

    (shared.mark_succeeded if writer == "shared" else container._mark_succeeded)(scan_id)

    scan = _scan(sync_session, scan_id)
    if before == "running":
        assert (scan.status, scan.progress_percent) == ("succeeded", 100)
        assert scan.completed_at is not None
        assert published == ["succeeded"]
    else:
        assert (scan.status, scan.error_message) == (before, f"was {before}")
        assert published == []


def test_a_cancel_after_the_last_status_check_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch, sync_session: Session
) -> None:
    """The cancel lands between the pipeline's last running check and the
    success write (the finalize stage). The scan stays cancelled."""
    import tasks.scan_source as scan_source
    from tasks.scan_source import scan_source_engine_task

    docker = _clean_docker()
    _install(monkeypatch, docker)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    scan_id = _seed_running_source_scan()
    real_set_stage = scan_source._set_stage

    def set_stage_then_cancel(scan_uuid: uuid.UUID, stage: str, **kwargs: Any) -> None:
        real_set_stage(scan_uuid, stage, **kwargs)
        if stage == "finalize":
            _set(scan_id, status="cancelled", error_message="cancelled by user")

    monkeypatch.setattr(scan_source, "_set_stage", set_stage_then_cancel)

    result = scan_source_engine_task.apply(args=[str(scan_id)])
    assert result.successful(), result.traceback

    scan = _scan(sync_session, scan_id)
    assert (scan.status, scan.error_message) == ("cancelled", "cancelled by user")
    # The run did reach the finalize stage, so the success writer was what
    # had to refuse.
    assert docker.runs_of("stage2")
    assert scan.current_step == "finalize"
