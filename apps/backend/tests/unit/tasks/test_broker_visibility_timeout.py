"""
Unit tests for S1 (concurrency-scaling-plan-2026-08-22.md §3.2): the broker
visibility timeout must always sit above every task's own hard time limit.

Background: Celery's Redis transport redelivers an un-acked message once the
broker's visibility timeout elapses. ``task_acks_late=True`` (celery_app.py)
means the ack only happens after a task finishes, so if the visibility
timeout is shorter than a task's hard time limit, a task that runs past the
timeout gets redelivered to a second worker while the first worker is still
running it. Redis' own transport default (3600s) sits BELOW this
deployment's scan hard limit default (3900s, ``scan_hard_time_limit_seconds()``
in core/config.py), so the bug is live at the tuned default, not just at some
pathological override.

The fix derives the visibility timeout from the LARGER of
``scan_hard_time_limit_seconds()`` and ``backup_task_time_limit_seconds()``
plus a fixed margin (not a hardcoded number in celery_app.py), so it moves
automatically whenever an operator retunes either ceiling. #436's security
review is why the backup side exists at all: a duplicate redelivery of
``trustedoss.backup.restore`` is not merely a wasted worker slot the way a
duplicate scan is, but a second ``psql`` restore transaction running
concurrently against the same live database.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Config accessor: derives from scan_hard_time_limit_seconds(), not a literal
# ---------------------------------------------------------------------------


def test_visibility_timeout_default_exceeds_hard_limit_default() -> None:
    from core.config import (
        broker_visibility_timeout_seconds,
        scan_hard_time_limit_seconds,
    )

    assert broker_visibility_timeout_seconds() > scan_hard_time_limit_seconds()


@pytest.mark.parametrize(
    "hard_override",
    [
        "3900",  # deployment default
        "7200",  # operator doubled the hard limit
        "100",  # operator shrank it well below the default
        "36000",  # a very large hard limit (10h), margin must still move with it
    ],
)
def test_visibility_timeout_always_exceeds_hard_limit(
    monkeypatch: pytest.MonkeyPatch, hard_override: str
) -> None:
    """Regression contract (plan §4, S1 row): raising the hard limit never
    makes the visibility timeout smaller than it."""
    from core.config import (
        broker_visibility_timeout_seconds,
        scan_hard_time_limit_seconds,
    )

    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", hard_override)
    hard = scan_hard_time_limit_seconds()
    visibility = broker_visibility_timeout_seconds()
    assert visibility > hard


def test_visibility_timeout_moves_with_hard_limit_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No module-level caching (CLAUDE.md core rule #11); a second env
    mutation must be reflected on the next call."""
    from core.config import broker_visibility_timeout_seconds

    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "5000")
    first = broker_visibility_timeout_seconds()

    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "9000")
    second = broker_visibility_timeout_seconds()

    assert second > first


def test_visibility_timeout_margin_is_fixed_grace_above_the_larger_hard_limit() -> None:
    """Pins the exact derivation (max(scan, backup) + fixed margin), not just
    the inequality, so a future edit that changes the margin or drops one
    side of the max() is a deliberate, visible diff rather than a silent
    behavior change. At today's defaults the backup ceiling (8100s) exceeds
    the scan one (3900s), so this also pins which side is currently binding."""
    from core.config import (
        BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS,
        backup_task_time_limit_seconds,
        broker_visibility_timeout_seconds,
        scan_hard_time_limit_seconds,
    )

    assert backup_task_time_limit_seconds() > scan_hard_time_limit_seconds()
    assert (
        broker_visibility_timeout_seconds()
        == backup_task_time_limit_seconds() + BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS
    )


def test_visibility_timeout_also_exceeds_the_backup_task_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#436 security review: raising SCAN_HARD_TIME_LIMIT_SECONDS alone (the
    only knob the pre-#436 derivation considered) must not shrink the margin
    the backup ceiling gets, and raising BACKUP_SUBPROCESS_TIMEOUT (which
    moves backup_task_time_limit_seconds()) must widen the visibility timeout
    to match even though it never touches SCAN_HARD_TIME_LIMIT_SECONDS."""
    from core.config import backup_task_time_limit_seconds, broker_visibility_timeout_seconds

    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "100")  # well below backup's ceiling
    assert broker_visibility_timeout_seconds() > backup_task_time_limit_seconds()

    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "10000")  # operator sizing for a huge DB
    hard = backup_task_time_limit_seconds()
    assert broker_visibility_timeout_seconds() > hard


# ---------------------------------------------------------------------------
# Celery app wiring: broker_transport_options actually carries the value
# ---------------------------------------------------------------------------


def test_celery_app_sets_broker_transport_options_visibility_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app-level config (not just the accessor) must apply the derived
    value, and must reflect an env override present at app-construction time."""
    monkeypatch.setenv("SCAN_HARD_TIME_LIMIT_SECONDS", "10000")
    monkeypatch.setenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "9000")

    from core.config import broker_visibility_timeout_seconds
    from tasks.celery_app import create_celery_app

    expected = broker_visibility_timeout_seconds()
    app = create_celery_app()

    options = app.conf.broker_transport_options
    assert options is not None
    assert options["visibility_timeout"] == expected
    assert expected > 10000


def test_celery_app_task_time_limits_unaffected_by_visibility_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1 must not introduce a GLOBAL task_time_limit / task_soft_time_limit
    setting. Scan tasks stay per-dispatch (tasks.enqueue_scan); every other
    task's limit (#436, test_celery_app.py) is set per-task via
    task_annotations instead, precisely because this pair is a single value
    with no per-task variation - the same "one size fits every task" problem
    PR-A1 already rejected once for exactly this pair."""
    from tasks.celery_app import create_celery_app

    app = create_celery_app()
    assert app.conf.task_time_limit is None
    assert app.conf.task_soft_time_limit is None
