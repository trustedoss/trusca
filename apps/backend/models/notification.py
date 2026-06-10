"""
In-app notification + per-user notification preferences models — Chore A2.

Tables:
  - ``notifications``               — one row per delivered in-app notification
  - ``notification_preferences``    — singleton per user, channel toggles

Conventions (CLAUDE.md core rules + neighboring model files):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for every timestamp.
  - Closed enum (``Notification.kind``) uses a native Postgres ENUM type
    ``notification_kind`` so invalid values are rejected at the DB layer and
    new kinds are added via ``ALTER TYPE ADD VALUE`` in a future migration.
  - Every FK column gets an explicit Index — Postgres does not auto-create
    them.
  - No environment access at import time (CLAUDE.md core rule #11).

Design notes:
  - ``Notification.target_table`` / ``target_id`` deliberately do NOT carry a
    foreign key. Notifications can reference any domain row (project / scan /
    component / etc.) and the row may be deleted without cascading: in-app
    notifications are read-once, archive-soon — a stale link in the UI is
    fine, a cascade delete that nukes notification history because a project
    was archived is not.
  - The ``(user_id, read_at NULLS FIRST, created_at DESC)`` composite index
    serves the unread-first list path — Postgres ``NULLS FIRST`` puts unread
    rows ahead of read rows in the same scan.
  - The ``(user_id, created_at DESC)`` index serves the plain "all my
    notifications" path. Both indexes are bounded by ``user_id`` so the query
    never sees another user's rows.
  - ``NotificationPreferences.user_id`` is both the PK and the FK to
    ``users.id``. There is at most one prefs row per user; the service layer
    is responsible for the upsert (``get_or_create_prefs``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

# Closed kind set — encoded as a Postgres native ENUM. The migration owns
# CREATE TYPE; the model binds with ``create_type=False`` so SQLAlchemy never
# tries to emit a duplicate CREATE TYPE during metadata creation.
NOTIFICATION_KIND_VALUES = (
    "scan_completed",
    "scan_failed",
    "cve_detected",
    "license_violation",
    "approval_pending",
    "policy_gate_failed",
    # H-5: the external-dispatch catalog emits ``approval_state_changed`` when an
    # approval is disposed; the requester also gets an in-app row of this kind,
    # so it must be a valid in-app notification_kind (migration 0030).
    "approval_state_changed",
)


def _kind_enum() -> PG_ENUM:
    return PG_ENUM(
        *NOTIFICATION_KIND_VALUES,
        name="notification_kind",
        create_type=False,
    )


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


class Notification(Base):
    """One in-app notification delivered to a single user.

    ``read_at IS NULL`` is the canonical "unread" predicate. We never
    hard-delete read rows — retention is handled separately by a future
    Celery sweeper. The user's UI shows the most recent N rows; older rows
    stay queryable for audit.

    ``target_table`` / ``target_id`` are intentionally untyped string + uuid:
    notifications point at any domain row and we do NOT want a CASCADE that
    would erase notification history when (e.g.) a project is archived.
    Stale links in the UI are tolerated; the SPA renders ``link`` as-is.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(_kind_enum(), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(String(1024), nullable=False)
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_table: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID_PK, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        # Hot path: "give me my unread notifications, newest first". Postgres
        # uses ``NULLS FIRST`` to keep unread rows (read_at IS NULL) ahead of
        # read rows in the same index scan.
        Index(
            "ix_notifications_user_unread_created",
            "user_id",
            "read_at",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        # Plain list: "all my notifications, newest first".
        Index(
            "ix_notifications_user_created",
            "user_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )


# ---------------------------------------------------------------------------
# NotificationPreferences
# ---------------------------------------------------------------------------


class NotificationPreferences(Base):
    """Per-user channel toggles for notification delivery.

    Singleton row keyed off ``user_id`` (also the FK and the PK). The service
    layer's ``get_or_create_prefs`` creates a default row on first read so
    callers never see a missing prefs row.

    Defaults (matching the contract in ``docs/chore-backlog.md`` A2):
      - ``email_enabled``  = True  (existing dispatcher behaviour pre-A2)
      - ``slack_enabled``  = False (off by default — only fires when a Slack
                                    webhook is configured at the deployment
                                    level AND the user has opted in)
      - ``teams_enabled``  = False (same rationale as slack)
      - ``in_app_enabled`` = True  (the whole point of A2 is in-app)
    """

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    slack_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    teams_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    in_app_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )


__all__ = [
    "NOTIFICATION_KIND_VALUES",
    "Notification",
    "NotificationPreferences",
]
