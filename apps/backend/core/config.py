"""
Runtime configuration accessors.

CLAUDE.md core rule #11: do not cache environment variables in module-level
constants. Every accessor below calls os.getenv() at the moment it is invoked
so the values stay correct when the process re-reads its environment (e.g.
docker-compose --env-file changes between sessions).
"""

from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql+asyncpg://trustedoss:trustedoss@postgres:5432/trustedoss"
DEFAULT_REDIS_URL = "redis://redis:6379/0"

# C-1: minimum SECRET_KEY length (HS256 JWT). 32 chars is the floor we enforce
# in non-dev environments so an attacker cannot guess the signing key.
_MIN_SECRET_LEN = 32

# Dev-only placeholder. Used only when APP_ENV=dev and SECRET_KEY is unset.
# The string is intentionally self-documenting so a leak is obvious.
_DEV_PLACEHOLDER_SECRET = "dev-only-secret-key-min-32-chars-DO-NOT-USE-IN-PROD"  # noqa: S105


def database_url() -> str:
    """Return the SQLAlchemy async DSN (asyncpg driver)."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def database_url_sync() -> str:
    """
    Sync DSN derived from DATABASE_URL.

    Alembic runs migrations through the synchronous engine (psycopg2) while the
    application uses asyncpg. We strip the +asyncpg suffix here so callers do
    not have to think about driver dialects.
    """
    raw = database_url()
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


def jsonb_row_size_limit_bytes() -> int:
    """Per-row JSON byte ceiling before truncate (I-1 guard)."""
    return int(os.getenv("JSONB_ROW_SIZE_LIMIT_BYTES", str(256 * 1024)))


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
