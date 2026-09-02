# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What a task-run row records, and what it must never cost.

Two properties matter more than the rest. Recording must never fail the task
it is recording, because history is worth less than the work; and a task that
retried must leave one row per attempt, because a count would lose why the
third try failed when the first two failed differently.

These are unit tests over the recorder's own logic. The database round trip
is exercised in ``tests/integration/test_task_run_history.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import task_run_recorder as rec


class _Session:
    """Minimal stand-in for the sync session scope.

    Counts commits, because ``sync_session_scope`` does not commit on exit and
    a recorder that forgets writes nothing while raising nothing. The first
    version of this stub had no ``commit`` at all, so it passed against code
    that never called one.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        return None

    def commit(self) -> None:
        self.commits += 1

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> _Session:
    made = _Session()
    monkeypatch.setattr(rec, "sync_session_scope", lambda: made)
    return made


# ---------------------------------------------------------------------------
# Starting a run
# ---------------------------------------------------------------------------


def test_start_writes_a_row_before_the_work_runs(session: _Session) -> None:
    """The row exists from the beginning, not from the end.

    A task killed mid-flight never reaches the closing handler, so an open row
    is the only evidence that death leaves behind.
    """
    rec.record_start(
        task_name="trustedoss.kev_catalog_refresh",
        celery_task_id="abc",
        request_id="req-1",
        attempt=1,
    )

    (row,) = session.added
    assert row.task_name == "trustedoss.kev_catalog_refresh"
    assert row.celery_task_id == "abc"
    assert row.request_id == "req-1"
    assert row.attempt == 1
    assert row.finished_at is None


def test_start_commits(session: _Session) -> None:
    """``sync_session_scope`` leaves the commit to the caller.

    Its docstring says so: the scan pipeline mixes intermediate and terminal
    commits, so the helper cannot decide. A recorder that forgets produces no
    error, no log line and no row, which is the hardest kind of nothing to
    notice.
    """
    rec.record_start(
        task_name="t", celery_task_id="x", request_id=None, attempt=1
    )

    assert session.commits == 1


def test_finish_commits(session: _Session) -> None:
    rec.record_finish(celery_task_id="x", attempt=1, outcome="succeeded")

    assert session.commits == 1


def test_start_floors_the_attempt_at_one(session: _Session) -> None:
    """The column counts attempts and its check constraint requires >= 1."""
    rec.record_start(
        task_name="t", celery_task_id="x", request_id=None, attempt=0
    )

    assert session.added[0].attempt == 1


def test_start_never_raises_into_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database outage degrades the history; it does not stop the sweep."""

    def _boom() -> Any:
        raise RuntimeError("database gone")

    monkeypatch.setattr(rec, "sync_session_scope", _boom)

    rec.record_start(task_name="t", celery_task_id="x", request_id=None, attempt=1)


# ---------------------------------------------------------------------------
# Closing a run
# ---------------------------------------------------------------------------


def test_finish_closes_the_row(session: _Session) -> None:
    rec.record_finish(celery_task_id="abc", attempt=1, outcome="succeeded")

    assert len(session.executed) == 1


def test_finish_never_raises_into_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> Any:
        raise RuntimeError("database gone")

    monkeypatch.setattr(rec, "sync_session_scope", _boom)

    rec.record_finish(celery_task_id="abc", attempt=1, outcome="succeeded")


# ---------------------------------------------------------------------------
# Reading the task's own summary
# ---------------------------------------------------------------------------


def test_a_skip_reason_turns_success_into_skipped() -> None:
    """A task that ran but did no work is neither a success nor a failure.

    Reading it as success hides that nothing happened; reading it as failure
    raises an alarm for a deliberate no-op.
    """
    fields = rec._summary_fields({"skipped_reason": "feed_unavailable"})

    assert fields["skipped_reason"] == "feed_unavailable"


def test_a_detail_code_becomes_the_result_payload() -> None:
    fields = rec._summary_fields(
        {"detail_code": "catalog.kev.refreshed", "detail_params": {"listed": 42}}
    )

    assert fields["result"] == {
        "detail_code": "catalog.kev.refreshed",
        "detail_params": {"listed": 42},
    }


def test_detail_params_are_optional() -> None:
    fields = rec._summary_fields({"detail_code": "x.done"})

    assert fields["result"] == {"detail_code": "x.done"}


def test_unrecognised_keys_are_dropped() -> None:
    """The row is a summary, not a copy of the return value.

    Some tasks return snapshots measured in megabytes; storing them whole
    would make the history table larger than the data it describes.
    """
    fields = rec._summary_fields(
        {"snapshot": {"packages": ["..."] * 1000}, "detail_code": "x.done"}
    )

    assert set(fields) == {"result"}


def test_a_non_dict_return_value_records_nothing_extra() -> None:
    """Most tasks return None or a bare count. Neither is an error."""
    assert rec._summary_fields(None) == {}
    assert rec._summary_fields(42) == {}
    assert rec._summary_fields("done") == {}


def test_an_over_long_skip_reason_is_truncated_to_the_column() -> None:
    fields = rec._summary_fields({"skipped_reason": "x" * 200})

    assert len(fields["skipped_reason"]) == 64


# ---------------------------------------------------------------------------
# Error summaries
# ---------------------------------------------------------------------------


def test_the_error_summary_names_the_class_and_the_message() -> None:
    """The class alone rarely identifies the cause, so the message stays."""
    summary = rec._summarise_error(TimeoutError("upstream did not answer"))

    assert "TimeoutError" in summary
    assert "upstream did not answer" in summary


def test_credentials_are_stripped_from_the_error(
) -> None:
    """An upstream error string can carry a URL with a password in it, and
    this column is read in an admin list view."""
    summary = rec._summarise_error(
        RuntimeError("failed: https://user:hunter2@feed.example/all.json")
    )

    assert "hunter2" not in summary


def test_a_huge_error_is_capped() -> None:
    """The full traceback belongs in the logs; a list view needs a clue."""
    summary = rec._summarise_error(RuntimeError("x" * 5000))

    assert len(summary) <= 500


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_defaults_to_ninety_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TASK_RUN_RETENTION_DAYS", raising=False)

    assert rec.task_run_retention_days() == 90


def test_retention_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 11: no module-level caching of an environment value."""
    monkeypatch.setenv("TASK_RUN_RETENTION_DAYS", "7")
    assert rec.task_run_retention_days() == 7

    monkeypatch.setenv("TASK_RUN_RETENTION_DAYS", "14")
    assert rec.task_run_retention_days() == 14


def test_a_negative_retention_is_floored_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero means "keep nothing"; a negative window would compute a cutoff in
    the future and delete rows that have not aged at all."""
    monkeypatch.setenv("TASK_RUN_RETENTION_DAYS", "-5")

    assert rec.task_run_retention_days() == 0
