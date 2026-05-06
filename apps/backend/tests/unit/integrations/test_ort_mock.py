"""
ORT adapter — mock backend behaviour.

ORT real-mode requires a JRE + the ORT distribution; unit tests must never
spawn it. This module pins the mock-backend contract:

  - Returns an `OrtResult` with a real on-disk evaluation JSON.
  - The mock pulls components from the SBOM (so the deterministic shape is
    `len(evaluated_packages) == len(sbom.components)`).
  - Every package is classified `category="allowed"` with license `MIT`,
    matching the worker image's bundled rules.kts default.
  - When the SBOM file is missing or unreadable, the mock degrades to an
    empty `evaluated_packages` list (defensive — does not crash).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _write_sbom(path: Path, components: list[dict[str, Any]]) -> Path:
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components}
    path.write_text(json.dumps(sbom), encoding="utf-8")
    return path


def test_ort_mock_emits_evaluation_keyed_off_sbom_components(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import ort as ort_adapter

    sbom_path = _write_sbom(
        tmp_path / "cdx.json",
        components=[
            {
                "purl": "pkg:pypi/foo@1.0.0",
                "name": "foo",
                "version": "1.0.0",
            },
            {
                "purl": "pkg:npm/bar@2.0.0",
                "name": "bar",
                "version": "2.0.0",
            },
        ],
    )

    result = ort_adapter.run_ort(
        source_dir=tmp_path,
        sbom_path=sbom_path,
        output_dir=tmp_path / "ort",
    )

    assert result.result_path.exists()
    evaluated = result.evaluation["evaluated_packages"]
    assert len(evaluated) == 2
    # Every component must classify as allowed/MIT in the canned mock.
    for pkg in evaluated:
        assert pkg["category"] == "allowed"
        assert pkg["concluded_license"] == "MIT"
    # No violations in the mock.
    assert result.evaluation["violations"] == []


def test_ort_mock_handles_missing_sbom_gracefully(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """If the SBOM file does not exist the mock returns an empty evaluation."""
    from integrations import ort as ort_adapter

    result = ort_adapter.run_ort(
        source_dir=tmp_path,
        sbom_path=tmp_path / "missing.json",
        output_dir=tmp_path / "ort",
    )

    assert result.result_path.exists()
    assert result.evaluation["evaluated_packages"] == []
    assert result.evaluation["violations"] == []


def test_ort_mock_writes_well_formed_json(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import ort as ort_adapter

    sbom_path = _write_sbom(
        tmp_path / "cdx.json",
        components=[{"purl": "pkg:pypi/x@1.0.0", "name": "x", "version": "1.0.0"}],
    )
    result = ort_adapter.run_ort(
        source_dir=tmp_path,
        sbom_path=sbom_path,
        output_dir=tmp_path / "ort",
    )
    on_disk = json.loads(result.result_path.read_text(encoding="utf-8"))
    assert on_disk == result.evaluation
