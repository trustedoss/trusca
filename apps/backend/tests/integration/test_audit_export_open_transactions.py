"""
The audit export and transactions that are still open (#587).

An audit row's ``created_at`` is ``now()``: the moment its transaction began,
not the moment it committed. A transaction that stays open therefore writes
rows dated behind the present, and they become visible only when it commits.
An export that had already read past that date would have moved its position
beyond them, and they would never be sent.

The fixed lag cannot carry this, since nothing bounds how long a transaction
stays open, so these tests set it to its floor of one second and pin the lag
horizon just past their own rows: any open transaction is then longer than
the lag, which is the case the issue is about. What is asserted is the whole
sequence the export lives through. A run while a transaction is open, the
transaction committing, and the next run. That includes a transaction the
export cannot see the start of, which is what a session of another database
role looks like to a role without ``pg_read_all_stats``.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration

_HELD_BACK = "audit_export_held_back_by_open_transaction"


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


@pytest.fixture
def export_role_session(engine: Engine) -> Iterator[Session]:
    """A session as a plain role, the way a role-separated deployment runs.

    Created per test with a random name and dropped afterwards. It gets what
    the export needs and nothing else; in particular not ``pg_read_all_stats``,
    so it is not shown the start of other roles' transactions, including the
    superuser the rest of this suite connects as.
    """
    role = f"audit_export_probe_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    with engine.connect() as admin:
        admin.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        admin.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        admin.execute(text(f"GRANT SELECT ON audit_logs TO {role}"))
        admin.execute(text(f"GRANT SELECT, INSERT, UPDATE ON audit_export_cursors TO {role}"))
        admin.commit()
    role_engine = create_engine(
        make_url(_sync_url()).set(username=role, password=password), future=True
    )
    try:
        with sessionmaker(bind=role_engine, future=True)() as s:
            superuser = s.execute(
                text("SELECT rolsuper OR pg_has_role(current_user, 'pg_read_all_stats', 'USAGE') "
                     "FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            s.rollback()
            assert not superuser, "the probe role can see every session; the test proves nothing"
            yield s
    finally:
        role_engine.dispose()
        with engine.connect() as admin:
            admin.execute(text(f"DROP OWNED BY {role}"))
            admin.execute(text(f"DROP ROLE {role}"))
            admin.commit()


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


def _pin_cursor(session: Session, *, destination: str, before: datetime):
    from services.audit_export_service import get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)
    cursor.last_created_at = before - timedelta(microseconds=1)
    cursor.last_id = uuid.UUID(int=0)
    session.commit()
    return cursor


def _configure(monkeypatch, destination: str, *, ceiling: list[datetime]) -> list[list[str]]:
    """Point the task at ``destination`` and record what it posts.

    The lag is at its floor, so only the open-transaction bound can hold a
    row back. ``ceiling`` pins the instant the lag is measured from, a second
    past this test's own rows, so a run reads them and not whatever else the
    shared database holds after them. It is a list so the test can fill it in
    once it knows its rows' dates.
    """
    from services import audit_export_service
    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "1")
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


def _events(logged, name):
    return [entry for entry in logged if entry.get("event") == name]


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
    # Not "idle": rows are waiting, and the result and the log say so.
    assert first["status"] == "held_back", first
    assert first["pending"] >= 1
    assert first["held_back_seconds"] > 0
    held = _events(logged, _HELD_BACK)
    assert [entry["log_level"] for entry in held] == ["warning"]
    assert held[0]["held_back_seconds"] > 0
    finished = _events(logged, "audit_export_run_finished")
    assert [entry["status"] for entry in finished] == ["held_back"]

    long_transaction.commit()
    with capture_logs() as logged_second:
        second = audit_export._run(None)

    sent = [row_id for post in posts for row_id in post]
    mine = [row_id for row_id in sent if row_id in {late_id, early_id}]
    assert mine == [late_id, early_id], (first, second)
    assert len(sent) == len(set(sent))
    # Nothing open any more, so a normal run says nothing at WARNING.
    assert second["status"] == "delivered"
    assert not _events(logged_second, _HELD_BACK)


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


def test_a_transaction_of_another_role_holds_the_export_at_its_position(
    session, long_transaction, export_role_session, destination, monkeypatch
) -> None:
    """The export cannot see when another role's transaction began.

    In a role-separated deployment the export runs as the runtime role, and
    the anonymisation script runs as the owner. The script's transaction is
    open while it rewrites the subject's rows and then writes its audit row,
    dated when it began. Here a superuser plays the other role: the probe
    role is not shown its ``xact_start``, only that it holds a transaction
    open. Reading anything in that state could move the position past the
    hidden transaction's row, so nothing is read until it ends.
    """
    from services.audit_export_service import advance_cursor, collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "1")
    long_transaction.begin()
    began = long_transaction.execute(text("SELECT now()")).scalar_one()
    hidden_pid = long_transaction.execute(text("SELECT pg_backend_pid()")).scalar_one()
    _pin_cursor(session, destination=destination, before=began)
    hidden_id, _ = _insert_with_default_stamp(long_transaction)
    early_id, early_stamp = _insert_with_default_stamp(session)
    session.commit()
    ceiling = early_stamp + timedelta(seconds=1)

    from sqlalchemy import select

    from models import AuditExportCursor

    cursor = export_role_session.execute(
        select(AuditExportCursor).where(AuditExportCursor.destination == destination)
    ).scalar_one()

    from services.audit_export_service import read_open_transactions

    seen = read_open_transactions(export_role_session)
    assert hidden_pid in seen.hidden
    assert hidden_pid not in seen.starts

    held = collect_batch(export_role_session, cursor=cursor, now=ceiling)
    delivered = [row["id"] for row in held.rows]
    advance_cursor(export_role_session, cursor=cursor, batch=held)

    long_transaction.commit()
    for _ in range(3):
        batch = collect_batch(export_role_session, cursor=cursor, now=ceiling)
        delivered.extend(row["id"] for row in batch.rows)
        advance_cursor(export_role_session, cursor=cursor, batch=batch)
    export_role_session.commit()

    # The outcome first: both rows, once each, in date order. Reading while
    # the hidden transaction was open would have sent only the early one.
    assert [row_id for row_id in delivered if row_id in {hidden_id, early_id}] == [
        hidden_id,
        early_id,
    ]
    assert held.is_empty
    assert held.hidden_transactions >= 1
    assert held.rows_held_back


def test_a_vacuum_the_export_cannot_see_into_does_not_hold_it(
    engine, export_role_session
) -> None:
    """A VACUUM holds a transaction open, and run by a superuser (as
    autovacuum is) its start is hidden from the export's role. It cannot run
    inside a transaction that wrote anything, and ``pg_stat_progress_vacuum``
    names its pid to every role, so it is left out rather than holding the
    export for the length of a vacuum. Slowed with a cost delay so it is still
    running when the export looks."""
    import threading
    import time

    from services.audit_export_service import read_open_transactions

    table = f"vacuum_probe_{secrets.token_hex(4)}"
    with engine.connect() as setup:
        setup.execute(
            text(f"CREATE TABLE {table} AS SELECT g AS id FROM generate_series(1, 20000) g")
        )
        setup.execute(text(f"DELETE FROM {table} WHERE id % 2 = 0"))
        setup.commit()
    vacuum = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    vacuum_pid = vacuum.execute(text("SELECT pg_backend_pid()")).scalar_one()
    vacuum.execute(text("SET vacuum_cost_delay = 50"))
    vacuum.execute(text("SET vacuum_cost_limit = 1"))
    outcome: dict[str, object] = {}

    def _run_vacuum() -> None:
        try:
            vacuum.execute(text(f"VACUUM {table}"))
        except Exception as exc:  # noqa: BLE001 - cancelled on purpose below
            outcome["error"] = exc

    worker = threading.Thread(target=_run_vacuum)
    worker.start()
    try:
        deadline = time.monotonic() + 30
        listed = False
        while time.monotonic() < deadline and not listed:
            listed = vacuum_pid in {
                pid for (pid,) in export_role_session.execute(
                    text("SELECT pid FROM pg_stat_progress_vacuum")
                ).all()
            }
            export_role_session.rollback()
            if not listed:
                time.sleep(0.01)
        assert listed, "the vacuum finished before the export looked"
        held_lock = export_role_session.execute(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE pid = :p AND locktype = 'virtualxid' AND granted"
            ),
            {"p": vacuum_pid},
        ).scalar_one()
        seen = read_open_transactions(export_role_session)
        export_role_session.rollback()
    finally:
        with engine.connect() as admin:
            admin.execute(text("SELECT pg_cancel_backend(:p)"), {"p": vacuum_pid})
            admin.commit()
        worker.join(timeout=30)
        vacuum.close()
        with engine.connect() as cleanup:
            cleanup.execute(text(f"DROP TABLE {table}"))
            cleanup.commit()

    # The control: it really is an open transaction the export cannot date.
    assert held_lock == 1
    assert vacuum_pid not in seen.starts
    assert vacuum_pid not in seen.hidden


def test_the_lag_never_drops_below_one_second(monkeypatch) -> None:
    """A transaction is dated before it is listed in ``pg_stat_activity``; a
    horizon at the very present could reach one that was not yet listed."""
    from core.config import audit_export_lag_seconds

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    assert audit_export_lag_seconds() == 1


def test_a_long_transaction_with_nothing_waiting_behind_it_is_not_a_warning(
    session, long_transaction, destination, monkeypatch
) -> None:
    """Caught up, with a transaction open that is older than the lag.

    The transaction is the tighter bound, but nothing sits between it and the
    lag horizon, so there is nothing to warn about: the run is idle, and says
    how many rows remain after the position, which is the real figure.
    """
    from structlog.testing import capture_logs

    from tasks import audit_export

    long_transaction.begin()
    began = long_transaction.execute(text("SELECT now()")).scalar_one()
    _pin_cursor(session, destination=destination, before=began)
    ceiling = [began + timedelta(seconds=10)]
    _configure(monkeypatch, destination, ceiling=ceiling)

    with capture_logs() as logged:
        result = audit_export._run(None)

    assert result["status"] == "idle", result
    assert "held_back_seconds" not in result
    assert not _events(logged, _HELD_BACK)
    finished = _events(logged, "audit_export_run_finished")
    assert [entry["status"] for entry in finished] == ["idle"]
    assert finished[0]["pending"] == result["pending"]


def test_the_bound_sees_a_transaction_that_began_after_an_earlier_read(
    session, long_transaction
) -> None:
    """``pg_stat_activity`` is one copy per transaction, taken at first read.

    Reading it twice in one transaction would return the first copy and miss a
    transaction that began in between, so the copy is discarded before each
    read.
    """
    from services.audit_export_service import read_open_transactions

    session.execute(text("SELECT 1"))
    read_open_transactions(session)
    long_transaction.begin()
    began = long_transaction.execute(text("SELECT now()")).scalar_one()
    pid = long_transaction.execute(text("SELECT pg_backend_pid()")).scalar_one()

    seen = read_open_transactions(session)
    session.rollback()

    assert seen.starts.get(pid) == began
    assert seen.oldest is not None and seen.oldest <= began


def test_the_exports_own_transaction_does_not_hold_it_back(
    session, long_transaction
) -> None:
    """Left out by pid, while another session's transaction is counted."""
    from services.audit_export_service import read_open_transactions

    own_pid = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
    long_transaction.begin()
    other_pid = long_transaction.execute(text("SELECT pg_backend_pid()")).scalar_one()
    seen = read_open_transactions(session)
    session.rollback()

    assert other_pid in seen.starts
    assert own_pid not in seen.starts
    assert own_pid not in seen.hidden


def test_activity_tracking_switched_off_is_said_out_loud(session, destination) -> None:
    """With ``track_activities`` off nothing publishes a start time, so the
    export reads only when nothing at all is open. Correct, and easy to
    mistake for a stall, so it is logged."""
    from structlog.testing import capture_logs

    from services.audit_export_service import collect_batch, get_or_create_cursor

    cursor = get_or_create_cursor(session, destination=destination)
    session.execute(text("SET track_activities = off"))
    try:
        with capture_logs() as logged:
            collect_batch(session, cursor=cursor, now=datetime(2000, 1, 1).astimezone())
    finally:
        session.rollback()
        session.execute(text("RESET track_activities"))
        session.commit()

    warned = _events(logged, "audit_export_activity_tracking_off")
    assert [entry["log_level"] for entry in warned] == ["warning"]


def test_the_present_is_the_databases_clock_not_the_workers(
    session, destination, monkeypatch
) -> None:
    """A worker clock running ahead of the database by more than the lag would
    place the horizon past transactions that begin after the read. The read
    therefore measures the lag from the database's own clock.

    Simulated with a worker clock an hour ahead and a row dated ten minutes
    into the database's future: read against the worker's clock it would be
    sent; against the database's it is not due yet. The row is written in the
    reading session's own transaction and rolled back, so the shared table is
    left without a future-dated row.
    """
    from services import audit_export_service
    from services.audit_export_service import collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "1")
    db_now = session.execute(text("SELECT now()")).scalar_one()
    session.commit()
    future_at = db_now + timedelta(minutes=10)
    cursor = _pin_cursor(session, destination=destination, before=future_at)
    future_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO audit_logs (id, created_at, action, target_table) "
            "VALUES (:id, :at, 'create', 'test_targets')"
        ),
        {"id": future_id, "at": future_at},
    )

    class _WorkerClockAhead(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz=tz) + timedelta(hours=1)

    monkeypatch.setattr(audit_export_service, "datetime", _WorkerClockAhead)
    # The control: the row is readable here, so its absence below is the
    # clock's doing and not the row being out of reach.
    visible = session.execute(
        text("SELECT count(*) FROM audit_logs WHERE id = :id"), {"id": future_id}
    ).scalar_one()

    batch = collect_batch(session, cursor=cursor)
    session.rollback()

    assert visible == 1
    assert future_id not in {row["id"] for row in batch.rows}


def _over_tcp(conn) -> bool:
    return bool(conn.execute(text("SELECT inet_server_addr() IS NOT NULL")).scalar_one())


_EXPECTED_KEEPALIVES = {
    "tcp_keepalives_idle": "60",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "6",
}


def test_worker_connections_ask_the_server_to_probe_them() -> None:
    """A worker host that vanishes leaves its lock and any open transaction on
    the server until the server notices. Only the server's keepalive settings
    decide when, so they are set on every worker connection, and set in a way
    the pool's rollback on return does not undo."""
    from core import db

    engine = db.build_sync_engine()
    try:
        for _ in range(2):
            with engine.connect() as conn:
                if not _over_tcp(conn):
                    pytest.skip("Unix-socket connection: the server ignores TCP keepalives")
                conn.execute(text("SELECT 1"))
                conn.rollback()
                values = {
                    name: conn.execute(text(f"SHOW {name}")).scalar_one()
                    for name in _EXPECTED_KEEPALIVES
                }
            assert values == _EXPECTED_KEEPALIVES
    finally:
        engine.dispose()


async def test_api_connections_ask_the_server_to_probe_them() -> None:
    from core import db

    engine = db.build_engine()
    try:
        for _ in range(2):
            async with engine.connect() as conn:
                tcp = (
                    await conn.execute(text("SELECT inet_server_addr() IS NOT NULL"))
                ).scalar_one()
                if not tcp:
                    pytest.skip("Unix-socket connection: the server ignores TCP keepalives")
                await conn.execute(text("SELECT 1"))
                await conn.rollback()
                values = {
                    name: (await conn.execute(text(f"SHOW {name}"))).scalar_one()
                    for name in _EXPECTED_KEEPALIVES
                }
            assert values == _EXPECTED_KEEPALIVES
    finally:
        await engine.dispose()


def test_a_server_that_refuses_the_settings_still_gets_a_connection(monkeypatch) -> None:
    """A pooler or server that rejects one of the settings costs the faster
    probe, not the connection. Logged, so the loss is visible."""
    from structlog.testing import capture_logs

    from core import db

    monkeypatch.setattr(db, "_SERVER_KEEPALIVES", (("no_such_setting_587", 1),))
    engine = db.build_sync_engine()
    try:
        with capture_logs() as logged, engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
    assert [e["log_level"] for e in _events(logged, "db_server_keepalives_not_set")] == [
        "warning"
    ]


async def test_an_api_server_that_refuses_the_settings_still_gets_a_connection(
    monkeypatch,
) -> None:
    from structlog.testing import capture_logs

    from core import db

    monkeypatch.setattr(db, "_SERVER_KEEPALIVES", (("no_such_setting_587", 1),))
    engine = db.build_engine()
    try:
        with capture_logs() as logged:
            async with engine.connect() as conn:
                assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()
    assert [e["log_level"] for e in _events(logged, "db_server_keepalives_not_set")] == [
        "warning"
    ]
