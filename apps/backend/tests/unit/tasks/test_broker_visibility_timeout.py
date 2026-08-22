"""
Unit tests for S1 (concurrency-scaling-plan-2026-08-22.md §3.2): the broker
visibility timeout must always sit above the scan hard time limit.

Background: Celery's Redis transport redelivers an un-acked message once the
broker's visibility timeout elapses. ``task_acks_late=True`` (celery_app.py)
means the ack only happens after a scan task finishes, so if the visibility
timeout is shorter than the scan hard time limit, a scan that runs past the
timeout gets redelivered to a second worker while the first worker is still
running it, and the same scan occupies two slots at once. Redis' own
transport default (3600s) sits BELOW this deployment's scan hard limit
default (3900s, ``scan_hard_time_limit_seconds()`` in core/config.py), so the
bug is live at the tuned default, not just at some pathological override.

The fix derives the visibility timeout from ``scan_hard_time_limit_seconds()``
plus a fixed margin (not a hardcoded number in celery_app.py) so it moves
automatically whenever an operator retunes the hard limit via
``SCAN_HARD_TIME_LIMIT_SECONDS``.
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


def test_visibility_timeout_margin_is_fixed_grace_above_hard_limit() -> None:
    """Pins the exact derivation (hard + fixed margin), not just the
    inequality, so a future edit that changes the margin is a deliberate,
    visible diff rather than a silent behavior change."""
    from core.config import (
        BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS,
        broker_visibility_timeout_seconds,
        scan_hard_time_limit_seconds,
    )

    assert (
        broker_visibility_timeout_seconds()
        == scan_hard_time_limit_seconds() + BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS
    )


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
    """S1 must not introduce a conflicting global task_time_limit /
    task_soft_time_limit; those stay per-dispatch (tasks.enqueue_scan), as
    documented in celery_app.py, so non-scan tasks (notifications, backups)
    are never capped by the scan timeout."""
    from tasks.celery_app import create_celery_app

    app = create_celery_app()
    assert app.conf.task_time_limit is None
    assert app.conf.task_soft_time_limit is None
