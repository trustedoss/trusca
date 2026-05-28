"""
Pydantic schemas for the in-app notification center — Chore A2.

Public shapes (frozen contract — frontend depends on these byte-for-byte):

  - ``NotificationKind``           — Literal of the closed kind set.
  - ``NotificationOut``            — single row in list responses.
  - ``NotificationListResponse``   — paginated list wrapper with
                                     ``unread_count`` for badge rendering.
  - ``NotificationPrefsOut``       — singleton prefs row (read).
  - ``NotificationPrefsIn``        — singleton prefs row (PUT body — full row,
                                     not partial; the API is PUT not PATCH).
  - ``UnreadCountOut``             — minimal {count} payload for the bell badge.

Design notes:
  - ``NotificationOut.model_config = ConfigDict(from_attributes=True)`` so we
    can validate straight from a SQLAlchemy ORM row.
  - ``NotificationPrefsIn`` mirrors the PUT contract: every channel must be
    supplied (no partial updates). The frontend always sends the full row;
    splitting into a PATCH would invite "ghost write" bugs where a forgotten
    field silently reverts to the server default.
  - ``in_app_enabled`` is exposed in the ``Out`` shape so the UI can render
    a disabled toggle. It is also accepted in the ``In`` shape so the user
    can opt out of in-app delivery entirely; the dispatcher fan-out
    respects this when it decides whether to write a notification row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Closed kind set — string-typed so OpenAPI renders a clean enum without
# needing the Postgres ENUM machinery on the wire.
NotificationKind = Literal[
    "scan_completed",
    "scan_failed",
    "cve_detected",
    "license_violation",
    "approval_pending",
    "policy_gate_failed",
]


class NotificationOut(BaseModel):
    """One row in the notifications list response.

    Frozen contract — the SPA's notification drawer depends on every field
    name and shape. Add new optional fields as nullable; do not rename.
    """

    id: UUID
    kind: NotificationKind
    title: str
    body: str
    link: str | None
    target_table: str | None
    target_id: UUID | None
    read_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Paginated list of notifications + the bell badge count.

    ``unread_count`` is the global unread count for the caller (NOT the
    unread count within this page) — the SPA renders it as a badge in the
    nav, independent of the drawer's pagination state.
    """

    items: list[NotificationOut]
    total: int
    unread_count: int
    page: int
    page_size: int


class UnreadCountOut(BaseModel):
    """Minimal payload for the bell badge poll."""

    count: int


class NotificationPrefsOut(BaseModel):
    """Per-user notification channel toggles (read shape)."""

    email_enabled: bool
    slack_enabled: bool
    teams_enabled: bool
    in_app_enabled: bool

    model_config = ConfigDict(from_attributes=True)


class NotificationPrefsIn(BaseModel):
    """Per-user notification channel toggles (PUT body — full row).

    The endpoint is PUT, not PATCH: every channel must be supplied. Sending
    a partial body would be a 422 (Pydantic enforces required fields).
    """

    email_enabled: bool
    slack_enabled: bool
    teams_enabled: bool
    in_app_enabled: bool


__all__ = [
    "NotificationKind",
    "NotificationListResponse",
    "NotificationOut",
    "NotificationPrefsIn",
    "NotificationPrefsOut",
    "UnreadCountOut",
]
