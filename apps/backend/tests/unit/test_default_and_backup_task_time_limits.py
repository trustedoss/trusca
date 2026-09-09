# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
core.config's #436 accessors: the fallback default-queue time limit pair,
and the backup/restore task pair derived from BACKUP_SUBPROCESS_TIMEOUT.

Mirrors the pattern tests/unit/tasks/test_broker_visibility_timeout.py
already established for the scan pair: pin the exact derivation (not just an
inequality), and prove the clamp actually clamps rather than merely reading
back whatever default happened to already satisfy it.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# default_task_soft_time_limit_seconds / default_task_time_limit_seconds
# ---------------------------------------------------------------------------


def test_default_pair_uses_documented_defaults() -> None:
    from core.config import (
        default_task_soft_time_limit_seconds,
        default_task_time_limit_seconds,
    )

    assert default_task_soft_time_limit_seconds() == 2400
    assert default_task_time_limit_seconds() == 2700


def test_default_hard_limit_exceeds_soft_limit() -> None:
    from core.config import (
        default_task_soft_time_limit_seconds,
        default_task_time_limit_seconds,
    )

    assert default_task_time_limit_seconds() > default_task_soft_time_limit_seconds()


def test_default_pair_reads_env_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import (
        default_task_soft_time_limit_seconds,
        default_task_time_limit_seconds,
    )

    monkeypatch.setenv("DEFAULT_TASK_SOFT_TIME_LIMIT_SECONDS", "600")
    monkeypatch.setenv("DEFAULT_TASK_TIME_LIMIT_SECONDS", "900")
    assert default_task_soft_time_limit_seconds() == 600
    assert default_task_time_limit_seconds() == 900


def test_default_hard_limit_clamps_when_misconfigured_at_or_below_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator setting the hard limit <= the soft limit must not SIGKILL
    a task before its soft-limit handler (if any) gets a chance to run."""
    from core.config import (
        DEFAULT_TASK_TIMEOUT_MIN_GRACE_SECONDS,
        default_task_soft_time_limit_seconds,
        default_task_time_limit_seconds,
    )

    monkeypatch.setenv("DEFAULT_TASK_SOFT_TIME_LIMIT_SECONDS", "1000")
    monkeypatch.setenv("DEFAULT_TASK_TIME_LIMIT_SECONDS", "500")  # below soft

    soft = default_task_soft_time_limit_seconds()
    hard = default_task_time_limit_seconds()
    assert hard == soft + DEFAULT_TASK_TIMEOUT_MIN_GRACE_SECONDS
    assert hard > soft


# ---------------------------------------------------------------------------
# backup_subprocess_timeout_seconds
# ---------------------------------------------------------------------------


def test_backup_subprocess_timeout_default() -> None:
    from core.config import backup_subprocess_timeout_seconds

    assert backup_subprocess_timeout_seconds() == 3600


def test_backup_subprocess_timeout_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import backup_subprocess_timeout_seconds

    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "1800")
    assert backup_subprocess_timeout_seconds() == 1800

    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "9000")
    assert backup_subprocess_timeout_seconds() == 9000


# ---------------------------------------------------------------------------
# backup_task_soft_time_limit_seconds / backup_task_time_limit_seconds
# ---------------------------------------------------------------------------


def test_backup_task_pair_covers_two_sequential_subprocess_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backup/restore runs up to two sequential
    backup_subprocess_timeout_seconds()-bounded steps (dump/restore, then the
    workspace tar) - the soft limit must cover both, not one."""
    from core.config import (
        BACKUP_TASK_TIME_LIMIT_MARGIN_SECONDS,
        backup_subprocess_timeout_seconds,
        backup_task_soft_time_limit_seconds,
    )

    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "1000")
    subprocess_timeout = backup_subprocess_timeout_seconds()
    soft = backup_task_soft_time_limit_seconds()

    assert soft == 2 * subprocess_timeout + BACKUP_TASK_TIME_LIMIT_MARGIN_SECONDS
    assert soft > 2 * subprocess_timeout


def test_backup_task_pair_moves_with_backup_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising BACKUP_SUBPROCESS_TIMEOUT (the operator's documented knob for
    a bigger database) must move both derived limits with it automatically,
    with no second env var to remember."""
    from core.config import (
        backup_task_soft_time_limit_seconds,
        backup_task_time_limit_seconds,
    )

    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "1000")
    soft_small = backup_task_soft_time_limit_seconds()
    hard_small = backup_task_time_limit_seconds()

    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "5000")
    soft_large = backup_task_soft_time_limit_seconds()
    hard_large = backup_task_time_limit_seconds()

    assert soft_large > soft_small
    assert hard_large > hard_small


def test_backup_task_hard_limit_exceeds_soft_limit_by_fixed_grace() -> None:
    from core.config import (
        BACKUP_TASK_TIMEOUT_MIN_GRACE_SECONDS,
        backup_task_soft_time_limit_seconds,
        backup_task_time_limit_seconds,
    )

    assert (
        backup_task_time_limit_seconds()
        == backup_task_soft_time_limit_seconds() + BACKUP_TASK_TIMEOUT_MIN_GRACE_SECONDS
    )


def test_backup_task_pair_exceeds_the_default_fallback_pair() -> None:
    """The whole reason backup gets its own accessor rather than
    default_task_*_time_limit_seconds(): a large database dump legitimately
    needs more than a webhook delivery or a catalog refresh does."""
    from core.config import (
        backup_task_soft_time_limit_seconds,
        backup_task_time_limit_seconds,
        default_task_soft_time_limit_seconds,
        default_task_time_limit_seconds,
    )

    assert backup_task_soft_time_limit_seconds() > default_task_soft_time_limit_seconds()
    assert backup_task_time_limit_seconds() > default_task_time_limit_seconds()
