# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
SavedSearch — a named search a user parked for later (S3).

Why a table rather than localStorage: the point of saving a search is that you
come back to it, and "come back" includes from another machine and from the
dashboard. A browser-local list would silently be a different list per device.

Scope is the user, not the team. A saved search is a bookmark, and the results
it produces are always re-run through the caller's own team scope at query
time — so sharing the row would not share the results anyway, it would only
share the filter text. Team-visible saved searches are a separate feature with
a separate permission question; this table deliberately does not prejudge it.

``params`` is opaque JSON on purpose. It is whatever query string the search
page had when the user pressed save, replayed verbatim when they open it. The
server never interprets it — validating it here would mean this table knowing
every filter the search page will ever grow, and going stale the first time one
is added.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class SavedSearch(Base):
    """One user's named search."""

    __tablename__ = "saved_searches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Which search surface the params belong to (``components``,
    #: ``vulnerabilities``, ``projects``, ``licenses``). Stored so the UI can
    #: route a saved row to the right tab without parsing ``params``.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The saved query string, replayed verbatim. Opaque to the server.
    params: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        # Names are how the user tells two saved searches apart, so a duplicate
        # is a mistake rather than a second entry. The constraint also gives the
        # delete-then-recreate cycle a defined outcome to test.
        UniqueConstraint("user_id", "name", name="uq_saved_searches_user_name"),
        # Every listing is "mine, newest first"; Postgres does not index a FK
        # for us.
        Index("ix_saved_searches_user_created", "user_id", "created_at"),
    )


__all__ = ["SavedSearch"]
