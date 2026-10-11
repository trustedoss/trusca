"""Security review on #522: the runner's answers to M2, M3, L1, L2, L3.

Same harness as ``test_runner.py``: the containers are played by
:class:`tests._bomlens_engine.FakeDocker` and every assertion is on what the
runner asked the Docker daemon for.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.bomlens import runner as runner_mod
from integrations.bomlens.plan import BomLensPlanRejected, parse_plan
from integrations.bomlens.runner import (
    BomLensEngineError,
    BomLensRunner,
    EngineLayout,
    _LogTail,
    check_requested_mounts,
    drain_log_stream,
    resolve_images,
    write_new_file,
)
from tests._bomlens_engine import (
    PLAN_FIXTURES,
    Behaviour,
    FakeDocker,
    Run,
    captured_images,
    write_plan,
    write_sbom,
)


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
    return scan_dir


def _output_file() -> str:
    return str(
        json.loads((PLAN_FIXTURES / "node-ok" / "bomlens-plan.json").read_text())["output_file"]
    )


def _docker(plan_case: str = "node-ok") -> FakeDocker:
    return (
        FakeDocker()
        .behave("plan", Behaviour(effect=write_plan(plan_case)))
        .behave("stage1", Behaviour(effect=write_sbom(_output_file())))
    )


def _runner(workspace: Path, docker: FakeDocker, name: str = "my-project") -> BomLensRunner:
    return BomLensRunner(
        scan_uuid=uuid.UUID(int=9),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source" / "repo"),
        images=resolve_images(deep_license=False),
        project_name=name,
        project_version="2.0",
        fetch_license=False,
        docker=docker,
    )


# ---------------------------------------------------------------------------
# M2: the registry network
# ---------------------------------------------------------------------------


def test_unknown_worker_networks_refuse_the_registry_stage(workspace: Path) -> None:
    docker = _docker()
    docker.networks = None
    with pytest.raises(BomLensEngineError, match="could not be read"):
        _runner(workspace, docker).run()
    assert not docker.runs_of("stage1")


def test_the_worker_network_is_refused_by_its_id_too(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "0f3c9a2b7d1e")
    docker = _docker()
    with pytest.raises(BomLensEngineError, match="worker's own network"):
        _runner(workspace, docker).run()


@pytest.mark.parametrize("name", ["trustedoss", "trustedoss-docker-api"])
def test_the_compose_networks_are_refused_by_name(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", name)
    docker = _docker()
    docker.networks = frozenset()  # even when the worker does not report it
    with pytest.raises(BomLensEngineError, match="is not allowed"):
        _runner(workspace, docker).run()


# ---------------------------------------------------------------------------
# M3: bounded log streaming
# ---------------------------------------------------------------------------


def _drain(data: bytes, budget: int) -> tuple[list[str], bytes]:
    lines: list[str] = []
    tail = _LogTail(limit=1024)
    drain_log_stream(
        io.BytesIO(data),
        stream_name="stdout",
        tail=tail,
        budget=[budget],
        budget_lock=threading.Lock(),
        callback=lambda _s, line: lines.append(line),
    )
    return lines, tail.value()


def test_a_line_longer_than_the_cap_is_cut_once_and_the_rest_dropped() -> None:
    long = b"x" * (runner_mod.LOG_LINE_MAX_BYTES * 3) + b"\nnext\n"
    lines, _ = _drain(long, budget=10**9)
    assert [len(line) for line in lines] == [runner_mod.LOG_LINE_MAX_BYTES, 4]


def test_past_the_budget_the_callback_stops_but_the_stream_is_read_to_the_end() -> None:
    data = b"".join(f"line {i}\n".encode() for i in range(1000))
    lines, tail = _drain(data, budget=50)
    assert lines == [f"line {i}" for i in range(len(lines))]
    assert len(lines) < 10
    # The tail still has the very end: the stream was read through.
    assert tail.endswith(b"line 999\n")
    assert len(tail) <= 1024


def test_the_stage_1_log_is_the_tail() -> None:
    tail = _LogTail(limit=10)
    for chunk in (b"aaaa", b"bbbb", b"cccc"):
        tail.add(chunk)
    assert tail.value() == b"bbbbcccc"


# ---------------------------------------------------------------------------
# L1: the stage 1 log is never written through what a container left
# ---------------------------------------------------------------------------


def test_a_fifo_at_the_log_path_is_replaced_not_opened(tmp_path: Path) -> None:
    fifo = tmp_path / "bomlens-stage1.log"
    os.mkfifo(fifo)
    done: list[bool] = []

    def write() -> None:
        write_new_file(fifo, b"log\n")
        done.append(True)

    worker = threading.Thread(target=write, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert done == [True], "writing blocked on the FIFO"
    assert fifo.read_bytes() == b"log\n"
    assert not fifo.is_symlink()


def test_a_link_at_the_log_path_is_replaced_not_followed(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "bomlens-stage1.log"
    link.symlink_to(victim)
    write_new_file(link, b"log\n")
    assert victim.read_text() == "keep"
    assert not link.is_symlink() and link.read_bytes() == b"log\n"


# ---------------------------------------------------------------------------
# L2: stage 2 is TRUSCA's; project tokens are TRUSCA's
# ---------------------------------------------------------------------------


def _hostile_stage2(run: Run) -> None:
    """A plan whose stage 2 asks for a user, an entrypoint, arguments, network."""
    write_plan("node-ok")(run)
    path = run.host("/host-output") / "bomlens-plan.json"
    document = json.loads(path.read_text())
    stage2 = document["stages"][1]
    stage2.update(
        user="0:0",
        entrypoint=["sh"],
        args=["-c", "id"],
        network={"required": True, "purpose": "x"},
    )
    stage2["env"]["PROJECT_NAME"] = "planned-name"
    document["stages"][0]["env"]["PROJECT_NAME"] = "planned-name"
    path.write_text(json.dumps(document))


def test_stage_2_ignores_what_the_plan_asks_beyond_image_and_mounts(workspace: Path) -> None:
    docker = FakeDocker().behave("plan", Behaviour(effect=_hostile_stage2))
    docker.behave("stage1", Behaviour(effect=write_sbom(_output_file())))
    _runner(workspace, docker).run()
    (stage2,) = docker.runs_of("stage2")
    assert stage2.option_values("--user") == []
    assert stage2.option_values("--entrypoint") == []
    assert stage2.args == []
    assert stage2.option_values("--network") == ["none"]
    assert stage2.option_values("--workdir") == ["/host-output"]
    for label in ("stage1", "stage2"):
        (run,) = docker.runs_of(label)
        assert run.env["PROJECT_NAME"] == "my-project", label
        assert run.env["PROJECT_VERSION"] == "2.0", label


@pytest.mark.parametrize(
    ("stage", "role", "target", "allowed"),
    [
        (0, "plan-file", "/tmp/build-prep.sh", True),
        (0, "output", "/tmp/out", False),
        (0, "guard-state", "/tmp", False),
        (1, "output", "/tmp/x", False),
        (0, "plan-file", "/trivy-db/x", False),
        (1, "source", "/trivy-db", False),
    ],
)
def test_mounts_under_the_trusca_paths_are_refused(
    stage: int, role: str, target: str, allowed: bool
) -> None:
    document: dict[str, Any] = json.loads(
        (PLAN_FIXTURES / "node-ok" / "bomlens-plan.json").read_text()
    )
    mount = next(m for m in document["stages"][stage]["mounts"] if m["role"] == role)
    changed = mount["target"] != target
    mount["target"] = target
    if allowed:
        parse_plan(document)
        return
    assert changed
    with pytest.raises(BomLensPlanRejected, match="which TRUSCA mounts"):
        parse_plan(document)


# ---------------------------------------------------------------------------
# L3: the daemon's API and what it recorded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.44", None, "garbage"])
def test_an_old_or_unknown_docker_api_is_refused(workspace: Path, version: str | None) -> None:
    docker = _docker()
    docker.api_version = version
    with pytest.raises(BomLensEngineError, match="needs 1.45"):
        _runner(workspace, docker).run()
    assert docker.runs == []


def test_a_daemon_that_dropped_a_mount_option_never_starts_the_container(
    workspace: Path,
) -> None:
    docker = _docker()

    def drop_readonly(recorded: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in m.items() if k != "ReadOnly"} for m in recorded]

    docker.record_mounts = drop_readonly
    with pytest.raises(BomLensEngineError, match="recorded different mounts"):
        _runner(workspace, docker).run()
    # Only the ownership reset (all of its mounts writable, so nothing for this
    # daemon to drop) went on to run; the plan container never started.
    assert [docker.containers[c].run.label for c in docker.started] == ["chown"]
    assert sorted(docker.removed) == sorted(docker.containers)


def test_a_dropped_subpath_is_caught() -> None:
    argv = ["--mount", "type=volume,src=ws,dst=/out,volume-subpath=scan/out"]
    asked = [{"Type": "volume", "Source": "ws", "Target": "/out"}]
    with pytest.raises(BomLensEngineError):
        check_requested_mounts(argv, asked)
    check_requested_mounts(argv, [{**asked[0], "VolumeOptions": {"Subpath": "scan/out"}}])


def test_every_container_carries_a_deadline_past_its_time_limit(workspace: Path) -> None:
    before = int(time.time())
    docker = _docker()
    _runner(workspace, docker).run()
    for run in docker.runs:
        labels = dict(v.split("=", 1) for v in run.option_values("--label"))
        deadline = int(labels["trusca.deadline"])
        assert deadline > before + runner_mod.EXIT_GRACE_SECONDS, run.label
        assert labels["trusca.scan"] == uuid.UUID(int=9).hex
        assert labels["trusca.instance"] == "default", run.label


def test_the_instance_label_follows_the_setting_and_refuses_odd_values(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRUSCA_INSTANCE_ID", "site-b")
    docker = _docker()
    _runner(workspace, docker).run()
    for run in docker.runs:
        assert "trusca.instance=site-b" in run.option_values("--label"), run.label
    monkeypatch.setenv("TRUSCA_INSTANCE_ID", "a,b=c")
    with pytest.raises(BomLensEngineError, match="TRUSCA_INSTANCE_ID"):
        runner_mod.instance_id()


def test_the_cli_reports_the_worker_networks_by_name_and_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    payload = json.dumps(
        {"trustedoss": {"NetworkID": "abc123"}, "trustedoss-docker-api": {"NetworkID": "def456"}}
    ).encode()

    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        assert argv[1:3] == ["container", "inspect"]
        return subprocess.CompletedProcess(argv, 0, payload, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(socket, "gethostname", lambda: "a1b2c3d4e5f6")
    assert runner_mod.DockerCli().own_networks() == frozenset(
        {"trustedoss", "abc123", "trustedoss-docker-api", "def456"}
    )


def test_the_cli_reports_nothing_for_a_worker_that_is_not_a_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 1, b"", b"No such container"),
    )
    assert runner_mod.DockerCli().own_networks() is None
