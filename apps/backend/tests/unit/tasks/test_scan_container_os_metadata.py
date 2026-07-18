"""
Image OS / EOSL extraction from Trivy image reports — K-f1.

``tasks.scan_container.extract_os_metadata`` is the pure parse of Trivy's
top-level ``Metadata.OS`` block (base-image OS family/name + the end-of-service-
life flag). It is exercised here without a database — persistence into
``scan_metadata`` is covered by the container-scan integration test.

Realistic-fixture rule: the EOSL-true path is asserted against the RECORDED
real ``trivy image`` report (alpine 3.19 is past EOL in that recording), not a
hand-built minimal blob, so the exact schema path Trivy emits is what we parse.
"""

from __future__ import annotations

import json
from pathlib import Path

from integrations import trivy as trivy_adapter
from tasks.scan_container import extract_os_metadata

FIXTURE = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures"
    / "trivy"
    / "alpine-3.19-image-report.json"
)


def test_extracts_eosl_true_from_recorded_real_report() -> None:
    report = json.loads(FIXTURE.read_text())
    os_meta = extract_os_metadata(report)
    assert os_meta == {"family": "alpine", "name": "3.19.9", "eosl": True}


def test_mock_report_reports_os_but_not_eol(tmp_path: Path) -> None:
    """The mock backend emits a supported release so mock scans never falsely flag."""
    result = trivy_adapter._write_mock_report(
        tmp_path / "trivy.json", image_ref="alpine:3.19.1"
    )
    os_meta = extract_os_metadata(result.report)
    assert os_meta is not None
    assert os_meta["family"] == "alpine"
    assert os_meta["eosl"] is False


def test_returns_none_when_no_metadata_block() -> None:
    """SBOM-mode / older reports carry no Metadata — nothing to surface."""
    assert extract_os_metadata({"Results": []}) is None
    assert extract_os_metadata({"Metadata": {}}) is None
    assert extract_os_metadata({"Metadata": {"OS": {}}}) is None


def test_missing_eosl_defaults_to_false_and_name_is_optional() -> None:
    """Family is required; EOSL absent means not-EOL; Name may be omitted."""
    os_meta = extract_os_metadata({"Metadata": {"OS": {"Family": "debian"}}})
    assert os_meta == {"family": "debian", "eosl": False}


def test_non_dict_shapes_are_ignored() -> None:
    assert extract_os_metadata({"Metadata": {"OS": "alpine"}}) is None
    assert extract_os_metadata({"Metadata": [1, 2]}) is None
    assert extract_os_metadata({"Metadata": {"OS": {"Family": ""}}}) is None


def test_attacker_long_family_and_name_are_clamped() -> None:
    """family/name come from the scanned image's os-release — clamp before store.

    Guards the API's inbound 16 KiB scan_metadata cap, which does not cover
    worker-side writes.
    """
    from tasks.scan_container import _OS_FAMILY_MAX, _OS_NAME_MAX

    os_meta = extract_os_metadata(
        {"Metadata": {"OS": {"Family": "x" * 5000, "Name": "y" * 5000, "EOSL": True}}}
    )
    assert os_meta is not None
    assert len(os_meta["family"]) == _OS_FAMILY_MAX
    assert len(os_meta["name"]) == _OS_NAME_MAX
    assert os_meta["eosl"] is True
