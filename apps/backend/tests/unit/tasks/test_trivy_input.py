# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for :mod:`tasks._trivy_input`.

The seam has one job with two halves: hand Trivy a document that will match,
and leave the caller's own file alone while doing it. The second half is the
one worth pinning — the SBOM the ingest path passes in is the supplier's
upload, and it also backs the conformance verdict, the signature bundle and
the persisted artifact. A stage that edited it in place would corrupt all
three, quietly.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from tasks import _trivy_input
from tasks._trivy_input import prepare_trivy_sbom

_RPM_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "components": [
        {
            "type": "library",
            "name": "openssl",
            "version": "1.0.2k-19.el7",
            "purl": "pkg:rpm/centos/openssl@1.0.2k-19.el7?arch=x86_64",
        },
        {
            "type": "library",
            "name": "glibc",
            "version": "2.17-317.el7",
            "purl": "pkg:rpm/centos/glibc@2.17-317.el7?arch=x86_64",
        },
    ],
}

_NPM_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "components": [
        {
            "type": "library",
            "name": "lodash",
            "version": "4.17.21",
            "purl": "pkg:npm/lodash@4.17.21",
        }
    ],
}


def _write(directory: Path, name: str, doc: dict[str, Any]) -> Path:
    path = directory / name
    path.write_text(json.dumps(doc))
    return path


def test_returns_an_enriched_copy_and_leaves_the_original_alone(
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "sbom-ingest"
    upload_dir.mkdir()
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(upload_dir, "upload.cdx.json", _RPM_SBOM)
    before = original.read_bytes()

    prepared = prepare_trivy_sbom(original, workspace=workspace)

    assert prepared != original
    assert prepared == workspace / "os-context" / "upload.cdx.json"
    # The file name is preserved so Trivy's content sniffing sees what it
    # would have seen.
    assert prepared.name == original.name

    enriched = json.loads(prepared.read_text())
    assert [c for c in enriched["components"] if c["type"] == "operating-system"]
    # The upload is byte-identical to what the user sent.
    assert original.read_bytes() == before


def test_returns_the_original_when_there_is_nothing_to_add(tmp_path: Path) -> None:
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(tmp_path, "upload.cdx.json", _NPM_SBOM)

    assert prepare_trivy_sbom(original, workspace=workspace) == original
    # No stray directory in the caller's workspace either.
    assert not (workspace / "os-context").exists()


def test_announces_what_it_added_on_the_scan_log(tmp_path: Path) -> None:
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(tmp_path, "upload.cdx.json", _RPM_SBOM)
    lines: list[tuple[str, str]] = []

    prepare_trivy_sbom(
        original,
        workspace=workspace,
        scan_uuid=uuid.uuid4(),
        line_callback=lambda stream, line: lines.append((stream, line)),
    )

    assert len(lines) == 1
    stream, line = lines[0]
    assert stream == "stdout"
    assert "os-context" in line
    assert "centos 7" in line
    # The count says how much of the document voted, so a reader can judge it.
    assert "2/2" in line


def test_a_failing_log_callback_does_not_fail_the_scan(tmp_path: Path) -> None:
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(tmp_path, "upload.cdx.json", _RPM_SBOM)

    def _boom(stream: str, line: str) -> None:
        raise RuntimeError("publisher is down")

    prepared = prepare_trivy_sbom(original, workspace=workspace, line_callback=_boom)

    assert prepared == workspace / "os-context" / "upload.cdx.json"


def test_an_unreadable_file_falls_back_to_the_original_path(tmp_path: Path) -> None:
    workspace = tmp_path / "scan"
    workspace.mkdir()
    missing = tmp_path / "gone.cdx.json"

    # run_trivy_sbom raises its own typed error for a missing file; this seam
    # must not pre-empt that with a different failure.
    assert prepare_trivy_sbom(missing, workspace=workspace) == missing


def test_a_failing_write_falls_back_to_the_original_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(tmp_path, "upload.cdx.json", _RPM_SBOM)

    def _no_write(self: Path, data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", _no_write)

    assert prepare_trivy_sbom(original, workspace=workspace) == original


def test_a_document_too_large_to_hold_in_memory_is_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(tmp_path, "upload.cdx.json", _RPM_SBOM)

    monkeypatch.setattr(_trivy_input, "_MAX_ENRICH_BYTES", 1)

    assert prepare_trivy_sbom(original, workspace=workspace) == original


def test_rerunning_the_stage_overwrites_its_own_copy(tmp_path: Path) -> None:
    """``acks_late`` can re-enter on the same scan id and the same workspace."""
    workspace = tmp_path / "scan"
    workspace.mkdir()
    original = _write(tmp_path, "upload.cdx.json", _RPM_SBOM)

    first = prepare_trivy_sbom(original, workspace=workspace)
    second = prepare_trivy_sbom(original, workspace=workspace)

    assert first == second
    enriched = json.loads(second.read_text())
    assert (
        len([c for c in enriched["components"] if c["type"] == "operating-system"]) == 1
    )
