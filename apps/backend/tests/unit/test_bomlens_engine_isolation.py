# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Code from a scanned repository never runs where the Docker API is reachable.

Security review on #522 (H1): with the Docker API proxy up, an in-process scan
would run the scanned project's build (bundle lock, Gradle, dotnet restore,
govulncheck) on a worker that can create a privileged container. Two
independent defences, each asserted here on its own:

1. Placement. The BomLens engine has its own queue, consumed only by
   worker-engine, which subscribes to nothing else; worker-scan and
   worker-default have no Docker access and never take that queue.
2. A fail-closed guard. Build preparation and cdxgen (in process) and
   govulncheck refuse to start when ``DOCKER_HOST`` is set or the proxy's name
   resolves, whatever queue brought the task there.
"""

from __future__ import annotations

import json
import re
import socket
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from integrations.scan_executor import docker_guard
from integrations.scan_executor.docker_guard import (
    DockerApiReachable,
    docker_api_reachable,
    refuse_build_with_docker_access,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = json.loads((REPO_ROOT / "tests" / "contracts" / "queue-names.json").read_text())


def _compose() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    return document


def _queues(service: str, variable: str) -> list[str]:
    command = str(_compose()["services"][service]["command"])
    match = re.search(rf"-Q \$\{{{variable}:-([^}}]+)\}}", command)
    assert match, service
    return match.group(1).split(",")


@pytest.fixture
def no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKER_HOST", raising=False)

    def unresolvable(*_a: Any, **_k: Any) -> Any:
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", unresolvable)


# ---------------------------------------------------------------------------
# 1. Placement
# ---------------------------------------------------------------------------


def test_engine_tasks_are_routed_to_the_engine_queue_only() -> None:
    from tasks.celery_app import _ENGINE_QUEUE, _ENGINE_TASK_NAMES, create_celery_app

    assert _ENGINE_QUEUE == CONTRACT["engine_queue"]
    assert set(_ENGINE_TASK_NAMES) == set(CONTRACT["engine_task_names"])
    routes = create_celery_app().conf.task_routes
    engine_routed = {name for name, r in routes.items() if r == {"queue": _ENGINE_QUEUE}}
    assert engine_routed == set(CONTRACT["engine_task_names"])


def test_the_engine_task_names_are_the_real_tasks() -> None:
    from tasks.bomlens_engine_reaper import bomlens_engine_reaper_task
    from tasks.scan_source import scan_source_engine_task

    assert {scan_source_engine_task.name, bomlens_engine_reaper_task.name} == set(
        CONTRACT["engine_task_names"]
    )


def test_only_worker_engine_takes_the_engine_queue_and_it_takes_nothing_else() -> None:
    engine = CONTRACT["engine_queue"]
    assert (
        _queues("worker-engine", "WORKER_ENGINE_QUEUES")
        == CONTRACT["worker_subscriptions"]["engine"]
        == [engine]
    )
    assert engine not in _queues("worker-scan", "WORKER_SCAN_QUEUES")
    assert engine not in _queues("worker-default", "WORKER_DEFAULT_QUEUES")
    for kind, queues in CONTRACT["worker_subscriptions"].items():
        if kind != "engine":
            assert engine not in queues, kind


def test_only_worker_engine_reaches_the_docker_api() -> None:
    services = _compose()["services"]
    on_api = sorted(n for n, s in services.items() if "docker-api" in (s or {}).get("networks", []))
    assert on_api == ["docker-socket-proxy", "worker-engine"]
    with_host = sorted(
        n for n, s in services.items() if "DOCKER_HOST" in ((s or {}).get("environment") or {})
    )
    assert with_host == ["worker-engine"]
    assert services["worker-engine"]["profiles"] == ["bomlens-engine"]
    assert services["worker-engine"]["environment"]["DOCKER_HOST"] == (
        "tcp://docker-socket-proxy:2375"
    )


def test_a_bomlens_source_scan_is_dispatched_as_the_engine_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks import enqueue_scan

    sent: list[str] = []

    class _Result:
        id = "x"

    def record(name: str) -> Any:
        def apply_async(**_kw: Any) -> _Result:
            sent.append(name)
            return _Result()

        return apply_async

    monkeypatch.setattr("tasks.scan_source.scan_source_task.apply_async", record("inprocess"))
    monkeypatch.setattr("tasks.scan_source.scan_source_engine_task.apply_async", record("engine"))

    class _Scan:
        id = uuid.uuid4()
        kind = "source"

    monkeypatch.setenv("SCAN_ENGINE", "bomlens")
    enqueue_scan(_Scan())  # type: ignore[arg-type]
    monkeypatch.setenv("SCAN_ENGINE", "inprocess")
    enqueue_scan(_Scan())  # type: ignore[arg-type]
    assert sent == ["engine", "inprocess"]


@pytest.mark.parametrize(("engine", "scheduled"), [("bomlens", True), ("inprocess", False)])
def test_the_reaper_is_scheduled_only_with_the_engine(
    monkeypatch: pytest.MonkeyPatch, engine: str, scheduled: bool
) -> None:
    from tasks.celery_app import _build_beat_schedule

    monkeypatch.setenv("SCAN_ENGINE", engine)
    tasks = {entry["task"] for entry in _build_beat_schedule().values()}
    assert ("trustedoss.bomlens_engine_reaper" in tasks) is scheduled


# ---------------------------------------------------------------------------
# 2. The guard
# ---------------------------------------------------------------------------


def test_nothing_reachable_is_allowed(no_docker: None) -> None:
    assert docker_api_reachable() is None
    refuse_build_with_docker_access("x")


def test_docker_host_is_refused(no_docker: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
    with pytest.raises(DockerApiReachable, match="DOCKER_HOST is set"):
        refuse_build_with_docker_access("x")


def test_a_resolvable_proxy_name_is_refused(
    no_docker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def resolves(host: str, *_a: Any, **_k: Any) -> Any:
        asked.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 2375))]

    monkeypatch.setattr(socket, "getaddrinfo", resolves)
    with pytest.raises(DockerApiReachable, match="docker-socket-proxy"):
        refuse_build_with_docker_access("x")
    assert asked == ["docker-socket-proxy"]


def test_in_process_build_is_refused_before_prep_runs(
    no_docker: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations.scan_executor.base import SbomGenRequest
    from integrations.scan_executor.inprocess import InProcessExecutor

    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
    ran: list[str] = []
    request = SbomGenRequest(
        scan_uuid=uuid.uuid4(), source_dir=tmp_path, output_dir=tmp_path / "out"
    )
    with pytest.raises(DockerApiReachable, match="build preparation and cdxgen"):
        InProcessExecutor().generate_sbom(
            request, prep=lambda: ran.append("prep"), stage=lambda s: ran.append(s)
        )
    assert ran == []


def test_govulncheck_is_refused_where_docker_is_reachable(
    no_docker: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The full reachability path up to govulncheck, with the guard tripped."""
    from integrations import govulncheck as gv
    from models import Project, Scan
    from tasks import scan_reachability as sr
    from tests.unit.tasks.test_scan_reachability import (
        _FakeSession,
        _make_tarball,
        _patch_session,
    )

    scan_id, proj_id = uuid.uuid4(), uuid.uuid4()

    class _Scan:
        kind = "source"
        project_id = proj_id

    class _Project:
        id = proj_id

    _patch_session(
        monkeypatch, _FakeSession({(Scan, scan_id): _Scan(), (Project, proj_id): _Project()})
    )
    tarball = tmp_path / "src.tar.gz"
    _make_tarball(tarball, {"go.mod": "module x\n", "main.go": "package main\n"})
    monkeypatch.setattr("tasks.scan_reachability.scan_source_tarball_path", lambda p, s: tarball)
    called: list[bool] = []

    def spy(**_k: Any) -> gv.ReachabilityResult:
        called.append(True)
        return gv.ReachabilityResult(verdicts={}, analysed=False)

    monkeypatch.setattr(gv, "run_govulncheck", spy)
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")

    sr._run(scan_uuid=scan_id, workspace=tmp_path / "ws")

    assert called == []


def test_the_engine_scan_task_has_the_scan_time_limits() -> None:
    from core.config import scan_hard_time_limit_seconds, scan_soft_time_limit_seconds
    from tasks.scan_source import scan_source_engine_task

    assert scan_source_engine_task.time_limit == scan_hard_time_limit_seconds()
    assert scan_source_engine_task.soft_time_limit == scan_soft_time_limit_seconds()


# ---------------------------------------------------------------------------
# Second review (N-L1, N-L3): the raw socket, and the guard's other direction
# ---------------------------------------------------------------------------


def _unix_socket(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    return sock


def test_a_mounted_docker_socket_is_refused(
    no_docker: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A short path: AF_UNIX names are limited to about a hundred bytes.
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="ds", dir="/tmp"))  # noqa: S108
    path = folder / "d.sock"
    sock = _unix_socket(path)
    try:
        monkeypatch.setattr(
            docker_guard, "DOCKER_SOCKET_PATHS", (str(tmp_path / "none"), str(path))
        )
        with pytest.raises(DockerApiReachable, match="Docker socket is mounted"):
            refuse_build_with_docker_access("x")
    finally:
        sock.close()
        path.unlink()
        folder.rmdir()


def test_a_regular_file_at_the_socket_path_is_not_a_socket(
    no_docker: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "docker.sock"
    fake.write_text("")
    monkeypatch.setattr(docker_guard, "DOCKER_SOCKET_PATHS", (str(fake),))
    assert docker_api_reachable() is None


def test_a_unix_docker_host_is_refused(no_docker: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    with pytest.raises(DockerApiReachable, match="DOCKER_HOST"):
        refuse_build_with_docker_access("x")


def test_where_docker_is_reachable_only_engine_tasks_run(
    no_docker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The task base class refuses any other task before its body runs."""
    from tasks import bomlens_engine_reaper
    from tasks.scan_source import scan_source_task

    ran: list[str] = []
    monkeypatch.setattr("tasks.scan_source._run_source_task", lambda *a, **k: ran.append("scan"))
    monkeypatch.setattr(
        bomlens_engine_reaper, "reap_engine_containers", lambda *a, **k: ran.append("reap")
    )
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")

    with pytest.raises(DockerApiReachable, match="trustedoss.scan_source"):
        scan_source_task.apply(args=["x"], throw=True)
    bomlens_engine_reaper.bomlens_engine_reaper_task.apply(throw=True)
    assert ran == ["reap"]

    monkeypatch.delenv("DOCKER_HOST")
    scan_source_task.apply(args=["x"], throw=True)
    assert ran == ["reap", "scan"]


def test_every_task_uses_the_guarded_base_class() -> None:
    from tasks.celery_app import _EngineIsolatedTask, celery_app
    from tests.unit.test_celery_app import _register_tasks_the_way_a_worker_would

    _register_tasks_the_way_a_worker_would()
    ours = [t for n, t in celery_app.tasks.items() if n.startswith("trustedoss.")]
    assert len(ours) > 20
    assert all(isinstance(t, _EngineIsolatedTask) for t in ours)


@pytest.mark.parametrize(("raw", "expected"), [(None, True), ("false", False), ("true", True)])
def test_remote_control_follows_the_setting(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: bool
) -> None:
    from tasks.celery_app import create_celery_app

    if raw is None:
        monkeypatch.delenv("CELERY_WORKER_REMOTE_CONTROL", raising=False)
    else:
        monkeypatch.setenv("CELERY_WORKER_REMOTE_CONTROL", raw)
    assert create_celery_app().conf.worker_enable_remote_control is expected


def test_worker_engine_takes_no_remote_control_and_checks_health_without_it() -> None:
    engine = _compose()["services"]["worker-engine"]
    assert engine["environment"]["CELERY_WORKER_REMOTE_CONTROL"] == "false"
    assert "inspect" not in " ".join(engine["healthcheck"]["test"])
    for name in ("worker-scan", "worker-default"):
        env = _compose()["services"][name]["environment"]
        assert (
            "CELERY_WORKER_REMOTE_CONTROL" not in {k for k in env if k != "<<"}
            or env["CELERY_WORKER_REMOTE_CONTROL"] != "false"
        ), name
