"""
Unit tests for scancode detected-license persistence in ``tasks.scan_source``
(PR-A2).

These cover the in-memory logic of ``_persist_detected_licenses`` and
``_get_or_create_first_party_component_version`` without a DB: we patch the
``_get_or_create_component`` / ``_get_or_create_component_version`` /
``_get_or_create_license`` helpers (which need a real session) and capture the
``LicenseFinding`` rows handed to ``session.add``.

What we pin:
  - Each detected (spdx, path) becomes ONE LicenseFinding with kind='detected',
    source_path=<file>, raw_data.source='scancode' — distinct provenance from
    the cdxgen 'declared' findings.
  - All detected findings anchor on a single synthetic first-party
    ComponentVersion (one component, one version per scan).
  - The first-party component name comes from sbom.metadata.component.name when
    present, else a generic fallback.
  - Empty detections → no findings, no first-party component created.
  - Duplicate (spdx, path) tuples are de-duplicated before add (defends the
    uq_license_findings unique constraint).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from integrations.scancode import DetectedLicense


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeLicense:
    def __init__(self, spdx_id: str) -> None:
        self.id = uuid.uuid4()
        self.spdx_id = spdx_id


class _FakeSession:
    """Records ``add``-ed rows; everything else is a no-op."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:  # pragma: no cover - not exercised here
        pass


@pytest.fixture
def patched_helpers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the DB-touching get_or_create helpers; record their calls."""
    calls: dict[str, Any] = {"components": [], "versions": [], "licenses": []}
    fp_component = _FakeComponent()
    fp_version = _FakeComponentVersion()

    def _fake_component(session: Any, *, purl: str, name: str, package_type: str) -> Any:
        calls["components"].append({"purl": purl, "name": name, "type": package_type})
        return fp_component

    def _fake_version(
        session: Any, *, component: Any, version: str, purl_with_version: str
    ) -> Any:
        calls["versions"].append(
            {"version": version, "purl_with_version": purl_with_version}
        )
        return fp_version

    def _fake_license(session: Any, *, spdx_id: str, reference_url: str | None) -> Any:
        calls["licenses"].append(spdx_id)
        return _FakeLicense(spdx_id)

    monkeypatch.setattr("tasks.scan_source._get_or_create_component", _fake_component)
    monkeypatch.setattr(
        "tasks.scan_source._get_or_create_component_version", _fake_version
    )
    monkeypatch.setattr("tasks.scan_source._get_or_create_license", _fake_license)
    calls["fp_version_id"] = fp_version.id
    return calls


def test_detected_findings_have_distinct_provenance(
    patched_helpers: dict[str, Any],
) -> None:
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    scan_uuid = uuid.uuid4()
    sbom = {"metadata": {"component": {"name": "my-app"}}}
    detections = [
        DetectedLicense(spdx_id="MIT", source_path="LICENSE"),
        DetectedLicense(spdx_id="Apache-2.0", source_path="src/app.py"),
    ]

    _persist_detected_licenses(
        session, scan_uuid=scan_uuid, sbom=sbom, detections=detections  # type: ignore[arg-type]
    )

    findings = session.added
    assert len(findings) == 2
    for f in findings:
        assert f.kind == "detected"
        assert f.raw_data == {"source": "scancode"}
        assert f.scan_id == scan_uuid
        # All anchor on the single synthetic first-party component version.
        assert f.component_version_id == patched_helpers["fp_version_id"]
    # source_path carries scancode's per-file path.
    paths = {f.source_path for f in findings}
    assert paths == {"LICENSE", "src/app.py"}


def test_first_party_component_named_from_sbom_metadata(
    patched_helpers: dict[str, Any],
) -> None:
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    sbom = {"metadata": {"component": {"name": "cool-service"}}}
    _persist_detected_licenses(
        session,  # type: ignore[arg-type]
        scan_uuid=uuid.uuid4(),
        sbom=sbom,  # type: ignore[arg-type]
        detections=[DetectedLicense(spdx_id="MIT", source_path="LICENSE")],
    )
    assert patched_helpers["components"][0]["name"] == "cool-service"
    assert patched_helpers["components"][0]["type"] == "trustedoss"
    assert patched_helpers["components"][0]["purl"] == "pkg:trustedoss/first-party"


def test_first_party_component_falls_back_when_metadata_absent(
    patched_helpers: dict[str, Any],
) -> None:
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    _persist_detected_licenses(
        session,  # type: ignore[arg-type]
        scan_uuid=uuid.uuid4(),
        sbom={},  # type: ignore[arg-type]
        detections=[DetectedLicense(spdx_id="MIT", source_path="LICENSE")],
    )
    assert patched_helpers["components"][0]["name"] == "first-party"


def test_first_party_version_is_scan_scoped(
    patched_helpers: dict[str, Any],
) -> None:
    """purl_with_version embeds the scan id so two scans don't share rows."""
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    scan_uuid = uuid.uuid4()
    _persist_detected_licenses(
        session,  # type: ignore[arg-type]
        scan_uuid=scan_uuid,
        sbom={},  # type: ignore[arg-type]
        detections=[DetectedLicense(spdx_id="MIT", source_path="LICENSE")],
    )
    assert patched_helpers["versions"][0]["version"] == str(scan_uuid)
    assert patched_helpers["versions"][0]["purl_with_version"] == (
        f"pkg:trustedoss/first-party@{scan_uuid}"
    )


def test_empty_detections_is_noop(patched_helpers: dict[str, Any]) -> None:
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    _persist_detected_licenses(
        session, scan_uuid=uuid.uuid4(), sbom={}, detections=[]  # type: ignore[arg-type]
    )
    assert session.added == []
    # No first-party component created when there is nothing to attach.
    assert patched_helpers["components"] == []


def test_duplicate_detections_deduped(patched_helpers: dict[str, Any]) -> None:
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    detections = [
        DetectedLicense(spdx_id="MIT", source_path="LICENSE"),
        DetectedLicense(spdx_id="MIT", source_path="LICENSE"),  # dup
        DetectedLicense(spdx_id="MIT", source_path="src/a.py"),  # distinct path
    ]
    _persist_detected_licenses(
        session,  # type: ignore[arg-type]
        scan_uuid=uuid.uuid4(),
        sbom={},  # type: ignore[arg-type]
        detections=detections,
    )
    assert len(session.added) == 2


# ---------------------------------------------------------------------------
# security-reviewer High — persistence-layer SPDX width guard (defence in
# depth). Even if the adapter cap is bypassed, an over-64-char detected token
# must NOT reach _get_or_create_license / the INSERT (which would raise
# StringDataRightTruncation and roll back the whole transaction).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spdx_id",
    [
        "A" * 65,
        "LicenseRef-" + "z" * 100,
        "MIT OR " + "Apache-2.0 OR " * 20,
        "GPL-2.0-only WITH " + "Classpath-exception-2.0-" * 10,
    ],
    ids=["65-char", "oversized-licenseref", "oversized-or", "oversized-with"],
)
def test_persist_skips_oversized_spdx_token(
    patched_helpers: dict[str, Any], spdx_id: str
) -> None:
    from tasks.scan_source import _persist_detected_licenses

    session = _FakeSession()
    detections = [
        DetectedLicense(spdx_id=spdx_id, source_path="evil.py"),
        DetectedLicense(spdx_id="MIT", source_path="good.py"),  # clean sibling
    ]
    _persist_detected_licenses(
        session,  # type: ignore[arg-type]
        scan_uuid=uuid.uuid4(),
        sbom={},  # type: ignore[arg-type]
        detections=detections,
    )
    # Only the clean MIT finding is persisted; the oversized token is skipped
    # and never handed to _get_or_create_license.
    assert len(session.added) == 1
    assert session.added[0].source_path == "good.py"
    assert patched_helpers["licenses"] == ["MIT"]


def test_persist_keeps_spdx_at_exactly_limit(
    patched_helpers: dict[str, Any],
) -> None:
    from tasks.scan_source import _SPDX_ID_MAX_LENGTH, _persist_detected_licenses

    at_limit = "L" * _SPDX_ID_MAX_LENGTH
    session = _FakeSession()
    _persist_detected_licenses(
        session,  # type: ignore[arg-type]
        scan_uuid=uuid.uuid4(),
        sbom={},  # type: ignore[arg-type]
        detections=[DetectedLicense(spdx_id=at_limit, source_path="a.py")],
    )
    assert len(session.added) == 1
    assert patched_helpers["licenses"] == [at_limit]
