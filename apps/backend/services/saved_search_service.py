# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Saved searches — a user's parked search filters (S3).

Every read and write is keyed on ``user_id``. A row that belongs to someone
else answers 404, not 403: the two are deliberately indistinguishable so that
probing an id teaches nothing about whether it exists. Same existence-hiding
contract the notification service applies.

The ``params`` blob is opaque. It is whatever query string the search page
carried when the user pressed save, replayed verbatim when they open it. The
server does not interpret it — validating it here would mean this module
knowing every filter the page will ever grow, and going stale the first time
one is added.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from models import SavedSearch
from schemas.saved_search import SavedSearchPublic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger("saved_search.service")

#: How many a single user may keep. A cap exists because the dashboard panel
#: renders the whole list, and an unbounded list is an unbounded panel.
MAX_PER_USER = 20

NAME_MAX_LEN = 60

ALLOWED_KINDS: frozenset[str] = frozenset(
    {"projects", "components", "vulnerabilities", "licenses"}
)


class SavedSearchError(Exception):
    """Base class the router maps to a problem+json."""

    status_code = 400
    title = "Saved search error"


class SavedSearchNotFound(SavedSearchError):
    """No such saved search — or it belongs to someone else."""

    status_code = 404
    title = "Saved search not found"


class SavedSearchLimitReached(SavedSearchError):
    """The user already holds :data:`MAX_PER_USER`."""

    status_code = 422
    title = "Saved search limit reached"


class SavedSearchNameTaken(SavedSearchError):
    """The user already has a saved search under this name.

    409 rather than 422: the request is well-formed, it conflicts with state
    the user can see and resolve (rename, or delete the existing one).
    """

    status_code = 409
    title = "Saved search name already used"


class SavedSearchInvalid(SavedSearchError):
    """Name or kind is unusable."""

    status_code = 422
    title = "Invalid saved search"


def _to_public(row: SavedSearch) -> SavedSearchPublic:
    return SavedSearchPublic(
        id=row.id,
        name=row.name,
        kind=row.kind,
        params=row.params or {},
        created_at=row.created_at,
    )


async def list_saved_searches(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[SavedSearchPublic]:
    """The caller's saved searches, newest first."""
    stmt = (
        select(SavedSearch)
        .where(SavedSearch.user_id == user_id)
        .order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [_to_public(row) for row in rows]


async def create_saved_search(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    kind: str,
    params: dict[str, object],
) -> SavedSearchPublic:
    """Save a search for the caller.

    Raises :class:`SavedSearchNameTaken` on a duplicate name rather than
    silently overwriting: the user asked to create, and quietly replacing the
    thing they were about to reuse is the wrong reading of that.
    """
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > NAME_MAX_LEN:
        raise SavedSearchInvalid("name must be 1..60 characters")
    if kind not in ALLOWED_KINDS:
        raise SavedSearchInvalid(f"unknown kind: {kind}")

    count_stmt = (
        select(func.count())
        .select_from(SavedSearch)
        .where(SavedSearch.user_id == user_id)
    )
    if int((await session.execute(count_stmt)).scalar_one()) >= MAX_PER_USER:
        raise SavedSearchLimitReached(
            f"a user may keep at most {MAX_PER_USER} saved searches"
        )

    row = SavedSearch(
        user_id=user_id, name=clean_name, kind=kind, params=params or {}
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        # uq_saved_searches_user_name. Catching the constraint rather than
        # pre-checking keeps two concurrent saves from both passing a SELECT
        # and one of them 500-ing.
        await session.rollback()
        raise SavedSearchNameTaken(clean_name) from exc
    await session.refresh(row)

    log.info(
        "saved_search.created", user_id=str(user_id), kind=kind, name_len=len(clean_name)
    )
    return _to_public(row)


async def delete_saved_search(
    session: AsyncSession, *, user_id: uuid.UUID, saved_search_id: uuid.UUID
) -> None:
    """Delete one of the caller's saved searches.

    Raises :class:`SavedSearchNotFound` when it does not exist OR belongs to
    another user — identical 404 for both, so an id probe learns nothing.
    """
    stmt = (
        delete(SavedSearch)
        .where(SavedSearch.id == saved_search_id)
        .where(SavedSearch.user_id == user_id)
        .returning(SavedSearch.id)
    )
    deleted = (await session.execute(stmt)).scalar_one_or_none()
    if deleted is None:
        await session.rollback()
        raise SavedSearchNotFound(str(saved_search_id))
    await session.commit()
    log.info("saved_search.deleted", user_id=str(user_id))


__all__ = [
    "ALLOWED_KINDS",
    "MAX_PER_USER",
    "NAME_MAX_LEN",
    "SavedSearchError",
    "SavedSearchInvalid",
    "SavedSearchLimitReached",
    "SavedSearchNameTaken",
    "SavedSearchNotFound",
    "create_saved_search",
    "delete_saved_search",
    "list_saved_searches",
]
