"""
Doc oracle for the API-key ``last_used_at`` update interval (A2).

concurrency-scaling-plan-2026-08-22.md §3.3/§4 (A2): coalescing the
``last_used_at`` write into an interval is a CONTRACT CHANGE. The column's
resolution moved from "the exact instant of the most recent use" to "used at
some point within this interval". Testing-standards hardening rule 4 (the
guide is an oracle) applies: the interval value has to agree everywhere it is
written down, or the docs can state a number the code no longer honours while
every code-side test still passes.

This intentionally does NOT hardcode "900" in the assertions below. It reads
the live default off ``core.config.api_key_last_used_at_update_interval_seconds``
and checks the docs against THAT, so a deliberate change to the default fails
here (forcing the docs to move with it) instead of the test silently agreeing
with whatever the new number happens to be.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ENV_VAR_NAME = "API_KEY_LAST_USED_AT_UPDATE_INTERVAL_SECONDS"


def test_env_example_default_matches_the_code_default() -> None:
    from core.config import api_key_last_used_at_update_interval_seconds

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(rf"^#?\s*{ENV_VAR_NAME}=(\d+)\s*$", text, re.M)
    assert match, f".env.example no longer declares {ENV_VAR_NAME}"
    assert int(match.group(1)) == api_key_last_used_at_update_interval_seconds(), (
        ".env.example's commented default has drifted from "
        "core.config.api_key_last_used_at_update_interval_seconds()"
    )


def test_admin_guide_names_the_interval_and_the_resolution_change() -> None:
    """Both the EN guide and its KO mirror must state the current interval.

    A reader who only sees a stale number believes the column is more
    precise (or less precise) than it actually is, and either way a wrong
    audit judgement about "is this key still in use" is one click away.
    """
    from core.config import api_key_last_used_at_update_interval_seconds

    interval_minutes = api_key_last_used_at_update_interval_seconds() // 60
    guides = (
        REPO_ROOT / "docs-site/docs/admin-guide/api-keys.md",
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "admin-guide/api-keys.md",
    )
    for guide in guides:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert ENV_VAR_NAME in body, (
            f"{guide.name} no longer names the env var that controls " "last_used_at resolution"
        )
        stated_en = f"{interval_minutes} minute" in body
        stated_ko = f"{interval_minutes}분" in body
        assert stated_en or stated_ko, (
            f"{guide.name} does not state the current interval "
            f"({interval_minutes} minutes) in either English or Korean form"
        )


def test_api_key_schema_field_description_matches_the_code_default() -> None:
    """The OpenAPI-facing field description is the third place this can drift."""
    from core.config import api_key_last_used_at_update_interval_seconds

    interval_minutes = api_key_last_used_at_update_interval_seconds() // 60
    text = (REPO_ROOT / "apps/backend/schemas/api_key.py").read_text(encoding="utf-8")
    assert f"{interval_minutes} minutes" in text, (
        "schemas.api_key.APIKeyListItem.last_used_at's description states a "
        f"different interval than the {interval_minutes}-minute code default"
    )
