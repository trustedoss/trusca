"""
Pure unit tests for the per-axis, non-saturating risk scorer
(``services.risk_score``) and the severity / license filter normalisation
helpers in ``services.project_detail_service``.

These tests do NOT need a database — they exercise pure Python helpers, so
they run on every PR even when DATABASE_URL is unset.

Wave 1 #34 rewrote the scorer from a single weighted sum capped at 100 into
two independent, non-saturating axes. These tests pin the new behaviour and,
crucially, the two defects the rewrite fixes:

  * conditional licenses alone can never read as "Critical";
  * the score no longer saturates (7 vs 700 criticals differ, never hit 100).
"""

from __future__ import annotations

import pytest

from services import risk_score


class TestAxisBands:
    """The worst category present fixes the band; its count fixes the position."""

    @pytest.mark.parametrize(
        ("distribution", "expected"),
        [
            ({}, 0.0),
            ({"info": 99, "none": 99}, 0.0),  # info/none never contribute
            ({"low": 1}, 5.6),  # band 1–24, n=1 → 1 + 23·(1/5)
            ({"medium": 1}, 29.8),  # band 25–49, n=1
            ({"high": 1}, 54.8),  # band 50–74, n=1
            ({"critical": 1}, 80.0),  # band 75–100, n=1
        ],
    )
    def test_security_axis_n1_band_values(
        self, distribution: dict[str, int], expected: float
    ) -> None:
        assert risk_score.security_score(distribution) == expected

    @pytest.mark.parametrize(
        ("distribution", "expected"),
        [
            ({}, 0.0),
            ({"allowed": 99}, 0.0),  # allowed never contributes
            ({"unknown": 1}, 5.6),  # band 1–24, n=1
            ({"conditional": 1}, 29.8),  # band 25–49, n=1
            ({"forbidden": 1}, 80.0),  # band 75–100, n=1
        ],
    )
    def test_license_axis_n1_band_values(
        self, distribution: dict[str, int], expected: float
    ) -> None:
        assert risk_score.license_score(distribution) == expected

    def test_worst_present_category_wins(self) -> None:
        # A single critical outranks a crowd of lesser findings — the band is
        # set by the worst present, never "outvoted" by lower severities.
        score = risk_score.security_score(
            {"critical": 1, "high": 50, "medium": 200, "low": 999}
        )
        assert 75.0 <= score < 100.0


class TestConditionalLicenseNeverCritical:
    """The reported bug: 24 conditional licenses + 0 vulns ⇒ NOT 'Critical'."""

    def test_conditional_only_caps_at_medium_band(self) -> None:
        lic = {"conditional": 24, "allowed": 100}
        score = risk_score.license_score(lic)
        # Medium band ceiling is 49 — conditional alone can never reach High (50)
        # let alone Critical (75).
        assert 25.0 <= score <= 49.0

    def test_conditional_only_overall_is_not_critical(self) -> None:
        sec = risk_score.security_score({})  # zero vulnerabilities
        lic = risk_score.license_score({"conditional": 24, "allowed": 100})
        overall = risk_score.overall_risk_score(sec, lic)
        assert sec == 0.0
        assert overall < 75.0  # below the Critical threshold


class TestNonSaturating:
    """No hard cap: more findings always score higher, bounded by the ceiling."""

    def test_more_criticals_score_strictly_higher(self) -> None:
        assert (
            risk_score.security_score({"critical": 7})
            < risk_score.security_score({"critical": 700})
        )

    def test_hundreds_of_findings_still_leave_headroom(self) -> None:
        # The old formula pinned anything past a handful of findings to a flat
        # 100; hundreds of findings now still read below the ceiling.
        assert risk_score.security_score({"critical": 700}) < 100.0
        assert risk_score.license_score({"forbidden": 700}) < 100.0

    def test_lower_bands_never_leak_into_a_higher_grade(self) -> None:
        # No count can push a sub-Critical band over its ceiling into the next
        # grade: high tops out at 74 (never 75 == Critical), medium at 49.
        assert risk_score.security_score({"high": 10_000}) <= 74.0
        assert risk_score.security_score({"medium": 10_000}) <= 49.0

    def test_within_band_is_monotonic(self) -> None:
        scores = [risk_score.security_score({"high": n}) for n in (1, 2, 5, 20)]
        assert scores == sorted(scores)
        assert all(50.0 <= s <= 74.0 for s in scores)


class TestOverallAndBackCompat:
    """overall = worse axis; the legacy single-number entry point delegates."""

    def test_overall_is_max_of_axes(self) -> None:
        assert risk_score.overall_risk_score(80.0, 30.0) == 80.0
        assert risk_score.overall_risk_score(10.0, 45.0) == 45.0

    def test_compute_risk_score_matches_overall(self) -> None:
        sev = {"critical": 1}
        lic = {"forbidden": 1}
        assert risk_score.compute_risk_score(sev, lic) == risk_score.overall_risk_score(
            risk_score.security_score(sev), risk_score.license_score(lic)
        )

    def test_empty_distributions_yield_zero(self) -> None:
        assert risk_score.compute_risk_score({}, {}) == 0.0

    def test_scores_return_float_not_int(self) -> None:
        # Frontend gauges expect a float 0..100. Pin the type so a future
        # refactor doesn't quietly downgrade to int.
        assert isinstance(risk_score.security_score({"critical": 1}), float)
        assert isinstance(risk_score.license_score({"forbidden": 1}), float)


class TestSeverityNormalization:
    """The DB enum carries 'unknown'; the API normalises invalid filter values."""

    def test_severity_filter_drops_invalid_values(self) -> None:
        from services.project_detail_service import _normalize_severity_filter

        assert _normalize_severity_filter(None) is None
        assert _normalize_severity_filter([]) == []
        # 'unknown' is in the DB enum vocabulary even though we don't surface
        # it as an output bucket — the filter accepts it.
        assert _normalize_severity_filter(["critical", "BOGUS"]) == ["critical"]
        # All-bogus collapses to [] (the service treats this as "no rows").
        assert _normalize_severity_filter(["BOGUS", "junk"]) == []

    def test_license_filter_drops_invalid_values(self) -> None:
        from services.project_detail_service import _normalize_license_filter

        assert _normalize_license_filter(None) is None
        assert _normalize_license_filter(["forbidden", "GIBBERISH"]) == ["forbidden"]
        assert _normalize_license_filter(["GIBBERISH"]) == []
