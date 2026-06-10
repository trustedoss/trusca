"""
API Key service — Phase 5 PR #16.

Pure async DB I/O for the ``/v1/api-keys`` HTTP surface and the
``core.api_key_auth`` bearer authentication path.

Security contracts:

  - Plaintext format: ``tos_<8-char prefix>_<32-char url-safe secret>``.
    The plaintext is returned ONCE from :func:`issue_api_key` and immediately
    discarded by the service (only the bcrypt hash and the public 12-char
    prefix are persisted). Subsequent reads return the prefix + metadata.

  - bcrypt cost-12 (CLAUDE.md §3) — same as user passwords.
    Verification (:func:`verify_api_key_plaintext`) uses
    :func:`core.security.verify_password` which is constant-time.

  - Soft-delete on revocation. The auth path filters on ``revoked_at IS NULL``
    so a revoked key is invisible without losing audit history.

  - Scope coherence is validated TWICE:
      1. Service preflight rejects mismatched (scope, team_id, project_id).
      2. The DB ``ck_api_keys_scope_consistency`` CHECK constraint backstops
         the service in case a future code path skips the preflight.

  - RBAC (issuer authorization):
      - scope=='org'     → actor must be super_admin
      - scope=='team'    → actor must be team_admin (or super_admin) of team_id
      - scope=='project' → actor must be a member (or super_admin) of the
                            project's team

  - Prefix collision retry. ``key_prefix`` is unique; on the (very rare)
    collision we regenerate up to ``_PREFIX_RETRIES`` times before giving up
    with a 503-equivalent. 8 random hex chars give 16^8 = 4.3B prefixes; even
    at scale the retry loop almost never fires.

  - Audit:
    The SQLAlchemy ``before_flush`` listener emits an ``audit_logs`` row for
    each INSERT / UPDATE on ``api_keys``. ``key_hash`` is masked to ``"***"``
    via ``core.audit._SENSITIVE_COLUMNS``.

  - Logging:
    The plaintext key, the secret half, and the bcrypt hash NEVER appear in
    log lines. We log only ``key_prefix``, ``id``, ``scope``, and the actor
    metadata.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser, hash_password, verify_password
from models import APIKey, Project, User

log = structlog.get_logger("api_key.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class APIKeyError(Exception):
    """Base class for API-key domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "API Key Error"


class APIKeyNotFound(APIKeyError):
    status_code = 404
    title = "API Key Not Found"


class APIKeyForbidden(APIKeyError):
    status_code = 403
    title = "Forbidden"


class APIKeyScopeMismatch(APIKeyError):
    """422 — scope/team_id/project_id are not internally consistent."""

    status_code = 422
    title = "Invalid API Key Scope"


class APIKeyIssueFailed(APIKeyError):
    """503 — could not allocate a unique prefix after retries."""

    status_code = 503
    title = "API Key Issue Failed"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 8 hex chars give 16^8 ≈ 4.3B distinct prefixes. The user-facing prefix string
# is "tos_" + the 8 hex chars = 12 chars total. The full plaintext bearer is
# "tos_<8 hex>_<32 url-safe>" so 41+ chars including separators.
_PREFIX_HEX_LEN = 8
_SECRET_BYTES = 24  # token_urlsafe(24) → 32 chars (url-safe base64, no padding)
_PUBLIC_PREFIX = "tos"
_PREFIX_RETRIES = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _generate_prefix() -> str:
    """Return a new ``tos_<8 hex>`` public prefix. Total length 12."""
    return f"{_PUBLIC_PREFIX}_{secrets.token_hex(_PREFIX_HEX_LEN // 2)}"


def _generate_secret() -> str:
    """Return a 32-char url-safe secret."""
    return secrets.token_urlsafe(_SECRET_BYTES)


def _format_plaintext(prefix: str, secret: str) -> str:
    """Compose the wire bearer string (``tos_<prefix>_<secret>``)."""
    return f"{prefix}_{secret}"


def parse_bearer(plaintext: str) -> tuple[str, str] | None:
    """
    Split the inbound bearer string into ``(key_prefix, secret)``.

    Returns ``None`` for any malformed input — the caller treats it as
    "not an API key" (and falls through to JWT auth, etc.).

    Format: ``tos_<8 hex>_<secret>``. The prefix portion is the first two
    underscore-separated segments (``tos`` + hex). The secret is whatever
    follows the second underscore — even if it itself contains underscores
    (url-safe base64 from ``token_urlsafe`` may include ``-`` and ``_``).
    """
    if not isinstance(plaintext, str):
        return None
    if not plaintext.startswith(f"{_PUBLIC_PREFIX}_"):
        return None
    # Split into 3 parts max so a secret containing "_" stays intact.
    parts = plaintext.split("_", 2)
    if len(parts) != 3:
        return None
    head, hex_part, secret = parts
    if head != _PUBLIC_PREFIX:
        return None
    if len(hex_part) != _PREFIX_HEX_LEN:
        return None
    # Hex part must be lowercase hex.
    try:
        int(hex_part, 16)
    except ValueError:
        return None
    if not secret:
        return None
    key_prefix = f"{head}_{hex_part}"
    return key_prefix, secret


def verify_api_key_plaintext(plaintext: str, hashed: str) -> bool:
    """Constant-time bcrypt verification (delegates to core.security)."""
    return verify_password(plaintext, hashed)


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


def _can_issue_at_scope(
    actor: CurrentUser,
    *,
    scope: str,
    team_id: uuid.UUID | None,
    project_team_id: uuid.UUID | None,
) -> bool:
    """Return True iff *actor* may issue a key at the requested scope.

    project_team_id is the team_id of the project's owning team (resolved by
    the caller for scope='project'); for other scopes it is unused.
    """
    if _is_super_admin(actor):
        return True
    if scope == "org":
        # Only super_admin issues org-scoped keys.
        return False
    if scope == "team":
        if team_id is None:
            return False
        return actor.team_roles.get(team_id) == "team_admin"
    if scope == "project":
        if project_team_id is None:
            return False
        # Any team member may issue a project-scoped key for that project.
        return project_team_id in actor.team_ids
    return False


def _can_view_key(actor: CurrentUser, key: APIKey) -> bool:
    """Return True iff *actor* is allowed to see this key in lists / GETs."""
    if _is_super_admin(actor):
        return True
    if key.created_by_user_id == actor.id:
        return True
    # Team-scoped key: any team member may see it (so a team_admin can audit
    # keys issued by a former colleague).
    if key.scope == "team" and key.team_id is not None and key.team_id in actor.team_ids:
        return True
    if key.scope == "project" and key.project_id is not None:
        # Project keys are visible to any member of the project's team. The
        # team_id was denormalized onto the key row at issuance for exactly
        # this lookup so we don't need a JOIN at read time.
        if key.team_id is not None and key.team_id in actor.team_ids:
            return True
    return False


def _can_revoke_key(actor: CurrentUser, key: APIKey) -> bool:
    """Return True iff *actor* may revoke this key.

    - super_admin: always
    - issuer (created_by_user_id == actor): always
    - team_admin of the key's team: yes (team / project keys)
    """
    if _is_super_admin(actor):
        return True
    if key.created_by_user_id == actor.id:
        return True
    if key.team_id is not None and actor.team_roles.get(key.team_id) == "team_admin":
        return True
    return False


# ---------------------------------------------------------------------------
# issue_api_key
# ---------------------------------------------------------------------------


async def issue_api_key(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    name: str,
    scope: str,
    team_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    expires_in_days: int | None = None,
) -> tuple[APIKey, str]:
    """
    Create a new API key and return ``(row, plaintext)``.

    The plaintext is the wire bearer string. It is built from a freshly-
    generated prefix + secret, hashed with bcrypt, and persisted; the local
    plaintext variable is then deleted before the function returns.

    Concurrency: ``key_prefix`` is unique. A collision (vanishingly unlikely
    at 16^8 ≈ 4.3B prefixes) is retried up to ``_PREFIX_RETRIES`` times before
    raising :class:`APIKeyIssueFailed` (503).

    RBAC: see :func:`_can_issue_at_scope`. A non-allowed scope raises
    :class:`APIKeyForbidden`; a malformed scope (e.g. team scope with no
    team_id) raises :class:`APIKeyScopeMismatch`.
    """
    # ----- Scope coherence (mirrors the DB CHECK) -----
    if scope == "org":
        if team_id is not None or project_id is not None:
            raise APIKeyScopeMismatch(
                "scope='org' must have team_id and project_id unset",
            )
    elif scope == "team":
        if team_id is None or project_id is not None:
            raise APIKeyScopeMismatch(
                "scope='team' requires team_id and forbids project_id",
            )
    elif scope == "project":
        if project_id is None:
            raise APIKeyScopeMismatch(
                "scope='project' requires project_id",
            )
    else:
        raise APIKeyScopeMismatch(f"unknown scope {scope!r}")

    # ----- Resolve project's team for RBAC + denormalization -----
    project_team_id: uuid.UUID | None = None
    if scope == "project":
        result = await session.execute(
            select(Project.team_id).where(Project.id == project_id)
        )
        project_row = result.first()
        if project_row is None:
            # Existence-hide: don't leak whether the project exists.
            raise APIKeyNotFound(f"project {project_id} not found")
        project_team_id = project_row[0]

    # ----- RBAC -----
    if not _can_issue_at_scope(
        actor,
        scope=scope,
        team_id=team_id,
        project_team_id=project_team_id,
    ):
        raise APIKeyForbidden(
            f"actor lacks permission to issue scope={scope!r}",
        )

    # For project keys we denormalize team_id onto the row so list / read
    # paths can do team-membership checks without joining projects.
    effective_team_id = team_id if scope != "project" else project_team_id

    expires_at = (
        _now() + timedelta(days=expires_in_days)
        if expires_in_days is not None
        else None
    )

    # ----- Generate + persist with collision retry -----
    last_error: Exception | None = None
    for attempt in range(_PREFIX_RETRIES):
        prefix = _generate_prefix()
        secret = _generate_secret()
        plaintext = _format_plaintext(prefix, secret)
        key_hash = hash_password(plaintext)

        row = APIKey(
            key_prefix=prefix,
            key_hash=key_hash,
            name=name,
            scope=scope,
            team_id=effective_team_id,
            project_id=project_id,
            created_by_user_id=actor.id,
            expires_at=expires_at,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            last_error = exc
            log.warning(
                "api_key.prefix_collision",
                attempt=attempt + 1,
                key_prefix=prefix,
            )
            continue

        await session.refresh(row)
        # Drop the bcrypt hash variable — defence in depth, the GC will get
        # to it but be explicit so an accidental late log line cannot capture it.
        del key_hash

        log.info(
            "api_key.issued",
            actor_id=str(actor.id),
            api_key_id=str(row.id),
            key_prefix=row.key_prefix,
            scope=scope,
            team_id=str(effective_team_id) if effective_team_id else None,
            project_id=str(project_id) if project_id else None,
        )
        return row, plaintext

    # All retries exhausted.
    raise APIKeyIssueFailed(
        "could not allocate a unique key prefix after retries",
    ) from last_error


# ---------------------------------------------------------------------------
# list_api_keys
# ---------------------------------------------------------------------------


async def list_api_keys(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    scope: str | None = None,
    team_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    include_revoked: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[APIKey], int]:
    """
    Return a paginated list of API keys visible to the actor.

    Visibility rules — see :func:`_can_view_key`:
      - super_admin: all keys
      - issuer: their own keys
      - team_admin: their team's keys + their own
      - team member: project keys for projects in their teams + their own
    """
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    base = select(APIKey)
    count_base = select(func.count()).select_from(APIKey)

    # ----- Tenant gate -----
    if not _is_super_admin(actor):
        # The actor sees: keys they created OR keys whose team_id is one of
        # their teams (covers team-scoped keys and project-scoped keys whose
        # team_id was denormalized at issuance).
        team_filter = APIKey.team_id.in_(actor.team_ids) if actor.team_ids else None
        if team_filter is not None:
            visibility = or_(
                APIKey.created_by_user_id == actor.id,
                team_filter,
            )
        else:
            visibility = APIKey.created_by_user_id == actor.id
        base = base.where(visibility)
        count_base = count_base.where(visibility)

    # ----- Caller filters -----
    if scope is not None:
        base = base.where(APIKey.scope == scope)
        count_base = count_base.where(APIKey.scope == scope)
    if team_id is not None:
        base = base.where(APIKey.team_id == team_id)
        count_base = count_base.where(APIKey.team_id == team_id)
    if project_id is not None:
        base = base.where(APIKey.project_id == project_id)
        count_base = count_base.where(APIKey.project_id == project_id)
    if not include_revoked:
        base = base.where(APIKey.revoked_at.is_(None))
        count_base = count_base.where(APIKey.revoked_at.is_(None))

    total = int((await session.execute(count_base)).scalar_one())
    # LEFT JOIN users so the list rows carry a human-readable issuer email in
    # ONE query (no per-row lookup). Outer join: created_by_user_id is SET NULL
    # on user deletion, and those rows must still list (email → None).
    rows_stmt = (
        base.add_columns(User.email)
        .outerjoin(User, User.id == APIKey.created_by_user_id)
        .order_by(APIKey.created_at.desc(), APIKey.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows: list[APIKey] = []
    for api_key, created_by_email in (await session.execute(rows_stmt)).all():
        # Plain (non-mapped) instance attribute — consumed by
        # APIKeyListItem.model_validate(from_attributes). It never dirties the
        # ORM unit of work and is NOT logged anywhere (email is PII; log lines
        # below carry only ids/counters).
        setattr(api_key, "created_by_email", created_by_email)  # noqa: B010
        rows.append(api_key)

    log.info(
        "api_key.list",
        actor_id=str(actor.id),
        total=total,
        page=page,
        page_size=page_size,
    )
    return rows, total


# ---------------------------------------------------------------------------
# revoke_api_key
# ---------------------------------------------------------------------------


async def revoke_api_key(
    session: AsyncSession,
    actor: CurrentUser,
    api_key_id: uuid.UUID,
) -> APIKey:
    """
    Soft-delete an API key by setting ``revoked_at`` / ``revoked_by_user_id``.

    Existence-hide: actors who cannot view the key get 404 (not 403) so the
    very fact that the id is valid does not leak.
    """
    row = (
        await session.execute(select(APIKey).where(APIKey.id == api_key_id))
    ).scalar_one_or_none()
    if row is None:
        raise APIKeyNotFound(f"api key {api_key_id} not found")

    if not _can_view_key(actor, row):
        # Existence-hide. The non-viewer should not be able to probe key ids.
        log.warning(
            "api_key.revoke.not_visible",
            actor_id=str(actor.id),
            api_key_id=str(api_key_id),
        )
        raise APIKeyNotFound(f"api key {api_key_id} not found")

    if not _can_revoke_key(actor, row):
        # Visible but not revokable — surface 403 here because hiding the
        # existence at this point would be inconsistent with the GET path.
        raise APIKeyForbidden(
            f"actor lacks permission to revoke api key {api_key_id}",
        )

    if row.revoked_at is not None:
        # Idempotent revoke — return the row unchanged. Audit trail already
        # has the original revocation event.
        return row

    row.revoked_at = _now()
    row.revoked_by_user_id = actor.id
    await session.commit()
    await session.refresh(row)

    log.info(
        "api_key.revoked",
        actor_id=str(actor.id),
        api_key_id=str(api_key_id),
        key_prefix=row.key_prefix,
    )
    return row


# ---------------------------------------------------------------------------
# Authentication path — used by core.api_key_auth
# ---------------------------------------------------------------------------


async def authenticate_api_key(
    session: AsyncSession,
    plaintext: str,
) -> APIKey | None:
    """
    Look up the bearer plaintext against the live api_keys set.

    Returns the matching APIKey row on success, None on any failure (bad
    format, unknown prefix, revoked, hash mismatch). The caller treats None
    as "no API-key auth" — the request continues into JWT auth or returns
    401 depending on the route's policy.

    Constant-time path:
      - We always run the bcrypt verification once on the matched row's
        ``key_hash``. If no row matches, we run a dummy verification against
        a sentinel hash so the timing distribution is similar between the
        "wrong prefix" and "right prefix, wrong secret" branches. This is
        defence in depth — a sophisticated attacker could still distinguish
        via DB latency, but that requires repeated probes which trigger
        rate limiting.
    """
    parsed = parse_bearer(plaintext)
    if parsed is None:
        return None
    key_prefix, _secret = parsed

    row = (
        await session.execute(
            select(APIKey).where(
                and_(
                    APIKey.key_prefix == key_prefix,
                    APIKey.revoked_at.is_(None),
                    # Expired keys are excluded at the query layer (not after the
                    # bcrypt verify) so an expired key takes the same "no row →
                    # dummy bcrypt" timing path as a revoked one.
                    or_(
                        APIKey.expires_at.is_(None),
                        APIKey.expires_at > _now(),
                    ),
                )
            )
        )
    ).scalar_one_or_none()

    if row is None:
        # Dummy bcrypt to flatten timing — passlib's verify is constant-time
        # against a single hash so we just call it on a known bcrypt hash that
        # will never match (a freshly hashed empty string).
        verify_password(plaintext, _DUMMY_BCRYPT_HASH)
        return None

    if not verify_api_key_plaintext(plaintext, row.key_hash):
        return None

    # Update last_used_at best-effort. We do NOT block the request on this
    # commit failing — a brief outage on this column is acceptable.
    try:
        row.last_used_at = _now()
        await session.commit()
        await session.refresh(row)
    except Exception as exc:  # noqa: BLE001 — best-effort
        await session.rollback()
        log.warning(
            "api_key.last_used_at_update_failed",
            api_key_id=str(row.id),
            error=str(exc),
        )

    return row


# A pre-computed bcrypt hash of an unguessable string. Used purely to keep
# the bcrypt timing path uniform when no prefix matches. Generated at import
# time once; the value never authenticates anyone.
# bcrypt cost 12 — same as live keys.
_DUMMY_BCRYPT_HASH = hash_password(secrets.token_hex(32))


__all__ = [
    "APIKeyError",
    "APIKeyForbidden",
    "APIKeyIssueFailed",
    "APIKeyNotFound",
    "APIKeyScopeMismatch",
    "authenticate_api_key",
    "issue_api_key",
    "list_api_keys",
    "parse_bearer",
    "revoke_api_key",
    "verify_api_key_plaintext",
]
