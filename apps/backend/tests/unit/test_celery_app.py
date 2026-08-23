"""
Unit tests for the Celery application factory.

Phase 0: just enough coverage to assert the factory is wired correctly and
respects CLAUDE.md core rule #11 (environment variables read at runtime, not
import time).
"""

from __future__ import annotations

import os

import pytest
from celery import Celery

from tasks.celery_app import celery_app, create_celery_app


def test_celery_app_singleton_is_configured() -> None:
    assert isinstance(celery_app, Celery)
    assert celery_app.main == "trustedoss"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_default_queue == "trustedoss.default"
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True


def test_create_celery_app_reads_redis_url_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md core rule #11: env vars are read at runtime, not module load.

    Flipping REDIS_URL between two factory calls must produce two apps with
    different broker URLs; if anything had cached at import time the second
    call would still see the first value.
    """
    monkeypatch.setenv("REDIS_URL", "redis://example-a:6379/0")
    app_a = create_celery_app()
    assert app_a.conf.broker_url == "redis://example-a:6379/0"
    assert app_a.conf.result_backend == "redis://example-a:6379/0"

    monkeypatch.setenv("REDIS_URL", "redis://example-b:6379/1")
    app_b = create_celery_app()
    assert app_b.conf.broker_url == "redis://example-b:6379/1"
    assert app_b.conf.result_backend == "redis://example-b:6379/1"

    assert app_a is not app_b


def test_create_celery_app_uses_redis_url_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6390/3")
    app = create_celery_app()
    expected = os.environ["REDIS_URL"]
    assert app.conf.broker_url == expected


def test_w6_44_trivy_db_refresh_task_registered() -> None:
    """W6-#44 — the weekly Trivy DB refresh task must be reachable.

    Regression guard: the task is registered via the ``_TASK_INCLUDES`` list
    in ``tasks.celery_app``. If a future cleanup drops the entry (e.g. a typo
    rename), the worker beat would silently stop refreshing the DB and the
    feed would go stale; this assertion fires immediately at unit-test time.
    """
    assert "trustedoss.trivy_db_refresh" in celery_app.tasks


def test_w6_44_trivy_db_refresh_beat_schedule_is_weekly() -> None:
    """W6-#44 — beat schedule MUST be a Sunday 03:00 UTC crontab entry."""
    schedule = celery_app.conf.beat_schedule
    assert "trivy-db-refresh-weekly" in schedule
    entry = schedule["trivy-db-refresh-weekly"]
    assert entry["task"] == "trustedoss.trivy_db_refresh"
    # Cadence assertion is by attribute on the crontab, not equality, so a
    # future operator-knob swap to interval-based scheduling fails this test
    # explicitly rather than silently.
    cron = entry["schedule"]
    assert getattr(cron, "minute", None) == {0}
    assert getattr(cron, "hour", None) == {3}
    # day_of_week=sun → {0} in Celery's cron normalisation.
    assert getattr(cron, "day_of_week", None) == {0}


def test_w6_44_trivy_db_bootstrap_module_imported() -> None:
    """W6-#44 — the bootstrap signal-handler module must be on _TASK_INCLUDES.

    The module isn't a Celery task, but it must be IMPORTED by the worker
    process so its ``worker_ready`` signal handler registers. Listing it
    in ``_TASK_INCLUDES`` triggers that import via Celery autodiscovery.
    """
    from tasks.celery_app import _TASK_INCLUDES

    assert "tasks.trivy_db_bootstrap" in _TASK_INCLUDES


# ---------------------------------------------------------------------------
# W9 (concurrency-scaling-plan-2026-08-22.md §3.5): the three new retention
# beats must be registered and on the expected daily cadence.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "task_name"),
    [
        ("tasks.auth_token_retention", "trustedoss.auth_token_retention"),
        ("tasks.operational_retention", "trustedoss.operational_retention"),
        ("tasks.audit_log_retention", "trustedoss.audit_log_retention_report"),
    ],
)
def test_w9_retention_tasks_registered(module: str, task_name: str) -> None:
    """W9: each retention task must be reachable via _TASK_INCLUDES."""
    from tasks.celery_app import _TASK_INCLUDES

    assert module in _TASK_INCLUDES
    assert task_name in celery_app.tasks


@pytest.mark.parametrize(
    ("beat_key", "task_name", "hour", "minute"),
    [
        ("auth-token-retention-daily", "trustedoss.auth_token_retention", 3, 15),
        ("operational-retention-daily", "trustedoss.operational_retention", 3, 30),
        (
            "audit-log-retention-report-daily",
            "trustedoss.audit_log_retention_report",
            3,
            45,
        ),
    ],
)
def test_w9_retention_beat_schedule_is_daily(
    beat_key: str, task_name: str, hour: int, minute: int
) -> None:
    """W9: each retention beat fires once a day at its assigned minute lane.

    The three entries share the same UTC hour but different minutes so they
    do not collide on the same tick, matching the convention every other
    multi-beat hour in this schedule already follows (see the KEV / SLA /
    EOL / malicious entries' minute-lane comments in _build_beat_schedule).
    """
    schedule = celery_app.conf.beat_schedule
    assert beat_key in schedule
    entry = schedule[beat_key]
    assert entry["task"] == task_name
    cron = entry["schedule"]
    assert getattr(cron, "hour", None) == {hour}
    assert getattr(cron, "minute", None) == {minute}


def test_w9_retention_beat_minutes_do_not_collide() -> None:
    """W9: the three new beats must not share a (hour, minute) with each
    other or with the pre-existing weekly Trivy refresh at hour=3 minute=0."""
    schedule = celery_app.conf.beat_schedule
    keys = [
        "auth-token-retention-daily",
        "operational-retention-daily",
        "audit-log-retention-report-daily",
        "trivy-db-refresh-weekly",
    ]
    seen: set[tuple[frozenset[int], frozenset[int]]] = set()
    for key in keys:
        cron = schedule[key]["schedule"]
        slot = (frozenset(cron.hour), frozenset(cron.minute))
        assert slot not in seen, f"{key} collides with an earlier beat's (hour, minute)"
        seen.add(slot)
