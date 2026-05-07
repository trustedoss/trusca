"""
cdxgen Gradle 8 compatibility shim — chore PR #5 Part C.

cdxgen 11.x's bundled init.gradle calls ``allprojects { ... }`` at root
scope; Gradle 8 removed that implicit closure, breaking the
pilot-java-gradle scan during the 2026-05-07 UAT (0 components). The
adapter now writes a ``trustedoss-gradle8-compat.init.gradle`` file
into the cdxgen output directory and points cdxgen at it via
``CDXGEN_GRADLE_ARGS``. These tests pin that contract:

  - Gradle build root → adapter writes the shim and exposes its path
    through ``CDXGEN_GRADLE_ARGS``.
  - Non-Gradle source dir → no shim, no env var.
  - Operator-supplied ``CDXGEN_GRADLE_ARGS`` is not stomped on.

We exercise ``_build_cdxgen_env`` directly (rather than running
``run_cdxgen``) because the adapter integration is otherwise covered
by ``test_cdxgen_mock.py``; a focused unit on the env builder keeps
this regression tight.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.cdxgen import (
    _GRADLE8_COMPAT_INIT,
    _build_cdxgen_env,
    _is_gradle_project,
    _write_gradle_compat_init,
)

# ---------------------------------------------------------------------------
# _is_gradle_project
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker", ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"]
)
def test_is_gradle_project_true_when_marker_present(
    tmp_path: Path, marker: str
) -> None:
    (tmp_path / marker).write_text("// gradle marker", encoding="utf-8")
    assert _is_gradle_project(tmp_path) is True


def test_is_gradle_project_false_for_npm_root(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert _is_gradle_project(tmp_path) is False


def test_is_gradle_project_false_for_empty_dir(tmp_path: Path) -> None:
    assert _is_gradle_project(tmp_path) is False


# ---------------------------------------------------------------------------
# _write_gradle_compat_init
# ---------------------------------------------------------------------------


def test_write_gradle_compat_init_creates_file(tmp_path: Path) -> None:
    out_dir = tmp_path / "cdxgen"
    init_path = _write_gradle_compat_init(out_dir)

    assert init_path.exists()
    assert init_path.parent == out_dir
    body = init_path.read_text(encoding="utf-8")
    # Sanity-pin: the script must mention the property cdxgen tries to
    # access ("allprojects") so a future refactor cannot accidentally
    # drop the shim.
    assert "allprojects" in body
    assert body == _GRADLE8_COMPAT_INIT


def test_write_gradle_compat_init_creates_parent(tmp_path: Path) -> None:
    """``output_dir`` may not exist yet on a fresh scan."""
    out_dir = tmp_path / "fresh" / "cdxgen"
    init_path = _write_gradle_compat_init(out_dir)
    assert init_path.exists()
    assert out_dir.is_dir()


# ---------------------------------------------------------------------------
# _build_cdxgen_env
# ---------------------------------------------------------------------------


def test_build_cdxgen_env_injects_compat_for_gradle_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CDXGEN_GRADLE_ARGS", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    (src / "build.gradle").write_text("// gradle 8", encoding="utf-8")
    out = tmp_path / "out"

    env = _build_cdxgen_env(source_dir=src, output_dir=out)

    assert "CDXGEN_GRADLE_ARGS" in env
    args_value = env["CDXGEN_GRADLE_ARGS"]
    assert args_value.startswith("--init-script ")
    init_path_str = args_value.split(" ", 1)[1]
    init_path = Path(init_path_str)
    assert init_path.exists()
    assert "allprojects" in init_path.read_text(encoding="utf-8")
    # PATH / HOME etc. should still be inherited so cdxgen can locate
    # gradle / java / npm; we just verify that *some* inherited key
    # survived.
    assert "PATH" in env


def test_build_cdxgen_env_skips_compat_for_non_gradle_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CDXGEN_GRADLE_ARGS", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    (src / "package.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "out"

    env = _build_cdxgen_env(source_dir=src, output_dir=out)

    assert "CDXGEN_GRADLE_ARGS" not in env
    # Output dir must NOT be created when there is nothing to write.
    assert not (out / "trustedoss-gradle8-compat.init.gradle").exists()


def test_build_cdxgen_env_respects_operator_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who set CDXGEN_GRADLE_ARGS in the worker env wins."""
    monkeypatch.setenv("CDXGEN_GRADLE_ARGS", "--no-build-cache")
    src = tmp_path / "src"
    src.mkdir()
    (src / "build.gradle").write_text("// gradle", encoding="utf-8")
    out = tmp_path / "out"

    env = _build_cdxgen_env(source_dir=src, output_dir=out)

    assert env["CDXGEN_GRADLE_ARGS"] == "--no-build-cache"
    # The compat init script is not written when the operator opted out.
    assert not (out / "trustedoss-gradle8-compat.init.gradle").exists()


# ---------------------------------------------------------------------------
# subprocess env scrubbing — security-reviewer Medium #1 v2 (chore PR #6)
# ---------------------------------------------------------------------------


def test_build_cdxgen_env_strips_worker_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker secrets must NOT inherit into cdxgen's env.

    cdxgen is a Node binary that, on a hostile clone, may load
    attacker-controlled ``package.json`` plugins or ``cdxgen.config.json``
    rules. Any inherited ``DT_API_KEY`` / ``SECRET_KEY`` /
    ``DATABASE_URL`` would then become a covert exfil channel through
    plugin telemetry or crash reporting.
    """
    monkeypatch.setenv("DT_API_KEY", "super-secret-dt-key")
    monkeypatch.setenv("SECRET_KEY", "super-secret-jwt-signing-key")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://trustedoss:hunter2@postgres:5432/trustedoss",
    )
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/secret")

    src = tmp_path / "src"
    src.mkdir()
    (src / "package.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "out"

    env = _build_cdxgen_env(source_dir=src, output_dir=out)

    assert "DT_API_KEY" not in env
    assert "SECRET_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "SLACK_WEBHOOK_URL" not in env


def test_build_cdxgen_env_forwards_node_extra_ca_certs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corporate TLS-intercept proxy hint must reach cdxgen.

    Without this Node falls back to its bundled CA bundle and silently
    fails x509 verification on every npm registry hit.
    """
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/etc/ssl/corp-ca.pem")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:8080")

    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"

    env = _build_cdxgen_env(source_dir=src, output_dir=out)

    assert env["NODE_EXTRA_CA_CERTS"] == "/etc/ssl/corp-ca.pem"
    assert env["HTTPS_PROXY"] == "http://proxy.corp.example:8080"
