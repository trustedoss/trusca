"""
Runtime configuration accessors.

CLAUDE.md core rule #11: do not cache environment variables in module-level
constants. Every accessor below calls os.getenv() at the moment it is invoked
so the values stay correct when the process re-reads its environment (e.g.
docker-compose --env-file changes between sessions).
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import quote_plus, urlparse

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
        return f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{name}"

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
        return f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
    return DEFAULT_DATABASE_URL


def database_url_owner_sync() -> str:
    """Sync owner DSN — used by alembic/env.py (psycopg2)."""
    raw = database_url_owner()
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


# ---------------------------------------------------------------------------
# B1 — connection-pool tuning (concurrency / stability for hundreds of
# simultaneous users).
#
# SQLAlchemy's QueuePool defaults (pool_size=5, max_overflow=10) cap the
# FastAPI process at ~15 connections, which exhausts under a few dozen
# concurrent request handlers each holding a session across an awaited DB
# round-trip. We raise the ceiling and expose every knob via os.getenv so an
# operator can match the pool to their Postgres `max_connections` budget
# without a rebuild (CLAUDE.md core rule #11 — read at call time, no
# module-level caching).
#
# Sizing guidance (per process):
#   total connections = pool_size + max_overflow
# Multiply by the number of uvicorn workers AND add the Celery worker pools
# (see *_sync helpers below) to stay under Postgres `max_connections`. The
# defaults below (20 + 10 = 30 per FastAPI process; 5 + 5 = 10 per Celery
# worker process) leave generous headroom under Postgres' default 100.
# ---------------------------------------------------------------------------


# L2 (security-reviewer): upper bounds on the connection-pool knobs. A typo
# like ``DB_POOL_SIZE=100000`` would otherwise have each FastAPI/Celery process
# try to open tens of thousands of connections, blowing past Postgres'
# ``max_connections`` (default 100) and DoS-ing the very database the pool is
# meant to serve. We clamp each knob to a generous ceiling — high enough that
# no legitimate single-process deployment is constrained, low enough that a
# fat-finger cannot exhaust ``max_connections``. Per-process total connections
# are ``pool_size + max_overflow``; with the ceilings below a single process
# tops out at 200 + 200 = 400, which an operator running that hot would have
# raised ``max_connections`` for deliberately.
_MAX_POOL_SIZE = 200
_MAX_POOL_OVERFLOW = 200
# Timeout / recycle are time values, not connection counts, but an absurd value
# (a multi-hour acquire timeout, a recycle age of years) is still a misconfig
# worth bounding so the pool stays responsive / fresh.
_MAX_POOL_TIMEOUT_SECONDS = 3600  # 1h — far past any sane acquire wait
_MAX_POOL_RECYCLE_SECONDS = 86_400  # 24h — past any proxy idle-reaper window


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    """Parse an int env var, clamping to ``[minimum, maximum]`` and ignoring junk.

    A misconfigured pool size (negative, zero where positive is required, a
    non-numeric string, or an absurdly large typo) must never crash engine
    construction at startup *or* let a single fat-finger exhaust Postgres'
    ``max_connections``. We fall back to the default on junk, clamp up to
    ``minimum`` (lower bound), and — when ``maximum`` is given — clamp down to
    ``maximum`` (upper bound). An over-the-ceiling value is logged at WARNING
    so the operator notices the typo instead of silently running clamped.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    value = max(value, minimum)
    if maximum is not None and value > maximum:
        # Local import keeps config import-time free of the logging stack.
        import structlog

        structlog.get_logger("config").warning(
            "config.int_env_clamped_to_max",
            env_var=name,
            requested=value,
            clamped_to=maximum,
        )
        value = maximum
    return value


def db_pool_size() -> int:
    """Persistent connections kept open by the async (FastAPI) engine pool."""
    return _int_env("DB_POOL_SIZE", 20, minimum=1, maximum=_MAX_POOL_SIZE)


def db_max_overflow() -> int:
    """Burst connections allowed above ``db_pool_size()`` under load.

    0 is valid (hard cap at pool_size); negative is clamped to 0; an absurd
    value is clamped down to ``_MAX_POOL_OVERFLOW`` (L2).
    """
    return _int_env("DB_MAX_OVERFLOW", 10, minimum=0, maximum=_MAX_POOL_OVERFLOW)


def db_pool_timeout_seconds() -> int:
    """Seconds a request waits for a free connection before raising.

    Bounds tail latency: a request that cannot get a connection within this
    window fails fast (and surfaces as a 500 problem+json) instead of hanging
    the worker indefinitely under a connection stampede.
    """
    return _int_env("DB_POOL_TIMEOUT", 30, minimum=1, maximum=_MAX_POOL_TIMEOUT_SECONDS)


def db_pool_recycle_seconds() -> int:
    """Recycle a pooled connection after this many seconds of age.

    Defends against Postgres / proxy idle-connection reaping (PgBouncer,
    Cloud SQL, stateful firewalls drop idle TCP after ~30-60 min). 1800s
    (30 min) keeps connections fresh well inside typical reaper windows.
    -1 disables recycling. An absurdly large value is clamped down to
    ``_MAX_POOL_RECYCLE_SECONDS`` (24h) so a typo cannot effectively disable
    recycling (the -1 disable sentinel is below the ceiling and unaffected).
    """
    return _int_env("DB_POOL_RECYCLE", 1800, minimum=-1, maximum=_MAX_POOL_RECYCLE_SECONDS)


def db_sync_pool_size() -> int:
    """Persistent connections for the sync (Celery worker) engine pool.

    Celery worker concurrency is low (default 2), so each worker process needs
    far fewer connections than a FastAPI process. Kept on a separate env var
    so operators can tune worker pools independently of the API pool. Clamped
    up to ``_MAX_POOL_SIZE`` (L2) so a typo cannot exhaust max_connections.
    """
    return _int_env("DB_SYNC_POOL_SIZE", 5, minimum=1, maximum=_MAX_POOL_SIZE)


def db_sync_max_overflow() -> int:
    """Burst connections above ``db_sync_pool_size()`` for the Celery engine."""
    return _int_env("DB_SYNC_MAX_OVERFLOW", 5, minimum=0, maximum=_MAX_POOL_OVERFLOW)


def db_sync_pool_timeout_seconds() -> int:
    """Connection-acquire timeout (seconds) for the Celery sync engine."""
    return _int_env("DB_SYNC_POOL_TIMEOUT", 30, minimum=1, maximum=_MAX_POOL_TIMEOUT_SECONDS)


def db_sync_pool_recycle_seconds() -> int:
    """Connection recycle age (seconds) for the Celery sync engine."""
    return _int_env("DB_SYNC_POOL_RECYCLE", 1800, minimum=-1, maximum=_MAX_POOL_RECYCLE_SECONDS)


# ---------------------------------------------------------------------------
# B1 — scan-trigger abuse controls.
#
# Two independent layers guard the scan-trigger surface against abuse and
# accidental overload from hundreds of concurrent users:
#
#   1. Per-user rate limit (slowapi) on POST /v1/projects/{id}/scans — caps
#      how *fast* a single authenticated user can fire triggers.
#   2. Per-team concurrent-scan cap (counted in the service) — caps how *many*
#      scans one team can have queued+running at once, protecting the shared
#      Celery worker pool from a single team's burst.
#
# These are stability caps, distinct from free-tier *quota* (project count /
# daily scan budget), which is a separate concern (bundle 5).
# ---------------------------------------------------------------------------


def scan_trigger_rate_limit() -> str:
    """slowapi limit string for POST /v1/projects/{id}/scans (per user).

    Format is slowapi's ``"<n>/<period>"`` (e.g. ``"20/minute"``). Keyed by
    authenticated user id (falling back to client IP for anonymous callers —
    though the route requires auth, so the fallback only matters for malformed
    tokens). Default 20/minute is generous for interactive use and CI bursts
    while still throttling a runaway script.
    """
    return os.getenv("SCAN_TRIGGER_RATE_LIMIT", "20/minute")


def scan_concurrency_cap_per_team() -> int:
    """Max concurrent (queued+running) scans allowed per team.

    When a trigger would push the team's active-scan count to this value or
    above, the service raises ``ConcurrentScanLimitExceeded`` (429). 0 or a
    negative value disables the cap entirely (treated as "unlimited") so an
    operator can opt out without code changes; this is intentional — the
    per-user rate limit and the per-project active-scan unique index still
    apply.
    """
    return _int_env("SCAN_CONCURRENCY_CAP_PER_TEAM", 10, minimum=0)


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


def demo_read_only() -> bool:
    """v2.1 Track B (B5) — live-demo read-only guard.

    When ``DEMO_READ_ONLY`` is truthy, the ``DemoReadOnlyMiddleware`` (core.middleware)
    rejects every state-changing HTTP request (POST/PUT/PATCH/DELETE) that is not on
    the auth allow-list, returning an RFC 7807 403. GET/HEAD/OPTIONS always pass.

    Resolved at call time per CLAUDE.md core rule #11 so a deploy can flip the flag
    via env without a code change. Accepts the same truthy spellings as the other
    boolean accessors (``1``/``true``/``yes``/``on``, case-insensitive)."""
    raw = os.getenv("DEMO_READ_ONLY", "false").lower()
    return raw in ("1", "true", "yes", "on")


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
    """`real` (subprocess cdxgen/scancode/trivy) or `mock` (fixture JSON)."""
    return os.getenv("TRUSTEDOSS_SCAN_BACKEND", "real").lower()


# ---------------------------------------------------------------------------
# scancode first-party license detection (PR-A2 — replaces ORT).
#
# scancode runs over the cloned first-party source tree only (third-party
# dependency licenses stay declared, sourced from cdxgen). Every accessor
# resolves the env at call time (CLAUDE.md core rule #11) so an operator can
# retune the worker without a rebuild. The three guards below bound the stage
# so a hostile / pathological repo cannot starve the scan budget or the DB.
# ---------------------------------------------------------------------------


def scancode_timeout_seconds() -> int:
    """Hard wall-clock limit (seconds) for one scancode invocation.

    scancode does a per-file license/copyright detection pass; on a large
    first-party tree it can take several minutes. Default 600s (10 min) sits
    comfortably inside the scan soft limit (SCAN_SOFT_TIME_LIMIT_SECONDS,
    default 3600s) alongside cdxgen + DT polling. Read at call time (rule #11).
    """
    return int(os.getenv("SCANCODE_TIMEOUT_SECONDS", "600"))


def scancode_max_files() -> int:
    """Maximum first-party files scancode is allowed to scan in one run.

    A pre-scan walk counts eligible files (after the exclude filter); when the
    count exceeds this ceiling we skip the detection stage with a clear WARNING
    rather than letting scancode spin for the whole budget on a giant monorepo.
    Default 20000 — enough for typical first-party trees, a guard for outliers.
    Read at call time (rule #11).
    """
    return int(os.getenv("SCANCODE_MAX_FILES", "20000"))


def scancode_max_detections() -> int:
    """Maximum number of detected license findings persisted from one scan.

    Bounds the row count written to ``license_findings`` (kind='detected') so a
    pathological tree (every file a distinct LicenseRef) cannot balloon the
    table. Excess detections beyond this cap are dropped with a WARNING; the
    scan still succeeds. Default 5000. Read at call time (rule #11).
    """
    return int(os.getenv("SCANCODE_MAX_DETECTIONS", "5000"))


def scancode_max_result_bytes() -> int:
    """Maximum size (bytes) of the scancode JSON result we will deserialize.

    scancode's result file is keyed off the (attacker-controlled) first-party
    tree: a pathological clone with millions of tiny files, or files seeded with
    huge embedded license texts, can produce a multi-GiB JSON. ``json.load``
    fully materialises the document in memory, so an unbounded result is an OOM
    vector for the worker. Before deserializing we ``stat()`` the file and skip
    parsing (returning zero detections, a degraded-but-non-fatal outcome) when
    it exceeds this ceiling. Default 256 MiB. Read at call time (rule #11).
    """
    return int(os.getenv("SCANCODE_MAX_RESULT_BYTES", str(256 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# v2.3 r1 — govulncheck reachability analysis (Go call-graph, best-effort).
#
# A follow-up Celery task (``tasks.scan_reachability``) runs Go ``govulncheck``
# over a scanned project's preserved source and marks which vulnerability
# findings sit on a real call path. Every guard resolves env at call time
# (CLAUDE.md core rule #11) so an operator retunes the worker without a rebuild.
# Reachability is enrichment, never a primary stage — the defaults bound the run
# so a hostile / pathological module cannot starve the budget or OOM the worker.
# ---------------------------------------------------------------------------


def reachability_enabled() -> bool:
    """Whether the reachability follow-up task is dispatched after a source scan.

    Default ``True`` — reachability is a best-effort enrichment that no-ops
    gracefully when govulncheck is absent / the project isn't Go, so it is safe
    to leave on. An operator can disable the dispatch entirely with
    ``REACHABILITY_ENABLED=false`` (e.g. to shed worker load). Read at call time
    (rule #11). Accepts the usual truthy spellings.
    """
    return os.getenv("REACHABILITY_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def govulncheck_timeout_seconds() -> int:
    """Hard wall-clock limit (seconds) for one ``govulncheck`` invocation.

    govulncheck does a whole-module build + call-graph analysis; on a large Go
    module it can take a few minutes. Default 600s (10 min) sits comfortably
    inside the scan soft limit. On timeout the adapter returns an empty result
    and the findings stay NULL ("not analysed"). Read at call time (rule #11).
    """
    return int(os.getenv("GOVULNCHECK_TIMEOUT_SECONDS", "600"))


def govulncheck_max_output_bytes() -> int:
    """Maximum size (bytes) of govulncheck stdout we will parse.

    The ``-json`` stream is keyed off the (attacker-controlled) module graph; a
    pathological module could in principle emit an unbounded report. The
    streaming parser materialises the text before decoding, so we cap stdout
    before parsing (parsing only the prefix that fits — the parser tolerates a
    truncated final object). Default 64 MiB. Read at call time (rule #11).
    """
    return int(os.getenv("GOVULNCHECK_MAX_OUTPUT_BYTES", str(64 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# G3.1 — source preservation (Protex-style source-tree view).
#
# After a successful source scan we preserve a gzip tarball of the scanned
# tree PLUS the scancode result JSON (folded in as ``.trustedoss/scancode.json``)
# so a later UI can render a file tree + per-line license matches. The scancode
# JSON is the ONLY place per-line match data survives — the adapter discards
# line numbers and ``license_findings`` keeps only spdx + source_path. The
# tarball would otherwise die with the workspace in the task's ``finally`` rmtree.
#
# All four accessors read env at call time (rule #11). Defaults are deliberately
# conservative: preservation is best-effort and must never threaten the volume.
# ---------------------------------------------------------------------------


def scan_source_retention() -> str:
    """Retention policy for preserved scan-source tarballs.

    Only ``"latest"`` is implemented today: a new succeeded scan supersedes the
    project's prior tarball, and the retention beat keeps exactly the tarball
    matching ``Project.latest_scan_id`` (plus any referenced by a non-terminal
    scan). The accessor exists so a future ``"all"`` / ``"none"`` policy can be
    wired without changing call sites. Read at call time (rule #11).
    """
    return os.getenv("SCAN_SOURCE_RETENTION", "latest")


def scan_source_project_quota_bytes() -> int:
    """Per-project ceiling on total preserved-tarball bytes (default 1 GiB).

    Mirrors ``SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES``: a project that scans a huge
    tree repeatedly must not fill the workspace volume. With retention=latest a
    project normally holds a single tarball, but a re-run that has not yet
    superseded the prior one, or a sweep that lost a race, can transiently leave
    two — the quota bounds that. On exceed the preservation stage skips + logs;
    it NEVER raises into the scan. Read at call time (rule #11).
    """
    return int(os.getenv("SCAN_SOURCE_PROJECT_QUOTA_BYTES", str(1024**3)))


def scan_source_max_tarball_bytes() -> int:
    """Hard ceiling on a single preserved tarball's *written* size (default 512 MiB).

    We count the actual gzip bytes as we stream members into the tar and abort
    (deleting the partial temp file, skipping preservation) the instant the total
    crosses this cap — a large monorepo source tree must not produce a multi-GiB
    artifact that defeats the per-project quota in one shot. Best-effort: an
    over-cap tree degrades to "no tarball", never a failed scan. Read at call
    time (rule #11).
    """
    return int(os.getenv("SCAN_SOURCE_MAX_TARBALL_BYTES", str(512 * 1024 * 1024)))


def scan_source_viewer_max_file_bytes() -> int:
    """Max bytes of a single preserved file the source-tree viewer will return.

    Defined now for G3.2 (the viewer endpoint): a tarball can hold an arbitrarily
    large individual file, and the viewer must bound how much it reads back into
    a response so a single huge member cannot OOM the API process. Default 2 MiB.
    Read at call time (rule #11).
    """
    return int(os.getenv("SCAN_SOURCE_VIEWER_MAX_FILE_BYTES", str(2 * 1024 * 1024)))


def scan_source_raw_download_max_bytes() -> int:
    """Max bytes of a single preserved file the RAW download endpoint will stream.

    G3.3 raw download: the in-app viewer caps content at
    ``scan_source_viewer_max_file_bytes()`` (default 2 MiB) for the rendered
    line-by-line preview. A truncated / binary file's "download" button needs the
    WHOLE member, not the capped viewer bytes, so it streams through
    ``source-file?raw=true`` bounded by this much larger ceiling instead. It is
    still a hard cap (a single preserved member cannot exceed
    ``scan_source_max_tarball_bytes()`` anyway) so a pathological member can never
    stream an unbounded body into the request. Default 512 MiB — large enough to
    cover any preserved member while still bounded. Read at call time (rule #11).
    """
    return int(os.getenv("SCAN_SOURCE_RAW_DOWNLOAD_MAX_BYTES", str(512 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# v2.3-s1 — cosign SBOM signing.
#
# After a source scan generates the CycloneDX SBOM we sign it with cosign so a
# downstream consumer can verify the artifact's integrity + provenance. D2
# decision: KEY-BASED signing is the DEFAULT (self-hosted / on-prem / air-gapped
# is the first-class target); KEYLESS (OIDC, sigstore Fulcio/Rekor) is an opt-in
# alternative enabled via COSIGN_KEYLESS=true.
#
# Signing is BEST-EFFORT: a missing cosign binary, an unconfigured key, or a
# cosign failure logs a structured WARNING and the scan still succeeds — an
# unsigned SBOM is a degraded-but-non-fatal outcome, never a scan-breaking one
# (same philosophy as the scancode / preserve stages). Every accessor reads env
# at call time (CLAUDE.md core rule #11) so an operator can flip the toggle /
# rotate the key path without rebuilding the image.
# ---------------------------------------------------------------------------


def cosign_keyless() -> bool:
    """Whether to use cosign KEYLESS (OIDC) signing instead of key-based.

    Default ``false`` → key-based (the D2 default for self-hosted / air-gapped).
    When truthy the adapter signs with ``cosign sign-blob --yes`` and lets cosign
    drive its keyless OIDC flow (ambient identity token in CI, or the configured
    OIDC provider). Read at call time (rule #11). Accepts the same truthy
    spellings as the other boolean accessors.
    """
    raw = os.getenv("COSIGN_KEYLESS", "false").lower()
    return raw in ("1", "true", "yes", "on")


def cosign_key_path() -> str | None:
    """Filesystem path to the cosign PRIVATE key (key-based signing).

    The key file itself lives on a mounted volume (NOT encrypted at rest — it is
    a file, and a passwordless key is meaningless); the key's PASSWORD is what we
    encrypt via ``core.crypto`` (Fernet) and store / pass through env. Returns
    ``None`` when unset/blank so the adapter can skip signing (best-effort).
    Read at call time (rule #11).
    """
    raw = os.getenv("COSIGN_KEY_PATH")
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def cosign_key_password_encrypted() -> str | None:
    """Fernet-encrypted ciphertext of the cosign private-key password.

    The plaintext password NEVER lives in env / config in cleartext: an operator
    encrypts it once (``core.crypto.encrypt_secret``) and stores the token here.
    The adapter decrypts it at signing time and feeds it to cosign via the
    ``COSIGN_PASSWORD`` subprocess env (never on the command line / argv, never
    logged). Returns ``None`` when unset/blank — a passwordless key is then
    assumed (cosign reads an empty ``COSIGN_PASSWORD``). Read at call time
    (rule #11).
    """
    raw = os.getenv("COSIGN_KEY_PASSWORD_ENCRYPTED")
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def cosign_timeout_seconds() -> int:
    """Hard wall-clock limit (seconds) for one cosign invocation.

    Signing a blob is fast (sub-second for key-based; keyless adds an OIDC +
    Rekor round-trip). Default 120s is generous headroom that still bounds a
    hung keyless network call so it cannot eat the scan budget. Read at call
    time (rule #11).
    """
    return int(os.getenv("COSIGN_TIMEOUT_SECONDS", "120"))


# ---------------------------------------------------------------------------
# v2.3-s2 — in-toto attestation + SLSA provenance.
#
# After the SBOM is signed (v2.3-s1) we additionally generate a SLSA provenance
# attestation (an in-toto Statement signed with ``cosign attest-blob``) so a
# downstream consumer can verify HOW + WHERE the SBOM was produced, not just
# that the bytes are intact. The predicate carries only the scan/project ids and
# the build context (timestamps, tool name/version) — NEVER secrets or PII.
#
# Attestation is BEST-EFFORT: it reuses the same cosign key / keyless config as
# signing, and a missing binary / unconfigured key / cosign failure logs a
# structured WARNING and the scan still succeeds (an un-attested SBOM is a
# degraded-but-non-fatal outcome). Both accessors read env at call time
# (CLAUDE.md core rule #11) so an operator can rebrand the builder id without a
# rebuild.
# ---------------------------------------------------------------------------


def slsa_builder_id() -> str:
    """Stable identifier for the TrustedOSS worker as a SLSA build platform.

    Goes into the SLSA provenance predicate's ``runDetails.builder.id`` — a URI
    naming the build platform that produced the SBOM. The default is a
    vendor-neutral URN; an operator can override it with ``SLSA_BUILDER_ID`` to
    name their own deployment (e.g. ``https://ci.example.com/trustedoss-worker``)
    so a verifier can pin provenance to a known builder. Read at call time
    (rule #11). It is build-platform identity, NOT a secret — safe in the
    predicate and logs.
    """
    raw = os.getenv("SLSA_BUILDER_ID")
    if raw is None or raw.strip() == "":
        return "https://github.com/trustedoss/trustedoss-portal/worker"
    return raw.strip()


def slsa_builder_version() -> str:
    """Version string recorded for the TrustedOSS build platform in provenance.

    Goes into the predicate's ``runDetails.builder.version`` (and the SBOM
    generation context's tool version) so a CISA-2025 / NTIA "tool name +
    version" element is satisfiable from the attestation alone. Defaults to the
    bundled portal version; override with ``TRUSTEDOSS_VERSION`` to stamp the
    exact release. Read at call time (rule #11). Not a secret.
    """
    raw = os.getenv("TRUSTEDOSS_VERSION")
    if raw is None or raw.strip() == "":
        return "2.3.0-dev"
    return raw.strip()


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
    return (
        os.getenv("OAUTH_LOGIN_REDIRECT_DEFAULT", "http://localhost:5173/").rstrip("/")
        or "http://localhost:5173"
    )


def oauth_login_redirect_failure() -> str:
    """Where the SPA lands when the OAuth callback fails.

    Receives ``?error=oauth_failed`` (or a more specific error code) so the
    UI can render an actionable message. Defaults to the SPA's /login route.
    """
    return os.getenv(
        "OAUTH_LOGIN_REDIRECT_FAILURE",
        "http://localhost:5173/login",
    ).rstrip("/")


# ---------------------------------------------------------------------------
# v2.2-b1 — GitHub App credential storage + token minting.
#
# Every accessor reads ``os.getenv`` at call time (CLAUDE.md core rule #11) so
# an operator (or GitHub Enterprise Server deployment) can point the App-token
# exchange at a non-public API host without a rebuild.
# ---------------------------------------------------------------------------


class GitHubAppConfigError(RuntimeError):
    """Raised when a GitHub-App-related config value is unsafe / malformed.

    Surfaced at the call boundary (e.g. :func:`github_api_url`) rather than at
    import time so CLAUDE.md core rule #11 (runtime ``os.getenv``) still holds:
    a misconfigured deployment fails the first time it tries to reach GitHub,
    with a clear operator-actionable message and NO secret material echoed.
    """


def _is_internal_host(host: str) -> bool:
    """Return True if ``host`` resolves to an obviously-internal target.

    SSRF guard for the prod App-token exchange: blocks loopback, link-local
    (incl. the cloud metadata IP ``169.254.169.254``), and RFC-1918 private
    ranges when the value is an IP literal, plus the literal ``localhost`` and
    bare single-label hostnames. We do NOT perform DNS resolution here (that
    would be a TOCTOU + a network call at config time); this is a cheap,
    fail-fast literal screen, not a substitute for egress network policy.
    """
    host = host.strip().lower()
    if host == "" or host == "localhost" or host.endswith(".localhost"):
        return True
    # Strip IPv6 brackets if present (urlparse leaves them on for [::1]).
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        # Not an IP literal. Block obvious single-label internal names ("vault",
        # "metadata") — anything without a dot is not a public FQDN.
        return "." not in host
    # IP literal: block loopback (127/8, ::1), link-local (169.254/16, fe80::/10
    # — covers the cloud metadata endpoint), and RFC-1918 private ranges.
    return ip.is_loopback or ip.is_link_local or ip.is_private


def github_api_url() -> str:
    """Base URL for the GitHub REST API (no trailing slash).

    Defaults to the public ``https://api.github.com``. GitHub Enterprise Server
    deployments override this with ``https://<host>/api/v3``. Used by
    ``services.github_app_service.mint_installation_token`` to exchange the
    short-lived App JWT for an installation access token.

    SSRF / cleartext guard (prod only): when ``app_env() == "prod"`` the value
    MUST be ``https://`` and MUST NOT point at an internal host (loopback,
    link-local incl. the ``169.254.169.254`` metadata IP, or RFC-1918 private
    ranges). A violation raises :class:`GitHubAppConfigError` so a misconfigured
    prod deployment cannot silently send the App JWT over cleartext or to an
    attacker-controlled / metadata host. In non-prod (dev / CI / local GHES)
    any scheme and host is allowed so tests and on-box GitHub Enterprise work.
    """
    value = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if app_env() != "prod":
        return value

    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise GitHubAppConfigError(
            "GITHUB_API_URL must use the https:// scheme in production "
            "(the App JWT must never traverse cleartext)"
        )
    host = parsed.hostname or ""
    if _is_internal_host(host):
        raise GitHubAppConfigError(
            "GITHUB_API_URL must not point at an internal host (loopback, "
            "link-local/metadata, or RFC-1918 private range) in production"
        )
    return value


def github_app_token_http_timeout_seconds() -> float:
    """HTTP timeout (seconds) for the App-token exchange call.

    The installation-token exchange is a single small POST; GitHub normally
    answers in <1s. 10s tolerates a transient slow-TLS / slow-DNS hop without
    hanging the request indefinitely.
    """
    return float(os.getenv("GITHUB_APP_TOKEN_HTTP_TIMEOUT_SECONDS", "10"))


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
