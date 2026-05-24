"""
Unit tests for ``services/upgrade_recommendation.py`` — v2.2 2.2-a3.

All pure (no DB): the recommendation engine takes plain ``FindingSignal`` lists
and version strings, so the full algorithm — minimum-safe-upgrade computation,
the three "no recommendation" outcomes, the priority signals, and the tolerant
version parser — is exercised here without a Postgres session.

Adversarial version input is the headline requirement (task §5): every
malformed / range / pre-release / epoch / oversized string must yield "no
recommendation" (or be handled correctly), NEVER raise.
"""

from __future__ import annotations

import pytest

from services.upgrade_recommendation import (
    FindingSignal,
    compare_versions,
    max_safe_version,
    parse_version,
    priority_rank,
    recommend_for_component,
)

# ---------------------------------------------------------------------------
# parse_version — happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_release", "is_pre"),
    [
        ("1.2.3", (1, 2, 3), False),
        ("2.17.1", (2, 17, 1), False),
        ("1.0", (1, 0), False),
        ("10", (10,), False),
        ("v2.17.1", (2, 17, 1), False),  # leading v stripped
        ("V3.0.0", (3, 0, 0), False),
        ("2.0.0-rc1", (2, 0, 0), True),  # pre-release marker
        ("1.2.3+build.5", (1, 2, 3), False),  # build metadata ignored
        ("1:2.3.4", (1, 2, 3, 4), False),  # epoch becomes first segment
        ("1_2_3", (1, 2, 3), False),  # underscore separators
    ],
)
def test_parse_version_happy(raw: str, expected_release: tuple[int, ...], is_pre: bool) -> None:
    parsed = parse_version(raw)
    assert parsed is not None
    assert parsed.release == expected_release
    assert parsed.is_prerelease is is_pre
    assert parsed.raw == raw  # the original string is preserved verbatim


# ---------------------------------------------------------------------------
# parse_version — ADVERSARIAL input (must return None, never raise)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        # non-version / operator-only / range expressions
        "",
        "   ",
        "*",
        "latest",
        "main",
        "x.y.z",
        ">=1.0.0",
        "<2.0",
        "~1.2.3",
        "^1.0.0",
        "1.x",
        "1.2.x",
        "[1.0,2.0)",
        "1.0 - 2.0",
        "1.0||2.0",
        "GA",
        "2.0.GA",
        "1.0rc",  # non-numeric release segment
        # injection-ish / hostile
        "../../etc/passwd",
        "javascript:alert(1)",
        "1.0.0; rm -rf /",
        "1.0\n2.0",  # embedded newline (control char)
        "1.0\t0",  # embedded tab
        "1.0\x00",  # NUL
        "1.0\rdev",  # embedded CR (mid-string control char)
        # oversized
        "1." + "9" * 5000,
        "9" * 5000,
        # leading dot / separator-only
        ".1.2",
        "...",
        "1.2..3",  # doubled separator
        "1.2.",  # trailing separator
        "+build.1",  # empty release after build-metadata strip
        "x:1.0",  # non-numeric epoch
        "-1.0",  # leading '-' → empty release part
        ":",
        # wrong type sentinels handled below separately
    ],
)
def test_parse_version_adversarial_returns_none(raw: str) -> None:
    # The contract: parse_version NEVER raises and returns None for junk.
    assert parse_version(raw) is None


@pytest.mark.parametrize("value", [None, 123, 1.5, [], {}, object()])
def test_parse_version_non_string_returns_none(value: object) -> None:
    assert parse_version(value) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["1.2.3\r", " 1.2.3 ", "\t2.0.0\n"])
def test_parse_version_strips_surrounding_whitespace(raw: str) -> None:
    # Surrounding whitespace (incl. CR/LF/TAB) is legitimately stripped, not an
    # injection — the trimmed value parses. Mid-string control chars are still
    # rejected (covered above).
    parsed = parse_version(raw)
    assert parsed is not None
    assert parsed.release[0] in (1, 2)


def test_parse_version_oversized_boundary() -> None:
    # Exactly at the cap parses (256 chars of digits-and-dots); over it does not.
    just_under = "1." + "2." * 100 + "3"  # well-formed, under the length cap
    assert parse_version(just_under) is not None
    over = "1" + "0" * 300
    assert parse_version(over) is None


# ---------------------------------------------------------------------------
# compare_versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "sign"),
    [
        ("1.0.0", "2.0.0", -1),
        ("2.0.0", "1.0.0", 1),
        ("1.2.0", "1.2.0", 0),
        ("1.2", "1.2.0", 0),  # zero-padding equivalence
        ("1.2", "1.2.1", -1),
        ("2.17.1", "2.17.0", 1),
        ("2.0.0-rc1", "2.0.0", -1),  # pre-release < final
        ("2.0.0", "2.0.0-rc1", 1),
        ("2.0.0-rc1", "2.0.0-rc2", -1),  # numeric pre-release tie-break
        ("1:0", "0:9", 1),  # epoch dominates
        ("10.0.0", "9.0.0", 1),  # numeric not lexical
    ],
)
def test_compare_versions(a: str, b: str, sign: int) -> None:
    pa, pb = parse_version(a), parse_version(b)
    assert pa is not None and pb is not None
    result = compare_versions(pa, pb)
    assert (result > 0) == (sign > 0)
    assert (result < 0) == (sign < 0)
    assert (result == 0) == (sign == 0)


# ---------------------------------------------------------------------------
# max_safe_version
# ---------------------------------------------------------------------------


def test_max_safe_version_picks_semver_max() -> None:
    # The minimum safe upgrade is the MAX of the fix versions.
    assert max_safe_version(["1.2.3", "1.5.0", "1.4.9"]) == "1.5.0"


def test_max_safe_version_ignores_unparseable() -> None:
    # Junk is skipped; the max of the parseable survivors wins.
    assert max_safe_version(["1.2.3", ">=2.0", "garbage", "1.4.0"]) == "1.4.0"


def test_max_safe_version_all_unparseable_returns_none() -> None:
    assert max_safe_version([">=1.0", "*", "latest"]) is None


def test_max_safe_version_empty_returns_none() -> None:
    assert max_safe_version([]) is None


def test_max_safe_version_returns_original_string() -> None:
    # The winning version's ORIGINAL form (incl. leading v) is returned.
    assert max_safe_version(["v2.0.0", "1.9.9"]) == "v2.0.0"


# ---------------------------------------------------------------------------
# recommend_for_component — outcomes
# ---------------------------------------------------------------------------


def _sig(fixed: str | None, sev: str = "high", epss: float | None = None) -> FindingSignal:
    return FindingSignal(fixed_version=fixed, severity=sev, epss_score=epss)


def test_recommend_ok_max_of_fixes() -> None:
    rec = recommend_for_component(
        [_sig("1.2.0"), _sig("1.5.0"), _sig("1.3.0")],
        direct=True,
    )
    assert rec.reason == "ok"
    assert rec.recommended_version == "1.5.0"
    assert rec.finding_count == 3
    assert rec.direct is True


def test_recommend_no_fix_version_when_any_missing() -> None:
    # One open finding has no fix → refuse to recommend a partial upgrade.
    rec = recommend_for_component(
        [_sig("1.2.0"), _sig(None), _sig("1.5.0")],
        direct=True,
    )
    assert rec.reason == "no_fix_version"
    assert rec.recommended_version is None
    # priority signals still populated for sorting / display.
    assert rec.finding_count == 3


def test_recommend_unparseable_when_all_fixes_malformed() -> None:
    rec = recommend_for_component(
        [_sig(">=1.0"), _sig("garbage")],
        direct=False,
    )
    assert rec.reason == "unparseable_version"
    assert rec.recommended_version is None


def test_recommend_no_open_findings() -> None:
    rec = recommend_for_component([], direct=False)
    assert rec.reason == "no_open_findings"
    assert rec.recommended_version is None
    assert rec.finding_count == 0


def test_recommend_partial_parse_recovers_max() -> None:
    # Some fixes parse, some don't, none are None → we still recommend the max
    # of the parseable ones (reason 'ok'): every finding HAD a fix string.
    rec = recommend_for_component(
        [_sig("1.2.0"), _sig("not-a-version"), _sig("1.9.0")],
        direct=True,
    )
    assert rec.reason == "ok"
    assert rec.recommended_version == "1.9.0"


# ---------------------------------------------------------------------------
# recommend_for_component — adversarial fix versions must never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_fix",
    [
        "*",
        ">=2.0",
        "[1.0,2.0)",
        "../etc/passwd",
        "javascript:alert(1)",
        "1.0\n2.0",
        "9" * 5000,
        "1:bad",
        "latest",
    ],
)
def test_recommend_adversarial_fix_never_raises(bad_fix: str) -> None:
    # A single malformed fix among real ones is skipped; the real max wins.
    rec = recommend_for_component(
        [_sig("1.2.0"), _sig(bad_fix), _sig("1.4.0")],
        direct=False,
    )
    assert rec.recommended_version == "1.4.0"
    assert rec.reason == "ok"

    # And when EVERY fix is the malformed token, we degrade to unparseable.
    rec2 = recommend_for_component([_sig(bad_fix)], direct=False)
    assert rec2.recommended_version is None
    assert rec2.reason == "unparseable_version"


# ---------------------------------------------------------------------------
# priority signals
# ---------------------------------------------------------------------------


def test_max_severity_and_epss_signals() -> None:
    rec = recommend_for_component(
        [
            _sig("1.0.0", sev="low", epss=0.01),
            _sig("1.1.0", sev="critical", epss=0.97),
            _sig("1.2.0", sev="medium", epss=None),
        ],
        direct=True,
    )
    assert rec.max_severity == "critical"
    assert rec.max_epss == 0.97
    assert rec.recommended_version == "1.2.0"


def test_priority_rank_direct_beats_transitive() -> None:
    direct = recommend_for_component([_sig("1.0.0", sev="high", epss=0.5)], direct=True)
    transitive = recommend_for_component(
        [_sig("1.0.0", sev="high", epss=0.5)], direct=False
    )
    assert priority_rank(direct) > priority_rank(transitive)


def test_priority_rank_severity_orders() -> None:
    crit = recommend_for_component([_sig("1.0.0", sev="critical")], direct=True)
    low = recommend_for_component([_sig("1.0.0", sev="low")], direct=True)
    assert priority_rank(crit) > priority_rank(low)


def test_priority_rank_epss_tiebreak() -> None:
    hi = recommend_for_component([_sig("1.0.0", sev="high", epss=0.9)], direct=True)
    lo = recommend_for_component([_sig("1.0.0", sev="high", epss=0.1)], direct=True)
    assert priority_rank(hi) > priority_rank(lo)


def test_priority_rank_non_actionable_demoted() -> None:
    # A 'no_fix_version' (non-actionable) recommendation must rank below an
    # actionable direct one even if its severity is higher.
    non_actionable = recommend_for_component(
        [_sig(None, sev="critical", epss=0.99)], direct=True
    )
    actionable = recommend_for_component(
        [_sig("1.0.0", sev="low", epss=0.0)], direct=True
    )
    assert priority_rank(actionable) > priority_rank(non_actionable)
