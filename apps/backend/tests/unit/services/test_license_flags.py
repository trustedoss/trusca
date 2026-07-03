"""Unit tests for services.license_flags — the AI review-flag classifier (Phase D1).

Pure logic, no DB. The named cases are the BomLens ``license-flags.jq`` oracle
set — every id/name the upstream tool flags must map to the same class here, and
every ordinary OSS license must stay unflagged so a normal software scan's NOTICE
is unchanged.
"""

from __future__ import annotations

import pytest

from services.license_flags import REVIEW_FLAG_VALUES, classify_review_flag

# ---------------------------------------------------------------------------
# Behavioral-use: RAIL family + AI community licenses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Llama-2-Community-License",
        "LLAMA-2",
        "llama3",
        "OpenRAIL-M",
        "openrail",
        "BigScience-OpenRAIL-M",
        "RAIL",
        "Responsible-AI-License",
        "Gemma",
        "Gemma-Terms-of-Use",
        "Falcon-LLM-License",
        "Some Community License",
    ],
)
def test_behavioral_use_matches_via_spdx_id(value: str) -> None:
    assert classify_review_flag(value, None) == "behavioral_use"


@pytest.mark.parametrize(
    "value",
    [
        "Llama 2 Community License",
        "Gemma Terms of Use",
        "Falcon LLM License",
        "OpenRAIL-M license",
    ],
)
def test_behavioral_use_matches_via_name(value: str) -> None:
    # id absent / non-matching, name carries the tell-tale token.
    assert classify_review_flag(None, value) == "behavioral_use"
    assert classify_review_flag("LicenseRef-custom", value) == "behavioral_use"


# ---------------------------------------------------------------------------
# Non-commercial: CC-BY-NC family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "CC-BY-NC-4.0",
        "CC BY-NC",
        "CC-BY-NC-SA-4.0",
        "Non-Commercial-License",
        "NonCommercial",
        "some noncommercial terms",
    ],
)
def test_non_commercial_matches(value: str) -> None:
    assert classify_review_flag(value, None) == "non_commercial"
    assert classify_review_flag(None, value) == "non_commercial"


# ---------------------------------------------------------------------------
# Out of scope: permissive + ordinary copyleft → None (BomLens parity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "MIT",
        "Apache-2.0",
        "GPL-3.0-only",
        "GPL-2.0-or-later",
        "LGPL-3.0-only",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",
        "The Apache Software License, Version 2.0",
        "GNU General Public License v3.0",
    ],
)
def test_ordinary_licenses_are_not_flagged(value: str) -> None:
    assert classify_review_flag(value, value) is None


# ---------------------------------------------------------------------------
# Precedence + defensive edges
# ---------------------------------------------------------------------------


def test_behavioral_use_wins_over_non_commercial() -> None:
    # A name tripping both patterns is reported as behavioral_use (jq if/elif order).
    assert classify_review_flag("Llama-NonCommercial", None) == "behavioral_use"


def test_either_field_matches() -> None:
    assert classify_review_flag("MIT", "Llama 2 Community License") == "behavioral_use"
    assert classify_review_flag("Llama-2", "MIT") == "behavioral_use"


@pytest.mark.parametrize("spdx_id,name", [(None, None), ("", ""), (None, ""), ("", None)])
def test_empty_and_none_are_safe(spdx_id, name) -> None:
    assert classify_review_flag(spdx_id, name) is None


def test_word_boundary_rail_does_not_match_substring() -> None:
    # \brail\b must not fire inside "guardrail" or "trailer".
    assert classify_review_flag("guardrails", None) is None
    assert classify_review_flag("trailer-license", None) is None


def test_word_boundary_gemma_does_not_match_substring() -> None:
    # \bgemma\b must not fire inside "gemmatology" (contrived) — whole word only.
    assert classify_review_flag("gemmatologyx", None) is None


def test_returned_values_are_in_the_single_source_of_truth() -> None:
    for value in ("Llama-2", "CC-BY-NC-4.0"):
        flag = classify_review_flag(value, None)
        assert flag in REVIEW_FLAG_VALUES
