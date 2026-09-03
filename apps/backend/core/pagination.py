# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Shared pagination: the bounds every endpoint uses, and the shape new ones take.

``page`` had a lower bound (``ge=1``) on every listing endpoint and no upper
bound on any of them. Python integers are unbounded, so a page number of any
size parsed fine, got multiplied into an OFFSET, and reached asyncpg as a
value outside int64:

    asyncpg.exceptions.DataError: invalid input for query argument $2:
    423599349510580893366758764207878963180 (value out of int64 range)

which surfaced as a 500. Schema-based fuzzing found it on two endpoints;
every listing endpoint had it.

PAGE_MAX is the cap. It is far above any page a real dataset reaches and far
below the point where ``(page - 1) * page_size`` threatens int64, so the
failure mode becomes a 422 naming the parameter instead of a server error.
Deep offsets are also slow, which is a second reason not to leave this open.

``PageParams`` and ``Page`` below are the second half: one query-parameter and
one response shape for endpoints written from here on, so the three spellings
already in the API stop multiplying. See the API overview's pagination section
for what those three are and why the existing ones are left alone.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")

# (PAGE_MAX - 1) * 200 (the largest page_size any endpoint allows) is ~2e8,
# comfortably inside int64.
PAGE_MAX = 1_000_000

#: Default rows per page for endpoints written against ``PageParams``.
DEFAULT_PAGE_SIZE = 50

#: Largest page a caller may request from those endpoints.
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class PageParams:
    """The pagination query parameters new list endpoints take.

    Three spellings are in the API today: ``limit``/``offset`` on eight
    endpoints, ``page``/``page_size`` on fifteen, and ``page``/``size`` on six,
    with the page-size default and maximum varying inside each group. Nothing
    made a new endpoint match an existing one, so each author picked something
    locally reasonable and a client ended up needing a branch per shape.

    This is the one shape from here on. It is deliberately not a compatibility
    layer over the other two: accepting both spellings everywhere would promise
    more API stability than a pre-1.0 project offers (``SECURITY.md`` says a
    minor release may change the HTTP API) and leave two code paths to keep
    correct. The existing endpoints keep their spelling until 1.0.0 converges
    them in one breaking change, where callers expect one.

    ``page`` is 1-based, matching every numbered endpoint already shipped.

    Use as a FastAPI dependency so the parameters, their bounds and their
    documentation come from one place::

        @router.get("/things")
        async def list_things(page: PageParams = Depends()) -> Page[Thing]:
            rows, total = await service.list_things(
                limit=page.limit, offset=page.offset
            )
            return Page.of(rows, total=total, params=page)
    """

    page: int = Query(
        default=1,
        ge=1,
        le=PAGE_MAX,
        description="1-based page number.",
    )
    page_size: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page, at most {MAX_PAGE_SIZE}.",
    )

    @property
    def offset(self) -> int:
        """Rows to skip, for the SQL layer.

        Bounded by construction: ``page`` is capped at ``PAGE_MAX`` and
        ``page_size`` at ``MAX_PAGE_SIZE``, so this cannot reach the int64
        overflow that made an unbounded page number a 500.
        """
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Rows to fetch, for the SQL layer. An alias, named for the caller."""
        return self.page_size


class Page(BaseModel, Generic[ItemT]):
    """The envelope those endpoints return.

    Field names match the fifteen endpoints already using ``page_size``, which
    is also what the six ``page``/``size`` endpoints put in their responses, so
    a client reading responses sees one shape even while the query parameters
    still differ.

    ``total`` is the full row count before pagination, not the length of
    ``items``. A caller computes the last page from it, so it must be the
    unpaginated count even when that costs a second query.
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[ItemT] = Field(description="This page's rows, in sort order.")
    total: int = Field(ge=0, description="Rows matching the query, before paging.")
    page: int = Field(ge=1, description="1-based page number, echoed back.")
    page_size: int = Field(ge=1, description="Rows per page, echoed back.")

    @classmethod
    def of(cls, items: Sequence[ItemT], *, total: int, params: PageParams) -> "Page[ItemT]":
        """Build a page, echoing the parameters the caller sent.

        Echoing rather than recomputing: a caller paging forward compares what
        it asked for against what it got, and a silently clamped value would
        make the two disagree without saying so.
        """
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            page_size=params.page_size,
        )


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PAGE_MAX",
    "Page",
    "PageParams",
]
