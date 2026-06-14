"""_docker_volume — how a sidecar reaches the named workspace volume."""

from __future__ import annotations

import pytest

from integrations.scan_executor import _docker_volume as dv

_MOD = "integrations.scan_executor._docker_volume"


def test_volumes_from_uses_worker_container_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN_DOCKER_VOLUME_STRATEGY", raising=False)  # default
    monkeypatch.setenv("SCAN_WORKER_CONTAINER", "worker-abc123")
    assert dv.volume_run_args() == ["--volumes-from", "worker-abc123"]


def test_worker_container_ref_defaults_to_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN_WORKER_CONTAINER", raising=False)
    monkeypatch.setattr(f"{_MOD}.socket.gethostname", lambda: "deadbeef0001")
    assert dv.worker_container_ref() == "deadbeef0001"


def test_worker_container_ref_blank_hostname_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN_WORKER_CONTAINER", raising=False)
    monkeypatch.setattr(f"{_MOD}.socket.gethostname", lambda: "   ")
    with pytest.raises(dv.DockerVolumeError):
        dv.worker_container_ref()


def test_named_strategy_mounts_volume_at_mount_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "scan-workspace")
    monkeypatch.delenv("SCAN_WORKSPACE_MOUNT", raising=False)  # default /tmp/trustedoss
    assert dv.volume_run_args() == ["-v", "scan-workspace:/tmp/trustedoss"]


def test_named_strategy_honours_custom_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "ws")
    monkeypatch.setenv("SCAN_WORKSPACE_MOUNT", "/workspace")
    assert dv.volume_run_args() == ["-v", "ws:/workspace"]


def test_named_strategy_without_volume_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.delenv("SCAN_WORKSPACE_VOLUME", raising=False)
    with pytest.raises(dv.DockerVolumeError):
        dv.volume_run_args()


def test_unknown_strategy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bogus")
    with pytest.raises(dv.DockerVolumeError):
        dv.volume_run_args()
