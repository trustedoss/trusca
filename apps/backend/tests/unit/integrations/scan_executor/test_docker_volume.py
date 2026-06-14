"""_docker_volume — how a sidecar reaches the named workspace volume.

Security posture (review 2026-06-14): the default is ``named`` (workspace-only).
``volumes_from`` re-shares ALL worker volumes — including the cosign signing key —
into the untrusted build sidecar, so it is refused unless explicitly acknowledged.
"""

from __future__ import annotations

import pytest

from integrations.scan_executor import _docker_volume as dv

# --------------------------------------------------------------------------- #
# named (secure default)
# --------------------------------------------------------------------------- #


def test_default_strategy_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_DOCKER_VOLUME_STRATEGY", raising=False)
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "scan-workspace")
    monkeypatch.delenv("SCAN_WORKSPACE_MOUNT", raising=False)  # default /tmp/trustedoss
    assert dv.volume_run_args() == ["-v", "scan-workspace:/tmp/trustedoss"]


def test_named_honours_custom_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "ws")
    monkeypatch.setenv("SCAN_WORKSPACE_MOUNT", "/workspace")
    assert dv.volume_run_args() == ["-v", "ws:/workspace"]


def test_named_without_volume_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "named")
    monkeypatch.delenv("SCAN_WORKSPACE_VOLUME", raising=False)
    with pytest.raises(dv.DockerVolumeError):
        dv.volume_run_args()


# --------------------------------------------------------------------------- #
# volumes_from (gated)
# --------------------------------------------------------------------------- #


def test_volumes_from_refused_without_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dangerous over-share must be refused unless explicitly acknowledged."""
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "volumes_from")
    monkeypatch.delenv("SCAN_VOLUMES_FROM_ACK", raising=False)
    monkeypatch.setenv("SCAN_WORKER_CONTAINER", "worker-abc")
    with pytest.raises(dv.DockerVolumeError):
        dv.volume_run_args()


def test_volumes_from_allowed_with_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "volumes_from")
    monkeypatch.setenv("SCAN_VOLUMES_FROM_ACK", "1")
    monkeypatch.setenv("SCAN_WORKER_CONTAINER", "worker-abc")
    assert dv.volume_run_args() == ["--volumes-from", "worker-abc"]


def test_volumes_from_requires_explicit_worker_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No hostname guess — an unset container ref raises (could over-share wrong vols)."""
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "volumes_from")
    monkeypatch.setenv("SCAN_VOLUMES_FROM_ACK", "1")
    monkeypatch.delenv("SCAN_WORKER_CONTAINER", raising=False)
    with pytest.raises(dv.DockerVolumeError):
        dv.volume_run_args()


def test_unknown_strategy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bogus")
    with pytest.raises(dv.DockerVolumeError):
        dv.volume_run_args()
