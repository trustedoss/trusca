"""
In-app notification service — Chore A2.

Pure async DB I/O for the ``/v1/notifications`` and
``/v1/users/me/notification-prefs`` HTTP surfaces. Also exposes a
sync-compatible variant (``create_notification_sync``) used by the
Celery-side dispatcher fan-out, which runs in a sync session scope.

Security contracts:

  - **Tenant isolation** — every read/write is keyed off ``user_id``. The
    service never accepts a "view all" mode. The endpoint layer is a thin
    HTTP adapter; the service is the canonical RBAC site for "this row
    belongs to me".
  - **Existence-hide on cross-user access** — :func:`mark_read` raises
    :class:`NotificationNotFound` whenever the row id does not exist OR
    the row belongs to a different user. The 404 path conveys nothing
    about whether the id is valid.
  - **Idempotent mark-read** — re-marking an already-read row is a no-op
    that returns the row unchanged. The current ``read_at`` timestamp is
    preserved (no clock churn on retries).
  - **Default prefs creation** — :func:`get_or_create_prefs` performs an
    INSERT ... ON CONFLICT DO NOTHING then re-reads the row, so two
    concurrent first-reads cannot both insert. The unique gate is the
    ``user_id`` PK on ``notification_preferences``.

Audit:
  - Mutations on ``notifications`` and ``notification_preferences`` are
    captured by the SQLAlchemy ``before_flush`` listener in
    :mod:`core.audit` (no manual emit). Notifications are domain rows,
    not auth events — the listener picks them up like any other table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from models import Notification, NotificationPreferences

log = structlog.get_logger("notifications.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class NotificationError(Exception):
    """Base class for notification service errors. Carries an HTTP status."""

    status_code: int = 400
    title: str = "Notification Error"


class NotificationNotFound(NotificationError):
    """404 — row does not exist OR belongs to a different user."""

    status_code = 404
    title = "Notification Not Found"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _clamp_page(page: int, page_size: int) -> tuple[int, int]:
    """Mirror the API Key service convention for pagination clamping."""
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    return page, page_size


# ---------------------------------------------------------------------------
# list_notifications
# ---------------------------------------------------------------------------


async def list_notifications(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Notification], int, int]:
    """Return (rows, total, unread_count) for the given user.

    ``total`` is the row count under the current ``unread_only`` filter;
    ``unread_count`` is the user's GLOBAL unread count (independent of the
    page filter) so the SPA can render the bell badge from a single
    response. Two separate counts let the drawer say
    "Showing 50 of 87 unread" while the bell still says "12 unread today".
    """
    page, page_size = _clamp_page(page, page_size)

    base = select(Notification).where(Notification.user_id == user_id)
    count_base = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id)
    )

    if unread_only:
        base = base.where(Notification.read_at.is_(None))
        count_base = count_base.where(Notification.read_at.is_(None))

    total = int((await session.execute(count_base)).scalar_one())

    # Always recompute the global unread_count — the caller may be filtering
    # by ``unread_only=False`` and still want the badge value.
    unread_stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    unread_count = int((await session.execute(unread_stmt)).scalar_one())

    rows_stmt = (
        base.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = list((await session.execute(rows_stmt)).scalars().all())

    return rows, total, unread_count


# ---------------------------------------------------------------------------
# count_unread
# ---------------------------------------------------------------------------


async def count_unread(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Return the unread notification count for the given user."""
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    return int((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# mark_read / mark_all_read
# ---------------------------------------------------------------------------


async def mark_read(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> Notification:
    """Mark a single notification as read. Idempotent.

    Raises :class:`NotificationNotFound` when the row does not exist OR
    belongs to a different user — same response shape so a caller cannot
    probe row ownership across users.
    """
    stmt = select(Notification).where(Notification.id == notification_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or row.user_id != user_id:
        # Existence-hide: identical 404 for "missing" vs "not yours".
        raise NotificationNotFound(f"notification {notification_id} not found")

    if row.read_at is None:
        row.read_at = _now()
        await session.commit()
        await session.refresh(row)
    return row


async def mark_all_read(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> int:
    """Mark every unread notification for the user as read.

    Returns the number of rows actually flipped (already-read rows are
    not touched, so the count never exceeds the user's unread count at
    call time).
    """
    now = _now()
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


_PREFS_DEFAULTS: dict[str, bool] = {
    "email_enabled": True,
    "slack_enabled": False,
    "teams_enabled": False,
    "in_app_enabled": True,
}


async def get_or_create_prefs(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> NotificationPreferences:
    """Return the user's prefs row, inserting defaults on first read.

    Concurrency: two parallel "first read" calls for the same user race
    on the INSERT — we use ``ON CONFLICT (user_id) DO NOTHING`` so the
    loser silently re-reads instead of raising IntegrityError.
    """
    stmt = select(NotificationPreferences).where(
        NotificationPreferences.user_id == user_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row

    insert_stmt = (
        pg_insert(NotificationPreferences)
        .values(user_id=user_id, **_PREFS_DEFAULTS)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    await session.execute(insert_stmt)
    await session.commit()

    row = (await session.execute(stmt)).scalar_one()
    return row


async def update_prefs(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    email_enabled: bool,
    slack_enabled: bool,
    teams_enabled: bool,
    in_app_enabled: bool,
) -> NotificationPreferences:
    """Full-row update of the user's prefs. Creates the row if missing."""
    # Ensure the row exists.
    await get_or_create_prefs(session, user_id=user_id)

    stmt = (
        update(NotificationPreferences)
        .where(NotificationPreferences.user_id == user_id)
        .values(
            email_enabled=email_enabled,
            slack_enabled=slack_enabled,
            teams_enabled=teams_enabled,
            in_app_enabled=in_app_enabled,
            updated_at=_now(),
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(stmt)
    await session.commit()

    row = (
        await session.execute(
            select(NotificationPreferences).where(
                NotificationPreferences.user_id == user_id
            )
        )
    ).scalar_one()
    return row


# ---------------------------------------------------------------------------
# create_notification — async (tests + future async callers)
# ---------------------------------------------------------------------------


async def create_notification(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    target_table: str | None = None,
    target_id: uuid.UUID | None = None,
) -> Notification:
    """Insert an in-app notification row for ``user_id`` and return it.

    The function does NOT consult preferences — the dispatcher fan-out
    (Celery side) is responsible for skipping the call when
    ``in_app_enabled=False``. Keeping the gate in one place avoids a
    "should we have written this row?" decision being smeared across
    multiple call sites.
    """
    row = Notification(
        user_id=user_id,
        kind=kind,
        title=_truncate(title, 256),
        body=_truncate(body, 1024),
        link=_truncate(link, 512) if link is not None else None,
        target_table=target_table,
        target_id=target_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    log.info(
        "notification.created",
        user_id=str(user_id),
        notification_id=str(row.id),
        kind=kind,
    )
    return row


# ---------------------------------------------------------------------------
# Sync variants — used by the Celery dispatcher fan-out
# ---------------------------------------------------------------------------


def get_prefs_sync(
    session: Session,
    *,
    user_id: uuid.UUID,
) -> NotificationPreferences:
    """Sync analogue of :func:`get_or_create_prefs` for Celery callers."""
    stmt = select(NotificationPreferences).where(
        NotificationPreferences.user_id == user_id
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        return row
    insert_stmt = (
        pg_insert(NotificationPreferences)
        .values(user_id=user_id, **_PREFS_DEFAULTS)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    session.execute(insert_stmt)
    session.commit()
    return session.execute(stmt).scalar_one()


def create_notification_sync(
    session: Session,
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    target_table: str | None = None,
    target_id: uuid.UUID | None = None,
) -> Notification:
    """Sync analogue of :func:`create_notification` for Celery callers."""
    row = Notification(
        user_id=user_id,
        kind=kind,
        title=_truncate(title, 256),
        body=_truncate(body, 1024),
        link=_truncate(link, 512) if link is not None else None,
        target_table=target_table,
        target_id=target_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    log.info(
        "notification.created_sync",
        user_id=str(user_id),
        notification_id=str(row.id),
        kind=kind,
    )
    return row


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate(value: str, max_len: int) -> str:
    """Defensive truncation — the DB column has the same cap, but defending
    in code lets us emit a single notification (truncated) rather than a
    failed INSERT that throws away the whole context.
    """
    if len(value) <= max_len:
        return value
    # Reserve 1 char for the ellipsis so we never hit the column cap.
    return value[: max_len - 1] + "…"


def prefs_to_dict(prefs: NotificationPreferences) -> dict[str, Any]:
    """Stable serialization helper used by the API + dispatcher fan-out."""
    return {
        "email_enabled": bool(prefs.email_enabled),
        "slack_enabled": bool(prefs.slack_enabled),
        "teams_enabled": bool(prefs.teams_enabled),
        "in_app_enabled": bool(prefs.in_app_enabled),
    }


__all__ = [
    "NotificationError",
    "NotificationNotFound",
    "count_unread",
    "create_notification",
    "create_notification_sync",
    "get_or_create_prefs",
    "get_prefs_sync",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "prefs_to_dict",
    "update_prefs",
]
