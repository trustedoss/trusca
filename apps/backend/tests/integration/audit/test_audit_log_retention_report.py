"""
Integration tests for W9 (concurrency-scaling-plan-2026-08-22.md §3.5, §4)
audit-log retention readiness: ``tasks.audit_log_retention`` never deletes
(``audit_logs`` stays append-only at the database layer, migration 0012);
it only counts rows that are BOTH already exported (at or before the
``tasks.audit_export`` cursor) AND older than the retention window.

Regression contract (plan §4, W9 row), reinterpreted for a read-only report
in place of an automated delete:

  - a row strictly AFTER the export cursor is never counted, no matter its
    age (the "do not touch unexported compliance rows" guarantee);
  - a row at or before the cursor is counted only once it is also past the
    retention window;
  - running the report repeatedly never changes ``audit_logs`` row count
    (there is no delete path for the "beat writes its own audit row, which
    then feeds back into what it reclaims" loop to run through).

Isolation note: ``audit_logs`` is shared across the whole test suite and is
never deleted (by this task or by design). Counting-based assertions below
measure a BEFORE/AFTER delta around each test's own insert rather than an
absolute count, so pre-existing rows from earlier runs of this file or of
``test_audit_export.py`` (which anchors its own fixture rows the same "far
in the past" way) cannot make an assertion flaky.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping audit log retention report tests")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(_sync_url(), future=True)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture
def destination() -> str:
    """A unique destination per test, so cursors never collide."""
    return f"https://collector.example/{uuid.uuid4().hex[:12]}"


def _own_window() -> datetime:
    """A slice of the past comfortably clear of realistic production data."""
    return datetime.now(tz=UTC) - timedelta(days=36_500)


def _insert_audit_row(session: Session, *, at: datetime) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO audit_logs (id, created_at, action, target_table) "
            "VALUES (:id, :at, 'create', 'w9_retention_report_targets')"
        ),
        {"id": str(row_id), "at": at},
    )
    session.commit()
    return row_id


def _set_cursor(session: Session, *, destination: str, position: datetime) -> None:
    """Position the export cursor at exactly *position*, creating it if absent."""
    from services.audit_export_service import get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)
    cursor.last_created_at = position
    cursor.last_id = uuid.UUID(int=(2**128 - 1))  # max UUID: no row at this instant loses the tie
    session.commit()


def _ready_count(session: Session, *, destination: str, retention_days: int) -> int:
    from services.audit_export_service import purge_ready_count

    return purge_ready_count(session, destination=destination, retention_days=retention_days)


def _audit_logs_row_count(session: Session) -> int:
    return int(session.execute(text("SELECT count(*) FROM audit_logs")).scalar_one())


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_report_returns_zero_when_no_destination_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks.audit_log_retention import audit_log_retention_report_task

    monkeypatch.delenv("AUDIT_EXPORT_URL", raising=False)

    result = audit_log_retention_report_task()

    assert result["status"] == "skipped"
    assert result["ready_to_purge"] == 0


def test_report_returns_zero_when_destination_configured_but_never_run(
    session: Session, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    """A destination configured but with no cursor row yet (export beat has
    not ticked) means nothing is known to be exported, so nothing counts,
    regardless of how much (or how little) history the table holds."""
    from tasks.audit_log_retention import audit_log_retention_report_task

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "1")

    _insert_audit_row(session, at=_own_window() - timedelta(days=200))

    result = audit_log_retention_report_task()

    assert result["status"] == "ran"
    assert result["ready_to_purge"] == 0


# ---------------------------------------------------------------------------
# Cursor gating: never count a row the export has not reached yet
# ---------------------------------------------------------------------------


def test_row_after_cursor_never_counted_no_matter_its_age(
    session: Session, destination: str
) -> None:
    window = _own_window()
    cursor_position = window - timedelta(days=300)
    _set_cursor(session, destination=destination, position=cursor_position)

    before = _ready_count(session, destination=destination, retention_days=1)

    # Old enough to be past a 1-day retention window, but AFTER the cursor
    # (unexported): must never be counted.
    _insert_audit_row(session, at=window - timedelta(days=200))

    after = _ready_count(session, destination=destination, retention_days=1)

    assert after == before


def test_row_at_or_before_cursor_and_past_retention_is_counted(
    session: Session, destination: str
) -> None:
    window = _own_window()
    # Cursor sits just after where the row will land, so it counts as exported.
    _set_cursor(session, destination=destination, position=window - timedelta(days=299))

    before = _ready_count(session, destination=destination, retention_days=1)
    _insert_audit_row(session, at=window - timedelta(days=300))
    after = _ready_count(session, destination=destination, retention_days=1)

    assert after == before + 1


def test_row_at_or_before_cursor_but_not_past_retention_is_not_counted(
    session: Session, destination: str
) -> None:
    # Anchored at the actual present, not the far-past window the other
    # tests in this file use: "only 1 hour old" must mean recent relative
    # to now, not recent relative to some historical anchor.
    now = datetime.now(UTC)
    _set_cursor(session, destination=destination, position=now)

    before = _ready_count(session, destination=destination, retention_days=365)
    # Exported (created before the cursor) but only 1 hour old, nowhere
    # near the 365-day retention window.
    _insert_audit_row(session, at=now - timedelta(hours=1))
    after = _ready_count(session, destination=destination, retention_days=365)

    assert after == before


def test_lowering_retention_days_can_only_ever_increase_the_count(
    session: Session, destination: str
) -> None:
    """A row at a fixed position is counted under a short retention window
    and still counted under an even shorter one: the predicate is
    monotonic in the configured age, for the same fixed row and cursor."""
    window = _own_window()
    _set_cursor(session, destination=destination, position=window)
    _insert_audit_row(session, at=window - timedelta(days=100))

    loose = _ready_count(session, destination=destination, retention_days=200)
    tight = _ready_count(session, destination=destination, retention_days=10)

    assert tight >= loose


# ---------------------------------------------------------------------------
# No feedback loop: the report never deletes, so it cannot grow the count it
# is measuring by measuring it.
# ---------------------------------------------------------------------------


def test_report_never_deletes_audit_rows(
    session: Session, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "1")

    from tasks.audit_log_retention import audit_log_retention_report_task

    window = _own_window()
    _insert_audit_row(session, at=window - timedelta(days=300))
    _set_cursor(session, destination=destination, position=window)

    before = _audit_logs_row_count(session)
    audit_log_retention_report_task()
    audit_log_retention_report_task()
    audit_log_retention_report_task()
    after = _audit_logs_row_count(session)

    assert after == before


def test_report_is_stable_across_repeated_runs(
    session: Session, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    """Repeated runs return the same ready_to_purge value: nothing about
    running the report changes what the next run will find."""
    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "1")

    from tasks.audit_log_retention import audit_log_retention_report_task

    window = _own_window()
    _set_cursor(session, destination=destination, position=window)
    baseline = audit_log_retention_report_task()["ready_to_purge"]

    _insert_audit_row(session, at=window - timedelta(days=300))
    _insert_audit_row(session, at=window - timedelta(days=200))

    first = audit_log_retention_report_task()
    second = audit_log_retention_report_task()
    third = audit_log_retention_report_task()

    assert first["ready_to_purge"] == second["ready_to_purge"] == third["ready_to_purge"]
    assert first["ready_to_purge"] == baseline + 2
