# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the search-results count cap (concurrency-scaling plan Q3).

Testing-standards hardening rule 4 (the guide is an oracle): the search page
promises a specific cap number ("1,000") and a specific display form
("1000+"). Neither is enforced by the API contract itself (a client cannot
tell "server capped at 1,000" from "server capped at some other number"
except by reading the number the guide states), so nothing catches the guide
drifting from ``services.search_results_service.RESULT_COUNT_CAP`` except a
test that reads both and compares them.

Reads the live cap off the module rather than hardcoding 1000 here, so a
deliberate change to the cap fails this test (forcing the docs to move with
it) instead of the test silently agreeing with whatever the new number
happens to be.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

_GUIDES = (
    REPO_ROOT / "docs-site/docs/user-guide/search.md",
    REPO_ROOT / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/user-guide/search.md",
)


def test_search_guide_states_the_current_cap_in_both_languages() -> None:
    from services.search_results_service import RESULT_COUNT_CAP

    comma_formatted = f"{RESULT_COUNT_CAP:,}"  # e.g. "1,000"
    plus_formatted = f"{RESULT_COUNT_CAP}+"  # e.g. "1000+", the wire/UI form

    for guide in _GUIDES:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert comma_formatted in body, (
            f"{guide.name} does not state the current count cap "
            f"({comma_formatted}), services.search_results_service."
            "RESULT_COUNT_CAP has drifted from what the guide promises"
        )
        assert plus_formatted in body, (
            f"{guide.name} does not show the capped-count display form " f"({plus_formatted})"
        )


def test_frontend_capped_display_form_matches_the_cap() -> None:
    """The literal "N+" example the guide shows must be a real value the
    frontend can actually render, i.e. built from ``{{total}}`` /
    ``{{count}}`` interpolation, not a hardcoded string, so raising the cap
    later does not silently orphan a stale example in the guide alone.
    """
    import json

    from services.search_results_service import RESULT_COUNT_CAP

    locale_dir = REPO_ROOT / "apps/frontend/src/locales"
    for lang in ("en", "ko"):
        catalog = json.loads((locale_dir / lang / "search.json").read_text(encoding="utf-8"))
        assert catalog["summary"]["count_at_least"].count("{{total}}") >= 1, (
            f"{lang}/search.json summary.count_at_least must interpolate "
            "{{total}} rather than hardcoding a number"
        )
        assert catalog["capped"]["count"].count("{{count}}") >= 1, (
            f"{lang}/search.json capped.count must interpolate {{count}} "
            "rather than hardcoding a number"
        )
        rendered = catalog["capped"]["count"].replace("{{count}}", str(RESULT_COUNT_CAP))
        assert rendered == f"{RESULT_COUNT_CAP}+", (
            f"{lang}/search.json capped.count renders as {rendered!r}, "
            f"not the {RESULT_COUNT_CAP}+ form the guide documents"
        )
