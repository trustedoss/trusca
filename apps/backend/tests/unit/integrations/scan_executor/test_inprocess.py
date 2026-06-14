"""InProcessExecutor — behaviour-preserving prep + cdxgen delegation.

These tests prove the in-process executor reproduces the legacy inline order
exactly: advance "prep", run the injected prep hook, advance "cdxgen", then
delegate to ``cdxgen.run_cdxgen`` (whose mock backend writes a fixture SBOM).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from integrations.scan_executor.base import SbomGenRequest
from integrations.scan_executor.inprocess import InProcessExecutor


def _request(tmp_path: Path, **overrides: Any) -> SbomGenRequest:
    src = tmp_path / "source"
    src.mkdir(exist_ok=True)
    kwargs: dict[str, Any] = {
        "scan_uuid": uuid.uuid4(),
        "source_dir": src,
        "output_dir": tmp_path / "cdxgen",
    }
    kwargs.update(overrides)
    return SbomGenRequest(**kwargs)


def test_generate_sbom_mock_backend_returns_result(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    res = InProcessExecutor().generate_sbom(_request(tmp_path))

    assert res.sbom_path.exists()
    assert res.sbom["bomFormat"] == "CycloneDX"
    assert res.executor == "inprocess"
    assert res.image is None
    assert res.detected_env == "inprocess"


def test_prep_and_stage_run_in_legacy_order(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """stage('prep') → prep() → stage('cdxgen'), then cdxgen runs."""
    events: list[tuple[str, ...]] = []

    InProcessExecutor().generate_sbom(
        _request(tmp_path),
        prep=lambda: events.append(("prep",)),
        stage=lambda name: events.append(("stage", name)),
    )

    assert events == [("stage", "prep"), ("prep",), ("stage", "cdxgen")]


def test_executor_runs_without_hooks(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """prep / stage / line_callback are all optional."""
    res = InProcessExecutor().generate_sbom(_request(tmp_path))
    assert res.sbom_path.exists()


def test_delegates_with_expected_cdxgen_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Source/output/verbose/line_callback flow through; timeout omitted by default."""
    captured: dict[str, Any] = {}

    from integrations.cdxgen import CdxgenResult

    def _fake_run_cdxgen(**kwargs: Any) -> CdxgenResult:
        captured.update(kwargs)
        sbom_path = kwargs["output_dir"] / "cdxgen.cdx.json"
        return CdxgenResult(sbom_path=sbom_path, sbom={"bomFormat": "CycloneDX"})

    monkeypatch.setattr(
        "integrations.scan_executor.inprocess.cdxgen_adapter.run_cdxgen",
        _fake_run_cdxgen,
    )

    def _cb(_stream: str, _line: str) -> None:  # pragma: no cover - identity check
        return None

    req = _request(tmp_path, verbose=True)
    InProcessExecutor().generate_sbom(req, line_callback=_cb)

    assert captured["source_dir"] == req.source_dir
    assert captured["output_dir"] == req.output_dir
    assert captured["verbose"] is True
    assert captured["line_callback"] is _cb
    # timeout_seconds unset on the request → NOT forwarded (adapter default).
    assert "timeout_seconds" not in captured


def test_explicit_timeout_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    from integrations.cdxgen import CdxgenResult

    def _fake_run_cdxgen(**kwargs: Any) -> CdxgenResult:
        captured.update(kwargs)
        return CdxgenResult(
            sbom_path=kwargs["output_dir"] / "cdxgen.cdx.json",
            sbom={"bomFormat": "CycloneDX"},
        )

    monkeypatch.setattr(
        "integrations.scan_executor.inprocess.cdxgen_adapter.run_cdxgen",
        _fake_run_cdxgen,
    )

    req = _request(tmp_path, timeout_seconds=123)
    InProcessExecutor().generate_sbom(req)

    assert captured["timeout_seconds"] == 123
