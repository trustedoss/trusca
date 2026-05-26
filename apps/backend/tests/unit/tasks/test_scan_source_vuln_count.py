"""Unit tests for the #35 Surface B scan-time DT vuln-DB count capture.

``_record_dt_vuln_count`` writes the DT vulnerability-database size observed
during a scan into ``scan_metadata['dt_vulnerability_count']`` so the project
Overview can later tell "0 CVEs = safe" apart from "0 CVEs = empty DB".

The subtle part is JSONB mutation tracking: an in-place ``dict`` edit on a
plain ``JSONB`` column is invisible to SQLAlchemy, so the helper must reassign
a fresh dict. These tests use a fake session (no DB) to pin both that the key
is written, that sibling keys survive, and that a missing row is a no-op.
"""

from __future__ import annotations

import uuid
from typing import Any

from tasks.scan_source import _record_dt_vuln_count


class _FakeScan:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.scan_metadata = metadata


class _FakeSession:
    def __init__(self, scan: _FakeScan | None) -> None:
        self._scan = scan

    def get(self, _model: Any, _ident: Any) -> _FakeScan | None:
        return self._scan


def test_record_dt_vuln_count_writes_key_and_preserves_siblings() -> None:
    scan = _FakeScan({"source_type": "git", "release": "v1.2.3"})
    original = scan.scan_metadata

    _record_dt_vuln_count(_FakeSession(scan), scan_uuid=uuid.uuid4(), count=43048)

    assert scan.scan_metadata["dt_vulnerability_count"] == 43048
    # Sibling metadata is untouched.
    assert scan.scan_metadata["source_type"] == "git"
    assert scan.scan_metadata["release"] == "v1.2.3"
    # Reassigned to a NEW dict so SQLAlchemy registers the JSONB mutation.
    assert scan.scan_metadata is not original


def test_record_dt_vuln_count_handles_empty_metadata() -> None:
    scan = _FakeScan({})
    _record_dt_vuln_count(_FakeSession(scan), scan_uuid=uuid.uuid4(), count=0)
    assert scan.scan_metadata == {"dt_vulnerability_count": 0}


def test_record_dt_vuln_count_noop_when_scan_missing() -> None:
    # Row vanished between commit and this write — must not raise.
    _record_dt_vuln_count(_FakeSession(None), scan_uuid=uuid.uuid4(), count=5)
