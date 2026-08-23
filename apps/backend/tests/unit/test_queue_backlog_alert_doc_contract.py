# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the S6 queue backlog alert
(concurrency-scaling-plan-2026-08-22.md §3.2/§4, testing-standards hardening
rule 4 - the guide is an oracle).

This does NOT hardcode the threshold/sustain/cooldown defaults. It reads them
live off the ``core.config`` accessors and checks the docs against THOSE
values, so a deliberate default change fails here (forcing the docs to move
with it) instead of the docs silently agreeing with whatever the new default
happens to be - the same pattern
``test_api_key_last_used_at_contract.py`` uses for A2.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ENV_VAR_NAMES = (
    "QUEUE_BACKLOG_ALERT_ENABLED",
    "QUEUE_BACKLOG_ALERT_SCAN_QUEUE_THRESHOLD",
    "QUEUE_BACKLOG_ALERT_DEFAULT_QUEUE_THRESHOLD",
    "QUEUE_BACKLOG_ALERT_SUSTAIN_SECONDS",
    "QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS",
)


def test_env_example_documents_every_key() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ENV_VAR_NAMES:
        assert name in text, f".env.example does not mention {name}"


def test_env_example_defaults_match_the_code_defaults() -> None:
    from core.config import (
        queue_backlog_alert_cooldown_seconds,
        queue_backlog_alert_default_queue_threshold,
        queue_backlog_alert_scan_queue_threshold,
        queue_backlog_alert_sustain_seconds,
    )

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    expectations = {
        "QUEUE_BACKLOG_ALERT_SCAN_QUEUE_THRESHOLD": queue_backlog_alert_scan_queue_threshold(),
        "QUEUE_BACKLOG_ALERT_DEFAULT_QUEUE_THRESHOLD": (
            queue_backlog_alert_default_queue_threshold()
        ),
        "QUEUE_BACKLOG_ALERT_SUSTAIN_SECONDS": queue_backlog_alert_sustain_seconds(),
        "QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS": queue_backlog_alert_cooldown_seconds(),
    }
    for name, expected in expectations.items():
        match = re.search(rf"^#?\s*{name}=(\d+)\s*$", text, re.M)
        assert match, f".env.example no longer declares a commented default for {name}"
        assert int(match.group(1)) == expected, (
            f".env.example's commented default for {name} has drifted from "
            f"core.config's live default ({expected})"
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


def test_docker_compose_guide_documents_the_slot_formula_and_the_scale_commands() -> None:
    """Both EN and KO must name the two worker services and the formula's
    inputs - a reader who only sees ``worker`` (the pre-S3 name) would scale
    the wrong service and see no change in scan throughput."""
    guides = (
        REPO_ROOT / "docs-site/docs/installation/docker-compose.md",
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "installation/docker-compose.md",
    )
    for guide in guides:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert "worker-scan" in body, f"{guide} does not name worker-scan"
        assert "worker-default" in body, f"{guide} does not name worker-default"
        assert "WORKER_REPLICAS" in body, f"{guide} does not name WORKER_REPLICAS"
        assert "CELERY_CONCURRENCY" in body, f"{guide} does not name CELERY_CONCURRENCY"
        assert "--scale worker-scan=" in body, f"{guide} does not show the scale command"
        assert "QUEUE_BACKLOG_ALERT_ENABLED" in body, (
            f"{guide} does not point at the alert toggle"
        )


def test_oncall_runbook_documents_the_queue_backlog_scenario() -> None:
    """Both EN and KO must name the config keys an operator needs to make
    sense of the alert (thresholds, cooldown) and the two worker services
    (so they scale the one that actually helps)."""
    guides = (
        REPO_ROOT / "docs-site/docs/admin-guide/oncall-runbook.md",
        REPO_ROOT
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "admin-guide/oncall-runbook.md",
    )
    for guide in guides:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert "Queue backlog alert" in body, f"{guide} does not name the alert"
        assert "worker-scan" in body, f"{guide} does not name worker-scan"
        assert "worker-default" in body, f"{guide} does not name worker-default"
        assert "QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS" in body, (
            f"{guide} does not name the cooldown knob"
        )
