"""Unit tests — fixed_version collection from DT findings (v2.2 2.2-a1).

Covers the two pure helpers that turn an untrusted DT finding into a stored
``vulnerability_findings.fixed_version`` value:

  - ``_normalize_fixed_version`` — validates / normalizes a single candidate
    version string (the adversarial-input surface).
  - ``_extract_fixed_version`` — walks a DT finding's ``vulnerability`` object in
    priority order (structured patched lists → CycloneDX VEX affects[] →
    free-text recommendation) and returns the first value that normalizes.

Plus an integration-with-``_persist_findings`` check (fake session) that the
extracted value lands on the created ``VulnerabilityFinding`` row, and that the
hard-coded ``None`` was actually removed.

Fake-session unit tests (no DB), matching the sync-task test pattern in
``test_vuln_and_adversarial.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from tasks.scan_source import (
    _extract_fixed_version,
    _normalize_fixed_version,
    _persist_findings,
)


# ---------------------------------------------------------------------------
# Fake session (mirrors test_vuln_and_adversarial._FakeSession)
# ---------------------------------------------------------------------------
class _Res:
    def __init__(self, v: object) -> None:
        self._v = v

    def scalar_one_or_none(self) -> object:
        return self._v


class _FakeSession:
    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.added: list = []

    def execute(self, *_a: object, **_k: object) -> _Res:
        return _Res(self._results.pop(0))

    def add(self, obj: object) -> None:
        self.added.append(obj)


# ---------------------------------------------------------------------------
# _normalize_fixed_version — happy path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.17.1", "2.17.1"),
        ("v2.17.1", "2.17.1"),          # leading v stripped
        ("V3.2.0", "3.2.0"),            # uppercase V stripped
        ("  1.0.0  ", "1.0.0"),         # surrounding whitespace trimmed
        ("1.0.0-rc.1", "1.0.0-rc.1"),   # pre-release identifier
        ("1.0.0+build.42", "1.0.0+build.42"),  # build metadata
        ("2:1.2.3-1", "2:1.2.3-1"),     # epoch (deb-style)
        ("4.17.21", "4.17.21"),
        ("10", "10"),                   # bare major
    ],
)
def test_normalize_accepts_real_versions(raw: str, expected: str) -> None:
    assert _normalize_fixed_version(raw) == expected


# ---------------------------------------------------------------------------
# _normalize_fixed_version — ADVERSARIAL inputs (must all → None, never raise)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        None,                            # not a string
        123,                             # int
        4.2,                             # float
        True,                            # bool
        ["2.0.0"],                       # list
        {"version": "2.0.0"},            # dict
        "",                              # empty
        "   ",                           # whitespace-only
        "\t\n",                          # whitespace control-only
        # separator / operator-only tokens (do not start with a digit)
        "*",
        ">=",
        "-",
        ".",
        "~",
        "^1.0.0",                        # range operator prefix
        ">=2.0.0",                       # comparator prefix
        "latest",                        # alpha-led, not a version
        "unknown",
        # control characters EMBEDDED (content after them) — rejected outright,
        # never folded into a clean-looking value. (Trailing-only whitespace is
        # trimmed by .strip() and is covered separately below.)
        "2.17.1\r\n3.0.0",               # CRLF injection
        "2.17.1\x00",                    # NUL byte
        "2.17.1\x003.0.0",               # NUL splice
        "2.17.1\tand more",              # embedded tab
        "2.17.1\nmalicious",             # embedded newline
        "\x1b[31m2.0.0",                 # ANSI escape prefix
        # path traversal / URL schemes / spaces
        "../../etc/passwd",
        "javascript:alert(1)",
        "file:///etc/shadow",
        "2.0.0 OR 1=1",                  # space + sqli-ish payload
        "2.0.0; rm -rf /",               # shell metachar
        "2.0.0/../3.0.0",                # slash (not in charset)
        # oversized (> _FIXED_VERSION_MAX_LEN = 100)
        "1." + "0" * 200,
        "9" * 500,
    ],
)
def test_normalize_rejects_adversarial_input(raw: object) -> None:
    assert _normalize_fixed_version(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.17.1\t", "2.17.1"),   # trailing tab trimmed
        ("2.17.1\n", "2.17.1"),   # trailing newline trimmed
        ("\t2.17.1", "2.17.1"),   # leading tab trimmed
        ("\n  2.17.1  \n", "2.17.1"),
    ],
)
def test_normalize_trims_surrounding_whitespace_only(raw: str, expected: str) -> None:
    # Surrounding whitespace/control is trimmed (the value is still a clean
    # version). This is distinct from EMBEDDED control characters (content after
    # them), which are rejected — see test_normalize_rejects_adversarial_input.
    assert _normalize_fixed_version(raw) == expected


def test_normalize_at_length_boundary() -> None:
    # Exactly 100 chars passes; 101 chars fails.
    just_under = "1." + "0" * 98  # len 100
    assert len(just_under) == 100
    assert _normalize_fixed_version(just_under) == just_under
    over = "1." + "0" * 99  # len 101
    assert len(over) == 101
    assert _normalize_fixed_version(over) is None


# ---------------------------------------------------------------------------
# _extract_fixed_version — source priority + shapes
# ---------------------------------------------------------------------------
def test_extract_from_patched_versions_list() -> None:
    finding = {
        "vulnerability": {
            "vulnId": "CVE-2021-44228",
            "patchedVersions": ["2.17.1", "2.18.0"],
        }
    }
    assert _extract_fixed_version(finding) == "2.17.1"


def test_extract_from_patched_versions_comma_string() -> None:
    finding = {"vulnerability": {"patchedVersions": "2.17.0, 2.17.1"}}
    assert _extract_fixed_version(finding) == "2.17.0"


def test_extract_from_fixed_version_singular() -> None:
    finding = {"vulnerability": {"fixedVersion": "v3.2.0"}}
    assert _extract_fixed_version(finding) == "3.2.0"


def test_extract_from_list_of_dicts() -> None:
    finding = {"vulnerability": {"fixedVersions": [{"version": "1.2.3"}]}}
    assert _extract_fixed_version(finding) == "1.2.3"


def test_extract_from_cyclonedx_affects_skips_non_dict_version_entry() -> None:
    # A versions list with junk (non-dict) entries before the real fixed one
    # must skip the junk and still find the fix.
    finding = {
        "vulnerability": {
            "affects": [
                {
                    "versions": [
                        "not-a-dict",
                        None,
                        {"version": "4.17.21", "status": "fixed"},
                    ],
                }
            ]
        }
    }
    assert _extract_fixed_version(finding) == "4.17.21"


def test_extract_from_cyclonedx_affects_fixed() -> None:
    finding = {
        "vulnerability": {
            "affects": [
                {
                    "ref": "pkg:npm/lodash@4.17.20",
                    "versions": [
                        {"version": "4.17.20", "status": "affected"},
                        {"version": "4.17.21", "status": "fixed"},
                    ],
                }
            ]
        }
    }
    assert _extract_fixed_version(finding) == "4.17.21"


def test_extract_ignores_non_fixed_affects_status() -> None:
    finding = {
        "vulnerability": {
            "affects": [
                {"versions": [{"version": "4.17.20", "status": "affected"}]}
            ]
        }
    }
    assert _extract_fixed_version(finding) is None


def test_extract_from_recommendation_text() -> None:
    finding = {
        "vulnerability": {
            "recommendation": "Upgrade to 2.17.1 or later to remediate.",
        }
    }
    assert _extract_fixed_version(finding) == "2.17.1"


def test_extract_recommendation_strips_v_prefix() -> None:
    finding = {"vulnerability": {"recommendation": "Fixed in v3.2.0."}}
    assert _extract_fixed_version(finding) == "3.2.0"


def test_extract_structured_wins_over_recommendation() -> None:
    # Priority: a structured patched list beats the free-text note.
    finding = {
        "vulnerability": {
            "patchedVersions": ["9.9.9"],
            "recommendation": "Upgrade to 1.0.0",
        }
    }
    assert _extract_fixed_version(finding) == "9.9.9"


def test_extract_recommendation_without_version_returns_none() -> None:
    finding = {
        "vulnerability": {"recommendation": "Upgrade your runtime environment."}
    }
    assert _extract_fixed_version(finding) is None


# ---------------------------------------------------------------------------
# _extract_fixed_version — malformed / hostile findings (must not raise)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "finding",
    [
        None,
        {},
        "not-a-dict",
        123,
        {"vulnerability": None},
        {"vulnerability": "string"},
        {"vulnerability": {}},
        {"vulnerability": {"patchedVersions": None}},
        {"vulnerability": {"patchedVersions": [None, 123, {"x": 1}]}},
        {"vulnerability": {"affects": "not-a-list"}},
        {"vulnerability": {"affects": [None, "x", {"versions": "bad"}]}},
        {"vulnerability": {"affects": [{"versions": [{"status": "fixed"}]}]}},  # no version
        {"vulnerability": {"recommendation": 999}},
        # control-char-laden structured value normalizes to None → overall None
        {"vulnerability": {"patchedVersions": ["2.0.0\x00"]}},
        # huge recommendation: scan is capped, no concrete version → None
        {"vulnerability": {"recommendation": "no version here " * 5000}},
    ],
)
def test_extract_never_raises_on_malformed(finding: object) -> None:
    assert _extract_fixed_version(finding) is None  # type: ignore[arg-type]


def test_extract_recommendation_scan_is_capped() -> None:
    # A real version sitting AFTER the 4000-char scan cap is not found — this
    # bounds the regex scan against a pathological multi-megabyte note.
    note = ("padding " * 1000) + " upgrade to 5.5.5"
    assert len(note) > 4000
    assert _extract_fixed_version({"vulnerability": {"recommendation": note}}) is None


# ---------------------------------------------------------------------------
# Integration with _persist_findings — value lands on the row (None removed)
# ---------------------------------------------------------------------------
def test_persist_findings_sets_fixed_version_from_recommendation() -> None:
    vuln = SimpleNamespace(id=uuid.uuid4())
    cv = SimpleNamespace(id=uuid.uuid4())
    session = _FakeSession([vuln, cv])
    finding = {
        "vulnerability": {
            "vulnId": "CVE-2021-44228",
            "recommendation": "Upgrade to 2.17.1.",
        },
        "component": {"purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1"},
    }
    _persist_findings(session, scan_uuid=uuid.uuid4(), findings=[finding])
    assert len(session.added) == 1
    assert session.added[0].fixed_version == "2.17.1"


def test_persist_findings_fixed_version_none_when_dt_silent() -> None:
    vuln = SimpleNamespace(id=uuid.uuid4())
    cv = SimpleNamespace(id=uuid.uuid4())
    session = _FakeSession([vuln, cv])
    finding = {
        "vulnerability": {"vulnId": "CVE-2021-44228"},  # no fix data
        "component": {"purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1"},
    }
    _persist_findings(session, scan_uuid=uuid.uuid4(), findings=[finding])
    assert len(session.added) == 1
    assert session.added[0].fixed_version is None
