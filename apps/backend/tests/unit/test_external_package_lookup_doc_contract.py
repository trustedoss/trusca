# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the package-lookup guide (C8).

Testing-standards hardening rule 4 (the guide is an oracle): the guide
promises specific rate limits, a specific ecosystem count, and a specific
not-found behaviour. None of those are enforced by the API contract itself
in a way a reader could check without a test reading both sides, so this
pins the guide against the values ``core.config`` and the shared ecosystems
fixture actually carry.

Reads the live values off the module/fixture rather than hardcoding them
here, so a deliberate change (e.g. raising a rate limit) fails this test
until the guide is updated to match, instead of the test silently agreeing
with whatever the new value happens to be.
"""

from __future__ import annotations

import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

_KO_GUIDE_DIR = REPO_ROOT / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
_GUIDES = (
    REPO_ROOT / "docs-site/docs/user-guide/package-lookup.md",
    _KO_GUIDE_DIR / "user-guide/package-lookup.md",
)


def _rate_per_minute(limit_string: str) -> str:
    count, _, period = limit_string.partition("/")
    assert period == "minute", f"unexpected rate limit shape: {limit_string!r}"
    return count


def test_guide_states_the_current_rate_limits_in_both_languages() -> None:
    from core.config import (
        external_advisory_lookup_rate_limit,
        external_package_lookup_rate_limit,
    )

    package_rate = _rate_per_minute(external_package_lookup_rate_limit())
    advisory_rate = _rate_per_minute(external_advisory_lookup_rate_limit())

    for guide in _GUIDES:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert package_rate in body, (
            f"{guide.name} does not state the current package lookup rate "
            f"limit ({package_rate}/minute); core.config."
            "external_package_lookup_rate_limit has drifted from what the "
            "guide promises"
        )
        assert advisory_rate in body, (
            f"{guide.name} does not state the current advisory lookup rate "
            f"limit ({advisory_rate}/minute); core.config."
            "external_advisory_lookup_rate_limit has drifted from what the "
            "guide promises"
        )


def test_guide_states_the_current_ecosystem_count_in_both_languages() -> None:
    fixture = json.loads(
        (REPO_ROOT / "tests/contracts/external-package-ecosystems.json").read_text(
            encoding="utf-8"
        )
    )
    count = len(fixture["ecosystems"])

    for guide in _GUIDES:
        body = guide.read_text(encoding="utf-8")
        assert str(count) in body, (
            f"{guide.name} does not state the current ecosystem count "
            f"({count}); tests/contracts/external-package-ecosystems.json "
            "has drifted from what the guide promises"
        )


def test_guide_promises_not_found_is_a_result_not_an_error() -> None:
    # This is the behaviour a caller relies on to distinguish "the catalog
    # has no record of this" from "the request failed" -- both are 200s, one
    # with found: false. If the guide stopped saying so, a reader would have
    # no way to tell the two apart without reading the endpoint's source.
    for guide in _GUIDES:
        body = guide.read_text(encoding="utf-8")
        assert "found: false" in body, (
            f"{guide.name} does not document that an unknown package is a "
            "200 with found: false, not an error"
        )
