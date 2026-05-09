"""
OAuth identity self-service — Chore G.

Pure async DB I/O for ``GET/DELETE /v1/users/me/oauth-identities[/{id}]``.
The router (``api.v1.users_me``) is a thin HTTP adapter that translates
domain exceptions into RFC 7807 ``application/problem+json`` responses.

Two top-level entry points:

  - :func:`list_user_oauth_identities` — return the caller's connected
    OAuth identities, sorted oldest-first.
  - :func:`unlink_oauth_identity` — DELETE one identity row, with two
    domain guards:
      1. **Existence-hide on cross-user access** — an attempt to delete
         someone else's identity raises :class:`OAuthIdentityNotFoundError`,
         indistinguishable from "row does not exist". Mirrors the pattern
         used by :mod:`services.notification_service`.
      2. **Last-authentication-method protection** — refusing to remove
         the user's only login mechanism. If the User has no
         ``hashed_password`` AND this is their only OAuth identity, the
         service raises :class:`OAuthUnlinkBlocksLoginError`. The caller
         must first set a password (via ``/auth/forgot-password``) or
         link a second provider.

Audit:
  - The SQLAlchemy ``before_flush`` listener (``core.audit``) automatically
    captures the ``DELETE`` against ``oauth_identities`` with
    ``action='delete'`` and a hashed ``email`` (``_PII_COLUMNS``).
  - In addition to the listener row, we emit an explicit semantic audit
    row with ``action='oauth.identity.unlinked'`` and a SHA-256 hash of
    ``provider_user_id`` so investigators can correlate forensic linkage
    requests against the provider's audit trail without retaining the
    raw provider id at rest. The two rows together preserve both "what
    column changed" (listener) and "what happened in domain terms"
    (explicit) — see ``api/v1/admin/dt.py`` for the same pattern.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import get_audit_context
from models import AuditLog, OAuthIdentity, User

log = structlog.get_logger("oauth_identity.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class OAuthIdentityError(Exception):
    """Base class for OAuth identity self-service errors.

    Subclasses carry an HTTP ``status_code`` + RFC 7807 ``type_uri`` so the
    HTTP adapter can translate domain failures into Problem Details
    responses without knowing about the service's internal taxonomy.
    """

    status_code: int = 400
    title: str = "OAuth Identity Error"
    type_uri: str = "about:blank"


class OAuthIdentityNotFoundError(OAuthIdentityError):
    """404 — row does not exist OR belongs to a different user.

    The two cases are deliberately conflated (existence-hide). Returning
    a distinct response shape would let an authenticated user enumerate
    other users' identity ids by probing.
    """

    status_code = 404
    title = "OAuth Identity Not Found"
    type_uri = "urn:trustedoss:problem:oauth_identity_not_found"


class OAuthUnlinkBlocksLoginError(OAuthIdentityError):
    """409 — unlink would leave the user with no way to authenticate.

    Tripped when both:
      - The User has no ``hashed_password`` (OAuth-only account), AND
      - The identity being removed is the user's last OAuth link.

    The frontend maps this into an actionable message: "set a password
    first, or link another provider".
    """

    status_code = 409
    title = "Cannot remove last authentication method"
    type_uri = "urn:trustedoss:problem:oauth_unlink_blocks_login"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_provider_user_id(provider_user_id: str) -> str:
    """SHA-256 hex digest of the provider's stable id.

    We hash so the audit log can prove "user X unlinked GitHub identity Y
    at T" without retaining Y in plaintext. SHA-256 is irreversible
    against a sufficiently large id space (GitHub numeric ids and Google
    OIDC ``sub`` are both effectively non-enumerable).
    """
    return hashlib.sha256(provider_user_id.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# list_user_oauth_identities
# ---------------------------------------------------------------------------


async def list_user_oauth_identities(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> Sequence[OAuthIdentity]:
    """Return the user's connected OAuth identities, sorted oldest-first.

    The order is keyed off ``linked_at ASC, id ASC`` so two identities
    linked in the same millisecond still produce a stable order. The SPA
    renders this list as a profile-page table; pagination is unnecessary
    because a single user is bounded by the configured provider count
    (currently 2: GitHub + Google).
    """
    stmt = (
        select(OAuthIdentity)
        .where(OAuthIdentity.user_id == user_id)
        .order_by(OAuthIdentity.linked_at.asc(), OAuthIdentity.id.asc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


# ---------------------------------------------------------------------------
# unlink_oauth_identity
# ---------------------------------------------------------------------------


async def unlink_oauth_identity(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> None:
    """Delete one OAuth identity row owned by ``user_id``.

    Guards (in order):
      1. ``OAuthIdentityNotFoundError`` if the row does not exist OR
         belongs to a different user (existence-hide).
      2. ``OAuthUnlinkBlocksLoginError`` if removing the row would leave
         the user with no authentication method.

    On success: deletes the row and writes an explicit ``AuditLog`` entry
    with ``action='oauth.identity.unlinked'`` carrying the provider name
    and a SHA-256 hash of ``provider_user_id``. The listener-driven
    delete row is also produced (with ``action='delete'``); the two
    together give us both the column-level diff and the semantic action.
    """
    # Lookup is keyed off (id, user_id) — never just id — so an attacker
    # cannot delete someone else's identity by guessing its UUID.
    stmt = select(OAuthIdentity).where(
        OAuthIdentity.id == identity_id,
        OAuthIdentity.user_id == user_id,
    )
    identity = (await session.execute(stmt)).scalar_one_or_none()
    if identity is None:
        raise OAuthIdentityNotFoundError(
            f"oauth identity {identity_id} not found for user {user_id}"
        )

    # Last-authentication-method check. We re-load the User to get the
    # current ``hashed_password`` value — the actor's CurrentUser dataclass
    # does not carry it (and we wouldn't trust it for a security decision
    # if it did; the JWT is the credential, not a snapshot of the column).
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        # The auth dependency proved this user exists at request entry, so
        # this branch only fires if the row was deleted between the auth
        # dep and this query (e.g. an admin deactivation racing with the
        # unlink). Treat as 404 — the caller can no longer act on their
        # own row anyway.
        raise OAuthIdentityNotFoundError(
            f"user {user_id} no longer exists"
        )

    has_password = bool(user.hashed_password)
    if not has_password:
        # Count remaining identities. If this is the only one, the user
        # has no fallback auth path — refuse the unlink.
        sibling_count = await _count_identities(session, user_id=user_id)
        if sibling_count <= 1:
            raise OAuthUnlinkBlocksLoginError(
                "removing this identity would leave the user with no way to log in"
            )

    # Capture the audit context BEFORE deletion so the explicit row gets
    # the request_id / ip / user_agent that the middleware bound.
    ctx = get_audit_context()
    provider = identity.provider
    pid_hash = _hash_provider_user_id(identity.provider_user_id)

    await session.delete(identity)

    audit_row = AuditLog(
        actor_user_id=user_id,
        team_id=None,
        target_table="oauth_identities",
        target_id=str(identity_id),
        action="oauth.identity.unlinked",
        request_id=ctx.get("request_id"),
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        diff={
            "provider": provider,
            "provider_user_id_hash": pid_hash,
        },
    )
    session.add(audit_row)

    await session.commit()
    log.info(
        "oauth.identity.unlinked",
        user_id=str(user_id),
        identity_id=str(identity_id),
        provider=provider,
    )


async def _count_identities(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> int:
    """Count OAuth identities currently linked to ``user_id``.

    Used by :func:`unlink_oauth_identity` to enforce the
    last-authentication-method guard. We deliberately use ``len()`` over
    the scalar list rather than a ``func.count()`` query so the read
    happens in the same transaction snapshot as the preceding
    ``SELECT`` — avoiding a TOCTOU window where a parallel link could
    sneak in between count and delete.
    """
    stmt = select(OAuthIdentity.id).where(OAuthIdentity.user_id == user_id)
    result = await session.execute(stmt)
    return len(result.scalars().all())


__all__ = [
    "OAuthIdentityError",
    "OAuthIdentityNotFoundError",
    "OAuthUnlinkBlocksLoginError",
    "list_user_oauth_identities",
    "unlink_oauth_identity",
]
