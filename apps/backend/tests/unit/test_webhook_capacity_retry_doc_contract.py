# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the S7 webhook capacity retry
(concurrency-scaling-plan-2026-08-22.md §3.2/§4, testing-standards hardening
rule 4 - the guide is an oracle).

Same shape as ``test_queue_backlog_alert_doc_contract.py``: env var presence
in ``.env.example`` and the EN/KO reference page, default values read live off
``core.config`` rather than hardcoded here (so a deliberate default change
fails this test and forces the docs to move with it), and the webhooks guide
naming the outcome value and the toggle an operator would look for.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ENV_VAR_NAMES = (
    "SCAN_QUEUE_SLOT_COUNT",
    "SCAN_AVERAGE_DURATION_SECONDS",
    "WEBHOOK_CAPACITY_RETRY_ENABLED",
)


def test_env_example_documents_every_key() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ENV_VAR_NAMES:
        assert name in text, f".env.example does not mention {name}"


def test_env_example_int_defaults_match_the_code_defaults() -> None:
    from core.config import scan_average_duration_seconds, scan_queue_slot_count

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    expectations = {
        "SCAN_QUEUE_SLOT_COUNT": scan_queue_slot_count(),
        "SCAN_AVERAGE_DURATION_SECONDS": scan_average_duration_seconds(),
    }
    for name, expected in expectations.items():
        match = re.search(rf"^#?\s*{name}=(\d+)\s*$", text, re.M)
        assert match, f".env.example no longer declares a commented default for {name}"
        assert int(match.group(1)) == expected, (
            f".env.example's commented default for {name} has drifted from "
            f"core.config's live default ({expected})"
        )


def test_env_example_toggle_default_matches_the_code_default() -> None:
    """The one toggle in this plan that defaults ON - see
    ``webhook_capacity_retry_enabled()``'s own docstring for why."""
    from core.config import webhook_capacity_retry_enabled

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"^#?\s*WEBHOOK_CAPACITY_RETRY_ENABLED=(\w+)\s*$", text, re.M)
    assert match, (
        ".env.example no longer declares a commented default for "
        "WEBHOOK_CAPACITY_RETRY_ENABLED"
    )
    documented = match.group(1).strip().lower()
    actual = str(webhook_capacity_retry_enabled()).lower()
    assert documented == actual == "true", (
        ".env.example's commented default for WEBHOOK_CAPACITY_RETRY_ENABLED has "
        "drifted from core.config's live default"
    )


def test_env_variables_reference_documents_every_key() -> None:
    text = (REPO_ROOT / "docs-site/docs/reference/env-variables.md").read_text(encoding="utf-8")
    for name in ENV_VAR_NAMES:
        assert name in text, f"env-variables.md does not mention {name}"


def test_env_variables_reference_ko_mirror_documents_every_key() -> None:
    ko_path = (
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "reference/env-variables.md"
    )
    text = ko_path.read_text(encoding="utf-8")
    for name in ENV_VAR_NAMES:
        assert name in text, f"env-variables.md (KO) does not mention {name}"


def test_webhooks_guide_documents_the_retry_toggle_and_outcome() -> None:
    """Both EN and KO must name the toggle and the terminal outcome value an
    operator would grep the database for."""
    guides = (
        REPO_ROOT / "docs-site/docs/ci-integration/webhooks.md",
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "ci-integration/webhooks.md",
    )
    for guide in guides:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert "WEBHOOK_CAPACITY_RETRY_ENABLED" in body, f"{guide} does not name the toggle"
        assert "capacity_retry_exhausted" in body, f"{guide} does not name the terminal outcome"
        assert "enqueued" in body, f"{guide} does not describe the resolved-by-retry outcome"


def test_webhooks_guide_attempt_count_matches_the_code_constant() -> None:
    """The guide says 'up to six more attempts' - pin that literal against
    the module constant rather than letting the two drift apart silently."""
    from tasks.webhook_capacity_retry import _MAX_RETRY_ATTEMPTS

    assert _MAX_RETRY_ATTEMPTS == 6, (
        "tasks.webhook_capacity_retry._MAX_RETRY_ATTEMPTS changed - update the "
        "'up to six more attempts' / '최대 6회' wording in docs-site/docs/"
        "ci-integration/webhooks.md and its KO mirror to match"
    )
    en_text = (REPO_ROOT / "docs-site/docs/ci-integration/webhooks.md").read_text(
        encoding="utf-8"
    )
    assert "six more attempts" in en_text
    ko_text = (
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "ci-integration/webhooks.md"
    ).read_text(encoding="utf-8")
    assert "6회" in ko_text
