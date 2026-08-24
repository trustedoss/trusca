# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
API Key service — Phase 5 PR #16.

Pure async DB I/O for the ``/v1/api-keys`` HTTP surface and the
``core.api_key_auth`` bearer authentication path.

Security contracts:

  - Plaintext format: ``tos_<8-char prefix>_<32-char url-safe secret>``.
    The plaintext is returned ONCE from :func:`issue_api_key` and immediately
    discarded by the service (only the stored hash and the public 12-char
    prefix are persisted). Subsequent reads return the prefix + metadata.

  - Hashing (A5, concurrency-scaling-plan-2026-08-22.md §3.3): NEW keys are
    hashed with keyed HMAC-SHA256 (:func:`core.security.hash_api_key_secret`),
    not bcrypt. The secret half is a 192-bit random value, not a human-chosen
    password, so bcrypt's slowness bought no defence against brute force
    (the only recovery path either way is exhaustive search over 192 bits)
    while still costing ~213ms of CPU per verification. Keys issued BEFORE
    this landed keep their bcrypt hash: this is an expand/read-both
    migration, not a rewrite. :func:`verify_api_key_plaintext` inspects the
    stored value's format marker (:func:`core.security.is_api_key_hmac_hash`)
    and dispatches to the matching verifier, both constant-time
    (``hmac.compare_digest`` / passlib's bcrypt ``checkpw``). See
    ``core.config.api_key_hmac_secret`` for the server-side key and
    ``authenticate_api_key``'s docstring for the timing-flattening dummy.

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
    The plaintext key, the secret half, and the stored hash (either format)
    NEVER appear in log lines. We log only ``key_prefix``, ``id``, ``scope``,
    and the actor metadata.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import (
    api_key_last_used_at_update_interval_seconds,
    api_key_verification_min_duration_seconds,
)
from core.security import (
    API_KEY_HASH_SCHEME,
    CurrentUser,
    hash_api_key_secret,
    is_api_key_hmac_hash,
    verify_api_key_hmac,
    verify_password,
)
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
    """Constant-time verification against a stored API-key hash.

    Dispatches on ``hashed``'s format marker (A5,
    concurrency-scaling-plan-2026-08-22.md §3.3):

      - ``hmac-sha256$<hex>`` (new keys, issued after A5 landed) -> keyed
        HMAC-SHA256 (:func:`core.security.verify_api_key_hmac`).
      - anything else (bcrypt's own ``$2a$``/``$2b$``/``$2y$`` marker; every
        key issued before A5) -> the legacy bcrypt path
        (:func:`core.security.verify_password`), unchanged.

    Both branches are constant-time within themselves, but NOT
    constant-time relative to EACH OTHER (HMAC is microseconds, bcrypt is
    ~213ms) -- see :func:`_verify_api_key_plaintext_padded` for the caller
    that closes that gap back up. This function is itself synchronous and
    CPU-bound on the bcrypt branch; callers on the request path MUST run it
    via ``asyncio.to_thread`` (see :func:`authenticate_api_key`), never call
    it directly from an ``async def`` body.
    """
    if is_api_key_hmac_hash(hashed):
        return verify_api_key_hmac(plaintext, hashed)
    return verify_password(plaintext, hashed)


async def _verify_api_key_plaintext_padded(plaintext: str, hashed: str) -> bool:
    """Run :func:`verify_api_key_plaintext` off the event loop, then pad the
    wall-clock time up to
    :func:`core.config.api_key_verification_min_duration_seconds`.

    Fixes a residual timing oracle the A5 hash-format migration opened
    (security-reviewer finding on the A5 PR): once real HMAC verification
    and the timing-flattening dummy branch both got fast, a row still on
    the legacy bcrypt format kept its old ~213ms cost, so response time
    ALONE could tell an attacker "this key_prefix still exists and is
    still bcrypt-format" without ever presenting a valid secret. The
    192-bit secret itself never leaked, but this reopened exactly the kind
    of timing asymmetry unit A1 closed, which this migration must not
    reopen any part of. Padding every branch (real HMAC, real bcrypt, or
    the dummy) up to the same floor closes the gap again. See the floor
    accessor's own docstring for the full rationale, why this padding is
    temporary, and how to tell when it can be removed.

    Both :func:`authenticate_api_key` call sites (the real branch and the
    dummy branch) go through this wrapper rather than calling
    ``asyncio.to_thread(verify_api_key_plaintext, ...)`` directly, so the
    padding fix applies uniformly to every branch, not just some of them.
    """
    floor = api_key_verification_min_duration_seconds()
    start = time.monotonic()
    result = await asyncio.to_thread(verify_api_key_plaintext, plaintext, hashed)
    elapsed = time.monotonic() - start
    remaining = floor - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)
    return result


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
    permission_breadth: str = "read_only",
    service_account_id: uuid.UUID | None = None,
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
    # Resolved before the retry loop: whose key this is does not change between
    # attempts, and asking again on a prefix collision would re-run the steward
    # check for no reason.
    issuer_id = actor.id
    if service_account_id is not None:
        from services.service_account_service import assert_may_issue_for

        account = await assert_may_issue_for(session, actor, service_account_id)
        issuer_id = account.id

    for attempt in range(_PREFIX_RETRIES):
        prefix = _generate_prefix()
        secret = _generate_secret()
        plaintext = _format_plaintext(prefix, secret)
        # A5: every key issued from this point on is hashed with the fast
        # keyed HMAC format, not bcrypt. See the module docstring's
        # "Hashing (A5)" contract.
        key_hash = hash_api_key_secret(plaintext)

        row = APIKey(
            key_prefix=prefix,
            key_hash=key_hash,
            name=name,
            scope=scope,
            # Read-only unless the caller asked otherwise. The default lives
            # here and in the request schema rather than on the column, because
            # the column default has to keep every key issued before this
            # feature at the breadth it already had.
            permission_breadth=permission_breadth,
            team_id=effective_team_id,
            project_id=project_id,
            # The issuer, which is what the auth path checks for liveness. A
            # service account here is the whole of the feature: the rule is
            # unchanged, and the answer to "is the issuer still active" simply
            # stops depending on whether a particular person still works here.
            created_by_user_id=issuer_id,
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
        # Drop the hash variable (defence in depth: the GC will get to it,
        # but being explicit means an accidental late log line cannot
        # capture it, whichever format it is).
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


async def narrow_api_key_breadth(
    session: AsyncSession,
    actor: CurrentUser,
    api_key_id: uuid.UUID,
) -> APIKey:
    """Make a read-write key read-only. There is no way back.

    Narrowing only, and the asymmetry is deliberate. Taking privilege away
    from a key that is already in somebody's pipeline is safe: the worst case
    is that the pipeline fails loudly and its owner asks for a new key.
    Handing privilege back to a key that has been in a CI log for months is
    not, so widening means issuing a fresh key with a fresh secret.

    Idempotent on a key that is already read-only, for the same reason revoke
    is: a caller retrying after a timeout should not get an error for a state
    that is already what they asked for.
    """
    row = (
        await session.execute(select(APIKey).where(APIKey.id == api_key_id))
    ).scalar_one_or_none()
    if row is None:
        raise APIKeyNotFound(f"api key {api_key_id} not found")

    if not _can_view_key(actor, row):
        # Existence-hide, matching revoke: a non-viewer must not be able to
        # probe key ids by status code.
        log.warning(
            "api_key.narrow.not_visible",
            actor_id=str(actor.id),
            api_key_id=str(api_key_id),
        )
        raise APIKeyNotFound(f"api key {api_key_id} not found")

    if not _can_revoke_key(actor, row):
        # The same people who may revoke a key may narrow it. Narrowing is
        # strictly the lesser act, so anybody allowed to destroy the key is
        # allowed to weaken it.
        raise APIKeyForbidden(
            f"actor lacks permission to change api key {api_key_id}",
        )

    if row.permission_breadth == "read_only":
        return row

    row.permission_breadth = "read_only"
    await session.commit()
    await session.refresh(row)
    log.info(
        "api_key.narrowed_to_read_only",
        actor_id=str(actor.id),
        api_key_id=str(api_key_id),
    )
    return row


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
      - We always run one verification (dispatched by
        :func:`verify_api_key_plaintext` to whichever format the matched
        row's ``key_hash`` uses) on the matched row. If no row matches, we
        run a dummy verification against a sentinel hash so the timing
        distribution is similar between the "wrong prefix" and "right
        prefix, wrong secret" branches. This is defence in depth: a
        sophisticated attacker could still distinguish via DB latency, but
        that requires repeated probes.

      - A5 (concurrency-scaling-plan-2026-08-22.md §3.3) changed WHICH
        format the dummy targets: it now hashes with
        :func:`core.security.hash_api_key_secret` (the fast HMAC-SHA256
        path), not bcrypt. On its own this would OPEN a new gap: once real
        verification is fast for the common case (a key issued after A5),
        a row still on the legacy bcrypt format would keep its slow
        ~213ms wrong-secret cost while everything else (HMAC, the dummy)
        is microseconds, so response time alone would tell an attacker
        "this key_prefix still exists and is still bcrypt-format" with no
        valid secret required. security-reviewer flagged exactly this gap
        on the A5 PR. The fix is :func:`_verify_api_key_plaintext_padded`
        (used by BOTH branches below, real and dummy, either format):
        it pads every verification's wall-clock time up to
        :func:`core.config.api_key_verification_min_duration_seconds`
        (default 220ms), so a branch that finished early sleeps off the
        remainder and a branch already at or above the floor (bcrypt) is
        unaffected. This padding is TEMPORARY -- it exists only while
        bcrypt-format rows can still be matched, and should be removed once
        :func:`count_legacy_hash_api_keys` reports zero in production and
        the contract step (dropping bcrypt reads entirely) ships; see that
        accessor's docstring for the removal criteria.

    Rate-limit coverage (RED-team F-1):
      Repeated probes are throttled ONLY on routes that carry a limiter
      decorator keyed by ``_authenticated_user_key`` (which buckets a ``tos_``
      bearer by ``apikey:<prefix>`` before this verify runs) — e.g. the
      scan-trigger / sbom-ingest writes and, since the F-1 fix, the
      api-key-accepting read GETs (``GET /v1/scans/{id}``,
      ``.../scans/{id}/conformance``). The limiter is decorator-opt-in
      (``default_limits=[]``), so any FUTURE route that accepts a key bearer
      MUST add ``@limiter.shared_limit(api_read_rate_limit, scope="api_read",
      key_func=_authenticated_user_key)`` (or a stricter policy) or it inherits
      zero throttling on this verification path.
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
        # Dummy verification to flatten timing (A1, retargeted to the fast
        # HMAC profile by A5; see this function's docstring for why, and
        # for the min-duration padding that closes the resulting gap back
        # up). The dummy hash is built fresh on every call rather than
        # cached at module scope: hash_api_key_secret reads
        # API_KEY_HMAC_SECRET via os.getenv at call time (CLAUDE.md core
        # rule #11), and a fresh random plaintext each call means the same
        # dummy digest never repeats. Routed through the identical padded
        # helper as the real branch below, so there is exactly one code
        # path to keep offloaded-and-padded, not two.
        dummy_hash = hash_api_key_secret(secrets.token_hex(32))
        await _verify_api_key_plaintext_padded(plaintext, dummy_hash)
        return None

    if not await _verify_api_key_plaintext_padded(plaintext, row.key_hash):
        return None

    # last_used_at is coalesced into a bucket instead of updated once per
    # request (concurrency-scaling-plan-2026-08-22.md A2). Skipping the write
    # inside the interval is a deliberate resolution trade: this column means
    # "used within the last api_key_last_used_at_update_interval_seconds()",
    # not "used at this exact instant", see that function's docstring and the
    # API-key admin guide. A CI scan that polls the same key dozens of times
    # over its run now costs one or two write transactions instead of one
    # per poll. NULL (never used) is unconditionally stale.
    now = _now()
    if row.last_used_at is not None:
        elapsed_seconds = (now - row.last_used_at).total_seconds()
        if elapsed_seconds < api_key_last_used_at_update_interval_seconds():
            return row

    # Update last_used_at best-effort. We do NOT block the request on this
    # commit failing — a brief outage on this column is acceptable.
    try:
        row.last_used_at = now
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


# ---------------------------------------------------------------------------
# Hash-format migration visibility (A5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class APIKeyHashFormatCounts:
    """Snapshot of how many still-usable API keys use each hash format.

    "Active" mirrors :func:`authenticate_api_key`'s own row filter: not
    revoked, and not expired. A key nobody can use any more (revoked, or
    past its ``expires_at``) is excluded, since it never runs through the
    bcrypt-vs-HMAC branch again; counting it would overstate how much
    legacy-format traffic remains.
    """

    legacy_bcrypt: int
    hmac_sha256: int

    @property
    def total(self) -> int:
        return self.legacy_bcrypt + self.hmac_sha256


async def count_legacy_hash_api_keys(session: AsyncSession) -> APIKeyHashFormatCounts:
    """Count active API keys by stored-hash format (A5 migration progress).

    concurrency-scaling-plan-2026-08-22.md §3.3 A5: two things only happen
    once every active key has moved to the new HMAC format. First, the
    contraction step (dropping bcrypt reads from
    :func:`authenticate_api_key` entirely). Second, removing the timing
    padding in :func:`_verify_api_key_plaintext_padded` (see
    ``core.config.api_key_verification_min_duration_seconds``'s docstring)
    -- that padding exists only to hide the bcrypt-vs-HMAC timing gap, and
    once no bcrypt-format row can be matched, there is nothing left to
    hide. There is no bulk-migration job: a key's stored hash only changes
    when the key is reissued (see the module docstring's "Hashing (A5)"
    contract), so keys that are not rotated stay on bcrypt indefinitely.
    This is the counter an operator uses to see migration progress, so
    "every active key has moved" can be confirmed from data rather than
    assumed from elapsed time. Exposed to super_admin via ``GET
    /v1/admin/api-keys/hash-migration``.

    Does no RBAC of its own: it counts across the whole deployment, not
    one caller's visible set, so only a super_admin-gated caller may
    reach it.
    """
    hmac_prefix = f"{API_KEY_HASH_SCHEME}$"
    active_filter = and_(
        APIKey.revoked_at.is_(None),
        or_(
            APIKey.expires_at.is_(None),
            APIKey.expires_at > _now(),
        ),
    )
    result = (
        await session.execute(
            select(
                func.count().filter(APIKey.key_hash.startswith(hmac_prefix)).label("hmac_count"),
                func.count().label("total_count"),
            )
            .select_from(APIKey)
            .where(active_filter)
        )
    ).one()
    hmac_count = int(result.hmac_count)
    total_count = int(result.total_count)
    return APIKeyHashFormatCounts(
        legacy_bcrypt=total_count - hmac_count,
        hmac_sha256=hmac_count,
    )


__all__ = [
    "APIKeyError",
    "APIKeyForbidden",
    "APIKeyHashFormatCounts",
    "APIKeyIssueFailed",
    "APIKeyNotFound",
    "APIKeyScopeMismatch",
    "authenticate_api_key",
    "count_legacy_hash_api_keys",
    "issue_api_key",
    "list_api_keys",
    "parse_bearer",
    "revoke_api_key",
    "verify_api_key_plaintext",
]
