"""
ORT real-path env scrubbing — security-reviewer Medium #1 v2 (chore PR #6).

The ORT adapter normally runs in mock mode under unit tests
(``test_ort_mock.py``); these tests exercise the real-binary code path
by monkeypatching ``shutil.which`` + ``subprocess.run`` so we can pin
that the env handed to the JVM excludes worker secrets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _seed_sbom(tmp_path: Path) -> Path:
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [],
    }
    sbom_path = tmp_path / "cdx.json"
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    return sbom_path


@pytest.fixture
def captured_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Monkeypatch the ORT subprocess plumbing and record the call.

    Forces ``shutil.which`` to return a stub path so the adapter takes
    the real-mode branch. Replaces ``subprocess.run`` with a recorder
    that writes a minimal evaluation JSON so ``run_ort`` returns
    successfully.
    """
    captured: dict[str, Any] = {}

    monkeypatch.setattr("integrations.ort.shutil.which", lambda _: "/opt/ort/bin/ort")
    # Force the non-mock backend so the real-path code runs.
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")

    class _FakeResult:
        returncode = 0
        stdout = b""
        stderr = b""

    def _capture(cmd: list[str], **kwargs: Any) -> _FakeResult:
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        # Drop a placeholder evaluation JSON where the adapter expects
        # one so the post-subprocess load succeeds.
        output_dir = Path(captured.get("output_dir", tmp_path))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "evaluation-result.json").write_text(
            json.dumps({"violations": [], "evaluated_packages": []}),
            encoding="utf-8",
        )
        return _FakeResult()

    monkeypatch.setattr("integrations.ort.subprocess.run", _capture)
    return captured


def test_run_ort_passes_only_scrubbed_env(
    captured_subprocess: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker secrets must NOT inherit into the ORT JVM subprocess.

    A malicious ORT plugin or a JVM CVE in the rule evaluator could
    otherwise tunnel ``DT_API_KEY`` / ``SECRET_KEY`` / ``DATABASE_URL``
    through telemetry / crash reports / DNS lookups in error paths.
    """
    monkeypatch.setenv("DT_API_KEY", "super-secret-dt-key")
    monkeypatch.setenv("SECRET_KEY", "super-secret-jwt-signing-key")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://trustedoss:hunter2@postgres:5432/trustedoss",
    )
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/secret")
    monkeypatch.setenv("JAVA_HOME", "/opt/java/temurin-21")
    monkeypatch.setenv("JAVA_OPTS", "-Xmx4g")

    output_dir = tmp_path / "ort-out"
    captured_subprocess["output_dir"] = output_dir
    sbom_path = _seed_sbom(tmp_path)

    from integrations.ort import run_ort

    run_ort(
        source_dir=tmp_path,
        sbom_path=sbom_path,
        output_dir=output_dir,
    )

    env = captured_subprocess["env"]
    assert env is not None, "subprocess.run must receive a scrubbed env dict"
    assert "DT_API_KEY" not in env
    assert "SECRET_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "SLACK_WEBHOOK_URL" not in env
    # JVM toolchain hints must survive — ORT cannot start without them.
    assert env["JAVA_HOME"] == "/opt/java/temurin-21"
    assert env["JAVA_OPTS"] == "-Xmx4g"


def test_run_ort_drops_ort_credential_band(
    captured_subprocess: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``ORT_*`` allowlist still strips credential-named keys."""
    monkeypatch.setenv("ORT_DATA_DIR", "/work/ort-data")
    monkeypatch.setenv("ORT_GITHUB_TOKEN", "gh-secret")
    monkeypatch.setenv("ORT_NEXUS_PASSWORD", "nexus-secret")

    output_dir = tmp_path / "ort-out"
    captured_subprocess["output_dir"] = output_dir
    sbom_path = _seed_sbom(tmp_path)

    from integrations.ort import run_ort

    run_ort(
        source_dir=tmp_path,
        sbom_path=sbom_path,
        output_dir=output_dir,
    )

    env = captured_subprocess["env"]
    assert env["ORT_DATA_DIR"] == "/work/ort-data"
    assert "ORT_GITHUB_TOKEN" not in env
    assert "ORT_NEXUS_PASSWORD" not in env
