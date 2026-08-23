"""
Doc oracle for the permission-cache decision criteria (A4).

concurrency-scaling-plan-2026-08-22.md §3.3/§4 (A4): the default stays 0
(off) and this unit documents WHEN an operator may turn it on, not code that
changes behaviour. Testing-standards hardening rule 4 (the guide is an
oracle) applies: the criteria and the measured savings ratio the docs state
have to agree with the code they describe, or the docs can drift the moment
somebody changes the query shape they are describing without noticing they
also need to edit prose in four other files.

This intentionally does NOT hardcode "5 statements" or "20%" as bare
integers in the assertions below. It reads the measured statement count off
``tests.integration.test_request_query_budget.AUTHENTICATED_READ_MEASURED_STATEMENTS``
(the same constant the query-budget test itself is pinned against) and
derives the percentage from it, so a future change to that measured count
(e.g. a new statement added to the authenticated-read path, or another one
removed the way A3 did) fails this test instead of leaving stale prose
behind.
"""

from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ENV_VAR_NAME = "PERMISSION_CACHE_TTL_SECONDS"

DOC_PATHS = {
    "env_example": REPO_ROOT / ".env.example",
    "config": REPO_ROOT / "apps/backend/core/config.py",
    "admin_guide_en": REPO_ROOT / "docs-site/docs/admin-guide/users-and-teams.md",
    "admin_guide_ko": (
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "admin-guide/users-and-teams.md"
    ),
}


def _measured_statement_count() -> int:
    from tests.integration.test_request_query_budget import (
        AUTHENTICATED_READ_MEASURED_STATEMENTS,
    )

    return AUTHENTICATED_READ_MEASURED_STATEMENTS


def test_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import permission_cache_ttl_seconds

    monkeypatch.delenv(ENV_VAR_NAME, raising=False)
    assert permission_cache_ttl_seconds() == 0, (
        "the permission cache must stay off by default (concurrency-scaling-plan "
        "§2 principle 5: new toggles start off); A4 documents when to turn it on, "
        "it does not flip the default"
    )


def test_savings_percentage_the_docs_state_matches_the_measured_query_count() -> None:
    """The "~20%" figure has to be recomputed from the measured count, not typed twice.

    A3 folded principal resolution from 2 statements into 1; that 1
    remaining statement is everything the permission cache can remove. The
    percentage every doc quotes is that one statement over the measured
    total, rounded to the nearest whole percent.
    """
    measured = _measured_statement_count()
    cache_removes = 1  # the one remaining principal-resolution statement (A3's joinedload)
    expected_percent = round(cache_removes / measured * 100)

    assert expected_percent == 20, (
        f"AUTHENTICATED_READ_MEASURED_STATEMENTS changed to {measured}; the derived "
        f"savings figure is now {expected_percent}%, not the 20% every doc below "
        "states. Update .env.example, core/config.py's permission_cache_ttl_seconds "
        "docstring, and both admin-guide mirrors together, then update this test's "
        "expectation."
    )

    needle = f"{expected_percent}%"
    for name, path in DOC_PATHS.items():
        assert path.is_file(), f"{path} is missing"
        body = path.read_text(encoding="utf-8")
        assert needle in body, (
            f"{name} ({path}) does not state the measured cache savings figure "
            f"({needle}) that core.config.permission_cache_ttl_seconds's docstring "
            "and the admin guide both promise"
        )


def test_admin_guide_states_the_three_conditions() -> None:
    """The three-condition gate (§3.3) has to be legible in both languages.

    A reader who sees only "off by default" without the criteria has no way
    to judge when turning it on is appropriate for their deployment.
    """
    en_body = DOC_PATHS["admin_guide_en"].read_text(encoding="utf-8")
    ko_body = DOC_PATHS["admin_guide_ko"].read_text(encoding="utf-8")

    # Condition 1: authenticated-read p95 over target.
    assert "p95" in en_body
    assert "p95" in ko_body

    # Condition 2: connection pool already tuned.
    assert "DB_POOL_SIZE" in en_body
    assert "DB_POOL_SIZE" in ko_body

    # Condition 3: the organisation has accepted the revocation-delay ceiling
    # as policy.
    assert "policy" in en_body
    assert "규정" in ko_body


def test_env_example_links_the_criteria_and_the_ratio() -> None:
    body = DOC_PATHS["env_example"].read_text(encoding="utf-8")
    assert ENV_VAR_NAME in body
    assert "p95" in body
    assert "DB_POOL_SIZE" in body
    assert "policy" in body


def test_config_docstring_states_the_three_conditions() -> None:
    from core.config import permission_cache_ttl_seconds

    doc = permission_cache_ttl_seconds.__doc__ or ""
    assert "p95" in doc
    assert "DB_POOL_SIZE" in doc
    assert "policy" in doc
