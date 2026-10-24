"""
The audit export and transactions that are still open (#587).

An audit row's ``created_at`` is ``now()``: the moment its transaction began,
not the moment it committed. A transaction that stays open therefore writes
rows dated behind the present, and they become visible only when it commits.
An export that had already read past that date would have moved its position
beyond them, and they would never be sent.

The fixed lag cannot carry this, since nothing bounds how long a transaction
stays open, so every test here sets it to zero: with no margin at all, any
open transaction is longer than the lag, which is the case the issue is about.
What is asserted is the whole sequence the export lives through. A run while a
transaction is open, the transaction committing, and the next run.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping audit export tests")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(_sync_url(), future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


@pytest.fixture
def long_transaction(engine: Engine) -> Iterator[Connection]:
    """A second connection, for the transaction that stays open."""
    conn = engine.connect()
    try:
        yield conn
    finally:
        if conn.in_transaction():
            conn.rollback()
        conn.close()


@pytest.fixture
def destination() -> str:
    return f"https://collector.example/{uuid.uuid4().hex[:12]}"


def _insert_with_default_stamp(conn, *, row_id: uuid.UUID | None = None) -> tuple[str, datetime]:
    """One audit row dated the way the application dates them: by the default."""
    rid = row_id or uuid.uuid4()
    stamped = conn.execute(
        text(
            "INSERT INTO audit_logs (id, action, target_table) "
            "VALUES (:id, 'create', 'test_targets') RETURNING created_at"
        ),
        {"id": str(rid)},
    ).scalar_one()
    return str(rid), stamped


def _pin_cursor(session: Session, *, destination: str, before: datetime) -> None:
    from services.audit_export_service import get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)
    cursor.last_created_at = before - timedelta(microseconds=1)
    cursor.last_id = uuid.UUID(int=0)
    session.commit()


def _configure(monkeypatch, destination: str, *, ceiling: list[datetime]) -> list[list[str]]:
    """Point the task at ``destination`` and record what it posts.

    The lag is zero, so only the open-transaction bound can hold a row back.
    ``ceiling`` caps the lag bound once the test knows its own rows' dates, so
    a run reads this test's rows and not whatever else the shared database
    holds after them. It is a list so the test can fill it in after setup.
    """
    from services import audit_export_service
    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "50")
    real_collect = audit_export_service.collect_batch

    def _collect_up_to_ceiling(session, *, cursor, now=None):
        return real_collect(session, cursor=cursor, now=ceiling[0])

    monkeypatch.setattr(audit_export, "collect_batch", _collect_up_to_ceiling)
    posts: list[list[str]] = []

    async def _accept(url, body):
        posts.append([row["id"] for row in body["rows"]])
        return 202

    monkeypatch.setattr(audit_export, "_post", _accept)
    return posts


def test_a_row_from_a_transaction_open_across_a_run_is_sent_once_it_commits(
    session, long_transaction, destination, monkeypatch
) -> None:
    """The sequence from the issue, end to end through the task.

    A long transaction writes a row and stays open. A short one writes a row
    dated after it and commits. A run happens. Then the long one commits and
    the next run happens. Both rows must arrive, once each, in date order.

    Reading up to the present would send the short transaction's row in the
    first run and move the position past the long one's date, and the long
    one's row would then never be sent.
    """
    from structlog.testing import capture_logs

    from tasks import audit_export

    ceiling: list[datetime] = []
    posts = _configure(monkeypatch, destination, ceiling=ceiling)

    long_transaction.begin()
    began = long_transaction.execute(text("SELECT now()")).scalar_one()
    _pin_cursor(session, destination=destination, before=began)
    late_id, late_stamp = _insert_with_default_stamp(long_transaction)
    assert late_stamp == began, "the default no longer dates a row by its transaction"

    early_id, early_stamp = _insert_with_default_stamp(session)
    session.commit()
    assert early_stamp > began
    ceiling.append(early_stamp + timedelta(seconds=1))

    with capture_logs() as logged:
        first = audit_export._run(None)

    sent_first = {row_id for post in posts for row_id in post}
    assert late_id not in sent_first
    # Held back too, though it is committed: sending it would move the
    # position past the open transaction's date.
    assert early_id not in sent_first, first
    held = [
        entry for entry in logged
        if entry.get("event") == "audit_export_held_back_by_open_transaction"
    ]
    assert held, "a run held back by an open transaction said nothing"
    assert held[0]["log_level"] == "warning"
    assert held[0]["held_back_seconds"] > 0

    long_transaction.commit()
    second = audit_export._run(None)

    sent = [row_id for post in posts for row_id in post]
    mine = [row_id for row_id in sent if row_id in {late_id, early_id}]
    assert mine == [late_id, early_id], (first, second)
    assert len(sent) == len(set(sent))


def test_a_committed_row_dated_at_the_open_transactions_start_waits_for_it(
    session, long_transaction, destination, monkeypatch
) -> None:
    """The bound is strictly before the open transaction's start.

    The position is a (date, id) pair. A committed row sharing the open
    transaction's date but with a larger id, if sent, would leave the position
    at that date and id, and the open transaction's own row at the same date
    with a smaller id would then sort behind the position and be skipped.
    """
    from tasks import audit_export

    ceiling: list[datetime] = []
    posts = _configure(monkeypatch, destination, ceiling=ceiling)

    long_transaction.begin()
    began = long_transaction.execute(text("SELECT now()")).scalar_one()
    _pin_cursor(session, destination=destination, before=began)
    # Fresh each run (audit rows are never deleted), and ordered: the open
    # transaction's row sorts first by id, the committed one last.
    tail = uuid.uuid4().hex[8:]
    late_id, _ = _insert_with_default_stamp(
        long_transaction, row_id=uuid.UUID("00000000" + tail)
    )
    same_instant_id = str(uuid.UUID("ffffffff" + tail))
    session.execute(
        text(
            "INSERT INTO audit_logs (id, created_at, action, target_table) "
            "VALUES (:id, :at, 'create', 'test_targets')"
        ),
        {"id": same_instant_id, "at": began},
    )
    session.commit()
    ceiling.append(began + timedelta(seconds=1))

    audit_export._run(None)
    assert not any(same_instant_id in post for post in posts)

    long_transaction.commit()
    audit_export._run(None)

    sent = [row_id for post in posts for row_id in post]
    mine = [row_id for row_id in sent if row_id in {late_id, same_instant_id}]
    assert mine == [late_id, same_instant_id]


def test_the_bound_sees_a_transaction_that_began_after_an_earlier_read(
    session, long_transaction
) -> None:
    """``pg_stat_activity`` is one copy per transaction, taken at first read.

    Reading it twice in one transaction would return the first copy and miss a
    transaction that began in between, so the copy is discarded before each
    read.
    """
    from services.audit_export_service import oldest_open_transaction

    session.execute(text("SELECT 1"))
    oldest_open_transaction(session)
    long_transaction.begin()
    began = long_transaction.execute(text("SELECT now()")).scalar_one()

    _, oldest = oldest_open_transaction(session)
    session.rollback()

    assert oldest is not None
    assert oldest <= began


def test_the_exports_own_transaction_does_not_hold_it_back(session) -> None:
    from services.audit_export_service import oldest_open_transaction

    own_start = session.execute(text("SELECT now()")).scalar_one()
    _, oldest = oldest_open_transaction(session)
    session.rollback()

    assert oldest != own_start


def test_the_present_is_the_databases_clock_not_the_workers(
    session, destination, monkeypatch
) -> None:
    """A worker clock running ahead of the database by more than the lag would
    place the horizon past transactions that begin after the read. The read
    therefore measures the lag from the database's own clock.

    Simulated with a worker clock an hour ahead and a committed row dated ten
    minutes into the database's future: read against the worker's clock it
    would be sent; against the database's it is not due yet.
    """
    from services import audit_export_service
    from services.audit_export_service import collect_batch, get_or_create_cursor

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    db_now = session.execute(text("SELECT now()")).scalar_one()
    session.commit()
    future_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO audit_logs (id, created_at, action, target_table) "
            "VALUES (:id, :at, 'create', 'test_targets')"
        ),
        {"id": future_id, "at": db_now + timedelta(minutes=10)},
    )
    session.commit()
    _pin_cursor(session, destination=destination, before=db_now + timedelta(minutes=10))
    cursor = get_or_create_cursor(session, destination=destination)

    class _WorkerClockAhead(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz=tz) + timedelta(hours=1)

    monkeypatch.setattr(audit_export_service, "datetime", _WorkerClockAhead)

    batch = collect_batch(session, cursor=cursor)
    session.rollback()

    assert future_id not in {row["id"] for row in batch.rows}


def test_worker_connections_ask_the_server_to_probe_them(monkeypatch) -> None:
    """A worker host that vanishes leaves its lock and any open transaction on
    the server until the server notices. Only the server's keepalive settings
    decide when, so they are set on every worker connection, and set in a way
    the pool's rollback on return does not undo."""
    from core import db

    engine = db.build_sync_engine()
    try:
        for _ in range(2):
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.rollback()
                values = {
                    name: conn.execute(text(f"SHOW {name}")).scalar_one()
                    for name, _ in db._SERVER_KEEPALIVES
                }
            assert values == {
                "tcp_keepalives_idle": "60",
                "tcp_keepalives_interval": "10",
                "tcp_keepalives_count": "6",
            }
    finally:
        engine.dispose()
