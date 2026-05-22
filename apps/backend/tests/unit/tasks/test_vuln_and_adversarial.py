"""Tier 5 — deterministic vulnerability mapping + adversarial parser inputs.

Two gaps the prior suite left open:

1. **Deterministic vuln detection.** Vuln matching itself needs an NVD mirror,
   but the part WE own — turning a DT finding into a persisted
   ``VulnerabilityFinding`` — is fully deterministic and was untested. A recorded
   DT finding (a known CVE) must map to a finding row; malformed / unknown-vuln /
   unknown-component findings must be skipped, never crash. This catches a
   "we silently stopped persisting CVEs" regression without a live NVD.

2. **Adversarial license-expression inputs.** ``_classify_license_category`` is
   fed untrusted scancode/cdxgen SPDX strings; hostile values (CRLF, NUL, huge,
   deeply nested) must not crash and must classify sanely.

Fake-session unit tests (no DB), matching the sync-task test pattern.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from tasks.scan_source import _classify_license_category, _persist_findings


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v


class _FakeSession:
    """Returns queued ``execute`` results in call order (per finding the task
    runs the Vulnerability lookup then the ComponentVersion lookup)."""

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []

    def execute(self, *_a, **_k):
        return _Res(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)


_LOG4SHELL = {
    "vulnerability": {"vulnId": "CVE-2021-44228", "severity": "CRITICAL"},
    "component": {"purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1"},
}


def test_known_cve_maps_to_vulnerability_finding() -> None:
    vuln = SimpleNamespace(id=uuid.uuid4())
    cv = SimpleNamespace(id=uuid.uuid4())
    s = _FakeSession([vuln, cv])  # Vulnerability lookup, then ComponentVersion
    sid = uuid.uuid4()

    _persist_findings(s, scan_uuid=sid, findings=[_LOG4SHELL])

    assert len(s.added) == 1
    vf = s.added[0]
    assert vf.vulnerability_id == vuln.id
    assert vf.component_version_id == cv.id
    assert vf.scan_id == sid
    assert vf.status == "new"


def test_external_id_falls_back_to_source_name() -> None:
    vuln = SimpleNamespace(id=uuid.uuid4())
    cv = SimpleNamespace(id=uuid.uuid4())
    s = _FakeSession([vuln, cv])
    finding = {
        "vulnerability": {"source": {"name": "GHSA-jfh8-c2jp-5v3q"}},
        "component": {"purl": "pkg:npm/lodash@4.17.20"},
    }
    _persist_findings(s, scan_uuid=uuid.uuid4(), findings=[finding])
    assert len(s.added) == 1


def test_finding_skipped_when_vuln_not_in_catalog() -> None:
    # Vulnerability lookup returns None (resync hasn't materialised it) → skip,
    # ComponentVersion lookup never runs.
    s = _FakeSession([None])
    _persist_findings(s, scan_uuid=uuid.uuid4(), findings=[_LOG4SHELL])
    assert s.added == []


def test_finding_skipped_when_component_unknown() -> None:
    vuln = SimpleNamespace(id=uuid.uuid4())
    s = _FakeSession([vuln, None])  # vuln found, component not
    _persist_findings(s, scan_uuid=uuid.uuid4(), findings=[_LOG4SHELL])
    assert s.added == []


@pytest.mark.parametrize(
    "finding",
    [
        None,
        {},
        {"vulnerability": None, "component": None},
        {"vulnerability": {"vulnId": "CVE-1"}},  # no purl
        {"component": {"purl": "pkg:npm/x@1"}},  # no vulnId
        {"vulnerability": {}, "component": {}},
        "not-a-dict",
        123,
    ],
)
def test_malformed_findings_are_skipped_not_crashed(finding) -> None:
    s = _FakeSession([])  # no lookups should run
    _persist_findings(s, scan_uuid=uuid.uuid4(), findings=[finding])
    assert s.added == []


# ---------------------------------------------------------------------------
# Adversarial license-expression classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spdx",
    [
        "GPL-3.0-only\r\nMIT",          # CRLF
        "GPL-3.0-only\x00",             # NUL
        "MIT " * 5000,                  # huge
        "(((MIT OR Apache-2.0)))",     # deep parens
        "AND OR WITH",                  # operators only
        "   ",                          # whitespace
        "MIT\tAND\tGPL-3.0-only",      # tab separators
    ],
)
def test_classify_does_not_crash_on_hostile_input(spdx: str) -> None:
    result = _classify_license_category(spdx)
    assert result in {"forbidden", "conditional", "allowed", "unknown"}


def test_classify_huge_gpl_compound_is_forbidden() -> None:
    # A forbidden term anywhere in a large compound still wins.
    expr = " AND ".join(["MIT"] * 100 + ["GPL-3.0-only"] + ["Apache-2.0"] * 100)
    assert _classify_license_category(expr) == "forbidden"
