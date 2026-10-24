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
        pytest.skip("DATABASE_URL not set, skipping audit export tests")
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


# ---------------------------------------------------------------------------
# The delivered path
#
# Everything above stops short of a successful hand-over: the batch is built,
# the position is checked, and the post is mocked away. These run the path a
# working deployment takes every five minutes, which is the one worth being
# sure about.
# ---------------------------------------------------------------------------


def test_a_delivered_batch_advances_the_position_and_reports_what_it_sent(
    session, destination, monkeypatch
) -> None:
    from services.audit_export_service import get_or_create_cursor
    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "500")
    at = _own_window()
    _cursor_just_before(session, destination=destination, moment=at)
    written = _insert_audit_rows(session, count=3, at=at)
    # Every post, not the last one: a run posts as many batches as it takes
    # to catch up, and the shared database usually holds more than one
    # batch of other tests' rows after this window.
    sent: list[tuple[str, dict]] = []

    async def _accept(url, body):
        sent.append((url, body))
        return 202

    monkeypatch.setattr(audit_export, "_post", _accept)

    result = audit_export._run(None)

    assert result["status"] == "delivered"
    assert result["exported"] >= 3
    assert sent and {url for url, _body in sent} == {destination}
    posted = {row["id"] for _url, body in sent for row in body["rows"]}
    assert posted >= set(written)

    cursor = get_or_create_cursor(session, destination=destination)
    session.refresh(cursor)
    assert cursor.last_id is not None
    assert cursor.rows_exported >= 3


def test_a_run_with_nothing_to_send_is_idle_rather_than_a_delivery(
    session, destination, monkeypatch
) -> None:
    """A collector that receives an empty batch every five minutes learns
    nothing and logs forever."""
    from services.audit_export_service import get_or_create_cursor
    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "86400")
    # Caught up: the position is the present, and the lag excludes everything
    # after it. That is the state a healthy export spends most of its life in.
    cursor = get_or_create_cursor(session, destination=destination)
    cursor.last_created_at = datetime.now(tz=UTC)
    cursor.last_id = uuid.UUID(int=(1 << 128) - 1)
    session.commit()

    async def _must_not_post(*_args, **_kwargs):
        raise AssertionError("an empty batch was posted")

    monkeypatch.setattr(audit_export, "_post", _must_not_post)

    result = audit_export._run(None)

    assert result["status"] == "idle"
    assert result["exported"] == 0


async def test_the_batch_travels_with_the_token_as_a_bearer_header(
    monkeypatch,
) -> None:
    import httpx

    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_TOKEN", "collector-secret")
    seen: dict[str, object] = {}

    async def _capture(self, url, **kwargs):  # noqa: ANN001
        seen["headers"] = dict(kwargs.get("headers") or {})
        seen["json"] = kwargs.get("json")
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _capture)

    status = await audit_export._post("https://collector.example/x", {"rows": []})

    assert status == 200
    assert seen["headers"]["Authorization"] == "Bearer collector-secret"  # type: ignore[index]


async def test_no_token_configured_means_no_authorization_header(monkeypatch) -> None:
    import httpx

    from tasks import audit_export

    monkeypatch.delenv("AUDIT_EXPORT_TOKEN", raising=False)
    seen: dict[str, object] = {}

    async def _capture(self, url, **kwargs):  # noqa: ANN001
        seen["headers"] = dict(kwargs.get("headers") or {})
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _capture)

    await audit_export._post("https://collector.example/x", {"rows": []})

    assert "Authorization" not in seen["headers"]  # type: ignore[operator]


async def test_a_collector_that_cannot_be_reached_is_a_retryable_failure(
    monkeypatch,
) -> None:
    import httpx

    from tasks import audit_export

    async def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)

    with pytest.raises(audit_export.AuditExportDeliveryError):
        await audit_export._post("https://collector.example/x", {"rows": []})


@pytest.mark.parametrize("status_code", [400, 422, 500, 503])
async def test_a_refused_batch_is_retried_rather_than_skipped(
    monkeypatch, status_code: int
) -> None:
    """Including the 4xx cases, which the ticket webhook treats as permanent.

    The two integrations differ on purpose. A dropped ticket is a
    convenience lost; a dropped audit row is a hole in the record, so this one
    keeps retrying and stalls visibly rather than moving past it.
    """
    import httpx

    from tasks import audit_export

    async def _refuse(*_args, **_kwargs):
        return httpx.Response(status_code)

    monkeypatch.setattr(httpx.AsyncClient, "post", _refuse)

    with pytest.raises(audit_export.AuditExportDeliveryError):
        await audit_export._post("https://collector.example/x", {"rows": []})


def test_the_log_line_names_the_host_and_not_the_path(monkeypatch) -> None:
    """Collector URLs often carry a token in the path."""
    from tasks import audit_export

    assert (
        audit_export._safe_host("https://collector.example/ingest/s3cr3t")
        == "collector.example"
    )
    assert audit_export._safe_host("not a url at all") in {"", "<unparseable>"}


def test_how_far_behind_the_export_is(session, destination, monkeypatch) -> None:
    """For the operator reading the cursor row, not for the loop."""
    from services.audit_export_service import pending_count

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    at = _own_window()
    cursor = _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=4, at=at)

    assert pending_count(session, cursor=cursor) >= 4


def test_whether_a_destination_is_configured_is_readable(monkeypatch) -> None:
    from services.audit_export_service import is_configured

    monkeypatch.delenv("AUDIT_EXPORT_URL", raising=False)
    assert is_configured() is False

    monkeypatch.setenv("AUDIT_EXPORT_URL", "https://collector.example/x")
    assert is_configured() is True


def test_an_empty_batch_still_records_that_the_export_ran(
    session, destination
) -> None:
    """Otherwise an operator cannot tell a healthy idle export from a stopped
    one: both leave last_run_at where it was."""
    from services.audit_export_service import (
        Batch,
        advance_cursor,
        get_or_create_cursor,
    )

    cursor = get_or_create_cursor(session, destination=destination)
    assert cursor.last_run_at is None

    advance_cursor(
        session, cursor=cursor, batch=Batch(rows=[], last_created_at=None, last_id=None)
    )

    session.refresh(cursor)
    assert cursor.last_run_at is not None
    assert cursor.last_id is None


# ---------------------------------------------------------------------------
# Draining a backlog, one run at a time (#570)
#
# One batch per beat meant a large existing trail took weeks to hand over.
# A run now keeps going until it is caught up or out of time, which makes two
# runs overlapping (a retry landing on the next beat) a real case rather than
# a theoretical one. The lock and the conditional move are asserted
# separately, each with the other out of the way, because either one alone
# would make the overlap test pass.
# ---------------------------------------------------------------------------


def _ids_posted(body: dict) -> list[str]:
    return [row["id"] for row in body["rows"]]


def _configure(monkeypatch, destination: str, *, batch_size: int) -> datetime:
    """Point the task at ``destination`` and confine what it reads to one window.

    A run now drains everything it can see, and the shared database holds
    every audit row the rest of the suite wrote, all of them after a window a
    century back. Reading with the horizon pinned at the window's instant
    keeps a run to the rows this test wrote, which is what lets these tests
    assert exact counts, and keeps a batch size of two from turning into
    thousands of posts on a full CI database.
    """
    from services import audit_export_service
    from tasks import audit_export

    monkeypatch.setenv("AUDIT_EXPORT_URL", destination)
    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", str(batch_size))
    window = _own_window()
    real_collect = audit_export_service.collect_batch

    def _collect_within_window(session, *, cursor, now=None):
        return real_collect(session, cursor=cursor, now=window)

    monkeypatch.setattr(audit_export, "collect_batch", _collect_within_window)
    return window


def test_one_run_drains_more_than_one_batch(session, destination, monkeypatch) -> None:
    from services.audit_export_service import get_or_create_cursor
    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    _cursor_just_before(session, destination=destination, moment=at)
    written = _insert_audit_rows(session, count=5, at=at)
    posts: list[list[str]] = []

    async def _accept(url, body):
        posts.append(_ids_posted(body))
        return 202

    monkeypatch.setattr(audit_export, "_post", _accept)

    result = audit_export._run(None)

    seen = [row_id for batch in posts for row_id in batch]
    # All five, once each, in the order the cursor walks them (one shared
    # timestamp, so by id), in three posts of this one run.
    assert seen == sorted(written, key=uuid.UUID)
    assert [len(batch) for batch in posts] == [2, 2, 1]
    assert result["status"] == "delivered"
    assert result["batches"] == 3
    assert result["exported"] == 5

    cursor = get_or_create_cursor(session, destination=destination)
    session.refresh(cursor)
    assert int(cursor.rows_exported) == len(seen)


def test_a_spent_budget_stops_the_run_after_the_batch_in_hand(
    session, destination, monkeypatch
) -> None:
    """The budget is checked between batches, so a run always makes progress
    and never starts a batch it was out of time for."""
    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=5, at=at)
    posts: list[list[str]] = []

    async def _accept(url, body):
        posts.append(_ids_posted(body))
        return 202

    monkeypatch.setattr(audit_export, "_post", _accept)
    monkeypatch.setattr(audit_export, "_run_budget_seconds", lambda: 0.0)

    result = audit_export._run(None)

    assert len(posts) == 1
    assert result["exported"] == 2
    assert result["pending"] >= 3


def test_the_run_budget_sits_below_the_beat_interval() -> None:
    """A budget at or above the beat would make every run overlap the next."""
    from tasks import audit_export
    from tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["audit-export-every-five-minutes"]
    interval = entry["schedule"].run_every
    assert isinstance(interval, timedelta)
    assert 0 < audit_export._run_budget_seconds() < interval.total_seconds()


def test_a_failed_post_mid_run_keeps_the_batches_already_delivered(
    session, destination, monkeypatch
) -> None:
    """The cursor ends exactly after the last accepted batch: not before it,
    which would re-send what the collector already holds, and not after it,
    which would skip what it never received."""
    from services.audit_export_service import get_or_create_cursor
    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=5, at=at)
    accepted: list[list[str]] = []

    async def _second_fails(url, body):
        if accepted:
            raise audit_export.AuditExportDeliveryError("collector went away")
        accepted.append(_ids_posted(body))
        return 202

    monkeypatch.setattr(audit_export, "_post", _second_fails)

    with pytest.raises(audit_export.AuditExportDeliveryError):
        audit_export._run(None)

    cursor = get_or_create_cursor(session, destination=destination)
    session.refresh(cursor)
    assert str(cursor.last_id) == accepted[0][-1]
    assert int(cursor.rows_exported) == 2


def test_a_failed_post_leaves_a_resumed_position_where_it_was(
    session, destination, monkeypatch
) -> None:
    """The older check starts from a fresh cursor, where "did not move" and
    "was reset to the beginning" look the same. This one starts from a real
    position so the two differ."""
    from services.audit_export_service import get_or_create_cursor
    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    pinned = _cursor_just_before(session, destination=destination, moment=at)
    before = (pinned.last_created_at, pinned.last_id, int(pinned.rows_exported))
    _insert_audit_rows(session, count=3, at=at)

    async def _fails(*_args, **_kwargs):
        raise audit_export.AuditExportDeliveryError("collector is down")

    monkeypatch.setattr(audit_export, "_post", _fails)

    with pytest.raises(audit_export.AuditExportDeliveryError):
        audit_export._run(None)

    cursor = get_or_create_cursor(session, destination=destination)
    session.refresh(cursor)
    assert (cursor.last_created_at, cursor.last_id, int(cursor.rows_exported)) == before


def test_two_overlapping_runs_do_not_send_the_same_batch_twice(
    session, destination, monkeypatch
) -> None:
    """A retry landing on top of the next beat.

    The first run is held inside its post; the second starts meanwhile. Without
    the lock both read the same position and post the same rows.
    """
    import threading

    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    _cursor_just_before(session, destination=destination, moment=at)
    written = set(_insert_audit_rows(session, count=3, at=at))
    posted: list[str] = []
    guard = threading.Lock()
    first_inside = threading.Event()
    release_first = threading.Event()

    async def _slow_then_accept(url, body):
        with guard:
            is_first = not first_inside.is_set()
            first_inside.set()
            posted.extend(_ids_posted(body))
        if is_first:
            assert release_first.wait(timeout=30)
        return 202

    monkeypatch.setattr(audit_export, "_post", _slow_then_accept)

    outcome: dict[str, object] = {}

    def _first_run() -> None:
        try:
            outcome["first"] = audit_export._run(None)
        except BaseException as exc:  # noqa: BLE001
            outcome["first_error"] = exc

    worker = threading.Thread(target=_first_run)
    worker.start()
    try:
        assert first_inside.wait(timeout=30)
        second = audit_export._run(None)
    finally:
        release_first.set()
        worker.join(timeout=60)

    assert "first_error" not in outcome, outcome.get("first_error")
    assert second["status"] == "busy"
    assert second["exported"] == 0
    assert sorted(posted) == sorted(written)

    # And the lock is given back. Asked of the server rather than shown by a
    # third run: a session advisory lock is reentrant, so a third run handed
    # the same pooled connection would take it again even if it leaked.
    class_id, object_id = audit_export._lock_key(destination)
    held = session.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid::bigint = :c AND objid::bigint = :o"
        ),
        {"c": class_id, "o": object_id & 0xFFFFFFFF},
    ).scalar_one()
    session.rollback()
    assert held == 0
    third = audit_export._run(None)
    assert third["status"] == "idle"


def test_nothing_is_held_open_while_the_collector_is_waited_on(
    session, destination, monkeypatch
) -> None:
    """No transaction during the post, on the work session or on the
    connection that holds the lock. A transaction left open across a slow
    collector pins a snapshot for as long as the collector takes."""
    from contextlib import contextmanager

    import core.db
    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=1, at=at)

    real_scope = core.db.sync_session_scope
    captured: dict[str, object] = {}

    @contextmanager
    def _capturing_scope():
        with real_scope() as work:
            captured["session"] = work
            yield work

    monkeypatch.setattr(core.db, "sync_session_scope", _capturing_scope)
    class_id, object_id = audit_export._lock_key(destination)
    work_flags: list[bool] = []
    holder_sets: list[list[tuple[object, object]]] = []

    async def _inspect(url, body):
        work_flags.append(captured["session"].in_transaction())  # type: ignore[attr-defined]
        holders = session.execute(
            text(
                "SELECT a.state, a.xact_start FROM pg_locks l "
                "JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.locktype = 'advisory' AND l.granted "
                "AND l.classid::bigint = :c AND l.objid::bigint = :o"
            ),
            {"c": class_id, "o": object_id & 0xFFFFFFFF},
        ).all()
        session.rollback()
        holder_sets.append([tuple(row) for row in holders])
        return 202

    monkeypatch.setattr(audit_export, "_post", _inspect)

    audit_export._run(None)

    assert work_flags, "the post never ran"
    assert not any(work_flags)
    for holders in holder_sets:
        # Exactly one backend holds the destination's lock, and it is idle
        # with no transaction, rather than "idle in transaction".
        assert len(holders) == 1, holders
        state, xact_start = holders[0]
        assert state == "idle"
        assert xact_start is None


def test_a_batch_read_from_a_position_that_has_since_moved_does_not_move_it(
    session, destination, monkeypatch
) -> None:
    """The conditional move, with the lock out of the picture.

    Two batches read from the same position stand for two runs that both got
    past the lock somehow. The second to advance must leave the cursor where
    the first put it, rather than writing its own end over it.
    """
    from dataclasses import replace

    from services.audit_export_service import advance_cursor, collect_batch

    monkeypatch.setenv("AUDIT_EXPORT_LAG_SECONDS", "0")
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_SIZE", "2")
    at = _own_window()
    cursor = _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=3, at=at)

    first = collect_batch(session, cursor=cursor)
    stale = collect_batch(session, cursor=cursor)
    assert advance_cursor(session, cursor=cursor, batch=first) is True
    position = (cursor.last_created_at, cursor.last_id, int(cursor.rows_exported))

    # A stale batch that ends somewhere else entirely, so an unconditional
    # move would show up as a change of position.
    elsewhere = replace(stale, last_id=uuid.UUID(int=(1 << 128) - 1))
    assert advance_cursor(session, cursor=cursor, batch=elsewhere) is False

    session.refresh(cursor)
    assert (cursor.last_created_at, cursor.last_id, int(cursor.rows_exported)) == position


def test_a_run_that_finds_the_position_moved_stops_instead_of_carrying_on(
    session, destination, monkeypatch
) -> None:
    """What the task does with a refused move: stop, report it, and leave the
    position to whoever moved it."""
    from services.audit_export_service import get_or_create_cursor
    from tasks import audit_export

    at = _configure(monkeypatch, destination, batch_size=2)
    _cursor_just_before(session, destination=destination, moment=at)
    _insert_audit_rows(session, count=5, at=at)
    far = datetime.now(tz=UTC) + timedelta(days=1)
    posts: list[list[str]] = []

    async def _accept_and_move(url, body):
        posts.append(_ids_posted(body))
        # Somebody else moves the cursor while this batch is in flight.
        session.execute(
            text(
                "UPDATE audit_export_cursors SET last_created_at = :at, "
                "last_id = :id WHERE destination = :d"
            ),
            {"at": far, "id": str(uuid.UUID(int=7)), "d": destination},
        )
        session.commit()
        return 202

    monkeypatch.setattr(audit_export, "_post", _accept_and_move)

    result = audit_export._run(None)

    assert result["status"] == "superseded"
    assert len(posts) == 1
    cursor = get_or_create_cursor(session, destination=destination)
    session.refresh(cursor)
    assert cursor.last_id == uuid.UUID(int=7)


def test_lock_keys_are_int4_pairs_that_differ_by_destination() -> None:
    from tasks import audit_export

    keys = {
        audit_export._lock_key(f"https://collector.example/{n}") for n in range(50)
    }
    assert len(keys) == 50
    for class_id, object_id in keys:
        assert class_id == audit_export._LOCK_CLASS_ID
        assert -(1 << 31) <= object_id < (1 << 31)


def test_a_failed_unlock_does_not_replace_what_the_run_returned() -> None:
    """The lock connection can break mid-run; the server has dropped the lock
    by then, and the run's own outcome is what the caller needs to see."""
    from tasks import audit_export

    calls: list[str] = []

    class _Result:
        def scalar(self) -> bool:
            return True

    class _Conn:
        def execution_options(self, **_kwargs):
            return self

        def execute(self, stmt):
            name = str(stmt)
            calls.append(name)
            if "pg_advisory_unlock" in name:
                raise RuntimeError("server closed the connection")
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    class _Engine:
        def connect(self):
            return _Conn()

    with audit_export._destination_lock(_Engine(), "https://collector.example/x") as got:  # type: ignore[arg-type]
        outcome = got

    assert outcome is True
    # The unlock was attempted, and its failure stayed inside the helper.
    assert any("pg_advisory_unlock" in call for call in calls)
