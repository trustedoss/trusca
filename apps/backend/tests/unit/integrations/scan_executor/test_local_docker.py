"""LocalDockerExecutor — Android sidecar routing, fallback, cleanup.

No real Docker is spawned: ``run_with_line_streaming`` and ``subprocess.run`` are
stubbed. The actual 0→67-component Android gap was verified end-to-end on Colima
during increment-3 pre-verification; these tests pin the routing, the docker
command construction, and the force-remove cleanup.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations import cdxgen as cdxgen_adapter
from integrations.scan_executor._docker_volume import DockerVolumeError
from integrations.scan_executor.base import SbomGenRequest
from integrations.scan_executor.local_docker import LocalDockerExecutor

_MOD = "integrations.scan_executor.local_docker"


def _request(tmp_path: Path, *, detected_env: str, **overrides: Any) -> SbomGenRequest:
    src = tmp_path / "source"
    src.mkdir(exist_ok=True)
    # A gradle file so android_compile_sdk resolves a real API for android cases.
    (src / "build.gradle").write_text(
        "android { compileSdk 33 }", encoding="utf-8"
    )
    kwargs: dict[str, Any] = {
        "scan_uuid": uuid.uuid4(),
        "source_dir": src,
        "output_dir": tmp_path / "cdxgen",
        "detected_env": detected_env,
    }
    kwargs.update(overrides)
    return SbomGenRequest(**kwargs)


@pytest.fixture
def _docker_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: "/usr/bin/docker")
    monkeypatch.setenv("SCAN_WORKER_CONTAINER", "worker-test")
    monkeypatch.delenv("SCAN_DOCKER_VOLUME_STRATEGY", raising=False)


def _stub_sidecar(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, write_sbom: bool = True
) -> dict[str, Any]:
    """Stub run_with_line_streaming + subprocess.run; capture both."""
    captured: dict[str, Any] = {}

    def _fake_run_streaming(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        if write_sbom:
            # The SBOM path is the last-but-three argv element (… out spec).
            out = Path(cmd[-2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                '{"bomFormat":"CycloneDX","components":[{"name":"androidx.appcompat"}]}',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, returncode, b"out", b"err")

    def _fake_subprocess_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.setdefault("rm_calls", []).append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(f"{_MOD}.run_with_line_streaming", _fake_run_streaming)
    monkeypatch.setattr(f"{_MOD}.subprocess.run", _fake_subprocess_run)
    return captured


# --------------------------------------------------------------------------- #
# Fallback paths
# --------------------------------------------------------------------------- #


def test_non_android_falls_back_to_inprocess(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="python"))
    assert res.executor == "inprocess"


def test_mock_backend_falls_back_even_for_android(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "inprocess"


def test_android_without_docker_cli_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: None)
    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "inprocess"


# --------------------------------------------------------------------------- #
# Android sidecar happy path + command construction
# --------------------------------------------------------------------------- #


def test_android_routes_to_sidecar(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    _stub_sidecar(monkeypatch)
    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))

    assert res.executor == "local_docker"
    assert res.detected_env == "android"
    # compileSdk 33 → API-tagged android image, latest tag.
    assert res.image == "ghcr.io/sktelecom/sbom-scanner-android-sdk33:latest"
    assert res.sbom["components"][0]["name"] == "androidx.appcompat"


def test_android_docker_command_shape(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    captured = _stub_sidecar(monkeypatch)
    req = _request(tmp_path, detected_env="android")
    LocalDockerExecutor().generate_sbom(req)

    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "--volumes-from" in cmd and "worker-test" in cmd
    assert "--security-opt" in cmd and "no-new-privileges" in cmd
    assert f"truscan-{req.scan_uuid.hex}" in cmd
    assert "ghcr.io/sktelecom/sbom-scanner-android-sdk33:latest" in cmd
    # Inline build-prep: the gradle script body is passed via `sh -c <script>`.
    assert cmd[cmd.index("-c") + 1].lstrip().startswith("#!/bin/sh")
    # argv tail: build-prep <src> <out> <spec>
    assert str(req.source_dir) in cmd
    assert cmd[-1] == req.spec_version


def test_force_remove_runs_on_success(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    captured = _stub_sidecar(monkeypatch)
    req = _request(tmp_path, detected_env="android")
    LocalDockerExecutor().generate_sbom(req)
    rm_calls = captured.get("rm_calls", [])
    assert ["docker", "rm", "-f", f"truscan-{req.scan_uuid.hex}"] in rm_calls


# --------------------------------------------------------------------------- #
# Android sidecar failure modes
# --------------------------------------------------------------------------- #


def test_sidecar_nonzero_raises_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    captured = _stub_sidecar(monkeypatch, returncode=1, write_sbom=False)
    req = _request(tmp_path, detected_env="android")
    with pytest.raises(cdxgen_adapter.CdxgenFailed):
        LocalDockerExecutor().generate_sbom(req)
    # finally still force-removes the container.
    assert captured.get("rm_calls")


def test_sidecar_missing_sbom_raises(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    _stub_sidecar(monkeypatch, returncode=0, write_sbom=False)
    with pytest.raises(cdxgen_adapter.CdxgenFailed):
        LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))


def test_sidecar_timeout_raises_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def _raise_timeout(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 1)

    def _fake_rm(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured.setdefault("rm_calls", []).append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(f"{_MOD}.run_with_line_streaming", _raise_timeout)
    monkeypatch.setattr(f"{_MOD}.subprocess.run", _fake_rm)

    with pytest.raises(cdxgen_adapter.CdxgenTimeout):
        LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert captured.get("rm_calls"), "timeout path must still force-remove the container"


def test_volume_unresolved_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A volume-resolution error degrades to in-process rather than failing."""
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: "/usr/bin/docker")
    # LocalDocker proceeds to the android path (sees "real"), while the cdxgen
    # adapter used by the in-process fallback resolves the mock fixture SBOM.
    monkeypatch.setattr(f"{_MOD}.scan_backend_mode", lambda: "real")
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")

    def _boom() -> list[str]:
        raise DockerVolumeError("no volume")

    monkeypatch.setattr(f"{_MOD}.volume_run_args", _boom)

    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "inprocess"
