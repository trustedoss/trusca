"""
GitHub App credential service — v2.2-b1.

Pure async DB I/O for the ``/v1/github-app-credentials`` HTTP surface plus the
SECURITY-CRITICAL :func:`mint_installation_token` path that exchanges a freshly
minted, short-lived App JWT (RS256, signed with the stored PEM private key) for
a per-installation access token from GitHub.

Security contracts:

  - **Reversible secret, encrypted at rest.** The App PEM private key is a
    reversible secret (we must recover it to sign the App JWT). It is encrypted
    with ``core.crypto.encrypt_secret`` (Fernet) BEFORE persist and decrypted
    only inside :func:`mint_installation_token`, in memory, for the lifetime of
    one JWT signing. The plaintext PEM is NEVER persisted, NEVER logged, and
    NEVER returned by any read path.

  - **App JWT clamping.** The App JWT is RS256-signed with ``iss=app_id`` and a
    TTL of at most ``_APP_JWT_MAX_TTL_SECONDS`` (GitHub rejects > 10 min). We
    set ``iat`` 60s in the PAST to absorb clock skew between us and GitHub (a
    forward-skewed ``iat`` makes GitHub reject the JWT as "issued in the
    future") and clamp ``exp`` to ``iat + TTL`` so a skew-padded token still
    sits inside GitHub's 10-minute ceiling.

  - **RBAC (team-scoped, mirrors api_keys).**
      - register / revoke / link / unlink → caller must be ``team_admin`` of the
        credential's team (or ``super_admin``).
      - list / get / list_installations → any member of the credential's team
        (or ``super_admin``) — read access for the whole team so a team_admin can
        audit a colleague's registration.
    Existence-hide: a caller who cannot view a credential gets 404, not 403, so
    credential ids cannot be probed (matches the api_key service contract).

  - **Audit.** The ``before_flush`` listener emits an ``audit_logs`` row for each
    INSERT / UPDATE. ``private_key_encrypted`` / ``webhook_secret_encrypted`` are
    masked to ``"***"`` via ``core.audit._SENSITIVE_COLUMNS``. We bind the team
    into the audit context (``bind_audit_team``) before each mutating commit so
    the audit row's ``team_id`` is non-NULL.

  - **Logging.** We log only ids, app_id, team_id, and actor metadata. The PEM,
    the webhook secret, the minted JWT, and the installation token NEVER appear
    in a log line.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import bind_audit_team
from core.config import github_api_url, github_app_token_http_timeout_seconds
from core.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from core.security import CurrentUser
from models import GitHubAppCredential, GitHubAppInstallation

log = structlog.get_logger("github_app.service")


# ---------------------------------------------------------------------------
# Domain exceptions (mirror services.api_key_service)
# ---------------------------------------------------------------------------


class GitHubAppError(Exception):
    """Base class for GitHub-App domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "GitHub App Error"


class GitHubAppNotFound(GitHubAppError):
    status_code = 404
    title = "GitHub App Credential Not Found"


class GitHubAppForbidden(GitHubAppError):
    status_code = 403
    title = "Forbidden"


class GitHubAppConflict(GitHubAppError):
    """409 — a credential for (team, app_id) already exists (live)."""

    status_code = 409
    title = "GitHub App Credential Already Exists"


class GitHubAppTokenError(GitHubAppError):
    """502 — the App-token exchange with GitHub failed."""

    status_code = 502
    title = "GitHub App Token Exchange Failed"


class GitHubAppConfigError(GitHubAppError):
    """500-equivalent surfaced as 422 — stored key undecryptable / misconfig.

    Modelled as 422 (not a bare 500) so the endpoint returns a clean RFC 7807
    envelope: the request is well-formed but the server's stored credential
    can no longer be used (e.g. the encryption key was rotated). 422 keeps the
    failure attributable to the credential rather than a generic server crash.
    """

    status_code = 422
    title = "GitHub App Credential Unusable"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GitHub rejects App JWTs with an expiry more than 10 minutes out. We mint for
# strictly less (9 min effective after skew padding) to stay inside the ceiling.
# NB: this assumes host clocks are NTP-synced (within ~1 min). The 60s backdated
# iat plus the 9-min exp (1 min under GitHub's 10-min ceiling) leaves margin so a
# modest forward skew on either side does not push us outside the window.
_APP_JWT_MAX_TTL_SECONDS = 9 * 60
# installation_id is GitHub's numeric installation id. We re-validate it at the
# mint boundary (defence in depth — never trust callers pre-validated) before
# interpolating it into the request URL. Mirrors schemas.github_app's
# ``_INSTALLATION_ID_RE`` so the service and the HTTP edge agree.
_INSTALLATION_ID_RE = re.compile(r"^[0-9]{1,32}$")
# Backdate iat to absorb clock skew between us and GitHub (a forward-skewed iat
# is rejected as "issued in the future").
_APP_JWT_CLOCK_SKEW_SECONDS = 60
_APP_JWT_ALGORITHM = "RS256"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


# PostgreSQL SQLSTATE for foreign_key_violation (class 23 integrity constraint).
_PG_FK_VIOLATION_SQLSTATE = "23503"


def _is_foreign_key_violation(exc: IntegrityError) -> bool:
    """True if ``exc`` is a Postgres foreign-key violation (SQLSTATE 23503).

    Primary signal is the driver's SQLSTATE (asyncpg exposes ``sqlstate`` on the
    wrapped ``orig`` exception; psycopg exposes ``pgcode``). Falls back to a
    substring scan of the message so the classification still works if a driver
    surfaces the code differently. Conservative: anything we cannot positively
    identify as an FK violation is treated as NOT one (so it stays a 409).
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        if sqlstate is not None:
            return str(sqlstate) == _PG_FK_VIOLATION_SQLSTATE
    text = str(getattr(exc, "orig", exc)).lower()
    return "foreignkeyviolation" in text or "foreign key constraint" in text


def _validate_installation_id(installation_id: str) -> None:
    """Re-validate an installation id at a trust boundary (defence in depth).

    Raises :class:`GitHubAppError` (400) if ``installation_id`` is not a bare
    numeric string. Called BEFORE the value is interpolated into the GitHub URL
    so a CRLF / path-traversal / non-numeric value can never reach the network
    layer — we do not trust that the caller pre-validated.
    """
    if not isinstance(installation_id, str) or not _INSTALLATION_ID_RE.match(
        installation_id
    ):
        raise GitHubAppError("installation_id must be a numeric GitHub installation id")


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


def _is_team_admin(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if _is_super_admin(actor):
        return True
    return actor.team_roles.get(team_id) == "team_admin"


def _is_team_member(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if _is_super_admin(actor):
        return True
    return team_id in actor.team_ids


def _credential_out_fields(row: GitHubAppCredential) -> dict[str, Any]:
    """Project a credential row to the metadata-only response dict.

    NEVER includes the private key or any ciphertext — only booleans flagging
    presence. The router builds ``GitHubAppCredentialOut`` from this.
    """
    return {
        "id": row.id,
        "team_id": row.team_id,
        "app_id": row.app_id,
        "app_slug": row.app_slug,
        "has_private_key": bool(row.private_key_encrypted),
        "has_webhook_secret": row.webhook_secret_encrypted is not None,
        "created_by_user_id": row.created_by_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "revoked_at": row.revoked_at,
    }


# ---------------------------------------------------------------------------
# register_credential
# ---------------------------------------------------------------------------


async def register_credential(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID,
    app_id: str,
    app_slug: str | None,
    private_key: str,
    webhook_secret: str | None,
) -> GitHubAppCredential:
    """Register a GitHub App credential for a team.

    The PEM private key (and optional webhook secret) is encrypted BEFORE the
    row is persisted. RBAC: caller must be team_admin of ``team_id`` (or
    super_admin). A live credential already existing for (team, app_id) raises
    :class:`GitHubAppConflict` (the DB unique constraint backstops the service).
    """
    if not _is_team_admin(actor, team_id):
        raise GitHubAppForbidden(
            "actor must be a team admin of the target team to register a credential"
        )

    # Encrypt before persist — the plaintext never reaches the model layer.
    private_key_encrypted = encrypt_secret(private_key)
    webhook_secret_encrypted = (
        encrypt_secret(webhook_secret) if webhook_secret is not None else None
    )

    # Bind team into the audit context so the audit row's team_id is non-NULL.
    bind_audit_team(team_id)

    row = GitHubAppCredential(
        team_id=team_id,
        app_id=app_id,
        app_slug=app_slug,
        private_key_encrypted=private_key_encrypted,
        webhook_secret_encrypted=webhook_secret_encrypted,
        created_by_user_id=actor.id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Two distinct integrity failures land here and must NOT both be 409:
        #   - unique violation (uq_github_app_credentials_team_app): a live
        #     credential for (team, app_id) already exists → genuine 409.
        #   - FK violation (team_id → teams.id): the target team does not exist.
        #     Only reachable via the super_admin path (a team member's team_id is
        #     gated by RBAC), but a stale/typo'd team_id must surface as 404, not
        #     a misleading "already exists" 409.
        if _is_foreign_key_violation(exc):
            raise GitHubAppNotFound(
                f"team {team_id} does not exist"
            ) from exc
        # Default to 409 (unique violation, or any other integrity error we
        # cannot positively attribute) — never leak which other team/row collided.
        raise GitHubAppConflict(
            f"a GitHub App credential for app_id={app_id} already exists for this team"
        ) from exc

    await session.refresh(row)
    # Defence in depth: drop the plaintext + ciphertext locals promptly.
    del private_key
    del private_key_encrypted

    log.info(
        "github_app.credential_registered",
        actor_id=str(actor.id),
        credential_id=str(row.id),
        team_id=str(team_id),
        app_id=app_id,
    )
    return row


# ---------------------------------------------------------------------------
# list_credentials / get_credential
# ---------------------------------------------------------------------------


async def list_credentials(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID | None = None,
    include_revoked: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[GitHubAppCredential], int]:
    """Return a paginated list of credentials visible to the actor.

    Visibility: super_admin sees all; everyone else sees credentials for teams
    they belong to. The optional ``team_id`` filter is intersected with that
    tenant gate (a non-member filtering by a foreign team gets an empty page,
    never a leak).
    """
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    base = select(GitHubAppCredential)
    count_base = select(func.count()).select_from(GitHubAppCredential)

    if not _is_super_admin(actor):
        if actor.team_ids:
            tenant = GitHubAppCredential.team_id.in_(actor.team_ids)
        else:
            # No memberships → can see nothing. A false predicate keeps the
            # query shape uniform.
            tenant = GitHubAppCredential.team_id.in_([uuid.UUID(int=0)])
        base = base.where(tenant)
        count_base = count_base.where(tenant)

    if team_id is not None:
        base = base.where(GitHubAppCredential.team_id == team_id)
        count_base = count_base.where(GitHubAppCredential.team_id == team_id)
    if not include_revoked:
        base = base.where(GitHubAppCredential.revoked_at.is_(None))
        count_base = count_base.where(GitHubAppCredential.revoked_at.is_(None))

    total = int((await session.execute(count_base)).scalar_one())
    rows_stmt = (
        base.order_by(GitHubAppCredential.created_at.desc(), GitHubAppCredential.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = list((await session.execute(rows_stmt)).scalars().all())

    log.info(
        "github_app.credential_list",
        actor_id=str(actor.id),
        total=total,
        page=page,
        page_size=page_size,
    )
    return rows, total


async def get_credential(
    session: AsyncSession,
    actor: CurrentUser,
    credential_id: uuid.UUID,
) -> GitHubAppCredential:
    """Fetch a single credential the actor may view, else 404 (existence-hide)."""
    row = (
        await session.execute(
            select(GitHubAppCredential).where(GitHubAppCredential.id == credential_id)
        )
    ).scalar_one_or_none()
    if row is None or not _is_team_member(actor, row.team_id):
        # Existence-hide: a non-member must not be able to probe credential ids.
        raise GitHubAppNotFound(f"github app credential {credential_id} not found")
    return row


# ---------------------------------------------------------------------------
# revoke_credential (soft-delete)
# ---------------------------------------------------------------------------


async def revoke_credential(
    session: AsyncSession,
    actor: CurrentUser,
    credential_id: uuid.UUID,
) -> GitHubAppCredential:
    """Soft-delete a credential (flip ``revoked_at`` / ``revoked_by_user_id``).

    Existence-hide for non-members (404). A member who is not a team_admin gets
    403. Idempotent: a second revoke returns the row unchanged.
    """
    row = (
        await session.execute(
            select(GitHubAppCredential).where(GitHubAppCredential.id == credential_id)
        )
    ).scalar_one_or_none()
    if row is None or not _is_team_member(actor, row.team_id):
        raise GitHubAppNotFound(f"github app credential {credential_id} not found")

    if not _is_team_admin(actor, row.team_id):
        raise GitHubAppForbidden(
            f"actor lacks permission to revoke github app credential {credential_id}"
        )

    if row.revoked_at is not None:
        return row  # idempotent

    bind_audit_team(row.team_id)
    row.revoked_at = _now()
    row.revoked_by_user_id = actor.id
    await session.commit()
    await session.refresh(row)

    log.info(
        "github_app.credential_revoked",
        actor_id=str(actor.id),
        credential_id=str(credential_id),
        team_id=str(row.team_id),
    )
    return row


# ---------------------------------------------------------------------------
# Installations: link (opt-in) / list / unlink
# ---------------------------------------------------------------------------


async def link_installation(
    session: AsyncSession,
    actor: CurrentUser,
    credential_id: uuid.UUID,
    *,
    installation_id: str,
    account_login: str | None,
    repository_full_name: str | None,
    project_id: uuid.UUID | None,
) -> GitHubAppInstallation:
    """Link (opt-in) an installation under a credential, optionally to a project.

    RBAC: team_admin of the credential's team. Idempotent on
    (credential, installation, repo): a re-link returns the existing row (with
    ``project_id`` / metadata refreshed to the new values).

    If ``project_id`` is given it must belong to the SAME team as the credential
    (cross-team opt-in is a P0 leak — a credential must never be attachable to
    another team's project).
    """
    credential = await get_credential(session, actor, credential_id)
    if not _is_team_admin(actor, credential.team_id):
        raise GitHubAppForbidden(
            "actor must be a team admin of the credential's team to link an installation"
        )

    # Cross-team opt-in guard: the project must belong to the credential's team.
    if project_id is not None:
        # Local import avoids widening the module's import graph unnecessarily.
        from models import Project

        project_team_id = (
            await session.execute(select(Project.team_id).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project_team_id is None:
            # Existence-hide: do not leak whether the project exists.
            raise GitHubAppNotFound(f"project {project_id} not found")
        if project_team_id != credential.team_id:
            raise GitHubAppForbidden("project belongs to a different team than the credential")

    bind_audit_team(credential.team_id)

    # Idempotent re-link: look up an existing row for this slot first. NULL repo
    # needs an explicit IS NULL match (=NULL is never true in SQL).
    existing_stmt = select(GitHubAppInstallation).where(
        GitHubAppInstallation.credential_id == credential_id,
        GitHubAppInstallation.installation_id == installation_id,
    )
    if repository_full_name is None:
        existing_stmt = existing_stmt.where(GitHubAppInstallation.repository_full_name.is_(None))
    else:
        existing_stmt = existing_stmt.where(
            GitHubAppInstallation.repository_full_name == repository_full_name
        )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()

    if existing is not None:
        # Refresh mutable metadata + opt-in link on re-link.
        existing.account_login = account_login
        existing.project_id = project_id
        await session.commit()
        await session.refresh(existing)
        log.info(
            "github_app.installation_relinked",
            actor_id=str(actor.id),
            credential_id=str(credential_id),
            installation_id=installation_id,
            project_id=str(project_id) if project_id else None,
        )
        return existing

    row = GitHubAppInstallation(
        credential_id=credential_id,
        installation_id=installation_id,
        account_login=account_login,
        repository_full_name=repository_full_name,
        project_id=project_id,
        created_by_user_id=actor.id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Lost a race against a concurrent identical link — treat as idempotent.
        await session.rollback()
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        raise GitHubAppConflict("installation link conflict") from exc

    await session.refresh(row)
    log.info(
        "github_app.installation_linked",
        actor_id=str(actor.id),
        credential_id=str(credential_id),
        installation_id=installation_id,
        project_id=str(project_id) if project_id else None,
    )
    return row


async def list_installations(
    session: AsyncSession,
    actor: CurrentUser,
    credential_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[GitHubAppInstallation], int]:
    """List installations under a credential the actor may view (else 404)."""
    # get_credential enforces the existence-hide tenant gate.
    await get_credential(session, actor, credential_id)

    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    base = select(GitHubAppInstallation).where(GitHubAppInstallation.credential_id == credential_id)
    count_base = (
        select(func.count())
        .select_from(GitHubAppInstallation)
        .where(GitHubAppInstallation.credential_id == credential_id)
    )
    total = int((await session.execute(count_base)).scalar_one())
    rows_stmt = (
        base.order_by(GitHubAppInstallation.created_at.desc(), GitHubAppInstallation.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = list((await session.execute(rows_stmt)).scalars().all())
    return rows, total


async def unlink_installation(
    session: AsyncSession,
    actor: CurrentUser,
    credential_id: uuid.UUID,
    installation_row_id: uuid.UUID,
) -> None:
    """Remove an installation link (hard delete of the link row).

    RBAC: team_admin of the credential's team. The link row is a pure pointer
    (no secret), so a hard DELETE is correct — there is nothing to soft-retain.
    Existence-hide on a missing / non-member credential (404). Idempotent: an
    already-absent link row is a no-op.
    """
    credential = await get_credential(session, actor, credential_id)
    if not _is_team_admin(actor, credential.team_id):
        raise GitHubAppForbidden(
            "actor must be a team admin of the credential's team to unlink an installation"
        )

    row = (
        await session.execute(
            select(GitHubAppInstallation).where(
                GitHubAppInstallation.id == installation_row_id,
                GitHubAppInstallation.credential_id == credential_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return  # idempotent no-op

    bind_audit_team(credential.team_id)
    await session.delete(row)
    await session.commit()

    log.info(
        "github_app.installation_unlinked",
        actor_id=str(actor.id),
        credential_id=str(credential_id),
        installation_row_id=str(installation_row_id),
    )


# ---------------------------------------------------------------------------
# mint_installation_token (SECURITY-CRITICAL)
# ---------------------------------------------------------------------------


def build_app_jwt(*, app_id: str, private_key_pem: str, now: datetime | None = None) -> str:
    """Build a short-lived RS256 App JWT signed with the App PEM private key.

    Claims:
      - ``iss`` = app_id
      - ``iat`` = now - clock-skew pad (absorbs forward skew vs GitHub)
      - ``exp`` = iat + min(TTL, 9 min) — strictly inside GitHub's 10-min ceiling

    The JWT is signed in-memory and returned; the PEM is never logged.

    Clock assumption: this assumes host clocks are NTP-synced (within ~1 min);
    the 9-min exp sits 1 min under GitHub's 10-min ceiling, leaving margin under
    modest forward skew.
    """
    issued = now or _now()
    iat = int((issued - timedelta(seconds=_APP_JWT_CLOCK_SKEW_SECONDS)).timestamp())
    exp = iat + _APP_JWT_MAX_TTL_SECONDS
    claims: dict[str, Any] = {"iss": app_id, "iat": iat, "exp": exp}
    return str(jwt.encode(claims, private_key_pem, algorithm=_APP_JWT_ALGORITHM))


async def mint_installation_token(
    session: AsyncSession,
    credential_id: uuid.UUID,
    installation_id: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Mint a short-lived installation access token for ``installation_id``.

    Flow:
      1. Load the (live) credential and decrypt its PEM private key in memory.
      2. Build a short-lived RS256 App JWT (see :func:`build_app_jwt`).
      3. POST ``{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens``
         with ``Authorization: Bearer <app_jwt>`` to exchange it for an
         installation token.

    Returns ``{"token": <str>, "expires_at": <iso8601 str>}``.

    The ``http_client`` is INJECTABLE so tests mock the GitHub exchange via an
    ``httpx.MockTransport`` — this function never opens an un-mockable network
    connection in tests. NEVER logs the PEM, the App JWT, or the returned token.

    Note: this is the b1 FOUNDATION (b3 will consume it for auto-PRs). It is NOT
    wired to an HTTP endpoint in b1 — exercising it requires the credential row
    + a mocked GitHub, which the unit tests provide.
    """
    # Re-validate the installation id at this trust boundary BEFORE any DB load
    # or URL interpolation — never trust the caller pre-validated. A non-numeric
    # / CRLF / path-traversal value raises here, so it can never reach the URL.
    _validate_installation_id(installation_id)

    row = (
        await session.execute(
            select(GitHubAppCredential).where(
                GitHubAppCredential.id == credential_id,
                GitHubAppCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise GitHubAppNotFound(f"github app credential {credential_id} not found or revoked")

    try:
        private_key_pem = decrypt_secret(row.private_key_encrypted)
    except SecretDecryptionError as exc:
        # Key rotation mismatch / corruption. Surface as a clean operational
        # error (no key/plaintext bytes in the message — see core.crypto).
        log.error(
            "github_app.private_key_undecryptable",
            credential_id=str(credential_id),
        )
        raise GitHubAppConfigError(
            "stored GitHub App private key could not be decrypted "
            "(encryption key may have rotated)"
        ) from exc

    app_jwt = build_app_jwt(app_id=row.app_id, private_key_pem=private_key_pem)
    # Drop the PEM as soon as the JWT is signed — it is no longer needed.
    del private_key_pem

    url = f"{github_api_url()}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    timeout = github_app_token_http_timeout_seconds()

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout)
    try:
        # follow_redirects=False per-request: the App JWT must NEVER follow a 3xx
        # to an attacker-controlled host, regardless of how an injected client was
        # constructed. Matches the repo-wide outbound-client contract.
        resp = await client.post(
            url, headers=headers, timeout=timeout, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        raise GitHubAppTokenError(
            "failed to reach GitHub to exchange the App JWT for an installation token"
        ) from exc
    finally:
        if owns_client:
            await client.aclose()
        # Drop the signed JWT — it must not linger / be logged.
        del app_jwt

    if resp.status_code != 201:
        # Do NOT echo the GitHub response body verbatim (it may include rate-
        # limit / id details we don't want to leak). Log the status only.
        log.warning(
            "github_app.token_exchange_non_201",
            credential_id=str(credential_id),
            installation_id=installation_id,
            status_code=resp.status_code,
        )
        raise GitHubAppTokenError(
            f"GitHub returned status {resp.status_code} for the App-token exchange"
        )

    try:
        payload = resp.json()
        token = payload["token"]
        expires_at = payload["expires_at"]
    except (ValueError, KeyError, TypeError) as exc:
        raise GitHubAppTokenError(
            "GitHub App-token exchange returned an unexpected response shape"
        ) from exc

    log.info(
        "github_app.installation_token_minted",
        credential_id=str(credential_id),
        installation_id=installation_id,
        # NEVER the token; only its expiry for observability.
        expires_at=expires_at,
    )
    return {"token": token, "expires_at": expires_at}


__all__ = [
    "GitHubAppConfigError",
    "GitHubAppConflict",
    "GitHubAppError",
    "GitHubAppForbidden",
    "GitHubAppNotFound",
    "GitHubAppTokenError",
    "build_app_jwt",
    "get_credential",
    "link_installation",
    "list_credentials",
    "list_installations",
    "mint_installation_token",
    "register_credential",
    "revoke_credential",
    "unlink_installation",
    "_credential_out_fields",
]
