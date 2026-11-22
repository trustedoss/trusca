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

from core.config import BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT, BOMLENS_IMAGE_DEFAULT
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
    assert engine.stage1_image_prefix("  ") is None


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
        component=f"engine-{component}", image="img@sha256:" + "0" * 64, network="none"
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
        lambda options: _Containers(stage1={"exit_code": None, "start_error": "mount changed"}),
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
        lambda options: _Containers(
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: _Containers())
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: _Containers())
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: _Containers())
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: object())
    with pytest.raises(engine.EngineScanCancelled):
        _run_source(scan_dir=tmp_path)


def test_a_package_error_is_a_bomlens_error_and_its_text_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(*a: Any, **k: Any) -> Any:
        raise package.PlanRejected("x" * 5000)

    monkeypatch.setattr(engine, "run_source_scan", broken)
    monkeypatch.setattr(engine, "DockerBackend", lambda options: object())
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: object())
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: object())
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
    monkeypatch.setattr(engine, "DockerBackend", lambda options: object())
    run = engine.run_image(
        scan_uuid=uuid.uuid4(), scan_dir=tmp_path, target="alpine:3.19.0@sha256:" + "a" * 64,
        project="p", version="1",
    )
    assert run.run_status == "succeeded"
    assert captured["target"].startswith("alpine:3.19.0")
    assert captured["deep_license"] is False
