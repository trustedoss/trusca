"""Unit tests — CocoaPods fill-in pipeline wiring (Phase L).

Runs the real captured fixtures through ``_merge_cocoapods_components``:
``real_cyclonedx_swift.json`` is genuine cdxgen 12.3.3 output over a
Package.resolved-only tree captured WITHOUT a swift toolchain on PATH (that
capture doubles as the offline-parse verification: cdxgen read the committed
lockfile directly — Alamofire 5.8.1 + swift-log 1.5.3 as pkg:swift purls);
the Podfile.lock is the real ``pod install`` fixture vendored from BomLens.

Pins:
  * copy-then-commit — disk and memory identical after a merge; a failed
    disk rewrite leaves both untouched;
  * merged pods flow through ``persist_sbom_components`` with
    ``dependency_scope`` NULL (Podfile.lock has no runtime/test signal — the
    FE renders an em-dash, never a fabricated "Required");
  * no Podfile.lock → full no-op.
"""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from integrations.cdxgen import CdxgenResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SWIFT_SBOM = FIXTURES / "sbom" / "real_cyclonedx_swift.json"
PODFILE_LOCK = FIXTURES / "cocoapods" / "Podfile.lock"


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


@pytest.fixture
def quiet_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the helper's scan_metadata write into a no-op session scope."""

    class _NullSession:
        def get(self, *args: Any, **kwargs: Any) -> None:
            return None

        def commit(self) -> None:
            return None

    @contextmanager
    def _scope() -> Any:
        yield _NullSession()

    monkeypatch.setattr("tasks.scan_source.sync_session_scope", _scope)


def _cdxgen_result(tmp_path: Path) -> CdxgenResult:
    sbom = json.loads(SWIFT_SBOM.read_text(encoding="utf-8"))
    sbom_path = tmp_path / "bom.json"
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    return CdxgenResult(sbom_path=sbom_path, sbom=sbom)


def _source_dir_with_lock(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    shutil.copy(PODFILE_LOCK, source / "Podfile.lock")
    return source


def test_merge_updates_disk_and_memory_consistently(
    tmp_path: Path, quiet_telemetry: None
) -> None:
    from tasks import scan_source

    cdxgen_result = _cdxgen_result(tmp_path)
    before_count = len(cdxgen_result.sbom["components"])
    source = _source_dir_with_lock(tmp_path)

    scan_source._merge_cocoapods_components(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=source
    )

    on_disk = json.loads(cdxgen_result.sbom_path.read_text(encoding="utf-8"))
    assert on_disk == cdxgen_result.sbom
    purls = {
        c.get("purl")
        for c in cdxgen_result.sbom["components"]
        if isinstance(c, dict)
    }
    assert "pkg:cocoapods/Alamofire@5.8.1" in purls
    assert "pkg:cocoapods/Moya@15.0.0" in purls
    assert "pkg:cocoapods/Moya%2FCore@15.0.0" in purls
    assert len(cdxgen_result.sbom["components"]) == before_count + 3
    # The original pkg:swift components are untouched (union, not replace).
    assert "pkg:swift/github.com/Alamofire/Alamofire@5.8.1" in purls


def test_merge_noop_without_podfile_lock(
    tmp_path: Path, quiet_telemetry: None
) -> None:
    from tasks import scan_source

    cdxgen_result = _cdxgen_result(tmp_path)
    original = copy.deepcopy(cdxgen_result.sbom)
    source = tmp_path / "src"
    source.mkdir()

    scan_source._merge_cocoapods_components(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=source
    )

    assert cdxgen_result.sbom == original
    assert json.loads(cdxgen_result.sbom_path.read_text(encoding="utf-8")) == original


def test_merge_rewrite_failure_keeps_memory_and_disk_unmerged(
    tmp_path: Path, quiet_telemetry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks import scan_source

    monkeypatch.setattr(
        "tasks.scan_source.sbom_scope_filter.rewrite_sbom_file",
        lambda path, sbom: False,
    )
    cdxgen_result = _cdxgen_result(tmp_path)
    original = copy.deepcopy(cdxgen_result.sbom)
    source = _source_dir_with_lock(tmp_path)

    scan_source._merge_cocoapods_components(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=source
    )

    assert cdxgen_result.sbom == original
    assert json.loads(cdxgen_result.sbom_path.read_text(encoding="utf-8")) == original


def test_merged_pods_persist_with_null_dependency_scope(
    tmp_path: Path, quiet_telemetry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from models import ScanComponent
    from tasks import scan_source

    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component",
        lambda session, *, purl, name, package_type: _FakeComponent(),
    )
    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component_version",
        lambda session, *, component, version, purl_with_version: _FakeComponentVersion(),
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_component_licenses",
        lambda session, *, scan_uuid, component_version_id, cdxgen_component, purl: None,
    )
    monkeypatch.setattr(
        "tasks.scan_source._persist_dependency_graph",
        lambda session, **kwargs: None,
    )

    cdxgen_result = _cdxgen_result(tmp_path)
    source = _source_dir_with_lock(tmp_path)
    scan_source._merge_cocoapods_components(
        scan_uuid=uuid.uuid4(), cdxgen_result=cdxgen_result, source_dir=source
    )

    session = _FakeSession()
    scan_source.persist_sbom_components(
        session, scan_uuid=uuid.uuid4(), sbom=cdxgen_result.sbom
    )
    pod_rows = [
        r
        for r in session.added
        if isinstance(r, ScanComponent)
        and (r.dependency_path or "").startswith("pkg:cocoapods/")
    ]
    assert len(pod_rows) == 3
    assert all(r.dependency_scope is None for r in pod_rows)
