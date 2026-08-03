"""cdxgen spec-version / fetch-license toggles (increment 4).

Default output stays 1.5 / no-fetch (backward compatible). ``CDXGEN_SPEC_VERSION``
and ``CDXGEN_FETCH_LICENSE`` (or explicit args from the executor) flip cdxgen to
emit 1.6 and resolve component licenses. ``--spec-version`` is a CLI flag;
``FETCH_LICENSE`` is an env var cdxgen reads (matching the BomLens sidecar).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.config import cdxgen_fetch_license, cdxgen_spec_version

_MOD = "integrations.cdxgen"


# --------------------------------------------------------------------------- #
# config accessors
# --------------------------------------------------------------------------- #


def test_spec_version_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDXGEN_SPEC_VERSION", raising=False)
    assert cdxgen_spec_version() == "1.5"


def test_spec_version_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDXGEN_SPEC_VERSION", "1.6")
    assert cdxgen_spec_version() == "1.6"


def test_fetch_license_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDXGEN_FETCH_LICENSE", raising=False)
    assert cdxgen_fetch_license() is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "On"])
def test_fetch_license_truthy(monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    monkeypatch.setenv("CDXGEN_FETCH_LICENSE", truthy)
    assert cdxgen_fetch_license() is True


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "bogus"])
def test_fetch_license_falsy(monkeypatch: pytest.MonkeyPatch, falsy: str) -> None:
    monkeypatch.setenv("CDXGEN_FETCH_LICENSE", falsy)
    assert cdxgen_fetch_license() is False


# --------------------------------------------------------------------------- #
# mock SBOM reflects the resolved spec version
# --------------------------------------------------------------------------- #


def test_mock_sbom_uses_explicit_spec(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import cdxgen

    src = tmp_path / "src"
    src.mkdir()
    res = cdxgen.run_cdxgen(
        source_dir=src, output_dir=tmp_path / "o", spec_version="1.6"
    )
    assert res.sbom["specVersion"] == "1.6"


def test_mock_sbom_falls_back_to_env_spec(
    scan_backend_mock: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import cdxgen

    monkeypatch.setenv("CDXGEN_SPEC_VERSION", "1.6")
    src = tmp_path / "src"
    src.mkdir()
    res = cdxgen.run_cdxgen(source_dir=src, output_dir=tmp_path / "o")
    assert res.sbom["specVersion"] == "1.6"


# --------------------------------------------------------------------------- #
# real path: --spec-version flag + FETCH_LICENSE env
# --------------------------------------------------------------------------- #


def _capture_real_run(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _n: "/usr/bin/cdxgen")
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")

    def _fake_stream(
        cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        # cdxgen's -o target is the element after "-o"; write a minimal SBOM there.
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"bomFormat":"CycloneDX","components":[]}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(f"{_MOD}.run_with_line_streaming", _fake_stream)
    return captured


def test_real_cmd_uses_spec_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import cdxgen

    captured = _capture_real_run(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    cdxgen.run_cdxgen(source_dir=src, output_dir=tmp_path / "o", spec_version="1.6")

    cmd = captured["cmd"]
    assert "--spec-version" in cmd
    assert cmd[cmd.index("--spec-version") + 1] == "1.6"


def test_real_fetch_license_sets_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import cdxgen

    captured = _capture_real_run(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    cdxgen.run_cdxgen(source_dir=src, output_dir=tmp_path / "o", fetch_license=True)

    assert captured["env"].get("FETCH_LICENSE") == "true"


def test_real_fetch_license_off_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import cdxgen

    monkeypatch.delenv("CDXGEN_FETCH_LICENSE", raising=False)
    captured = _capture_real_run(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    cdxgen.run_cdxgen(source_dir=src, output_dir=tmp_path / "o")

    assert "FETCH_LICENSE" not in (captured["env"] or {})
