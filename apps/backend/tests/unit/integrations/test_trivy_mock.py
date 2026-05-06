"""
Trivy adapter — mock backend behaviour.

Real Trivy needs network access (to pull the registry image and the vuln DB)
and a Docker daemon; unit tests must never invoke it. The mock-backend
contract this module pins:

  - Returns a `TrivyResult` with an on-disk JSON report whose shape matches
    Trivy's real output (`SchemaVersion`, `ArtifactName`, `Results[].Class`,
    `Results[].Vulnerabilities[]`) so downstream persistence helpers can
    consume it without branching on the backend mode.
  - The mock vulnerability is a stable synthetic CVE (`CVE-2024-MOCK-0001`)
    so unit + integration tests can assert on a known value.
  - Real-mode + missing binary raises `TrivyNotInstalled` (proves the env
    flag is the only switch).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_trivy_mock_writes_realistic_report(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    result = trivy_adapter.run_trivy_image(
        image_ref="alpine:3.19",
        output_dir=tmp_path / "trivy",
    )

    assert result.report_path.exists()
    assert result.report["ArtifactName"] == "alpine:3.19"
    assert result.report["SchemaVersion"] == 2
    assert isinstance(result.report["Results"], list)
    assert result.report["Results"], "mock report must include at least one Result"

    first = result.report["Results"][0]
    assert first["Class"] == "os-pkgs"
    vulns = first["Vulnerabilities"]
    assert vulns, "mock Result must carry at least one vulnerability"
    cve = vulns[0]
    assert cve["VulnerabilityID"] == "CVE-2024-MOCK-0001"
    assert cve["Severity"] == "HIGH"
    # The downstream persister also reads PkgName / InstalledVersion.
    assert cve["PkgName"] == "example-pkg"
    assert cve["InstalledVersion"] == "1.0.0"


def test_trivy_mock_report_round_trips_through_disk(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    result = trivy_adapter.run_trivy_image(
        image_ref="ghcr.io/example/img:1.0",
        output_dir=tmp_path / "trivy",
    )
    on_disk = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert on_disk == result.report


def test_trivy_real_mode_without_binary_raises_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which",
        lambda _name: None,
    )
    with pytest.raises(trivy_adapter.TrivyNotInstalled):
        trivy_adapter.run_trivy_image(
            image_ref="alpine:3.19",
            output_dir=tmp_path / "trivy",
        )
