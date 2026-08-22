# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Pure-unit tests for the report column-selection helpers (N22).

These exercise ``api.v1.reports._validate_requested_columns`` and
``_resolve_report_columns`` directly (no DB, no HTTP): the request-time
validation and the request-vs-organization-default priority rule the plan
requires a dedicated test for. The DB-backed round trip (org template CRUD,
the PDF endpoint's 422 on an unknown column) lives in
``tests/integration/test_reports_api.py``.
"""

from __future__ import annotations

import pytest

from api.v1.reports import (
    _InvalidReportColumns,
    _resolve_report_columns,
    _validate_requested_columns,
)
from models import REPORT_COMPONENT_COLUMNS, REPORT_VULNERABILITY_COLUMNS

# ---------------------------------------------------------------------------
# _validate_requested_columns
# ---------------------------------------------------------------------------


def test_none_requested_passes_through_as_none() -> None:
    assert _validate_requested_columns(None, REPORT_VULNERABILITY_COLUMNS, "x") is None


def test_empty_list_requested_is_treated_as_none() -> None:
    assert _validate_requested_columns([], REPORT_VULNERABILITY_COLUMNS, "x") is None


def test_known_subset_is_returned_unchanged() -> None:
    requested = ["status", "cve"]
    assert _validate_requested_columns(requested, REPORT_VULNERABILITY_COLUMNS, "x") is requested


def test_unknown_column_raises() -> None:
    with pytest.raises(_InvalidReportColumns, match="typo"):
        _validate_requested_columns(["cve", "typo"], REPORT_VULNERABILITY_COLUMNS, "x")


def test_component_columns_validated_against_their_own_vocabulary() -> None:
    with pytest.raises(_InvalidReportColumns):
        _validate_requested_columns(["cve"], REPORT_COMPONENT_COLUMNS, "component_columns")


# ---------------------------------------------------------------------------
# _resolve_report_columns — priority: request-time > organization default > all
# ---------------------------------------------------------------------------


def test_request_time_selection_wins_over_the_organization_default() -> None:
    assert _resolve_report_columns(["cve"], ["cve", "cvss", "summary"]) == ["cve"]


def test_organization_default_applies_when_no_request_time_selection() -> None:
    assert _resolve_report_columns(None, ["cve", "status"]) == ["cve", "status"]


def test_neither_set_resolves_to_none_all_columns() -> None:
    assert _resolve_report_columns(None, None) is None


def test_priority_never_merges_the_two_selections() -> None:
    """A regression here would silently union both selections instead of the
    request-time one winning outright — the plan calls out this exact case."""
    merged_would_be = ["cve", "cvss", "summary", "status"]
    result = _resolve_report_columns(["cve"], ["cvss", "summary"])
    assert result == ["cve"]
    assert result != merged_would_be
