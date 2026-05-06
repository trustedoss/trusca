"""
Pure unit tests for the project-detail risk-score formula and the
severity / license filter normalisation helpers.

These tests do NOT need a database — they exercise pure Python helpers in
``services/project_detail_service``. Keeping them in their own file (rather
than under the ``integration`` mark in ``test_project_detail_service.py``)
guarantees they run on every PR even when DATABASE_URL is unset.

Phase 3 PR #10 task 3.1 / 3.3.
"""

from __future__ import annotations


class TestRiskScore:
    """Pin the risk-score formula independent of any DB."""

    def test_zero_distribution_yields_zero(self) -> None:
        from services.project_detail_service import _compute_risk_score

        assert _compute_risk_score({}, {}) == 0.0

    def test_severity_only_weights_match_formula(self) -> None:
        # 1 critical (15) + 2 high (10) + 5 medium (5) + 100 low (0) = 30
        # info / none / unknown buckets carry zero weight by design.
        from services.project_detail_service import _compute_risk_score

        sev = {"critical": 1, "high": 2, "medium": 5, "low": 100, "info": 99, "none": 99}
        assert _compute_risk_score(sev, {}) == 30.0

    def test_license_only_weights_match_formula(self) -> None:
        # 2 forbidden (60) + 3 conditional (15) + 50 allowed (0) = 75
        from services.project_detail_service import _compute_risk_score

        lic = {"forbidden": 2, "conditional": 3, "allowed": 50, "unknown": 99}
        assert _compute_risk_score({}, lic) == 75.0

    def test_combined_score_clamped_to_100(self) -> None:
        # 10 critical (150) + 10 forbidden (300) → clamped to 100
        from services.project_detail_service import _compute_risk_score

        sev = {"critical": 10}
        lic = {"forbidden": 10}
        assert _compute_risk_score(sev, lic) == 100.0

    def test_score_returns_float_not_int(self) -> None:
        # Frontend gauge expects a float 0..100. Pin the type so a future
        # refactor doesn't quietly downgrade to int.
        from services.project_detail_service import _compute_risk_score

        result = _compute_risk_score({"critical": 1}, {})
        assert isinstance(result, float)


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
