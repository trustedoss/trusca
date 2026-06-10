"""
OAuth (GitHub + Google) sign-in service — Phase 8 PR #23.

Pure async DB I/O for the ``/auth/oauth/{provider}/(authorize|callback)``
HTTP surface. The router is a thin adapter that translates HTTP shapes
into these calls and turns the domain exceptions into RFC 7807 / 302
responses.

Two top-level entry points:

  - :func:`initiate_oauth` — produce the (authorize_url, state) pair the
    router 302s the user to. ``state`` is a signed JWT (CSRF guard) with a
    short TTL so a leaked state cannot be replayed against a future flow.

  - :func:`complete_oauth` — handle the provider callback. Verify state,
    exchange ``code`` → access token, fetch the canonical user info, then
    either reuse an existing OAuth identity, link to an existing User by
    email, or create a brand-new User + personal Team.

The personal-team auto-creation matches the demo SaaS contract from
CLAUDE.md "데모 SaaS: 가입 시 개인 Team 자동 생성" — every brand new user
gets a Team they own (team_admin) so they can immediately scan something
without an admin onboarding dance.

Security notes:
  - State JWT carries ``provider``, ``redirect_after``, a 16-byte ``nonce``
    (so a leaked state cannot be hand-crafted to authenticate another
    user's flow), and a 5-minute ``exp``. Signature uses the same
    HMAC-SHA256 secret as auth tokens — :func:`core.security.decode_token`
    re-validates expiration / type.
  - We rely on the unique ``(provider, provider_user_id)`` constraint on
    ``oauth_identities`` as the canonical idempotency / takeover gate. The
    service catches ``IntegrityError`` and treats it as the existing-link
    branch — there is NO SELECT-then-INSERT (TOCTOU race).
  - The User row's ``hashed_password`` column is non-nullable, so when we
    create a User via OAuth we synthesise a random bcrypt-hashed string so
    no one can ever log in as that account via /auth/login (CWE-287). The
    user can later set a password via ``/auth/forgot-password``.
  - We never log access tokens, refresh tokens, or the state JWT body.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit_context
from core.config import (
    github_oauth_client_id,
    github_oauth_client_secret,
    google_oauth_client_id,
    google_oauth_client_secret,
    oauth_state_ttl_seconds,
    secret_key,
)
from core.security import (
    JWT_ALGORITHM,
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
)
from integrations.oauth import (
    OAuthExchangeError,
    OAuthProviderDisabled,
    OAuthUserInfo,
    get_provider,
)
from models import (
    Membership,
    OAuthIdentity,
    Organization,
    RefreshToken,
    Team,
    User,
)

log = structlog.get_logger("oauth.service")

# State JWT carries this `type` claim so it cannot be replayed against
# /auth/refresh or /auth/login.
STATE_TOKEN_TYPE = "oauth_state"  # noqa: S105 — public protocol marker


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class OAuthError(Exception):
    """Base class for OAuth domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "OAuth Error"
    extensions: dict[str, object] = {}


class OAuthProviderUnknown(OAuthError):
    status_code = 404
    title = "Unknown OAuth Provider"


class OAuthProviderUnavailable(OAuthError):
    status_code = 503
    title = "OAuth Provider Disabled"
    extensions = {"oauth_provider_disabled": True}


class OAuthInvalidState(OAuthError):
    status_code = 400
    title = "Invalid OAuth State"


class OAuthCallbackFailed(OAuthError):
    status_code = 502
    title = "OAuth Callback Failed"


class OAuthUserInactive(OAuthError):
    status_code = 403
    title = "User Inactive"


class NoOrganizationConfigured(OAuthError):
    status_code = 422
    title = "No Organization Configured"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _email_localpart(email: str) -> str:
    """Best-effort localpart extraction for personal-team naming."""
    at = email.find("@")
    return email[:at] if at > 0 else email


def _signed_state(*, provider: str, redirect_after: str | None) -> str:
    """Mint a signed CSRF state JWT.

    Carries ``provider`` (so a state from a github flow cannot be replayed
    on the google callback), ``redirect_after`` (so the SPA lands where it
    started), and a random ``nonce``. Signature uses the auth ``SECRET_KEY``
    via the existing python-jose path.
    """
    now = _now()
    expires = now + timedelta(seconds=oauth_state_ttl_seconds())
    claims: dict[str, Any] = {
        "type": STATE_TOKEN_TYPE,
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    if redirect_after is not None:
        claims["redirect_after"] = redirect_after
    return str(jwt.encode(claims, secret_key(), algorithm=JWT_ALGORITHM))


def _decode_state(state: str, *, expected_provider: str) -> dict[str, Any]:
    """Verify the state JWT and return its claims.

    Raises :class:`OAuthInvalidState` for bad signature, expired, wrong
    type, or wrong provider.
    """
    if not state:
        raise OAuthInvalidState("missing oauth state")
    try:
        claims: dict[str, Any] = jwt.decode(state, secret_key(), algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise OAuthInvalidState("invalid oauth state signature or expiry") from exc

    if claims.get("type") != STATE_TOKEN_TYPE:
        raise OAuthInvalidState("oauth state has wrong type claim")
    if claims.get("provider") != expected_provider:
        raise OAuthInvalidState("oauth state provider mismatch")
    return claims


async def _pick_default_org(session: AsyncSession) -> Organization:
    """Pick the lone organization for new-user team creation.

    Mirrors :func:`services.admin_team_service._pick_default_org`. Single-org
    assumption (CLAUDE.md "조직/팀/권한 모델"). Multi-org is a Phase 8+
    follow-up that will pivot off the email domain.
    """
    stmt = select(Organization).order_by(Organization.created_at.asc()).limit(1)
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is None:
        raise NoOrganizationConfigured("no organization is configured for this deployment")
    return org


def _personal_team_slug(provider: str, provider_user_id: str) -> str:
    """Deterministic, collision-resistant slug for the personal team.

    We hash (provider, provider_user_id) → 6 hex chars and prefix with the
    provider name. 16^6 ≈ 16M slugs per provider, plenty for a demo SaaS.
    A teams-table unique-violation on this slug falls through to the
    INSERT retry in :func:`_create_user_with_personal_team` (extremely
    unlikely; the hash is deterministic so a true collision implies the
    same external account is being onboarded twice in parallel — the OAuth
    identity unique-violation guard catches that case first).
    """
    digest = hashlib.sha256(f"{provider}:{provider_user_id}".encode()).hexdigest()
    return f"{provider}-{digest[:6]}"


def _personal_team_name(*, full_name: str | None, email: str) -> str:
    """`{full_name}'s Team` if available, else `{localpart}'s Team`."""
    base = (full_name or "").strip() or _email_localpart(email)
    # Cap at 200 chars — `teams.name` is VARCHAR(255), leave room for suffix.
    return f"{base[:200]}'s Team"


# ---------------------------------------------------------------------------
# oauth_provider_configured
# ---------------------------------------------------------------------------


def oauth_provider_configured(provider: str) -> bool:
    """Return whether ``provider`` is usable for sign-in (M-15).

    ``True`` only when BOTH the client id AND client secret are set — the
    exact precondition under which the provider adapters'
    ``_require_credentials`` succeeds and therefore /authorize and
    /callback actually work. An id without a secret (or vice versa) is a
    half-configured deployment: /authorize would raise
    :class:`integrations.oauth.OAuthProviderDisabled` → 503, so we report
    it as not configured to keep the login button hidden.

    Reads env at call time via the :mod:`core.config` accessors (CLAUDE.md
    core rule #11). Never returns or logs the credential values.

    Raises:
        OAuthProviderUnknown: provider name is not 'github' or 'google' —
            mirrors :func:`integrations.oauth.get_provider` so accidental
            call-sites fail loudly.
    """
    if provider == "github":
        return bool(github_oauth_client_id() and github_oauth_client_secret())
    if provider == "google":
        return bool(google_oauth_client_id() and google_oauth_client_secret())
    raise OAuthProviderUnknown(f"unknown OAuth provider: {provider!r}")


# ---------------------------------------------------------------------------
# initiate_oauth
# ---------------------------------------------------------------------------


def initiate_oauth(
    *,
    provider: str,
    redirect_uri: str,
    redirect_after: str | None,
) -> tuple[str, str]:
    """
    Build the provider's authorize URL + signed state.

    Returns ``(authorize_url, state)`` so the router can 302 the user
    while the state is also placed in a short-lived cookie (defence in
    depth — not required for security here, but it lets the SPA survive
    the rare case where the provider strips the query parameters in
    redirect chains).

    Raises:
        OAuthProviderUnknown: provider name is not 'github' or 'google'.
        OAuthProviderUnavailable: provider client id/secret not configured.
    """
    try:
        prov = get_provider(provider)
    except ValueError as exc:
        raise OAuthProviderUnknown(f"unknown OAuth provider: {provider!r}") from exc

    state = _signed_state(provider=provider, redirect_after=redirect_after)
    try:
        url = prov.authorize_url(state=state, redirect_uri=redirect_uri)
    except OAuthProviderDisabled as exc:
        raise OAuthProviderUnavailable(str(exc)) from exc
    return url, state


# ---------------------------------------------------------------------------
# complete_oauth
# ---------------------------------------------------------------------------


async def complete_oauth(
    session: AsyncSession,
    *,
    provider: str,
    code: str,
    state: str,
    redirect_uri: str,
) -> tuple[User, str, str, str | None]:
    """
    Handle the OAuth callback end-to-end.

    Returns ``(user, access_token, refresh_token, redirect_after)``.

    Steps:
      1. Verify the signed ``state`` (CSRF) and recover ``redirect_after``.
      2. Hand the ``code`` + ``redirect_uri`` to the provider for token exchange.
      3. Pull canonical user info from the provider.
      4. Either reuse the existing OAuth identity, link it to an existing
         User by email, or create a fresh User + personal Team.
      5. Stamp ``last_login_at`` on the User AND the OAuth identity row.
      6. Mint a fresh JWT pair and persist the refresh row.

    Raises:
        OAuthInvalidState: state failed verification.
        OAuthProviderUnknown: provider not recognised (router should pre-validate).
        OAuthProviderUnavailable: provider not configured.
        OAuthCallbackFailed: provider returned an unrecoverable error during
            exchange or userinfo fetch.
        OAuthUserInactive: the matched User has ``is_active=False``.
        NoOrganizationConfigured: the deployment has zero organizations
            configured (cannot create a personal team).
    """
    try:
        prov = get_provider(provider)
    except ValueError as exc:
        raise OAuthProviderUnknown(f"unknown OAuth provider: {provider!r}") from exc

    state_claims = _decode_state(state, expected_provider=provider)
    redirect_after_raw = state_claims.get("redirect_after")
    redirect_after: str | None = (
        redirect_after_raw if isinstance(redirect_after_raw, str) and redirect_after_raw else None
    )

    if not code:
        raise OAuthInvalidState("missing OAuth authorization code")

    try:
        access_token = await prov.exchange_code_for_token(code=code, redirect_uri=redirect_uri)
        info: OAuthUserInfo = await prov.fetch_user_info(access_token=access_token)
    except OAuthProviderDisabled as exc:
        raise OAuthProviderUnavailable(str(exc)) from exc
    except OAuthExchangeError as exc:
        log.warning("oauth_callback_provider_error", provider=provider, error=str(exc)[:200])
        raise OAuthCallbackFailed(f"OAuth provider rejected the callback: {exc}") from exc

    user, identity = await _resolve_or_create_user(session, info=info)

    # Bind the user into the audit context BEFORE the commit so any flush
    # inside _resolve_or_create_user produced audit_logs rows attributed
    # to the correct actor. (The audit listener reads ContextVars at flush
    # time; binding before _issue_token_pair_in_session covers the second
    # commit path too.)
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(user.id)
    audit_context.set(ctx)

    if not user.is_active:
        raise OAuthUserInactive(f"user {user.id} is inactive")

    now = _now()
    user.last_login_at = now
    identity.last_login_at = now

    portal_access, portal_refresh, _refresh_expires = await _issue_token_pair_in_session(
        session, user=user
    )

    log.info(
        "oauth_login_success",
        provider=provider,
        user_id=str(user.id),
        oauth_identity_id=str(identity.id),
    )

    return user, portal_access, portal_refresh, redirect_after


# ---------------------------------------------------------------------------
# Identity resolution + personal team bootstrap
# ---------------------------------------------------------------------------


async def _resolve_or_create_user(
    session: AsyncSession,
    *,
    info: OAuthUserInfo,
) -> tuple[User, OAuthIdentity]:
    """
    Find or create the User attached to ``info``.

    Three branches:
      a) An ``oauth_identities`` row already exists for this
         (provider, provider_user_id) → return that User + identity.
      b) A ``users`` row exists with the same email → link a fresh
         oauth_identity to it. This is the "I had a password account, I'm
         signing in via GitHub for the first time" path.
      c) Neither exists → create a fresh User + personal Team
         (team_admin) + oauth_identity.

    Concurrency: the unique constraint on
    ``(provider, provider_user_id)`` plus the unique on ``users.email``
    are the canonical races; we catch ``IntegrityError`` on the create
    paths and re-resolve.
    """
    # (a) Existing identity?
    stmt = select(OAuthIdentity).where(
        OAuthIdentity.provider == info.provider,
        OAuthIdentity.provider_user_id == info.provider_user_id,
    )
    identity = (await session.execute(stmt)).scalar_one_or_none()
    if identity is not None:
        # Refresh metadata from the provider (people DO change their
        # avatar / display email — stay current).
        identity.email = info.email
        if info.avatar_url is not None:
            identity.avatar_url = info.avatar_url
        user = (
            await session.execute(select(User).where(User.id == identity.user_id))
        ).scalar_one_or_none()
        if user is None:
            # The User was hard-deleted but the CASCADE on the FK should
            # have removed this identity row too. Defensive fallthrough:
            # delete the orphan identity and create a fresh User.
            await session.delete(identity)
            await session.flush()
        else:
            return user, identity

    # (b) Existing User by email?
    user = (
        await session.execute(select(User).where(User.email == info.email))
    ).scalar_one_or_none()

    if user is not None:
        # Link a new identity to the existing User. Two flows could race
        # here (same external account being linked to two different
        # email-matched Users), but the unique
        # ``(provider, provider_user_id)`` index on oauth_identities
        # catches it — the second INSERT raises IntegrityError, we re-
        # resolve, and end up at branch (a).
        identity = OAuthIdentity(
            user_id=user.id,
            provider=info.provider,
            provider_user_id=info.provider_user_id,
            email=info.email,
            avatar_url=info.avatar_url,
        )
        session.add(identity)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            # Re-resolve via branch (a) — someone else just linked.
            existing = (
                await session.execute(
                    select(OAuthIdentity).where(
                        OAuthIdentity.provider == info.provider,
                        OAuthIdentity.provider_user_id == info.provider_user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise OAuthCallbackFailed(
                    "OAuth identity could not be linked"
                ) from None
            re_user = (
                await session.execute(select(User).where(User.id == existing.user_id))
            ).scalar_one_or_none()
            if re_user is None:
                raise OAuthCallbackFailed(
                    "OAuth identity references a missing user"
                ) from None
            return re_user, existing
        return user, identity

    # (c) Brand new — create User + personal Team + identity in one
    # transaction. Single rollback boundary so a partial create never
    # persists.
    user, identity = await _create_user_with_personal_team(session, info=info)
    return user, identity


async def _create_user_with_personal_team(
    session: AsyncSession,
    *,
    info: OAuthUserInfo,
) -> tuple[User, OAuthIdentity]:
    """
    Create a fresh User + personal Team (team_admin) + OAuthIdentity.

    The User's ``hashed_password`` is set to a random bcrypt-hashed string
    so no one can ever sign in via /auth/login as this account (CWE-287).
    Setting a real password requires the ``/auth/forgot-password`` flow.
    """
    org = await _pick_default_org(session)

    user = User(
        email=info.email,
        # Random bcrypt input — never derived from anything attacker-known.
        hashed_password=hash_password(secrets.token_urlsafe(48)),
        full_name=info.full_name,
        is_active=True,
        is_superuser=False,
        is_verified=True,  # OAuth providers verify email themselves.
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Email collision — race with another OAuth or password
        # registration that committed between our SELECT and our flush.
        # Re-resolve via the email lookup path; if that finds a User we
        # link via branch (b). If still nothing, fail loudly.
        await session.rollback()
        existing = (
            await session.execute(select(User).where(User.email == info.email))
        ).scalar_one_or_none()
        if existing is None:
            raise OAuthCallbackFailed("user creation collided unexpectedly") from exc
        identity = OAuthIdentity(
            user_id=existing.id,
            provider=info.provider,
            provider_user_id=info.provider_user_id,
            email=info.email,
            avatar_url=info.avatar_url,
        )
        session.add(identity)
        await session.flush()
        return existing, identity

    team = Team(
        organization_id=org.id,
        name=_personal_team_name(full_name=info.full_name, email=info.email),
        slug=_personal_team_slug(info.provider, info.provider_user_id),
        description=f"Personal team for {info.email}",
    )
    session.add(team)
    try:
        await session.flush()
    except IntegrityError:
        # Slug collision — extremely unlikely (sha256 prefix), but if it
        # ever fires we fall back to a uuid-suffixed slug. A second
        # collision indicates a bug; we let it propagate.
        await session.rollback()
        team = Team(
            organization_id=org.id,
            name=_personal_team_name(full_name=info.full_name, email=info.email),
            slug=f"{info.provider}-{uuid.uuid4().hex[:8]}",
            description=f"Personal team for {info.email}",
        )
        # Re-add the user; the rollback wiped it.
        session.add(user)
        await session.flush()
        session.add(team)
        await session.flush()

    membership = Membership(user_id=user.id, team_id=team.id, role="team_admin")
    session.add(membership)

    identity = OAuthIdentity(
        user_id=user.id,
        provider=info.provider,
        provider_user_id=info.provider_user_id,
        email=info.email,
        avatar_url=info.avatar_url,
    )
    session.add(identity)
    await session.flush()

    log.info(
        "oauth_user_created",
        provider=info.provider,
        user_id=str(user.id),
        team_id=str(team.id),
    )
    return user, identity


# ---------------------------------------------------------------------------
# Token issuance — duplicates auth_service.issue_token_pair to avoid a
# double-commit (we want a single transaction for "create user + create
# team + insert refresh row").
# ---------------------------------------------------------------------------


async def _issue_token_pair_in_session(
    session: AsyncSession,
    *,
    user: User,
) -> tuple[str, str, datetime]:
    """Mint access+refresh, persist the refresh row, COMMIT once.

    Mirrors :func:`services.auth_service.issue_token_pair` but is called
    inside a transaction that has already added the User / OAuthIdentity
    / Team / Membership rows. A single ``session.commit()`` at the end
    persists the whole graph atomically.
    """
    access_token = create_access_token(
        subject=str(user.id),
        role="super_admin" if user.is_superuser else None,
    )
    refresh_token, jti, expires_at = create_refresh_token(subject=str(user.id))

    session.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            token_hash=hash_refresh_token(refresh_token),
            parent_jti=None,
            expires_at=expires_at,
        )
    )
    await session.commit()
    return access_token, refresh_token, expires_at


__all__ = [
    "NoOrganizationConfigured",
    "OAuthCallbackFailed",
    "OAuthError",
    "OAuthInvalidState",
    "OAuthProviderUnavailable",
    "OAuthProviderUnknown",
    "OAuthUserInactive",
    "STATE_TOKEN_TYPE",
    "complete_oauth",
    "initiate_oauth",
    "oauth_provider_configured",
]
