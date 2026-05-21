"""
Runtime configuration accessors.

CLAUDE.md core rule #11: do not cache environment variables in module-level
constants. Every accessor below calls os.getenv() at the moment it is invoked
so the values stay correct when the process re-reads its environment (e.g.
docker-compose --env-file changes between sessions).
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus

DEFAULT_DATABASE_URL = "postgresql+asyncpg://trustedoss:trustedoss@postgres:5432/trustedoss"
DEFAULT_REDIS_URL = "redis://redis:6379/0"

# C-1: minimum SECRET_KEY length (HS256 JWT). 32 chars is the floor we enforce
# in non-dev environments so an attacker cannot guess the signing key.
_MIN_SECRET_LEN = 32

# Dev-only placeholder. Used only when APP_ENV=dev and SECRET_KEY is unset.
# The string is intentionally self-documenting so a leak is obvious.
_DEV_PLACEHOLDER_SECRET = "dev-only-secret-key-min-32-chars-DO-NOT-USE-IN-PROD"  # noqa: S105


def database_url() -> str:
    """Return the SQLAlchemy async DSN (asyncpg driver) for runtime use.

    Resolution order (Chore O — security-reviewer H2 fix; marathon
    bundle 8 — L1 role separation):

    1. ``DATABASE_URL_APP`` — runtime DML-only role (``trustedoss_app``).
       Set by install.sh / upgrade.sh after migration 0014 grants the
       role its DML privileges. When set, the runtime cannot DROP
       triggers, ALTER tables, TRUNCATE audit_logs, etc. Migration code
       paths use :func:`database_url_owner` instead.
    2. ``DATABASE_URL`` — single connection string (legacy / dev /
       single-role deployments). Preserves docker-compose dev/prod and
       any operator-supplied DSN. Returned verbatim.
    3. Composed from ``DB_USER`` / ``DB_PASSWORD`` / ``DB_HOST`` / ``DB_NAME``
       (+ optional ``DB_PORT``, default ``5432``). Used by the GCP Cloud Run
       module which mounts ``DB_PASSWORD`` from Secret Manager — building the
       URL at runtime keeps the secret out of Terraform state and out of the
       Cloud Run revision spec.
    4. Fallback to :data:`DEFAULT_DATABASE_URL` so unit tests and local bring-up
       work without explicit configuration.

    Per CLAUDE.md core rule #11 every ``os.getenv`` call happens here at
    invocation time — no module-level caching.

    The composed branch URL-encodes ``DB_PASSWORD`` via ``quote_plus`` so
    passwords containing ``@``, ``:``, ``/``, ``#``, or ``%`` survive the round
    trip into asyncpg's DSN parser.
    """
    runtime_url = os.getenv("DATABASE_URL_APP")
    if runtime_url:
        return runtime_url
    direct = os.getenv("DATABASE_URL")
    if direct:
        return direct

    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    name = os.getenv("DB_NAME")

    # All-or-nothing on the composed path: a partial set is almost always a
    # misconfiguration we want to fail fast on (rather than silently falling
    # through to DEFAULT_DATABASE_URL and hitting a confusing auth error).
    composed = [user, password, host, name]
    if any(composed):
        missing = [
            label
            for label, value in (
                ("DB_USER", user),
                ("DB_PASSWORD", password),
                ("DB_HOST", host),
                ("DB_NAME", name),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "DATABASE_URL not set and composed DB_* env vars incomplete; "
                f"missing: {', '.join(missing)}"
            )
        # `missing` empty implies all four are set — narrow types for mypy.
        assert user is not None
        assert password is not None
        assert host is not None
        assert name is not None
        port = os.getenv("DB_PORT", "5432")
        # asyncpg accepts host=/cloudsql/... as a unix socket path encoded in
        # the host segment (Cloud SQL Auth Proxy). quote_plus on the password
        # is the only piece that needs URL escaping; the host comes from
        # operator-controlled Terraform variables.
        return (
            f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        )

    return DEFAULT_DATABASE_URL


def database_url_sync() -> str:
    """
    Sync DSN derived from :func:`database_url`.

    Alembic runs migrations through the synchronous engine (psycopg2) while the
    application uses asyncpg. We strip the ``+asyncpg`` suffix here so callers
    do not have to think about driver dialects.
    """
    raw = database_url()
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def database_url_owner() -> str:
    """Return the DSN for the migration-owning role (Marathon bundle 8 / L1).

    Resolution order:

    1. ``DATABASE_URL_OWNER`` — explicit owner DSN (``trustedoss_owner``
       in the L1 split deployment). Used by ``alembic/env.py`` so DDL
       (CREATE / ALTER / DROP) runs as a role with table ownership.
    2. ``DATABASE_URL`` — legacy single-role fallback. Dev / CI use
       this; the migration's GRANT block is a no-op when the
       ``trustedoss_app`` runtime role doesn't exist.
    3. Composed ``DB_*`` env or ``DEFAULT_DATABASE_URL`` — same fallback
       chain as :func:`database_url`.

    Critical: this MUST NOT silently fall back to ``DATABASE_URL_APP``.
    If only the runtime URL is set, alembic would try to run DDL as a
    role without the necessary privileges and fail mid-migration.
    Operators following the L1 procedure set BOTH env vars; mixing
    them is an invalid configuration.

    Per CLAUDE.md core rule #11 — read at call time.
    """
    owner_url = os.getenv("DATABASE_URL_OWNER")
    if owner_url:
        return owner_url
    # Fall back to the legacy single-role DSN. We deliberately do NOT
    # consult DATABASE_URL_APP here — see docstring.
    direct = os.getenv("DATABASE_URL")
    if direct:
        return direct
    # Compose from DB_* env or use the default — duplicate the
    # database_url() composition path so the owner fallback shape stays
    # symmetric with the runtime fallback shape.
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    name = os.getenv("DB_NAME")
    if any([user, password, host, name]):
        if not all([user, password, host, name]):
            raise RuntimeError(
                "DATABASE_URL_OWNER unset and composed DB_* env vars "
                "incomplete; specify DATABASE_URL_OWNER or fill all of "
                "DB_USER / DB_PASSWORD / DB_HOST / DB_NAME"
            )
        assert user and password and host and name
        port = os.getenv("DB_PORT", "5432")
        return (
            f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        )
    return DEFAULT_DATABASE_URL


def database_url_owner_sync() -> str:
    """Sync owner DSN — used by alembic/env.py (psycopg2)."""
    raw = database_url_owner()
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


def secret_key() -> str:
    """
    Return the JWT signing key.

    C-1 (security-reviewer blocker): in non-dev environments SECRET_KEY MUST
    be set explicitly to a value of at least _MIN_SECRET_LEN characters. dev
    falls back to a clearly-marked placeholder so local bring-up still works.

    Raises:
        RuntimeError: when APP_ENV != 'dev' and SECRET_KEY is missing or too
            short. main.py's lifespan calls this once at startup so the
            process fails fast rather than booting with a weak key.
    """
    raw = os.getenv("SECRET_KEY")
    env = app_env()

    if raw is None or raw == "":
        if env == "dev":
            return _DEV_PLACEHOLDER_SECRET
        raise RuntimeError(
            "SECRET_KEY is required in non-dev environments " f"(set >={_MIN_SECRET_LEN} chars)"
        )

    if len(raw) < _MIN_SECRET_LEN:
        raise RuntimeError(
            f"SECRET_KEY must be at least {_MIN_SECRET_LEN} characters " f"(got {len(raw)})"
        )
    return raw


def access_token_expire_minutes() -> int:
    return int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))


def refresh_token_expire_days() -> int:
    return int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO").upper()


def cors_allowed_origins() -> list[str]:
    """
    Comma-separated origin list. Production must set this explicitly;
    dev defaults to the Vite dev server.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def app_env() -> str:
    """`dev`, `staging`, `prod` — informational, drives a few CORS/log defaults."""
    return os.getenv("APP_ENV", "dev").lower()


# ---------------------------------------------------------------------------
# Phase 2 PR #8 — scan pipeline configuration accessors.
#
# Every accessor below resolves the environment at call time so the worker
# picks up changes without a rebuild (CLAUDE.md core rule #11). Defaults match
# `.env.example` — the docker-compose dev stack runs out of the box.
# ---------------------------------------------------------------------------


def dt_url() -> str:
    """Dependency-Track REST base URL (no trailing slash)."""
    return os.getenv("DT_URL", "http://dtrack-api:8080").rstrip("/")


def dt_api_key() -> str:
    """DT API key. Empty string when unset (mock backend / local smoke)."""
    return os.getenv("DT_API_KEY", "")


def dt_request_timeout_seconds() -> float:
    return float(os.getenv("DT_REQUEST_TIMEOUT_SECONDS", "30"))


def dt_breaker_failure_threshold() -> int:
    """Consecutive failures that flip the breaker CLOSED → OPEN."""
    return int(os.getenv("DT_BREAKER_FAILURE_THRESHOLD", "5"))


def dt_breaker_cooldown_seconds() -> int:
    """How long the breaker stays OPEN before allowing a HALF_OPEN probe."""
    return int(os.getenv("DT_BREAKER_COOLDOWN_SECONDS", "30"))


def dt_health_check_endpoint() -> str:
    """Path appended to dt_url() for the health heartbeat."""
    return os.getenv("DT_HEALTH_ENDPOINT", "/api/version")


def dt_auto_restart_enabled() -> bool:
    """If true, the health monitor will attempt `docker restart dtrack-api`."""
    raw = os.getenv("DT_AUTO_RESTART", "false").lower()
    return raw in ("1", "true", "yes", "on")


def scan_backend_mode() -> str:
    """`real` (subprocess cdxgen/ort/trivy) or `mock` (fixture JSON)."""
    return os.getenv("TRUSTEDOSS_SCAN_BACKEND", "real").lower()


def workspace_root() -> str:
    """Root directory under which per-scan workspaces live."""
    return os.getenv("WORKSPACE_HOST_PATH", "/tmp/trustedoss")  # noqa: S108


def scan_soft_time_limit_seconds() -> int:
    """Celery ``soft_time_limit`` for scan tasks (PR-A1 scan stability).

    When a scan task runs longer than this, Celery raises
    :class:`celery.exceptions.SoftTimeLimitExceeded` inside the worker. The
    task catches it, cleans up the workspace, and marks the scan ``failed``
    with a clear ``error_message`` — this is the *primary* timeout mechanism.

    Default 3600s (1 hour) covers cdxgen + ORT + DT polling on the pilot repos
    with comfortable headroom. The hard limit (SIGKILL) sits above this as a
    safety net for a task that ignores or deadlocks past the soft signal.

    Read at call time per CLAUDE.md core rule #11 so an operator can retune
    the worker via env without a rebuild.
    """
    return int(os.getenv("SCAN_SOFT_TIME_LIMIT_SECONDS", "3600"))


# Minimum grace window the hard (SIGKILL) limit must sit ABOVE the soft limit,
# in seconds. The soft-limit handler needs time to rmtree the workspace and
# mark the scan ``failed`` before SIGKILL lands; 60s is comfortable for that
# bookkeeping even on a loaded worker.
SCAN_TIMEOUT_MIN_GRACE_SECONDS = 60


def scan_hard_time_limit_seconds() -> int:
    """Celery ``time_limit`` (hard, SIGKILL) for scan tasks (PR-A1).

    The hard limit is the last-resort backstop: if the worker thread does not
    surface ``SoftTimeLimitExceeded`` (e.g. a C-extension or subprocess stuck
    in an uninterruptible syscall), Celery sends SIGKILL at this boundary so
    the worker slot is reclaimed. It must be strictly greater than the soft
    limit; the default leaves a 5-minute window for graceful soft-limit
    cleanup before the kill.

    Default 3900s (65 minutes). Read at call time (rule #11).

    M2 (security-reviewer): we *enforce* the ``hard > soft`` invariant at read
    time by clamping, rather than trusting the operator. If someone sets
    ``SCAN_HARD_TIME_LIMIT_SECONDS <= SCAN_SOFT_TIME_LIMIT_SECONDS`` (e.g. a
    typo, or swapping the two env vars), SIGKILL would fire at or before the
    soft-limit handler — killing the worker mid-cleanup, leaking the workspace,
    and leaving the scan stuck in ``running`` forever. We clamp the effective
    hard limit to ``soft + SCAN_TIMEOUT_MIN_GRACE_SECONDS`` so the soft handler
    always gets a window. Clamp (not raise) is deliberate: a single mis-set env
    var must not break *every* scan dispatch — it degrades to a safe default
    instead. Both inputs are read via ``os.getenv`` at call time (rule #11).
    """
    soft = scan_soft_time_limit_seconds()
    raw_hard = int(os.getenv("SCAN_HARD_TIME_LIMIT_SECONDS", "3900"))
    return max(raw_hard, soft + SCAN_TIMEOUT_MIN_GRACE_SECONDS)


def workspace_orphan_max_age_seconds() -> int:
    """Minimum age before a terminal-scan workspace is eligible for reclaim.

    The workspace orphan cleaner only deletes a per-scan workspace directory
    when (a) the scan row is in a terminal state (succeeded / failed /
    cancelled) and (b) the directory's mtime is older than this grace period.
    The grace window avoids racing a worker that is still inside its
    ``finally: shutil.rmtree(...)`` block right after the row flipped terminal.

    Default 900s (15 minutes). Read at call time (rule #11).
    """
    return int(os.getenv("WORKSPACE_ORPHAN_MAX_AGE_SECONDS", "900"))


def jsonb_row_size_limit_bytes() -> int:
    """Per-row JSON byte ceiling before truncate (I-1 guard)."""
    return int(os.getenv("JSONB_ROW_SIZE_LIMIT_BYTES", str(256 * 1024)))


# ---------------------------------------------------------------------------
# Phase 2 PR #9 — WebSocket gateway configuration accessors.
#
# The WebSocket scan-progress channel name is shared between the FastAPI
# router (`api/v1/ws.py`) and any future publisher (Celery `_set_stage()` will
# publish here in a follow-up). Keeping it as a function rather than a module
# constant is intentional — CLAUDE.md core rule #11 forbids module-level
# environment caching, and even though this particular value is not env-driven
# today, the helper signature lets us layer in `WS_CHANNEL_PREFIX` later
# without changing call sites.
# ---------------------------------------------------------------------------


def scan_progress_channel(scan_id: str) -> str:
    """Redis pub/sub channel for one scan's progress events.

    Worker side publishes `{"percent": int, "step": str, "ts": iso8601}` JSON
    payloads here; the WebSocket gateway subscribes per-connection. Both ends
    must use this helper so a future prefix/namespace change is centralized.
    """
    return f"scan:{scan_id}:progress"


def websocket_max_connections_per_user() -> int:
    """Per-user concurrent WebSocket connection ceiling (DoS guard).

    A 4th connection from the same user evicts the oldest with close code
    1001 (going_away, reason="newer_connection"). Default 3 covers a normal
    user with two browser tabs + an iOS app; production can tune via the
    env var WEBSOCKET_MAX_CONNECTIONS_PER_USER.

    Note: the limit is enforced per worker process. Multi-worker deployments
    therefore allow up to N * worker_count connections per user; migrating to
    a Redis-backed counter is a follow-up TODO once we run more than one
    backend replica.
    """
    return int(os.getenv("WEBSOCKET_MAX_CONNECTIONS_PER_USER", "3"))


def websocket_auth_timeout_seconds() -> float:
    """How long the gateway waits for the first `{"type":"auth"}` frame.

    Connections that do not deliver an auth message within this window are
    closed with code 1008 (policy violation) and reason="auth_timeout".
    Default 1.0 second — generous for healthy clients, hostile to silent
    handshake-only attempts.
    """
    return float(os.getenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "1.0"))


# ---------------------------------------------------------------------------
# Phase 6 PR #18 — notification channel configuration.
#
# Every accessor reads the env at call time (CLAUDE.md core rule #11). When
# the relevant env var is unset / empty we return ``None`` so callers can
# raise :class:`notifications.NotificationDisabled` and fall through cleanly
# instead of attempting a connection to a phantom host.
# ---------------------------------------------------------------------------


def smtp_host() -> str | None:
    raw = os.getenv("SMTP_HOST", "").strip()
    return raw or None


def smtp_port() -> int:
    return int(os.getenv("SMTP_PORT", "587"))


def smtp_user() -> str | None:
    raw = os.getenv("SMTP_USER", "").strip()
    return raw or None


def smtp_password() -> str | None:
    raw = os.getenv("SMTP_PASSWORD", "")
    return raw or None


def smtp_use_starttls() -> bool:
    raw = os.getenv("SMTP_USE_STARTTLS", "true").lower()
    return raw in ("1", "true", "yes", "on")


def smtp_from_address() -> str:
    """``From:`` header for outgoing notifications.

    Defaults to ``no-reply@trustedoss.local`` so dev bring-up works without
    extra config; production deployments override via ``SMTP_FROM``.
    """
    return os.getenv("SMTP_FROM", "no-reply@trustedoss.local")


def smtp_request_timeout_seconds() -> float:
    return float(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))


def slack_webhook_url() -> str | None:
    raw = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    return raw or None


def teams_webhook_url() -> str | None:
    raw = os.getenv("TEAMS_WEBHOOK_URL", "").strip()
    return raw or None


def notification_http_timeout_seconds() -> float:
    return float(os.getenv("NOTIFICATION_HTTP_TIMEOUT_SECONDS", "10"))


def password_reset_base_url() -> str:
    """Frontend base URL embedded in password-reset emails.

    The reset link template is ``{base}/reset-password?token={token}``. Defaults
    to ``http://localhost:5173`` for the Vite dev server.
    """
    return os.getenv("PASSWORD_RESET_BASE_URL", "http://localhost:5173").rstrip("/")


def password_reset_request_rate_limit() -> str:
    """Per-IP slowapi limit for ``POST /auth/forgot-password``.

    Defaults to 5/minute (matches the login policy from CLAUDE.md §3). The
    email-level cooldown is enforced separately in the service so a single
    address cannot be spammed even if the limiter quota is shared across IPs.
    """
    return os.getenv("PASSWORD_RESET_RATE_LIMIT", "5/minute")


def password_reset_email_cooldown_seconds() -> int:
    """Minimum seconds between two reset emails to the same address.

    Returned to the client as ``Retry-After`` only when the cooldown trips.
    """
    return int(os.getenv("PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS", "300"))


# ---------------------------------------------------------------------------
# Phase 8 PR #23 — OAuth (GitHub + Google) demo SaaS configuration.
#
# Per CLAUDE.md core rule #11 every accessor reads ``os.getenv`` at call
# time. When the relevant client id / secret pair is unset (production
# self-hosted deployments without OAuth) the helpers return ``None`` so the
# service can raise a 503 Problem Details with extension
# ``oauth_provider_disabled = true``.
# ---------------------------------------------------------------------------


def github_oauth_client_id() -> str | None:
    raw = os.getenv("GITHUB_CLIENT_ID", "").strip()
    return raw or None


def github_oauth_client_secret() -> str | None:
    raw = os.getenv("GITHUB_CLIENT_SECRET", "")
    return raw or None


def google_oauth_client_id() -> str | None:
    raw = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    return raw or None


def google_oauth_client_secret() -> str | None:
    raw = os.getenv("GOOGLE_CLIENT_SECRET", "")
    return raw or None


def oauth_state_ttl_seconds() -> int:
    """Lifetime of the signed OAuth ``state`` JWT (CSRF guard).

    Five minutes is the OAuth 2.0 RFC 6749 §10.12 recommendation and is
    plenty for a normal browser round-trip from /authorize → consent →
    /callback. Tighter than the access-token TTL because the only
    legitimate consumer is the redirect within the same browser session.
    """
    return int(os.getenv("OAUTH_STATE_TTL_SECONDS", "300"))


def oauth_http_timeout_seconds() -> float:
    """HTTP timeout for outbound calls to OAuth provider APIs.

    GitHub and Google are normally <500ms; we use a 10s timeout so a
    transient slow-DNS / slow-TLS situation does not crash the callback.
    The user already paid the consent step, so retrying via "click sign
    in again" is acceptable on timeout.
    """
    return float(os.getenv("OAUTH_HTTP_TIMEOUT_SECONDS", "10"))


def oauth_login_redirect_default() -> str:
    """Where the SPA lands after a successful OAuth callback.

    Used as the fallback when the caller does not supply ``redirect_after``.
    Mirrors :func:`password_reset_base_url` for the dev Vite server default.
    """
    return os.getenv("OAUTH_LOGIN_REDIRECT_DEFAULT", "http://localhost:5173/").rstrip(
        "/"
    ) or "http://localhost:5173"


def oauth_login_redirect_failure() -> str:
    """Where the SPA lands when the OAuth callback fails.

    Receives ``?error=oauth_failed`` (or a more specific error code) so the
    UI can render an actionable message. Defaults to the SPA's /login route.
    """
    return os.getenv(
        "OAUTH_LOGIN_REDIRECT_FAILURE",
        "http://localhost:5173/login",
    ).rstrip("/")


def validate_cors_origins(origins: list[str], *, env: str) -> None:
    """
    H-3 (security-reviewer blocker): CORS bootstrap guard.

    - `*` is incompatible with `allow_credentials=True` (browsers reject the
      combination), so we reject it outright before the middleware sees it.
    - Production must use https:// — plain http:// origins in prod are a
      configuration mistake worth failing fast on.

    Called from main.py during app construction so a misconfiguration crashes
    boot instead of silently exposing a permissive policy.
    """
    if "*" in origins:
        raise RuntimeError("CORS allow_origins='*' is incompatible with allow_credentials=True")
    if env == "prod":
        bad = [o for o in origins if o.startswith("http://")]
        if bad:
            raise RuntimeError(f"Production CORS origins must use https:// (offenders: {bad})")
