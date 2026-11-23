# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The adapter around ``bomlens-runner`` (``integrations/bomlens/engine.py``).

The package is not run here: its own tests cover its containers. These tests
cover what TRUSCA adds around it. Each states what it keeps, so a change that
drops the check shows up here and not in a production scan.
"""

from __future__ import annotations

import importlib.metadata
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import bomlens_runner as package
import pytest
from bomlens_runner import SecretRef
from bomlens_runner import errors as package_errors

from core.config import (
    BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT,
    BOMLENS_IMAGE_DEFAULT,
    trusca_instance_id,
)
from integrations.bomlens import engine
from integrations.bomlens.contract import BomLensError

BACKEND = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def _engine_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "SCAN_BOMLENS_IMAGE",
        "SCAN_BOMLENS_DEEP_LICENSE_IMAGE",
        "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX",
        "SCAN_BOMLENS_STAGE1_REPOSITORIES",
        "SCAN_BOMLENS_NETWORK",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "GOPROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "trustedoss_workspace")
    monkeypatch.setenv("SCAN_WORKSPACE_MOUNT", str(tmp_path))
    monkeypatch.setenv("SCAN_TRIVY_CACHE_VOLUME", "trustedoss_trivy")


# --------------------------------------------------------------------------
# The package and the images agree
# --------------------------------------------------------------------------


def test_the_pinned_package_ships_the_images_core_config_pins() -> None:
    """One version, two places: the package carries its release's image digests,
    and ``core/config.py`` pins the same ones. A bump of only one of them fails
    here."""
    shipped = package.release_images()
    assert f"{shipped.engine.ref}@{shipped.engine.digest}" == BOMLENS_IMAGE_DEFAULT
    assert shipped.deep_license is not None
    deep = f"{shipped.deep_license.ref}@{shipped.deep_license.digest}"
    assert deep == BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT


def test_requirements_pin_the_installed_package_version() -> None:
    text = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^bomlens-runner==(\S+)$", text, re.MULTILINE)
    assert match is not None
    assert match.group(1) == importlib.metadata.version("bomlens-runner")
    lock = (BACKEND / "requirements.lock").read_text(encoding="utf-8")
    assert f"bomlens-runner=={match.group(1)} \\" in lock


# --------------------------------------------------------------------------
# The mirror prefix is checked before the package sees it
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["localhost", "reg.example:5000", "reg.example:5000/"])
def test_a_prefix_that_is_only_a_registry_host_is_refused(value: str) -> None:
    """The package admits it, and the allowed-repository check then admits every
    image of that registry."""
    with pytest.raises(engine.BomLensEngineError):
        engine.stage1_image_prefix(value)


@pytest.mark.parametrize(
    "value",
    [
        "Registry.example/mirror",
        "https://registry.example/mirror",
        "registry.example/mirror:tag",
        "a b/c",
    ],
)
def test_a_prefix_with_a_scheme_a_tag_upper_case_or_a_space_is_refused(value: str) -> None:
    with pytest.raises(engine.BomLensEngineError):
        engine.stage1_image_prefix(value)


def test_a_prefix_gets_its_final_slash_and_empty_means_off() -> None:
    expected = "registry.example/mirror/cdxgen/"
    assert engine.stage1_image_prefix("registry.example/mirror/cdxgen") == expected
    assert engine.stage1_image_prefix("registry.example/mirror/cdxgen/") == expected
    assert engine.stage1_image_prefix("") is None


def test_a_bad_prefix_in_the_settings_stops_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_STAGE1_IMAGE_PREFIX", "reg.example:5000")
    with pytest.raises(engine.BomLensEngineError):
        engine.build_policy(deep_license=False, include_non_shipped=False)


# --------------------------------------------------------------------------
# The policy and the Docker options
# --------------------------------------------------------------------------


def test_the_policy_carries_the_settings_and_the_pinned_images() -> None:
    policy, options = engine.build_policy(deep_license=False, include_non_shipped=True)
    assert policy.images.engine.ref == BOMLENS_IMAGE_DEFAULT.split("@")[0]
    assert policy.images.deep_license is None
    assert policy.deep_license is False
    assert policy.stage1_env["BOMLENS_INCLUDE_NON_SHIPPED"] == "true"
    assert policy.stage2_env["BOMLENS_INCLUDE_NON_SHIPPED"] == "true"
    assert policy.stage2_env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"].isdigit()
    age = policy.stage2_env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"]
    assert policy.image_env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"] == age
    assert options.volume_strategy == "named"
    assert options.workspace == "trustedoss_workspace"
    assert options.trivy_db == "trustedoss_trivy"
    assert options.instance_labels.get("trusca.instance")
    assert policy.uid is not None and policy.gid is not None


def test_scope_filter_off_leaves_the_non_shipped_switch_unset() -> None:
    policy, _ = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert "BOMLENS_INCLUDE_NON_SHIPPED" not in policy.stage1_env
    assert "BOMLENS_INCLUDE_NON_SHIPPED" not in policy.stage2_env


def test_deep_license_adds_its_image_and_nothing_else_changes() -> None:
    policy, _ = engine.build_policy(deep_license=True, include_non_shipped=False)
    assert policy.deep_license is True
    assert policy.stage2_image.ref == BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT.split("@")[0]
    assert policy.images.engine.ref == BOMLENS_IMAGE_DEFAULT.split("@")[0]


def test_the_compose_networks_of_the_database_and_the_docker_api_are_refused_as_egress() -> None:
    """The package refuses ``host``, ``none``, ``bridge`` and ``default`` itself.
    The two networks the compose files create are TRUSCA's to name."""
    assert set(engine.REFUSED_NETWORKS) == {"trustedoss", "trustedoss-docker-api"}
    _, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert set(options.refuse_networks) == set(engine.REFUSED_NETWORKS)


@pytest.mark.parametrize("network", ["trustedoss", "TrustedOSS", "trustedoss-docker-api"])
def test_a_compose_network_as_the_egress_network_is_refused_by_the_backend(
    monkeypatch: pytest.MonkeyPatch, network: str
) -> None:
    """The option is only as good as the backend honouring it, so build the
    backend the adapter builds."""
    from bomlens_runner.docker import DockerBackend

    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", network)
    _, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    with pytest.raises(package_errors.PolicyError):
        DockerBackend(options)


def test_stage_2_without_a_trivy_volume_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The package would run stage 2 with no database and no offline switches."""
    monkeypatch.setenv("SCAN_TRIVY_CACHE_VOLUME", "")
    with pytest.raises(engine.BomLensEngineError, match="Trivy database"):
        engine.build_policy(deep_license=False, include_non_shipped=False)


def test_the_bind_strategy_needs_a_downloaded_trivy_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "trivy"
    cache.mkdir()
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(cache))
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    with pytest.raises(engine.BomLensEngineError, match="has not been downloaded"):
        engine.build_policy(deep_license=False, include_non_shipped=False)
    (cache / "db").mkdir()
    _, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert options.volume_strategy == "bind"
    assert options.trivy_db == str(cache.resolve())


def test_an_unknown_volume_strategy_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "volumes_from")
    with pytest.raises(engine.BomLensEngineError, match="not usable"):
        engine.build_policy(deep_license=False, include_non_shipped=False)


# --------------------------------------------------------------------------
# Proxy values
# --------------------------------------------------------------------------


def test_a_proxy_with_a_login_becomes_a_secret_reference_and_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The login never sits in the policy or a command line: the Docker backend
    asks for it when the container is created."""
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@proxy.example:3128")
    policy, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    ref = policy.stage1_env["HTTPS_PROXY"]
    assert isinstance(ref, SecretRef)
    assert "pw" not in repr(policy.stage1_env) and "pw" not in repr(policy.image_env)
    assert options.secrets is not None
    assert options.secrets(ref) == "http://user:pw@proxy.example:3128"


def test_a_secret_reference_to_another_store_is_not_answered() -> None:
    with pytest.raises(engine.BomLensEngineError):
        engine._resolve_secret(SecretRef("elsewhere", "HTTPS_PROXY"))


def test_a_plain_proxy_is_passed_and_a_loopback_one_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("NO_PROXY", "localhost")
    policy, _ = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert policy.stage1_env["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert "HTTP_PROXY" not in policy.stage1_env
    assert policy.stage1_env["NO_PROXY"] == "localhost"
    assert policy.image_env["HTTPS_PROXY"] == "http://proxy.example:3128"


def test_a_value_that_is_too_long_or_has_a_line_break_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOPROXY", "https://goproxy.example/" + "a" * 2000)
    monkeypatch.setenv("NO_PROXY", "a\nb")
    policy, _ = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert "GOPROXY" not in policy.stage1_env
    assert "NO_PROXY" not in policy.stage1_env


# --------------------------------------------------------------------------
# What a run turns into
# --------------------------------------------------------------------------


def _result(*, run_status: str | None = "succeeded") -> Any:
    return SimpleNamespace(
        output_dir=Path("/x/out"),
        manifest=SimpleNamespace(run_status=run_status) if run_status else None,
        plan_status="ok",
        plan_reason=None,
        detected_ecosystem="node",
        source_restored=None,
    )


class _Containers:
    """The package's backend, as the adapter wraps it: each container ends as told."""

    reset_ownership = True

    def __init__(self, **ends: dict[str, Any]) -> None:
        self.ends = ends

    def run(self, spec: Any, on_line: Any, cancelled: Any) -> Any:  # noqa: ARG002
        end = self.ends.get(spec.component.removeprefix("engine-"), {})
        return SimpleNamespace(
            exit_code=end.get("exit_code", 0),
            oom_killed=end.get("oom_killed", False),
            timed_out=end.get("timed_out", False),
            start_error=end.get("start_error"),
        )


def _spec(component: str) -> Any:
    return SimpleNamespace(
        name=f"bomlens-0123abcd-1-{component}",
        component=f"engine-{component}",
        image="img@sha256:" + "0" * 64,
        network="none",
    )


def _containers_then(result: Any, *components: str) -> Any:
    """A ``run_source_scan`` that runs ``components`` through the backend it is given."""

    def run(policy: Any, backend: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        for component in components:
            backend.run(_spec(component), lambda _line: None, lambda: False)
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def _run_source(**overrides: Any) -> engine.EngineRun:
    scan_dir = overrides.pop("scan_dir")
    args: dict[str, Any] = {
        "scan_uuid": uuid.uuid4(),
        "scan_dir": scan_dir,
        "source": scan_dir / "src",
        "project": "p",
        "version": "1",
        "fetch_license": False,
        "deep_license": False,
        "include_non_shipped": False,
    }
    args.update(overrides)
    return engine.run_source(**args)


def test_a_run_whose_stage_1_could_not_start_fails_the_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The package goes on with stage 2 then, and a mount that changed after
    creation looks exactly like that. A reduced scan is not what TRUSCA reports
    for it."""
    monkeypatch.setattr(engine, "run_source_scan", _containers_then(_result(), "plan", "stage1"))
    monkeypatch.setattr(
        engine,
        "DockerBackend",
        lambda options, cli=None: _Containers(
            stage1={"exit_code": None, "start_error": "mount changed"}
        ),
    )
    with pytest.raises(engine.BomLensEngineError, match="stage 1"):
        _run_source(scan_dir=tmp_path)


def test_a_failed_run_still_records_how_its_containers_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The package returns its own record only when the run ends well, so the
    exit codes of a run that left no result come from the adapter's record."""
    monkeypatch.setattr(
        engine,
        "run_source_scan",
        _containers_then(package.EngineWroteNoResult("no manifest"), "plan", "stage1", "stage2"),
    )
    monkeypatch.setattr(
        engine,
        "DockerBackend",
        lambda options, cli=None: _Containers(
            stage1={"oom_killed": True, "exit_code": 137}, stage2={"exit_code": 1}
        ),
    )
    seen: list[engine.RunRecord] = []
    with pytest.raises(engine.EngineWroteNoResult):
        _run_source(scan_dir=tmp_path, on_finish=seen.append)
    (record,) = seen
    assert record.image_ref.startswith("ghcr.io/sktelecom/bomlens")
    assert record.metadata is not None
    assert record.metadata["stage1_oom"] is True
    assert record.metadata["stage1_exit_code"] == 137
    assert record.metadata["stage2_exit_code"] == 1
    assert record.metadata["plan_status"] is None


def test_a_finished_run_records_the_plan_and_gives_the_real_stage_1_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        engine, "run_source_scan", _containers_then(_result(), "plan", "stage1", "stage2")
    )
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: _Containers())
    run = _run_source(scan_dir=tmp_path)
    assert run.metadata["plan_status"] == "ok"
    assert run.metadata["detected_ecosystem"] == "node"
    assert run.metadata["stage2_exit_code"] == 0
    assert run.stage1_window is not None
    started, ended = run.stage1_window
    assert started <= ended


def test_a_finished_run_gives_the_folder_the_status_and_a_fresh_verified_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(engine, "run_source_scan", _containers_then(_result()))
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: _Containers())
    stale = tmp_path / "bomlens-verified"
    stale.mkdir()
    (stale / "old.json").write_text("{}")
    run = _run_source(scan_dir=tmp_path)
    assert run.run_status == "succeeded"
    assert run.output_dir == Path("/x/out")
    assert run.verified_dir == stale
    assert list(run.verified_dir.iterdir()) == []
    assert run.verified_dir.parent == tmp_path and run.verified_dir.name != "bomlens"


def test_a_verified_folder_that_is_a_link_is_replaced_not_followed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(engine, "run_source_scan", _containers_then(_result()))
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: _Containers())
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "scan").mkdir()
    (tmp_path / "scan" / "bomlens-verified").symlink_to(target, target_is_directory=True)
    run = _run_source(scan_dir=tmp_path / "scan")
    assert not run.verified_dir.is_symlink()
    assert list(target.iterdir()) == []


def test_a_cancellation_becomes_the_event_the_tasks_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def cancelled(*a: Any, **k: Any) -> Any:
        raise package.ScanCancelled("the scan was cancelled")

    monkeypatch.setattr(engine, "run_source_scan", cancelled)
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: object())
    with pytest.raises(engine.EngineScanCancelled):
        _run_source(scan_dir=tmp_path)


def test_a_package_error_is_a_bomlens_error_and_its_text_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(*a: Any, **k: Any) -> Any:
        raise package.PlanRejected("x" * 5000)

    monkeypatch.setattr(engine, "run_source_scan", broken)
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: object())
    with pytest.raises(BomLensError) as caught:
        _run_source(scan_dir=tmp_path)
    assert len(str(caught.value)) <= 400


def test_a_run_with_no_result_names_the_last_output_without_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The package reports only how the container ended. The lines it printed
    reach the scan's error cleaned of credentials, as they did before."""

    def no_result(*args: Any, **kwargs: Any) -> Any:
        on_log = kwargs["on_log"]
        on_log("postprocess", "starting")
        on_log("postprocess", "password=hunter2 failed to open")
        on_log("postprocess", "trivy: database is too old")
        raise package.EngineWroteNoResult("stage 2 exited 1 without a manifest")

    monkeypatch.setattr(engine, "run_source_scan", no_result)
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: object())
    with pytest.raises(engine.EngineWroteNoResult) as caught:
        _run_source(scan_dir=tmp_path)
    message = str(caught.value)
    assert "stage 2 exited 1" in message
    assert "database is too old" in message
    assert "hunter2" not in message


def test_the_log_lines_reach_the_scan_log_per_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, str, str]] = []

    def sink(stage: str) -> Any:
        return lambda stream, line: seen.append((stage, stream, line))

    def run(*args: Any, **kwargs: Any) -> Any:
        kwargs["on_log"]("cdxgen", "one")
        kwargs["on_log"]("postprocess", "two")
        kwargs["on_log"]("cdxgen", "three")
        return _result()

    monkeypatch.setattr(engine, "run_source_scan", run)
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: object())
    _run_source(scan_dir=tmp_path, line_sink=sink)
    assert seen == [
        ("cdxgen", "stdout", "one"),
        ("postprocess", "stdout", "two"),
        ("cdxgen", "stdout", "three"),
    ]


def test_the_cancellation_poll_asks_every_time_until_the_answer_is_yes() -> None:
    """The package also asks before it starts the next container, so a cached
    "no" would let one start after the scan was cancelled."""
    answers = iter([False, False, True])
    reads: list[int] = []

    def is_cancelled() -> bool:
        reads.append(1)
        return next(answers)

    poll = engine._cancel_poll(is_cancelled)
    assert poll is not None
    assert [poll(), poll(), poll(), poll(), poll()] == [False, False, True, True, True]
    assert len(reads) == 3
    assert engine._cancel_poll(None) is None


def test_an_image_scan_gives_the_same_run_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def image(policy: Any, backend: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        captured["deep_license"] = policy.deep_license
        return _result()

    monkeypatch.setattr(engine, "run_image_scan", image)
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: object())
    run = engine.run_image(
        scan_uuid=uuid.uuid4(), scan_dir=tmp_path, target="alpine:3.19.0@sha256:" + "a" * 64,
        project="p", version="1",
    )
    assert run.run_status == "succeeded"
    assert captured["target"].startswith("alpine:3.19.0")
    assert captured["deep_license"] is False


def test_inspect_asks_for_a_container_only() -> None:
    """``docker inspect <name>`` with no type goes on to ask for an image, a
    network and a volume once there is no such container. Behind the
    docker-socket-proxy those requests are refused, not answered "not found",
    so the package's removal check never saw the container gone and every scan
    failed after its first container."""
    from bomlens_runner.docker import DockerCli

    seen: list[list[str]] = []

    def record(self: Any, argv: list[str], timeout: float) -> Any:  # noqa: ARG001
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    cli = engine._ContainerOnlyCli("docker")
    original = DockerCli.run
    DockerCli.run = record  # type: ignore[method-assign,assignment]
    try:
        cli.run(["inspect", "--format", "{{.Id}}", "abc"], 60)
        cli.run(["image", "inspect", "--format", "{{.Id}}", "img"], 60)
        cli.run(["container", "inspect", "abc"], 60)
        cli.run(["ps", "--all", "--quiet"], 60)
    finally:
        DockerCli.run = original  # type: ignore[method-assign]
    assert seen == [
        ["container", "inspect", "--format", "{{.Id}}", "abc"],
        ["image", "inspect", "--format", "{{.Id}}", "img"],
        ["container", "inspect", "abc"],
        ["ps", "--all", "--quiet"],
    ]


def test_every_container_logs_its_start_and_exit_for_the_comparison_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tests/e2e/golden/shadow_report.py reads these two events (and the scan id
    and time the log adds) to say how long each container ran."""
    import structlog

    from tests.e2e.golden import shadow_report

    monkeypatch.setattr(
        engine, "run_source_scan", _containers_then(_result(), "plan", "stage1", "stage2")
    )
    monkeypatch.setattr(engine, "DockerBackend", lambda options, cli=None: _Containers())
    with structlog.testing.capture_logs() as captured:
        _run_source(scan_dir=tmp_path)
    events = [e for e in captured if e["event"].startswith("bomlens_container_")]
    assert [(e["event"], e["stage"]) for e in events] == [
        ("bomlens_container_start", "plan"),
        ("bomlens_container_exit", "plan"),
        ("bomlens_container_start", "stage1"),
        ("bomlens_container_exit", "stage1"),
        ("bomlens_container_start", "stage2"),
        ("bomlens_container_exit", "stage2"),
    ]
    assert all(e["exit_code"] == 0 for e in events if e["event"].endswith("exit"))
    # The report finds the container's label in the name the package gives.
    label = shadow_report._CONTAINER_LABEL.search("bomlens-0123abcd-2-stage1")
    assert label is not None and label.group(1) == "stage1"


# --------------------------------------------------------------------------
# The Kubernetes backend (SCAN_ENGINE_BACKEND=kubernetes)
# --------------------------------------------------------------------------


@pytest.fixture
def _kubernetes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    trivy = tmp_path / "trivy"
    (trivy / "db").mkdir(parents=True)
    monkeypatch.setenv("SCAN_ENGINE_BACKEND", "kubernetes")
    monkeypatch.setenv("SCAN_ENGINE_K8S_NAMESPACE", "trusca")
    monkeypatch.setenv("SCAN_ENGINE_K8S_WORKSPACE_CLAIM", "trusca-workspace")
    monkeypatch.setenv("SCAN_ENGINE_K8S_TRIVY_CLAIM", "trusca-trivy")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(trivy))
    monkeypatch.delenv("SCAN_ENGINE_K8S_SECRET_NAME", raising=False)
    monkeypatch.delenv("SCAN_ENGINE_K8S_FS_GROUP", raising=False)
    return tmp_path


def test_the_backend_is_docker_unless_the_setting_says_kubernetes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import scan_engine_backend

    monkeypatch.delenv("SCAN_ENGINE_BACKEND", raising=False)
    assert scan_engine_backend() == "docker"
    for value in ("", "nomad", "DOCKER ", "k8s"):
        monkeypatch.setenv("SCAN_ENGINE_BACKEND", value)
        assert scan_engine_backend() == "docker", value
    monkeypatch.setenv("SCAN_ENGINE_BACKEND", " Kubernetes ")
    assert scan_engine_backend() == "kubernetes"


def test_the_kubernetes_options_come_from_the_settings(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    from bomlens_runner.kubernetes import KubernetesOptions

    monkeypatch.setenv("SCAN_ENGINE_K8S_SERVICE_ACCOUNT", "trusca-engine")
    monkeypatch.setenv("SCAN_ENGINE_K8S_IMAGE_PULL_SECRETS", "regcred, other")
    monkeypatch.setenv("SCAN_ENGINE_K8S_NODE_SELECTOR", "pool=scan,disk=ssd,junk")
    monkeypatch.setenv("SCAN_ENGINE_K8S_PRIORITY_CLASS", "low")
    monkeypatch.setenv("SCAN_ENGINE_K8S_FS_GROUP", "2000")
    policy, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert isinstance(options, KubernetesOptions)
    assert options.namespace == "trusca"
    assert (options.workspace_claim, options.trivy_db_claim) == ("trusca-workspace", "trusca-trivy")
    assert options.service_account == "trusca-engine"
    assert options.image_pull_secrets == ("regcred", "other")
    assert dict(options.node_selector) == {"pool": "scan", "disk": "ssd"}
    assert options.priority_class == "low"
    assert options.fs_group == 2000
    # The Jobs carry the instance as a label; the Docker backend takes it by option.
    assert policy.labels == {"trusca.instance": trusca_instance_id()}


def test_the_pods_write_the_workspace_as_the_workers_group_by_default(
    _kubernetes: Path,
) -> None:
    import os

    _, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert options.fs_group == os.getgid()


@pytest.mark.parametrize(
    "missing", ["SCAN_ENGINE_K8S_WORKSPACE_CLAIM", "SCAN_ENGINE_K8S_TRIVY_CLAIM"]
)
def test_a_missing_claim_is_refused_before_any_job(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(engine.BomLensEngineError, match=missing):
        engine.build_policy(deep_license=False, include_non_shipped=False)


def test_without_a_namespace_setting_the_pods_own_is_read(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    from bomlens_runner.kubernetes import KubeApi

    monkeypatch.delenv("SCAN_ENGINE_K8S_NAMESPACE")
    monkeypatch.setattr(KubeApi, "in_cluster_namespace", staticmethod(lambda: "from-the-pod"))
    _, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert options.namespace == "from-the-pod"

    def no_file() -> str:
        raise FileNotFoundError("namespace")

    monkeypatch.setattr(KubeApi, "in_cluster_namespace", staticmethod(no_file))
    with pytest.raises(engine.BomLensEngineError, match="SCAN_ENGINE_K8S_NAMESPACE"):
        engine.build_policy(deep_license=False, include_non_shipped=False)


def test_a_proxy_with_a_login_needs_the_secret_the_pods_read_it_from(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    """On Docker the value is read from the worker's environment. A Job can
    only refer to a Secret, and one that does not exist stops the Pod at
    CreateContainerConfigError, so the missing name is refused up front."""
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@proxy.example:3128")
    with pytest.raises(engine.BomLensEngineError, match="SCAN_ENGINE_K8S_SECRET_NAME") as caught:
        engine.build_policy(deep_license=False, include_non_shipped=False)
    assert "pw" not in str(caught.value)

    monkeypatch.setenv("SCAN_ENGINE_K8S_SECRET_NAME", "trusca-engine-env")
    policy, _ = engine.build_policy(deep_license=False, include_non_shipped=False)
    for env in (policy.stage1_env, policy.image_env):
        ref = env["HTTPS_PROXY"]
        assert isinstance(ref, SecretRef)
        assert (ref.name, ref.key) == ("trusca-engine-env", "HTTPS_PROXY")


def test_a_plain_proxy_needs_no_secret_on_kubernetes(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    policy, _ = engine.build_policy(deep_license=False, include_non_shipped=False)
    assert policy.stage1_env["HTTPS_PROXY"] == "http://proxy.example:3128"


def _job(policy: Any, options: Any, component: str = "stage1") -> dict[str, Any]:
    """The Job the package would create for a stage-1 style container."""
    from bomlens_runner import ContainerSpec, Limits
    from bomlens_runner import backend as b
    from bomlens_runner.kubernetes import job_manifest

    spec = ContainerSpec(
        name="bomlens-abc-2-stage1",
        component=f"engine-{component}",
        image="ghcr.io/cdxgen/x@sha256:" + "0" * 64,
        user=None,
        entrypoint=None,
        args=(),
        workdir=None,
        env=dict(policy.stage1_env),
        mounts=(b.MountSpec("/app", b.VOLUME_WORKSPACE, "scan/source", False),),
        network="egress",
        read_only_root=True,
        capabilities=("CHOWN",),
        limits=Limits(),
        timeout=60,
        labels={**policy.labels, b.LABEL_PART_OF: b.PART_OF, b.LABEL_NETWORK: "egress"},
    )
    return job_manifest(spec, options)  # type: ignore[no-any-return]


def test_the_job_the_package_builds_carries_what_the_settings_ask_for(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@proxy.example:3128")
    monkeypatch.setenv("SCAN_ENGINE_K8S_SECRET_NAME", "trusca-engine-env")
    monkeypatch.setenv("SCAN_ENGINE_K8S_SERVICE_ACCOUNT", "trusca-engine")
    monkeypatch.setenv("SCAN_ENGINE_K8S_FS_GROUP", "2000")
    policy, options = engine.build_policy(deep_license=False, include_non_shipped=False)
    job = _job(policy, options)
    pod = job["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "trusca-engine"
    assert pod["securityContext"]["fsGroup"] == 2000
    # No token for the Pod: only the worker that creates Jobs has one.
    assert pod["automountServiceAccountToken"] is False
    claims = {
        v["name"]: v["persistentVolumeClaim"]["claimName"]
        for v in pod["volumes"]
        if "persistentVolumeClaim" in v
    }
    assert claims == {"workspace": "trusca-workspace"}
    labels = job["metadata"]["labels"]
    assert labels["trusca.instance"] == trusca_instance_id()
    assert labels["bomlens.engine/network"] == "egress"
    env = {e["name"]: e for e in pod["containers"][0]["env"]}
    assert env["HTTPS_PROXY"]["valueFrom"]["secretKeyRef"] == {
        "name": "trusca-engine-env",
        "key": "HTTPS_PROXY",
    }
    assert "pw" not in repr(job)


def test_the_kubernetes_backend_is_built_from_the_pods_token_for_each_run(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    """The package reads the token when it builds its client and a pod's token
    expires, so a backend is never kept between runs."""
    from bomlens_runner.kubernetes import KubeApi

    built: list[object] = []

    def in_cluster() -> object:
        built.append(object())
        return SimpleNamespace(request=lambda *a, **k: (200, {}))

    monkeypatch.setattr(KubeApi, "in_cluster", staticmethod(in_cluster))
    engine.engine_backend()
    engine.engine_backend()
    assert len(built) == 2


def test_a_worker_with_no_token_is_refused_with_a_plain_message(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    from bomlens_runner.kubernetes import KubeApi

    def no_token() -> object:
        raise FileNotFoundError("token")

    monkeypatch.setattr(KubeApi, "in_cluster", staticmethod(no_token))
    with pytest.raises(engine.BomLensEngineError, match="service account token"):
        engine.engine_backend()


def test_a_stage_needing_the_registries_needs_no_network_name_on_kubernetes(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path, tmp_path: Path
) -> None:
    """On Docker the stage is refused when SCAN_BOMLENS_NETWORK is unset. A Job
    has no network to name: its label and the cluster's NetworkPolicy decide."""
    from bomlens_runner.kubernetes import KubeApi

    monkeypatch.delenv("SCAN_BOMLENS_NETWORK", raising=False)
    api = SimpleNamespace(request=lambda *a, **k: (200, {}))
    monkeypatch.setattr(KubeApi, "in_cluster", staticmethod(lambda: api))
    seen: list[str] = []

    def run(policy: Any, backend: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        spec = SimpleNamespace(
            name="bomlens-abc-1-stage1",
            component="engine-stage1",
            image="img@sha256:" + "0" * 64,
            network="egress",
        )
        backend._inner = SimpleNamespace(
            run=lambda s, on_line, cancelled: SimpleNamespace(  # noqa: ARG005
                exit_code=0, oom_killed=False, timed_out=False, start_error=None
            ),
            reset_ownership=True,
        )
        backend.run(spec, lambda _line: None, lambda: False)
        seen.append("ran")
        return _result()

    monkeypatch.setattr(engine, "run_source_scan", run)
    _run_source(scan_dir=tmp_path)
    assert seen == ["ran"]


def test_the_reaper_skips_the_docker_only_cleanup_on_kubernetes(
    monkeypatch: pytest.MonkeyPatch, _kubernetes: Path
) -> None:
    from tasks import bomlens_engine_reaper

    calls: list[str] = []

    def legacy() -> int:
        calls.append("docker")
        return 1

    monkeypatch.setattr(
        bomlens_engine_reaper, "reap_engine_containers", lambda *a, **k: {"seen": 0, "removed": 0}
    )
    monkeypatch.setattr(bomlens_engine_reaper, "remove_legacy_containers", legacy)
    assert bomlens_engine_reaper.bomlens_engine_reaper_task.run()["legacy_removed"] == 0
    assert calls == []
    monkeypatch.setenv("SCAN_ENGINE_BACKEND", "docker")
    assert bomlens_engine_reaper.bomlens_engine_reaper_task.run()["legacy_removed"] == 1
