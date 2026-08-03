"""
Service-layer tests for ``services.notification_service`` — Chore A2.

Drives the pure async service against a live Postgres (``DATABASE_URL``) so
the SQLAlchemy listener fires and the service's INSERT/UPDATE statements
hit the real schema. Mirrors the shape of
``tests/unit/services/test_api_key_service.py``.

Coverage:
  - ``list_notifications``: empty, unread filter, total + unread_count,
    pagination clamp, ordering newest-first.
  - ``count_unread``: zero / mixed / all-read.
  - ``mark_read``: idempotent, cross-user existence-hide raises NotFound.
  - ``mark_all_read``: returns rowcount, affects only the caller's rows,
    second call is a no-op.
  - ``get_or_create_prefs``: defaults on first read, returns existing on
    second read.
  - ``update_prefs``: round-trips all four toggles, creates the row when
    missing.
  - ``create_notification``: round-trips required + optional fields,
    truncates oversized title/body without crashing.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip notification_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; notification_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# create_notification + list_notifications
# ---------------------------------------------------------------------------


async def test_list_notifications_empty_returns_zero_and_unread_zero(
    db_session: AsyncSession,
) -> None:
    from services.notification_service import list_notifications

    user = await make_user(db_session)
    rows, total, unread = await list_notifications(db_session, user_id=user.id)
    assert rows == []
    assert total == 0
    assert unread == 0


async def test_create_notification_round_trips_all_fields(
    db_session: AsyncSession,
) -> None:
    from services.notification_service import create_notification, list_notifications

    user = await make_user(db_session)
    target = uuid.uuid4()
    row = await create_notification(
        db_session,
        user_id=user.id,
        kind="scan_completed",
        title="Scan complete",
        body="Project foo finished scanning.",
        link="/projects/abc/scans/xyz",
        target_table="scans",
        target_id=target,
    )
    assert row.id is not None
    assert row.user_id == user.id
    assert row.kind == "scan_completed"
    assert row.title == "Scan complete"
    assert row.body == "Project foo finished scanning."
    assert row.link == "/projects/abc/scans/xyz"
    assert row.target_table == "scans"
    assert row.target_id == target
    assert row.read_at is None
    assert row.created_at is not None

    rows, total, unread = await list_notifications(db_session, user_id=user.id)
    assert total == 1
    assert unread == 1
    assert rows[0].id == row.id


async def test_create_notification_truncates_oversized_strings(
    db_session: AsyncSession,
) -> None:
    """Defensive truncation must keep the row under the column caps without raising."""
    from services.notification_service import create_notification

    user = await make_user(db_session)
    big_title = "A" * 1000  # exceeds 256
    big_body = "B" * 5000  # exceeds 1024
    # Marathon bundle 4 (S / L1): the link validator now requires a
    # same-origin path. Use a real ``/...`` shape so the truncation
    # branch is exercised; bare ``"C" * N`` would be rejected as
    # off-origin and stored as NULL.
    big_link = "/projects/" + ("c" * 1000)  # exceeds 512 after truncation
    row = await create_notification(
        db_session,
        user_id=user.id,
        kind="cve_detected",
        title=big_title,
        body=big_body,
        link=big_link,
    )
    assert len(row.title) <= 256
    assert len(row.body) <= 1024
    assert row.link is not None and len(row.link) <= 512


async def test_list_notifications_orders_newest_first(
    db_session: AsyncSession,
) -> None:
    from services.notification_service import create_notification, list_notifications

    user = await make_user(db_session)
    n1 = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="t1", body="b1"
    )
    n2 = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="t2", body="b2"
    )
    n3 = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="t3", body="b3"
    )

    rows, total, _unread = await list_notifications(db_session, user_id=user.id)
    assert total == 3
    # Newest first: created_at DESC, id DESC tiebreaker.
    ids = [r.id for r in rows]
    assert ids == [n3.id, n2.id, n1.id]


async def test_list_notifications_unread_only_filter(db_session: AsyncSession) -> None:
    from services.notification_service import (
        create_notification,
        list_notifications,
        mark_read,
    )

    user = await make_user(db_session)
    n_read = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="r", body="r"
    )
    n_unread = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="u", body="u"
    )
    await mark_read(db_session, user_id=user.id, notification_id=n_read.id)

    # unread_only=True
    rows, total, unread = await list_notifications(
        db_session, user_id=user.id, unread_only=True
    )
    assert total == 1
    assert unread == 1
    assert [r.id for r in rows] == [n_unread.id]

    # unread_only=False — total counts all rows; unread_count is global.
    rows_all, total_all, unread_all = await list_notifications(
        db_session, user_id=user.id, unread_only=False
    )
    assert total_all == 2
    assert unread_all == 1
    assert {r.id for r in rows_all} == {n_read.id, n_unread.id}


async def test_list_notifications_pagination_clamps_extreme_values(
    db_session: AsyncSession,
) -> None:
    """page<1 clamps to 1; page_size>200 clamps to 200; <1 clamps to 1."""
    from services.notification_service import list_notifications

    user = await make_user(db_session)
    rows, _t, _u = await list_notifications(
        db_session, user_id=user.id, page=-5, page_size=99999
    )
    assert rows == []  # empty user, but no crash


async def test_list_notifications_isolates_users(db_session: AsyncSession) -> None:
    """RBAC: one user must not see another user's rows via list_notifications."""
    from services.notification_service import create_notification, list_notifications

    alice = await make_user(db_session)
    bob = await make_user(db_session)
    await create_notification(
        db_session, user_id=alice.id, kind="scan_completed", title="a", body="a"
    )
    await create_notification(
        db_session, user_id=bob.id, kind="scan_completed", title="b", body="b"
    )

    alice_rows, alice_total, _ = await list_notifications(db_session, user_id=alice.id)
    bob_rows, bob_total, _ = await list_notifications(db_session, user_id=bob.id)
    assert alice_total == 1
    assert bob_total == 1
    assert alice_rows[0].title == "a"
    assert bob_rows[0].title == "b"


# ---------------------------------------------------------------------------
# count_unread
# ---------------------------------------------------------------------------


async def test_count_unread_zero_for_new_user(db_session: AsyncSession) -> None:
    from services.notification_service import count_unread

    user = await make_user(db_session)
    assert await count_unread(db_session, user_id=user.id) == 0


async def test_count_unread_after_mixed_state(db_session: AsyncSession) -> None:
    from services.notification_service import (
        count_unread,
        create_notification,
        mark_read,
    )

    user = await make_user(db_session)
    a = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="a", body="a"
    )
    await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="b", body="b"
    )
    await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="c", body="c"
    )
    await mark_read(db_session, user_id=user.id, notification_id=a.id)

    assert await count_unread(db_session, user_id=user.id) == 2


# ---------------------------------------------------------------------------
# mark_read
# ---------------------------------------------------------------------------


async def test_mark_read_flips_read_at(db_session: AsyncSession) -> None:
    from services.notification_service import create_notification, mark_read

    user = await make_user(db_session)
    n = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="x", body="x"
    )
    assert n.read_at is None

    after = await mark_read(db_session, user_id=user.id, notification_id=n.id)
    assert after.id == n.id
    assert after.read_at is not None


async def test_mark_read_idempotent_preserves_original_timestamp(
    db_session: AsyncSession,
) -> None:
    """A second mark_read call must not advance read_at."""
    from services.notification_service import create_notification, mark_read

    user = await make_user(db_session)
    n = await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="x", body="x"
    )
    first = await mark_read(db_session, user_id=user.id, notification_id=n.id)
    first_ts = first.read_at
    assert first_ts is not None

    second = await mark_read(db_session, user_id=user.id, notification_id=n.id)
    assert second.read_at == first_ts


async def test_mark_read_other_users_row_raises_not_found(
    db_session: AsyncSession,
) -> None:
    """RBAC: existence-hide on cross-user mark_read attempts."""
    from services.notification_service import (
        NotificationNotFound,
        create_notification,
        mark_read,
    )

    alice = await make_user(db_session)
    bob = await make_user(db_session)
    n = await create_notification(
        db_session, user_id=alice.id, kind="scan_completed", title="x", body="x"
    )

    with pytest.raises(NotificationNotFound):
        await mark_read(db_session, user_id=bob.id, notification_id=n.id)

    # Verify alice's row is still unread.
    fresh = await mark_read(db_session, user_id=alice.id, notification_id=n.id)
    assert fresh.read_at is not None


async def test_mark_read_unknown_id_raises_not_found(db_session: AsyncSession) -> None:
    from services.notification_service import NotificationNotFound, mark_read

    user = await make_user(db_session)
    with pytest.raises(NotificationNotFound):
        await mark_read(db_session, user_id=user.id, notification_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# mark_all_read
# ---------------------------------------------------------------------------


async def test_mark_all_read_returns_rowcount_and_flips_only_callers_rows(
    db_session: AsyncSession,
) -> None:
    from services.notification_service import (
        count_unread,
        create_notification,
        mark_all_read,
    )

    alice = await make_user(db_session)
    bob = await make_user(db_session)
    await create_notification(
        db_session, user_id=alice.id, kind="scan_completed", title="a1", body="a1"
    )
    await create_notification(
        db_session, user_id=alice.id, kind="scan_completed", title="a2", body="a2"
    )
    await create_notification(
        db_session, user_id=bob.id, kind="scan_completed", title="b1", body="b1"
    )

    affected = await mark_all_read(db_session, user_id=alice.id)
    assert affected == 2
    assert await count_unread(db_session, user_id=alice.id) == 0
    # Bob's row is untouched.
    assert await count_unread(db_session, user_id=bob.id) == 1


async def test_mark_all_read_second_call_returns_zero(
    db_session: AsyncSession,
) -> None:
    from services.notification_service import create_notification, mark_all_read

    user = await make_user(db_session)
    await create_notification(
        db_session, user_id=user.id, kind="scan_completed", title="a", body="a"
    )
    first = await mark_all_read(db_session, user_id=user.id)
    second = await mark_all_read(db_session, user_id=user.id)
    assert first == 1
    assert second == 0


# ---------------------------------------------------------------------------
# get_or_create_prefs / update_prefs
# ---------------------------------------------------------------------------


async def test_get_or_create_prefs_first_call_returns_defaults(
    db_session: AsyncSession,
) -> None:
    from services.notification_service import get_or_create_prefs

    user = await make_user(db_session)
    prefs = await get_or_create_prefs(db_session, user_id=user.id)
    assert prefs.user_id == user.id
    assert prefs.email_enabled is True
    assert prefs.slack_enabled is False
    assert prefs.teams_enabled is False
    assert prefs.in_app_enabled is True


async def test_get_or_create_prefs_second_call_returns_existing(
    db_session: AsyncSession,
) -> None:
    """Second call must NOT re-insert (PK collision would raise)."""
    from services.notification_service import get_or_create_prefs

    user = await make_user(db_session)
    p1 = await get_or_create_prefs(db_session, user_id=user.id)
    p2 = await get_or_create_prefs(db_session, user_id=user.id)
    assert p1.user_id == p2.user_id


async def test_update_prefs_round_trips_all_toggles(db_session: AsyncSession) -> None:
    from services.notification_service import get_or_create_prefs, update_prefs

    user = await make_user(db_session)
    await get_or_create_prefs(db_session, user_id=user.id)

    after = await update_prefs(
        db_session,
        user_id=user.id,
        email_enabled=False,
        slack_enabled=True,
        teams_enabled=True,
        in_app_enabled=False,
    )
    assert after.email_enabled is False
    assert after.slack_enabled is True
    assert after.teams_enabled is True
    assert after.in_app_enabled is False

    # Re-read confirms persistence.
    re_read = await get_or_create_prefs(db_session, user_id=user.id)
    assert re_read.email_enabled is False
    assert re_read.in_app_enabled is False


async def test_update_prefs_creates_row_when_missing(db_session: AsyncSession) -> None:
    """Calling update_prefs without a prior get_or_create must succeed."""
    from services.notification_service import get_or_create_prefs, update_prefs

    user = await make_user(db_session)
    after = await update_prefs(
        db_session,
        user_id=user.id,
        email_enabled=False,
        slack_enabled=False,
        teams_enabled=False,
        in_app_enabled=True,
    )
    assert after.user_id == user.id
    assert after.email_enabled is False
    assert after.in_app_enabled is True

    # Confirms the same row is found by re-read.
    fresh = await get_or_create_prefs(db_session, user_id=user.id)
    assert fresh.email_enabled is False


async def test_update_prefs_isolates_users(db_session: AsyncSession) -> None:
    """RBAC: one user's update must not touch another user's prefs row."""
    from services.notification_service import get_or_create_prefs, update_prefs

    alice = await make_user(db_session)
    bob = await make_user(db_session)
    await get_or_create_prefs(db_session, user_id=alice.id)
    await get_or_create_prefs(db_session, user_id=bob.id)

    await update_prefs(
        db_session,
        user_id=alice.id,
        email_enabled=False,
        slack_enabled=False,
        teams_enabled=False,
        in_app_enabled=False,
    )

    bob_prefs = await get_or_create_prefs(db_session, user_id=bob.id)
    assert bob_prefs.email_enabled is True
    assert bob_prefs.in_app_enabled is True


# ---------------------------------------------------------------------------
# prefs_to_dict — pure helper
# ---------------------------------------------------------------------------


async def test_prefs_to_dict_returns_stable_shape(db_session: AsyncSession) -> None:
    from services.notification_service import get_or_create_prefs, prefs_to_dict

    user = await make_user(db_session)
    prefs = await get_or_create_prefs(db_session, user_id=user.id)
    out = prefs_to_dict(prefs)
    assert out == {
        "email_enabled": True,
        "slack_enabled": False,
        "teams_enabled": False,
        "in_app_enabled": True,
    }


# ---------------------------------------------------------------------------
# Sync helpers — exercised by the Celery dispatcher fan-out
# ---------------------------------------------------------------------------


async def test_sync_helpers_round_trip_in_sync_session(
    db_session: AsyncSession,
) -> None:
    """``get_prefs_sync`` + ``create_notification_sync`` mirror the async
    helpers and run inside a sync ``Session``. We seed an async user, then
    drop down to sync to invoke the Celery-side entry points.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from core.config import database_url_sync
    from services.notification_service import (
        create_notification_sync,
        get_prefs_sync,
    )

    user = await make_user(db_session)

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    try:
        with Session(engine) as session:
            prefs = get_prefs_sync(session, user_id=user.id)
            assert prefs.user_id == user.id
            assert prefs.email_enabled is True
            assert prefs.in_app_enabled is True

            # Second call must NOT re-insert.
            prefs2 = get_prefs_sync(session, user_id=user.id)
            assert prefs2.user_id == user.id

            row = create_notification_sync(
                session,
                user_id=user.id,
                kind="scan_completed",
                title="sync-title",
                body="sync-body",
                link="/x/y",
                target_table="projects",
                target_id=uuid.uuid4(),
            )
            assert row.user_id == user.id
            assert row.kind == "scan_completed"
            assert row.title == "sync-title"
            assert row.read_at is None
    finally:
        engine.dispose()
