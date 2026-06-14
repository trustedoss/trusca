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
    # A minimal *hardened* working config: docker available, named volume set
    # (secure default), and unpinned :latest allowed (dev-style).
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: "/usr/bin/docker")
    monkeypatch.delenv("SCAN_DOCKER_VOLUME_STRATEGY", raising=False)  # default named
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "scan-workspace")
    monkeypatch.setenv("SCAN_WORKSPACE_MOUNT", "/tmp/trustedoss")
    monkeypatch.setenv("SCAN_ALLOW_UNPINNED_IMAGE", "1")
    monkeypatch.delenv("SCAN_SIDECAR_MEMORY", raising=False)
    monkeypatch.delenv("SCAN_SIDECAR_CPUS", raising=False)
    monkeypatch.delenv("SCAN_SIDECAR_NETWORK", raising=False)


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
    scan_backend_mock: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mock backend short-circuits to the in-process fallback before the docker
    # check; force real mode so we exercise the missing-docker-CLI branch, while
    # the cdxgen adapter still resolves the mock fixture SBOM.
    monkeypatch.setattr(f"{_MOD}.scan_backend_mode", lambda: "real")
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
    # Secure default: named workspace mount only, NOT --volumes-from.
    assert "--volumes-from" not in cmd
    assert "-v" in cmd and "scan-workspace:/tmp/trustedoss" in cmd
    assert "--security-opt" in cmd and "no-new-privileges" in cmd
    assert f"truscan-{req.scan_uuid.hex}" in cmd
    assert "ghcr.io/sktelecom/sbom-scanner-android-sdk33:latest" in cmd
    # Inline build-prep: the gradle script body is passed via `sh -c <script>`.
    assert cmd[cmd.index("-c") + 1].lstrip().startswith("#!/bin/sh")
    # argv tail: build-prep <src> <out> <spec>
    assert str(req.source_dir) in cmd
    assert cmd[-1] == req.spec_version


def test_android_command_is_capability_hardened(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    """Drops ALL caps and restores only the minimal build set (no privesc)."""
    monkeypatch.delenv("SCAN_SIDECAR_CAP_DROP", raising=False)
    monkeypatch.delenv("SCAN_SIDECAR_CAP_ADD", raising=False)
    captured = _stub_sidecar(monkeypatch)
    LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))

    cmd = captured["cmd"]
    assert "no-new-privileges" in cmd
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    added = {cmd[i + 1] for i, a in enumerate(cmd) if a == "--cap-add"}
    assert {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"} <= added
    # The dangerous Docker defaults a scan never needs must NOT be re-added.
    assert not ({"NET_RAW", "SYS_ADMIN", "MKNOD", "NET_BIND_SERVICE"} & added)


def test_sidecar_env_never_forwards_worker_secrets(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    """Worker secrets must never reach the untrusted-build sidecar's -e flags."""
    secrets = {
        "SECRET_KEY": "supersecret-key-value",
        "DATABASE_URL": "postgresql://u:p@db/trusted",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/leak",
        "TEAMS_WEBHOOK_URL": "https://teams/leak",
        "JIRA_TOKEN": "jira-token-leak",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)

    captured = _stub_sidecar(monkeypatch)
    LocalDockerExecutor().generate_sbom(
        _request(tmp_path, detected_env="android", fetch_license=True, verbose=True)
    )

    blob = "\x00".join(captured["cmd"])
    for name, value in secrets.items():
        assert name not in blob, f"secret name {name} leaked into sidecar command"
        assert value not in blob, f"secret value for {name} leaked into sidecar command"
    # Only the three curated, benign vars are forwarded.
    e_values = [captured["cmd"][i + 1] for i, a in enumerate(captured["cmd"]) if a == "-e"]
    assert e_values == ["HOME=/tmp/sbomhome", "FETCH_LICENSE=true", "CDXGEN_DEBUG_MODE=debug"]


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
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    """A volume-resolution error degrades to in-process rather than failing."""
    # LocalDocker proceeds to the android path (sees "real"), while the cdxgen
    # adapter used by the in-process fallback resolves the mock fixture SBOM.
    monkeypatch.setattr(f"{_MOD}.scan_backend_mode", lambda: "real")
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")

    def _boom() -> list[str]:
        raise DockerVolumeError("no volume")

    monkeypatch.setattr(f"{_MOD}.volume_run_args", _boom)

    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "inprocess"


def test_unpinned_image_falls_back_to_inprocess(
    scan_backend_mock: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unpinned :latest image is refused (rule #9) → in-process fallback."""
    monkeypatch.setattr(f"{_MOD}.scan_backend_mode", lambda: "real")
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: "/usr/bin/docker")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "scan-workspace")
    monkeypatch.delenv("SCAN_ALLOW_UNPINNED_IMAGE", raising=False)  # default: refuse
    monkeypatch.setenv("SCAN_ANDROID_IMAGE_TAG", "latest")

    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "inprocess"


def test_pinned_image_tag_routes_to_sidecar(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    """A semver-pinned tag (not :latest) is accepted even without the dev override."""
    monkeypatch.delenv("SCAN_ALLOW_UNPINNED_IMAGE", raising=False)
    monkeypatch.setenv("SCAN_ANDROID_IMAGE_TAG", "v1.2.3")
    captured = _stub_sidecar(monkeypatch)
    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "local_docker"
    assert "ghcr.io/sktelecom/sbom-scanner-android-sdk33:v1.2.3" in captured["cmd"]


@pytest.mark.parametrize("bad", ["4g --privileged", "-x", "a b", "\t2"])
def test_unsafe_env_value_falls_back(
    scan_backend_mock: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    """A flag-smuggling env value is rejected (no token splitting) → fallback."""
    monkeypatch.setattr(f"{_MOD}.scan_backend_mode", lambda: "real")
    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: "/usr/bin/docker")
    monkeypatch.setenv("SCAN_WORKSPACE_VOLUME", "scan-workspace")
    monkeypatch.setenv("SCAN_ALLOW_UNPINNED_IMAGE", "1")
    monkeypatch.setenv("SCAN_SIDECAR_MEMORY", bad)

    res = LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    assert res.executor == "inprocess"


def test_command_never_emits_dangerous_flags(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    """The create payload we build must never grant a host-escape (review #3)."""
    captured = _stub_sidecar(monkeypatch)
    LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))

    tokens = captured["cmd"]
    joined = " ".join(tokens)
    assert "--privileged" not in tokens
    assert "--ipc" not in tokens
    assert "--device" not in tokens
    # --pid=host / --pid host (but --pids-limit is fine).
    assert not any(t == "--pid" or t.startswith("--pid=") for t in tokens)
    assert "SYS_ADMIN" not in joined
    assert "unconfined" not in joined  # no seccomp/apparmor=unconfined
    # No host root bind mount.
    assert not any(t.startswith("/:") or ":/host" in t for t in tokens)


def test_resource_bounds_default_on(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    captured = _stub_sidecar(monkeypatch)
    LocalDockerExecutor().generate_sbom(_request(tmp_path, detected_env="android"))
    cmd = captured["cmd"]
    assert cmd[cmd.index("--memory") + 1] == "4g"
    assert cmd[cmd.index("--cpus") + 1] == "2"


def test_sidecar_is_labelled_for_reaping(
    monkeypatch: pytest.MonkeyPatch, _docker_present: None, tmp_path: Path
) -> None:
    captured = _stub_sidecar(monkeypatch)
    req = _request(tmp_path, detected_env="android")
    LocalDockerExecutor().generate_sbom(req)
    labels = {captured["cmd"][i + 1] for i, a in enumerate(captured["cmd"]) if a == "--label"}
    assert "trusca.role=scan-sidecar" in labels
    assert f"trusca.scan={req.scan_uuid.hex}" in labels
