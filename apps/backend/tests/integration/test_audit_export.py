"""
Handing the audit trail to whatever collects logs (N17).

The contract is short: off by default, the audit API unchanged, and the
position such that a stopped and resumed export loses nothing and repeats
nothing. Most of what follows is about that last one, because it is the
property that fails quietly.

Two failure shapes are covered specifically. A crowded millisecond, where
several audit rows share a timestamp and a timestamp-only cursor either sends
the tail twice or steps over it. And a delivery that fails after the position
has moved, which would leave a hole the next run starts past.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping audit export tests")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr[-400:]}")


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
    """A slice of the past no other run will write into.

    The suite shares a database and audit rows are never deleted, so rows an
    earlier run of this same test wrote are still there and would be swept up
    by a cursor pinned in the same neighbourhood. Two runs need distinct
    windows, and the only thing guaranteed to differ between them is when they
    started: this shifts the current instant a century back, so runs a
    microsecond apart get windows a microsecond apart, and nothing else in the
    database is anywhere near.
    """
    return datetime.now(tz=UTC) - timedelta(days=36_500)


def _cursor_just_before(session: Session, *, destination: str, moment: datetime):
    """A cursor positioned immediately before ``moment``.

    The suite shares a database with every other test, so an export starting
    at the beginning would hand over thousands of rows nobody here wrote. A
    resumed export starts from a position anyway; this is that, pinned where
    the test can reason about what follows.
    """
    from services.audit_export_service import get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)
    cursor.last_created_at = moment - timedelta(microseconds=1)
    cursor.last_id = uuid.UUID(int=0)
    session.commit()
    return cursor


def _insert_audit_rows(
    session: Session, *, count: int, at: datetime, action: str = "create"
) -> list[str]:
    """Write audit rows directly, all stamped at the same instant.

    Written with raw SQL rather than the ORM because the audit listener builds
    these from real mutations, and what is under test is the export's reading
    of them rather than the way they are produced.
    """
    ids: list[str] = []
    for _ in range(count):
        row_id = str(uuid.uuid4())
        session.execute(
            text(
                "INSERT INTO audit_logs (id, created_at, action, target_table) "
                "VALUES (:id, :at, :action, 'test_targets')"
            ),
            {"id": row_id, "at": at, "action": action},
        )
        ids.append(row_id)
    session.commit()
    return ids


# ---------------------------------------------------------------------------
# Off, which is the default
# ---------------------------------------------------------------------------


def test_the_task_does_nothing_when_no_destination_is_configured(monkeypatch) -> None:
    from tasks import audit_export

    monkeypatch.delenv("AUDIT_EXPORT_URL", raising=False)

    result = audit_export._run(None)

    assert result["status"] == "skipped"
    assert result["exported"] == 0


def test_nothing_is_read_when_no_destination_is_configured(monkeypatch) -> None:
    """Not one query. A deployment that has not switched this on pays a
    function call reading one environment variable, every five minutes."""
    from tasks import audit_export

    monkeypatch.delenv("AUDIT_EXPORT_URL", raising=False)

    def _must_not_open_a_session():
        raise AssertionError("the export opened a session with no destination set")

    monkeypatch.setattr(
        "core.db.sync_session_scope", _must_not_open_a_session, raising=False
    )

    assert audit_export._run(None)["status"] == "skipped"


# ---------------------------------------------------------------------------
# The position
# ---------------------------------------------------------------------------


def test_a_fresh_cursor_starts_at_the_beginning(session, destination) -> None:
    """An export that silently began at the moment it was configured would
    leave a hole nobody would think to look for."""
    from services.audit_export_service import get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)

    assert cursor.last_created_at is None
    assert cursor.last_id is None


def test_the_same_destination_keeps_one_cursor(session, destination) -> None:
    from services.audit_export_service import get_or_create_cursor

    first = get_or_create_cursor(session, destination=destination)
    again = get_or_create_cursor(session, destination=destination)

    assert first.id == again.id


def test_a_batch_stops_at_the_configured_size(session, destination, monkeypatch) -> None:
    from services.audit_export_service import collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "3")
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    at = _own_window()
    _insert_audit_rows(session, count=5, at=at)
    cursor = _cursor_just_before(session, destination=destination, moment=at)

    batch = collect_batch(session, cursor=cursor)

    assert len(batch.rows) == 3


def test_resuming_loses_nothing_and_repeats_nothing(
    session, destination, monkeypatch
) -> None:
    """The sequence the plan names: deliver, stop, resume.

    Every row is handed over exactly once across the three runs, which is the
    whole promise of a continuous export.
    """
    from services.audit_export_service import (
        advance_cursor,
        collect_batch,
    )

    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "2")
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    base = _own_window()
    cursor = _cursor_just_before(session, destination=destination, moment=base)
    written = []
    for offset in range(5):
        written.extend(
            _insert_audit_rows(
                session, count=1, at=base + timedelta(microseconds=offset)
            )
        )

    seen: list[str] = []
    for _ in range(3):
        batch = collect_batch(session, cursor=cursor)
        seen.extend(row["id"] for row in batch.rows)
        advance_cursor(session, cursor=cursor, batch=batch)

    # Every row this test wrote, once each, in order. Not "only these rows":
    # the suite shares a database and anything else pending is legitimately
    # exported too. The contract is that nothing is skipped and nothing is
    # repeated, which is what these three assertions say.
    assert [row_id for row_id in seen if row_id in set(written)] == written
    assert len(seen) == len(set(seen))


def test_rows_sharing_a_millisecond_are_not_repeated_or_skipped(
    session, destination, monkeypatch
) -> None:
    """The failure a timestamp-only cursor has.

    Five rows at one instant and a batch size of two: the second run must
    continue inside that instant rather than either re-sending the first two
    or stepping past the whole millisecond.
    """
    from services.audit_export_service import advance_cursor, collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "2")
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    crowded = _own_window()
    cursor = _cursor_just_before(session, destination=destination, moment=crowded)
    written = set(_insert_audit_rows(session, count=5, at=crowded))

    seen: list[str] = []
    for _ in range(3):
        batch = collect_batch(session, cursor=cursor)
        seen.extend(row["id"] for row in batch.rows)
        advance_cursor(session, cursor=cursor, batch=batch)

    assert written <= set(seen)
    assert len(seen) == len(set(seen))


def test_the_export_stays_behind_the_present(session, destination, monkeypatch) -> None:
    """A row is stamped when its transaction commits, so one that began
    earlier can commit later and land behind a position already passed. The
    export only reads stretches of time no open transaction can write into."""
    from services.audit_export_service import collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "300")
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "50")
    just_now = datetime.now(tz=UTC)
    settled = just_now - timedelta(hours=1)
    cursor = _cursor_just_before(session, destination=destination, moment=settled)
    settled_ids = set(_insert_audit_rows(session, count=2, at=settled))
    fresh_ids = set(_insert_audit_rows(session, count=2, at=just_now))

    batch = collect_batch(session, cursor=cursor)

    exported = {row["id"] for row in batch.rows}
    assert settled_ids <= exported
    assert not (fresh_ids & exported)


def test_the_position_does_not_move_when_nothing_was_delivered(
    session, destination, monkeypatch
) -> None:
    """Advancing before a successful post would turn one failed request into a
    permanent hole, and the hole would be invisible."""
    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    at = _own_window()
    _insert_audit_rows(session, count=3, at=at)

    async def _fails(*_args, **_kwargs):
        raise audit_export.AuditExportDeliveryError("collector is down")

    monkeypatch.setattr(audit_export, "_post", _fails)

    with pytest.raises(audit_export.AuditExportDeliveryError):
        audit_export._run(None)

    from services.audit_export_service import get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)
    assert cursor.last_id is None


# ---------------------------------------------------------------------------
# What the collector receives
# ---------------------------------------------------------------------------


def test_the_batch_carries_the_columns_the_audit_screen_shows(
    session, destination, monkeypatch
) -> None:
    from services.audit_export_service import BATCH_VERSION, build_body, collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    at = _own_window()
    cursor = _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=1, at=at)

    body = build_body(collect_batch(session, cursor=cursor), destination=destination)

    assert body["version"] == BATCH_VERSION
    assert body["source"] == "trusca"
    assert body["count"] == len(body["rows"])
    assert set(body["rows"][0]) == {
        "id",
        "created_at",
        "actor_user_id",
        "team_id",
        "action",
        "target_table",
        "target_id",
        "request_id",
        "ip",
        "user_agent",
        "diff",
    }


def test_a_masked_diff_stays_masked_on_the_way_out(session, destination, monkeypatch) -> None:
    """The listener masks credentials before the column is written, so the
    export cannot unmask them. Asserted because a future export that rebuilt
    the diff from somewhere else would not have that property."""
    from services.audit_export_service import collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    at = _own_window()
    cursor = _cursor_just_before(session, destination=destination, moment=at)
    row_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO audit_logs (id, created_at, action, target_table, diff) "
            "VALUES (:id, :at, 'update', 'users', :diff)"
        ),
        {"id": row_id, "at": at, "diff": '{"hashed_password": "***"}'},
    )
    session.commit()

    batch = collect_batch(session, cursor=cursor)

    exported = next(row for row in batch.rows if row["id"] == row_id)
    assert exported["diff"] == {"hashed_password": "***"}


# ---------------------------------------------------------------------------
# The trail itself is unchanged
# ---------------------------------------------------------------------------


def test_the_export_adds_no_column_to_the_audit_table(session) -> None:
    """The named silent break for this unit.

    Marking rows exported in place would mean either dropping the trigger that
    makes the table append-only or carving an exception into it, and the
    exception is the property.
    """
    columns = {
        row[0]
        for row in session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'audit_logs'"
            )
        ).all()
    }

    assert not any("export" in name for name in columns), sorted(columns)


def test_the_audit_table_is_still_append_only(session) -> None:
    """The existing trigger, re-asserted from this side.

    If a later change to the export needed the rows to be mutable, this is
    where that would surface rather than in a review comment.
    """
    row_id = _insert_audit_rows(session, count=1, at=_own_window())[0]

    with pytest.raises(Exception):
        session.execute(
            text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
            {"id": row_id},
        )
        session.commit()
    session.rollback()
