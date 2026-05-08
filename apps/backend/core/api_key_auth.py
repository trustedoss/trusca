"""
API Key bearer authentication — Phase 5 PR #16.

Two FastAPI dependencies live here:

  - :func:`get_current_api_key` — resolves the bearer header to a live
    :class:`APIKey` ORM row (or returns ``None`` if the header is missing /
    malformed / does not match a live key). Strict variant for endpoints that
    accept ONLY API-key auth (today: the webhook routes do not use this; they
    perform their own HMAC check; this dependency is the building block for
    Phase 5 PR #17 CI endpoints).

  - :func:`get_api_key_principal` — wraps the API key into a
    :class:`core.security.CurrentUser` so endpoints can accept either JWT or
    API-key auth uniformly. The synthesized principal carries the issuer's
    identity (``created_by_user_id``) and a single team membership derived
    from the key's scope.

The bearer header format is shared with JWT (``Authorization: Bearer <...>``).
The dispatcher distinguishes by inspecting the prefix:

    Authorization: Bearer tos_<prefix>_<secret>     → API key auth
    Authorization: Bearer eyJ...                    → JWT auth

A key that fails verification (wrong secret, revoked, malformed prefix) does
NOT fall through to JWT auth — it returns ``None`` and the route's existing
``Depends(get_current_user)`` then sees an absent JWT and returns 401. This
keeps each path's failure mode independent.
"""

from __future__ import annotations

from dataclasses import replace

import structlog
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.audit import audit_context
from core.db import get_db
from core.security import CurrentUser
from models import APIKey, User
from services.api_key_service import authenticate_api_key, parse_bearer

log = structlog.get_logger("api_key.auth")


def _bearer_token(request: Request) -> str | None:
    """Pull the bearer credential out of the Authorization header, or None."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _looks_like_api_key(token: str) -> bool:
    """Cheap pre-check before we hit bcrypt: does this look like a tos_ token?"""
    return parse_bearer(token) is not None


async def get_current_api_key(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> APIKey | None:
    """
    Dependency: returns the authenticated :class:`APIKey` row, or None.

    None covers ALL failure modes:
      - no Authorization header
      - non-bearer scheme
      - JWT-shaped token (skipped — JWT auth handles it elsewhere)
      - malformed key prefix
      - unknown / revoked key
      - bcrypt verification mismatch

    The route's caller is expected to translate None → 401 if API-key auth is
    mandatory for that endpoint.
    """
    token = _bearer_token(request)
    if not token:
        return None
    if not _looks_like_api_key(token):
        # JWT or some other bearer format — not our problem.
        return None

    api_key = await authenticate_api_key(session, token)
    if api_key is None:
        # Log the prefix only — never the secret. parse_bearer() already
        # validated the format, so the prefix is safe to surface.
        parsed = parse_bearer(token)
        log.warning(
            "api_key.auth_failed",
            key_prefix=parsed[0] if parsed else None,
        )
        return None

    return api_key


async def get_api_key_principal(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """
    Dependency: synthesize a :class:`CurrentUser` from the API key's issuer.

    Returns ``None`` if no valid API key is present (so callers can fall
    through to JWT auth). When a key is found, the returned principal:

      - has ``id`` / ``email`` from the issuer's User row,
      - inherits the issuer's role and team_roles (the key cannot ESCALATE
        privilege beyond what its issuer holds — if the issuer was demoted
        the key's effective permissions fall with them at the next request),
      - does NOT have ``is_superuser`` set unless the issuer is a super_admin.

    Side effect: the audit context is bound with ``user_id`` so any flush
    triggered later in this request gets ``actor_user_id`` populated.
    """
    api_key = await get_current_api_key(request, session)
    if api_key is None:
        return None

    if api_key.created_by_user_id is None:
        # Issuer was deleted (FK is ON DELETE SET NULL). The key is orphaned;
        # treat it as untrusted. A future op-script can purge orphans.
        log.warning(
            "api_key.orphaned",
            api_key_id=str(api_key.id),
            key_prefix=api_key.key_prefix,
        )
        return None

    stmt = (
        select(User)
        .where(User.id == api_key.created_by_user_id)
        .options(selectinload(User.memberships))
    )
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    memberships = list(user.memberships)
    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}

    # Highest-role calculation mirrors core.security._highest_role exactly so
    # JWT and API-key principals are interchangeable downstream.
    if user.is_superuser:
        role = "super_admin"
    elif memberships:
        role_priority = {"developer": 1, "team_admin": 2, "super_admin": 3}
        role = max((m.role for m in memberships), key=lambda r: role_priority.get(r, 0))
    else:
        role = "developer"

    principal = CurrentUser(
        id=user.id,
        email=user.email,
        role=role,
        team_ids=team_ids,
        team_roles=team_roles,
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
    )
    # Defensive copy via dataclasses.replace — keeps the dataclass immutable
    # contract intact even if a future field is mutable.
    principal = replace(principal)

    # Bind the principal into the audit context so downstream flushes carry
    # the actor.
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(principal.id)
    audit_context.set(ctx)

    return principal


__all__ = ["get_api_key_principal", "get_current_api_key"]
