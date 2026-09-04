"""
Unit tests for the Celery application factory.

Phase 0: just enough coverage to assert the factory is wired correctly and
respects CLAUDE.md core rule #11 (environment variables read at runtime, not
import time).
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from celery import Celery

from tasks.celery_app import _TASK_INCLUDES, celery_app, create_celery_app

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _register_tasks_the_way_a_worker_would() -> None:
    """Import exactly the modules ``_TASK_INCLUDES`` names, and nothing else.

    A worker registers tasks by importing that list at start-up; importing
    ``tasks.celery_app`` alone registers NONE of them (measured: zero
    ``trustedoss.*`` tasks in a fresh process). So a test that reads
    ``celery_app.tasks`` without doing this is reading whatever other test
    modules happened to import, which is why these assertions used to pass
    with an entry deleted from the list and used to give different answers
    depending on which files pytest collected.
    """
    for module in _TASK_INCLUDES:
        importlib.import_module(module)


# The obvious alternative does not work, recorded so the next reader does not
# spend the afternoon finding out: building a fresh app with
# ``create_celery_app()`` and asking IT what is registered. All 29 task modules
# declare their tasks with ``@celery_app.task``, bound to the singleton at
# import time, so a second app receives nothing no matter what its ``include``
# says. Registration is a property of the singleton here, which is why these
# tests check the singleton and lean on ``__module__`` for the part that
# ambient imports could otherwise fake.


def _defining_module(task_name: str) -> str:
    """The module a registered task was defined in.

    Checked in addition to "the task exists", and that pairing is the whole
    point. Existence alone is satisfied by any import anywhere in the process,
    including one that has nothing to do with ``_TASK_INCLUDES``. The defining
    module being ON that list is what actually says a worker would find it.
    """
    # str(): Celery's registry is untyped, so mypy sees Any here.
    return str(celery_app.tasks[task_name].__module__)


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

    Regression guard: if a cleanup drops the ``_TASK_INCLUDES`` entry (a typo
    rename, say), the worker beat silently stops refreshing the DB and the
    feed goes stale.

    This claim used to be false. The test asserted only that the name was in
    ``celery_app.tasks``, which is a process-wide registry any import fills,
    so deleting the entry changed nothing as long as some other test imported
    the module. Now the test builds the registration path itself and checks
    the task is defined by a module the list actually names.
    """
    _register_tasks_the_way_a_worker_would()

    assert "trustedoss.trivy_db_refresh" in celery_app.tasks
    assert _defining_module("trustedoss.trivy_db_refresh") in _TASK_INCLUDES


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
    """W9: each retention task must be reachable via _TASK_INCLUDES.

    ``module in _TASK_INCLUDES`` alone would pass with the module renamed out
    from under the entry, and ``task_name in celery_app.tasks`` alone passes
    on any ambient import. Together, with the imports done here, they say the
    listed module is what provides this task.
    """
    _register_tasks_the_way_a_worker_would()

    assert module in _TASK_INCLUDES
    assert task_name in celery_app.tasks
    assert _defining_module(task_name) == module


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


# ---------------------------------------------------------------------------
# ER59: the invariant the per-task guards above are each one instance of.
# ---------------------------------------------------------------------------


def test_every_beat_entry_is_provided_by_an_included_module() -> None:
    """A schedule entry naming a task no include provides does nothing, quietly.

    This is the failure the individual guards describe and the one that scales:
    they name three tasks between them, while the schedule has 21 entries. A
    beat entry whose task is missing does not error at start-up. Celery logs
    the dispatch and no worker has that name registered, so the job simply
    never runs and the only symptom is data that stops being refreshed.

    Checked against the include list rather than the live registry, because
    the registry is process-wide and full of whatever the test session
    imported. That is what made the older assertions unable to fail.
    """
    _register_tasks_the_way_a_worker_would()

    unprovided: list[tuple[str, str]] = []
    for beat_key, entry in celery_app.conf.beat_schedule.items():
        task_name = entry["task"]
        if task_name not in celery_app.tasks:
            unprovided.append((beat_key, f"{task_name} is registered by nothing"))
            continue
        module = _defining_module(task_name)
        if module not in _TASK_INCLUDES:
            unprovided.append(
                (beat_key, f"{task_name} comes from {module}, not on _TASK_INCLUDES")
            )

    assert not unprovided, (
        "these beat entries dispatch a task a worker would not have: "
        f"{unprovided}"
    )


def test_the_schedule_is_not_empty() -> None:
    """Guards the guard above: it iterates the schedule, so an empty schedule
    would make it pass while asserting nothing."""
    assert len(celery_app.conf.beat_schedule) >= 20


def test_importing_celery_app_alone_registers_no_tasks() -> None:
    """Why the helper exists, stated as a fact rather than a comment.

    If this ever fails, importing the app has started registering tasks by
    itself, and the tests above would go back to passing for a reason that has
    nothing to do with ``_TASK_INCLUDES``. The check runs in a subprocess
    because this process has already imported the task modules.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.');"
            "from tasks.celery_app import celery_app;"
            "print(len([t for t in celery_app.tasks if t.startswith('trustedoss.')]))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=BACKEND_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0", (
        "importing tasks.celery_app now registers tasks on its own, so "
        "'the task is in celery_app.tasks' no longer says the include list "
        "put it there"
    )
