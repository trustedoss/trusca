# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Saved-search endpoints (S3).

Everything here is scoped to the caller. A row belonging to another user
answers 404, never 403 — the two are indistinguishable on purpose so that
probing an id teaches nothing.

Authentication is `get_current_user` rather than `require_role`: saving a
search needs no privilege beyond being signed in, and gating it on a role would
imply a permission that does not exist.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, get_current_user
from schemas.saved_search import (
    SavedSearchCreate,
    SavedSearchListResponse,
    SavedSearchPublic,
)
from services.saved_search_service import (
    MAX_PER_USER,
    SavedSearchError,
    create_saved_search,
    delete_saved_search,
    list_saved_searches,
)

router = APIRouter(prefix="/v1/saved-searches", tags=["saved-searches"])
log = structlog.get_logger("saved_search.api")


def _problem_for(request: Request, exc: SavedSearchError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


@router.get(
    "",
    response_model=SavedSearchListResponse,
    summary="List the caller's saved searches",
)
async def list_saved_searches_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    items = await list_saved_searches(session, user_id=actor.id)
    body = SavedSearchListResponse(
        items=items, total=len(items), limit=MAX_PER_USER
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.post(
    "",
    response_model=SavedSearchPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Save a search",
    responses={
        409: {"description": "The caller already has a saved search by that name."},
        422: {"description": "Invalid name/kind, or the per-user limit is reached."},
    },
)
async def create_saved_search_endpoint(
    request: Request,
    payload: SavedSearchCreate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    try:
        body = await create_saved_search(
            session,
            user_id=actor.id,
            name=payload.name,
            kind=payload.kind,
            params=payload.params,
        )
    except SavedSearchError as exc:
        return _problem_for(request, exc)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


@router.delete(
    "/{saved_search_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of the caller's saved searches",
    responses={
        404: {
            "description": (
                "No such saved search, or it belongs to another user. The two "
                "are deliberately indistinguishable."
            )
        }
    },
)
async def delete_saved_search_endpoint(
    request: Request,
    saved_search_id: uuid.UUID = Path(...),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    try:
        await delete_saved_search(
            session, user_id=actor.id, saved_search_id=saved_search_id
        )
    except SavedSearchError as exc:
        return _problem_for(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
