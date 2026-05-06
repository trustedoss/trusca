"""
cdxgen adapter — mock backend behaviour.

We never spawn `cdxgen` from unit tests; the worker image is the only place
the real binary is installed. These tests pin the contract of the mock
backend (`TRUSTEDOSS_SCAN_BACKEND=mock`):

  - Returns a `CdxgenResult` with a real on-disk SBOM JSON that downstream
    persistence helpers can `json.loads`.
  - The SBOM is a minimal-but-valid CycloneDX 1.5 document with at least one
    component, so the `_persist_components` path has something to upsert.
  - Calling the adapter with a `backend="real"` override on a host without
    cdxgen raises `CdxgenNotInstalled` — proving the env var is the sole
    knob that switches modes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_cdxgen_mock_writes_valid_cyclonedx_sbom(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import cdxgen as cdxgen_adapter

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "out"

    result = cdxgen_adapter.run_cdxgen(source_dir=source_dir, output_dir=output_dir)

    assert result.sbom_path.exists()
    assert result.sbom_path.parent == output_dir
    # CycloneDX shape — bomFormat / specVersion / components are the fields
    # the downstream persister actually reads.
    assert result.sbom["bomFormat"] == "CycloneDX"
    assert result.sbom["specVersion"] == "1.5"
    assert isinstance(result.sbom.get("components"), list)
    assert result.sbom["components"], "mock SBOM must contain at least one component"
    first = result.sbom["components"][0]
    assert "purl" in first
    assert "name" in first


def test_cdxgen_mock_persists_sbom_to_disk_in_output_dir(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """The on-disk file must round-trip back to the parsed dict."""
    from integrations import cdxgen as cdxgen_adapter

    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "cdxgen-out"
    result = cdxgen_adapter.run_cdxgen(source_dir=src, output_dir=out)

    on_disk = json.loads(result.sbom_path.read_text(encoding="utf-8"))
    assert on_disk["bomFormat"] == result.sbom["bomFormat"]
    assert on_disk["components"] == result.sbom["components"]


def test_cdxgen_explicit_backend_arg_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Passing `backend='mock'` must work even when env says real."""
    from integrations import cdxgen as cdxgen_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")  # would normally try the binary
    src = tmp_path / "src"
    src.mkdir()
    result = cdxgen_adapter.run_cdxgen(
        source_dir=src,
        output_dir=tmp_path / "cdxgen",
        backend="mock",
    )
    assert result.sbom_path.exists()


def test_cdxgen_real_backend_without_binary_raises_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the real binary is missing, the adapter must raise — not subprocess-shell."""
    from integrations import cdxgen as cdxgen_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    # Force shutil.which to return None for cdxgen so we exercise the guard
    # path even on a host that happens to have cdxgen installed (unlikely
    # but possible when running locally).
    monkeypatch.setattr(
        "integrations.cdxgen.shutil.which",
        lambda _name: None,
    )

    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(cdxgen_adapter.CdxgenNotInstalled):
        cdxgen_adapter.run_cdxgen(source_dir=src, output_dir=tmp_path / "out")
