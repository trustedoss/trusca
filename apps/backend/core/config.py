# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Runtime configuration accessors.

CLAUDE.md core rule #11: do not cache environment variables in module-level
constants. Every accessor below calls os.getenv() at the moment it is invoked
so the values stay correct when the process re-reads its environment (e.g.
docker-compose --env-file changes between sessions).
"""

from __future__ import annotations

import ipaddress
import math
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

    Resolution order (Chore O — security review finding fix; marathon
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
# (see *_sync helpers below) to stay under Postgres `max_connections`. W2
# (concurrency-scaling-plan-2026-08-22.md §1.6) found the PREVIOUS defaults
# (20 + 10 = 30 per FastAPI process) already over budget on their own once
# multiplied by the image's baked-in 4 uvicorn workers (120, before the
# Celery worker + beat pools are even added); the docstring said "leaves
# generous headroom" while the arithmetic said otherwise. The defaults below
# (5 + 3 = 8 per FastAPI process; 3 + 3 = 6 per Celery worker/beat process)
# are sized so every deployment shape this repo ships (prod/dev/demo compose,
# Helm) fits under its Postgres `max_connections` with the process-count
# multiplier applied; see `core.connection_budget` for the shared formula
# and `.env.example`'s worked examples for the per-deployment numbers.
# ---------------------------------------------------------------------------


# L2 (security review): upper bounds on the connection-pool knobs. A typo
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


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Parse a float env var, clamping to ``[minimum, maximum]`` and ignoring junk.

    The float counterpart of :func:`_int_env`, with two differences that the
    type forces:

    - ``NaN`` and the infinities parse fine as floats but break every
      comparison they take part in (``x >= nan`` is always False), so a guard
      threshold set to one silently stops guarding. They are treated as junk.
    - Both bounds are required. An unbounded float knob has no safe reading of
      "0 or negative means disabled" the way an int cap does; a threshold that
      lands outside its meaningful range is a typo, not an opt-out.

    Junk and out-of-range values both emit a WARNING naming the variable. An
    operator who typed the value needs to learn that the deployment is not
    running what they wrote: silently falling back is how a misconfiguration
    survives to the next incident.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    def _warn(event: str, **fields: object) -> None:
        # Local import keeps config import-time free of the logging stack.
        import structlog

        structlog.get_logger("config").warning(event, env_var=name, **fields)

    try:
        value = float(raw)
    except ValueError:
        _warn("config.float_env_invalid", fell_back_to=default)
        return default
    if not math.isfinite(value):
        _warn("config.float_env_invalid", fell_back_to=default)
        return default
    if value < minimum:
        _warn("config.float_env_clamped_to_min", requested=value, clamped_to=minimum)
        return minimum
    if value > maximum:
        _warn("config.float_env_clamped_to_max", requested=value, clamped_to=maximum)
        return maximum
    return value


def db_pool_size() -> int:
    """Persistent connections kept open by the async (FastAPI) engine pool.

    W2: 5 (default) is sized against the *process count*, not just one
    process; the image bakes 4 uvicorn workers, so this multiplies by 4
    before it even reaches Postgres. See the module-level sizing comment
    above and `core.connection_budget`.
    """
    return _int_env("DB_POOL_SIZE", 5, minimum=1, maximum=_MAX_POOL_SIZE)


def db_max_overflow() -> int:
    """Burst connections allowed above ``db_pool_size()`` under load.

    0 is valid (hard cap at pool_size); negative is clamped to 0; an absurd
    value is clamped down to ``_MAX_POOL_OVERFLOW`` (L2).
    """
    return _int_env("DB_MAX_OVERFLOW", 3, minimum=0, maximum=_MAX_POOL_OVERFLOW)


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
    W2: this budget applies per WORKER PROCESS *and* per beat process, both of
    which multiply by their own replica counts; see `core.connection_budget`.
    """
    return _int_env("DB_SYNC_POOL_SIZE", 3, minimum=1, maximum=_MAX_POOL_SIZE)


def db_sync_max_overflow() -> int:
    """Burst connections above ``db_sync_pool_size()`` for the Celery engine."""
    return _int_env("DB_SYNC_MAX_OVERFLOW", 3, minimum=0, maximum=_MAX_POOL_OVERFLOW)


# ---------------------------------------------------------------------------
# W1: uvicorn worker count, a REAL knob.
#
# ``apps/backend/Dockerfile.prod``'s CMD reads UVICORN_WORKERS at container
# start (``${UVICORN_WORKERS:-4}`` inside an explicit ``sh -c``, evaluated at
# runtime, not a Dockerfile ARG/ENV substitution baked in at build time), so
# this accessor and the process uvicorn actually launched with always agree.
# Before W1, `core.connection_budget`'s estimate and the actual worker count
# were two independently-set values (a now-removed CONN_BUDGET_UVICORN_WORKERS
# hint vs. whatever the image/compose `command:` happened to bake in); an
# operator changing one without the other left the boot-time warning wrong.
# Reading the real env var here removes that double-configuration state.
# ---------------------------------------------------------------------------

_MAX_CONN_BUDGET_REPLICAS = 256


def uvicorn_workers() -> int:
    """How many uvicorn worker processes THIS container was launched with.

    Default 4 matches ``Dockerfile.prod``'s CMD default for an unset env
    (a bare ``docker run`` with no compose/Helm wiring). Minimum 1: zero
    workers would mean no server. Read at call time (rule #11): this only
    describes what the CMD already did at process start, it cannot change a
    running process's actual worker count.
    """
    return _int_env("UVICORN_WORKERS", 4, minimum=1, maximum=_MAX_CONN_BUDGET_REPLICAS)


# ---------------------------------------------------------------------------
# W2: connection-budget fleet-shape hints (informational only).
#
# The backend process cannot see how many sibling CONTAINERS exist (there is
# no env var a container can read to learn its own replica count), unlike
# uvicorn worker count above, which W1 turned into a real, directly-readable
# knob. These two remain estimates only, so `core.connection_budget` can
# still total this deployment's connection draw and warn at boot if it is
# over the Postgres `max_connections` the database actually reports. Set
# them to match how you actually run the containers; a mismatch only
# weakens the boot-time warning, it does not change how many connections the
# pools actually open (that is governed entirely by DB_POOL_SIZE /
# DB_MAX_OVERFLOW / DB_SYNC_* above, multiplied by however many processes
# you actually run).
#
# Defaults mirror this repo's own production docker-compose.yml (1 backend
# container, 1 worker container) so an operator who deploys with the shipped
# defaults and never touches these gets an accurate warning without any
# extra setup.
# ---------------------------------------------------------------------------


def conn_budget_backend_replicas() -> int:
    """How many backend CONTAINERS/PODS this deployment runs.

    Plain `docker-compose up` never scales the backend service (no
    `deploy.replicas`), so the compose default is 1. The Helm chart sets this
    from `.Values.backend.replicaCount` (default 2).
    """
    return _int_env("CONN_BUDGET_BACKEND_REPLICAS", 1, minimum=1, maximum=_MAX_CONN_BUDGET_REPLICAS)


def conn_budget_worker_replicas() -> int:
    """How many Celery worker CONTAINERS/PODS this deployment runs.

    Compose's `WORKER_REPLICAS` only takes effect under Docker Swarm's
    `deploy.replicas` (plain `docker-compose up` ignores it; operators scale
    with `--scale worker=N` instead), so it cannot double as this hint. Set
    this to match whichever scaling method you actually use; the Helm chart
    sets it from `.Values.worker.replicaCount` (default 2).
    """
    return _int_env("CONN_BUDGET_WORKER_REPLICAS", 1, minimum=1, maximum=_MAX_CONN_BUDGET_REPLICAS)


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


def api_read_rate_limit() -> str:
    """slowapi limit string for api-key-accepting read GETs (per actor).

    Covers the scan-status / SBOM-conformance polling endpoints that the CI
    scan-action hits with a ``tos_`` API key. Those routes had NO limiter
    decorator, and because the limiter is decorator-opt-in
    (``default_limits=[]``) an undecorated route has zero throttling. Every
    miss in :func:`services.api_key_service.authenticate_api_key` pays a
    constant-time dummy bcrypt (cost 12, ~50-100ms CPU), so an unbounded flood
    of ``Authorization: Bearer tos_...`` requests on these GETs is a CPU
    exhaustion amplifier (security review, low severity).

    Keyed by actor via ``_authenticated_user_key`` (``apikey:<prefix>`` for a
    key, ``user:<sub>`` for a JWT, ``ip:<addr>`` otherwise) so the key-prefix
    bucket caps failed/garbage ``tos_`` floods BEFORE the bcrypt verify on the
    hot path. Default 60/minute is generous for a CI poller (typically 1 req
    every few seconds) while bounding abuse.
    """
    return os.getenv("API_READ_RATE_LIMIT", "60/minute")


def search_rate_limit() -> str:
    """slowapi limit string for the global search endpoint (per actor).

    Global search (``GET /v1/search``) runs a leading-wildcard ``ILIKE`` over
    ``components`` / ``vulnerabilities`` (non-SARGable → sequential scan + sort
    per request; the ``LIMIT 20`` bounds output rows, not scan cost). The ⌘K
    palette fires one debounced query per keystroke, so search gets its OWN,
    tighter budget instead of sharing the CI-poll ``api_read_rate_limit`` bucket
    — bounding the seq-scan amplifier a scripted client could otherwise drive
    (security-review H-2, Low-1). Keyed per actor via ``_authenticated_user_key``.
    Default 20/minute comfortably covers interactive typing while capping abuse.
    """
    return os.getenv("SEARCH_RATE_LIMIT", "20/minute")


def csv_export_rate_limit() -> str:
    """slowapi limit string for the table CSV exports (per actor).

    An export walks its list service a page at a time up to the 100k row cap,
    so one request is on the order of a couple of hundred list queries, each a
    large join, and it holds a pooled connection for the whole stream. Sharing
    the search or read bucket would misprice it by two orders of magnitude:
    twenty exports a minute is not twenty searches a minute, it is four
    thousand list queries and twenty held connections.

    The budget is per actor rather than per team, because the cost is paid by
    the database the whole deployment shares and a team is not a unit of
    restraint. Default 5/minute leaves interactive use untouched (a person
    clicks Export, waits for the file, maybe re-narrows and clicks again) while
    making a scripted walk pointless.
    """
    return os.getenv("CSV_EXPORT_RATE_LIMIT", "5/minute")


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


# Percent-used range within which the workspace disk guard is a guard at all.
# Below the floor it blocks every scan on a volume that is mostly empty, which
# reads as "nothing is scanning" rather than as a threshold; above 100 it can
# never trip. The admin dashboard warns at 80 and calls 90 critical, and the
# on-call runbook has operators raise this to 98 to drain a full volume, so the
# usable band sits well inside these bounds.
_MIN_DISK_HARD_LIMIT_PCT = 50.0
_MAX_DISK_HARD_LIMIT_PCT = 100.0


def disk_hard_limit_pct() -> float:
    """Workspace percent-used at or above which new scans are refused.

    Default 95: below the point where any single scan can exhaust the volume,
    above the dashboard's 90 critical mark so the operator sees the warning
    before the guard starts refusing work. Set it to 100 to leave the guard
    effectively off.

    Junk falls back to the default and an out-of-range value is clamped, both
    with a WARNING. Before this went through :func:`_float_env` a non-numeric
    value raised out of the guard, a 500 on the webhook receiver, which the
    Git host answers by retrying, and ``0`` made every delivery report
    ``skipped_disk_full`` without a word in the log.
    """
    return _float_env(
        "DISK_HARD_LIMIT_PCT",
        95.0,
        minimum=_MIN_DISK_HARD_LIMIT_PCT,
        maximum=_MAX_DISK_HARD_LIMIT_PCT,
    )


# The webhook receivers are the one unauthenticated surface that does real
# work before it knows who is calling: the HMAC (GitHub) or shared token
# (GitLab) can only be checked once the body has been read and the repository
# resolved, because the signature covers the body. Both knobs below bound what
# a single anonymous caller can make the process spend getting that far.
#
# The floor is generous enough for any real delivery; the ceiling is what
# GitHub itself refuses to deliver above, so nothing under it can be rejected
# here that the Git host would have sent.
_MIN_WEBHOOK_BODY_BYTES = 64 * 1024
_MAX_WEBHOOK_BODY_BYTES = 25 * 1024 * 1024


def webhook_max_body_bytes() -> int:
    """Largest webhook body the receivers will read. Past it they answer 413.

    Default 2 MiB. GitHub caps a push payload's commit list and refuses to
    deliver above 25 MB, so a real delivery is orders of magnitude smaller;
    the headroom is for a pull request with a very long body.
    """
    return _int_env(
        "WEBHOOK_MAX_BODY_BYTES",
        2 * 1024 * 1024,
        minimum=_MIN_WEBHOOK_BODY_BYTES,
        maximum=_MAX_WEBHOOK_BODY_BYTES,
    )


def webhook_rate_limit() -> str:
    """slowapi limit string for the webhook receivers, keyed on source IP.

    IP is the only identity available before the signature is checked, and a
    Git host delivers every repository's events from a shared address range,
    so the budget has to cover an organisation's whole push traffic. 120/minute
    is far above what a busy install sends and far below what makes the parse
    work worth attempting as a flood.

    A 429 here costs a delivery: GitHub does not retry on its own, it only
    offers manual redelivery. Raise this rather than lose events if a large
    install trips it.
    """
    return os.getenv("WEBHOOK_RATE_LIMIT", "120/minute")


def secret_key() -> str:
    """
    Return the JWT signing key.

    C-1 (security review blocker): in non-dev environments SECRET_KEY MUST
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


def intake_requests_enabled() -> bool:
    """Whether the ask-before-using surface exists at all (N3).

    Off by default, and off means the routes are not there rather than there
    and empty. Whether an organization reviews a package before it is pulled
    in or after a scan finds it is a decision about how they work, not one the
    portal should make for them, and a menu entry that 403s teaches people the
    feature is broken rather than that it is not for them.

    Read at call time (rule #11) so a deployment can turn it on without a code
    change, and so a test can flip it around a single request.
    """
    raw = os.getenv("INTAKE_REQUESTS_ENABLED", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


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


def demo_allow_sandbox_scans() -> bool:
    """feat/demo-sandbox-scan — opt-in carve-out to the read-only demo lock.

    PUBLIC-EXPOSED SECURITY BOUNDARY. Default ``false``: when unset the
    ``DemoReadOnlyMiddleware`` (core.middleware) keeps the read-only demo locked
    exactly as before — only the auth allow-list (login / refresh / logout)
    passes and every scan / SBOM write is 403'd.

    When BOTH ``DEMO_READ_ONLY`` **and** this flag are truthy, the middleware
    additionally permits exactly two write path families against the seeded
    "Demo Sandbox" project so a visitor can drive a bounded (≤10 MiB) live scan
    / SBOM upload::

        POST /v1/projects/{id}/scans
        POST /v1/projects/{id}/sbom-ingest

    Nothing else is widened — every other mutation stays blocked.

    Fails CLOSED like :func:`scanoss_enabled`: only the exact truthy tokens
    ``true`` / ``1`` / ``yes`` (case-insensitive) enable it; any other value
    (typos, ``on``, ``enabled``, blank) reads as OFF so a mis-set variable keeps
    the demo fully locked rather than silently opening a public write surface.
    Read at call time (CLAUDE.md core rule #11) so a deploy flips it via env
    without a rebuild. security review target.
    """
    return os.getenv("DEMO_ALLOW_SANDBOX_SCANS", "false").strip().lower() in {
        "true",
        "1",
        "yes",
    }


# ---------------------------------------------------------------------------
# Phase 2 PR #8 — scan pipeline configuration accessors.
#
# Every accessor below resolves the environment at call time so the worker
# picks up changes without a rebuild (CLAUDE.md core rule #11). Defaults match
# `.env.example` — the docker-compose dev stack runs out of the box.
# ---------------------------------------------------------------------------


# W6-#43a (ADR-0001) — DT integration removed. The eight ``dt_*`` accessors
# that used to live here (dt_url / dt_api_key / dt_request_timeout_seconds /
# dt_breaker_{failure_threshold,cooldown_seconds} / dt_health_check_endpoint
# / dt_auto_restart_enabled) are deleted. CVE matching is now Trivy-only via
# ``run_trivy_sbom``; see the W6-#42 vulnerability rematch beat
# for the replacement data path.


def scan_backend_mode() -> str:
    """`real` (subprocess cdxgen/scancode/trivy) or `mock` (fixture JSON)."""
    return os.getenv("TRUSTEDOSS_SCAN_BACKEND", "real").lower()


def cdxgen_spec_version() -> str:
    """CycloneDX spec version cdxgen emits (``--spec-version``).

    Default ``1.5`` (the historical output). Set ``CDXGEN_SPEC_VERSION=1.6`` to
    emit 1.6 (matches the BomLens sidecar default). Resolved at call time
    (CLAUDE.md core rule #11).
    """
    return os.getenv("CDXGEN_SPEC_VERSION", "1.5")


def cdxgen_fetch_license() -> bool:
    """Whether cdxgen resolves each component's license (``FETCH_LICENSE`` env).

    Off by default — license lookups add network round-trips. Set
    ``CDXGEN_FETCH_LICENSE=true`` to enable. Resolved at call time (rule #11).
    """
    return os.getenv("CDXGEN_FETCH_LICENSE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def license_fetch_enabled() -> bool:
    """Whether the post-cdxgen license fetcher enriches unlicensed components.

    W8-#48. When cdxgen emits a component with NO SPDX license (the common
    case for a bare ``requirements.txt`` / ``go.mod`` with no installed
    packages), the pipeline falls back to
    :func:`integrations.license_fetcher.fetch_license`, which asks the
    component's PUBLIC registry (PyPI / Maven Central / crates.io /
    pkg.go.dev) for the declared license by purl and caches the answer. This
    is what pulls the self-scan's "unknown" ratio down from ~90%.

    Default ``true`` — the request carries only a package name+version (the
    same public registry the package manager already contacts), so it is far
    lower-sensitivity than the SCANOSS fingerprint egress (default off) and is
    kept on for the enrichment value. But it IS scan-time egress, so an
    air-gapped deployment sets ``LICENSE_FETCH_ENABLED=false`` to skip it
    cleanly (otherwise every unlicensed component pays a network timeout and
    poisons the fetch cache with negatives). Only the exact falsy tokens
    ``false`` / ``0`` / ``no`` disable; read at call time (rule #11).
    """
    return os.getenv("LICENSE_FETCH_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
    }


# ---------------------------------------------------------------------------
# Runtime-scope SBOM post-filter (Phase K — default ON).
#
# Drops test/provided/dev dependencies from the cdxgen SBOM before persist,
# signing and Trivy matching, so CVE counts and license obligations describe
# the deployable artifact (BomLens build-prep parity). Unlike SCANOSS this is
# a pure local transformation with no egress, so it defaults ON; the guards
# inside integrations/sbom_scope_filter.py make it a structural no-op wherever
# the data to filter safely is absent. Parsing is inverted vs scanoss_enabled:
# only the exact falsy tokens disable, so a typo fails OPEN to the
# correct-by-default behaviour rather than silently reverting to over-counting.
# ---------------------------------------------------------------------------


_SCOPE_FILTER_FALSY = {"false", "0", "no"}


def scan_scope_filter_enabled() -> bool:
    """Master switch for the runtime-scope post-filter stage.

    Default ``true``. Only ``false`` / ``0`` / ``no`` (case-insensitive)
    disable it. Read at call time (rule #11).
    """
    return (
        os.getenv("SCAN_SCOPE_FILTER_ENABLED", "true").strip().lower()
        not in _SCOPE_FILTER_FALSY
    )


def scan_scope_filter_maven_enabled() -> bool:
    """Maven scope-tag filter (drop ``optional``/``excluded`` maven nodes).

    Default ``true``; disable when a project relies on Maven
    ``<optional>true</optional>`` runtime deps (cdxgen tags those ``optional``
    like test scope — documented caveat). Read at call time (rule #11).
    """
    return (
        os.getenv("SCAN_SCOPE_FILTER_MAVEN_ENABLED", "true").strip().lower()
        not in _SCOPE_FILTER_FALSY
    )


def scan_scope_filter_node_enabled() -> bool:
    """npm dev-dependency filter (drop lockfile-classified ``dev`` nodes).

    Default ``true``. Read at call time (rule #11).
    """
    return (
        os.getenv("SCAN_SCOPE_FILTER_NODE_ENABLED", "true").strip().lower()
        not in _SCOPE_FILTER_FALSY
    )


def scan_executor_mode() -> str:
    """How the SBOM-generation stage (build-prep + cdxgen) is executed.

    - ``inprocess`` (default): run prep + cdxgen as worker-local subprocesses,
      exactly as the worker has always done. Fully backward compatible.
    - ``local_docker``: spin a per-environment cdxgen sidecar container via the
      host Docker socket (on-prem single-tenant — trust boundary internal).
    - ``k8s_job``: run the sidecar as a sandboxed Kubernetes Job (multi-tenant
      SaaS — model 2).

    Resolved at call time (CLAUDE.md core rule #11) so an operator can switch
    the executor without a rebuild. Unknown values fall back to ``inprocess``
    at the factory.
    """
    return os.getenv("SCAN_EXECUTOR", "inprocess").lower()


# ---------------------------------------------------------------------------
# scancode first-party license detection (PR-A2 — replaces ORT).
#
# scancode runs over the cloned first-party source tree only (third-party
# dependency licenses stay declared, sourced from cdxgen). Every accessor
# resolves the env at call time (CLAUDE.md core rule #11) so an operator can
# retune the worker without a rebuild. The three guards below bound the stage
# so a hostile / pathological repo cannot starve the scan budget or the DB.
# ---------------------------------------------------------------------------


def scancode_enabled() -> bool:
    """Master switch for the scancode first-party license-detection stage.

    Default ``true`` — scancode is a pure-local pass with NO network egress, so
    it stays on by default and existing behaviour is unchanged. Only the exact
    falsy tokens ``false`` / ``0`` / ``no`` (case-insensitive) disable it, so a
    typo fails OPEN to running (correct-by-default) rather than silently dropping
    detected-license data.

    The public demo sets ``SCANCODE_ENABLED=false`` to shed the per-scan cost on
    the shared sandbox worker (bound alongside SCANOSS off + concurrency cap 1 +
    the ≤10 MiB input ceiling). When disabled, :func:`integrations.scancode.
    run_scancode` short-circuits with a ``ScancodeDisabled`` skip and the
    pipeline continues on declared (cdxgen) licenses only — a degraded-but-non-
    fatal outcome, identical to the existing best-effort skip paths. Read at call
    time (CLAUDE.md core rule #11).
    """
    return os.getenv("SCANCODE_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
    }


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
# SCANOSS vendored-OSS identification (Phase J / P3-11 — opt-in, default OFF).
#
# SCANOSS fingerprints first-party files and sends those fingerprints (a
# Winnowing hash, NOT the source itself) to an external matching API
# (``api.osskb.org`` by default) to identify open-source code that was copied
# into the tree without a package manifest ("vendored" OSS). Because TRUSCA is
# an on-prem PERSISTENT portal — unlike BomLens, which is a local CLI where the
# operator already owns the network boundary — this MUST be explicit opt-in.
# When ``SCANOSS_ENABLED`` is false (the default) the pipeline stage is a
# complete no-op: no scanner runs, no fingerprints are computed, and NOTHING
# leaves the worker. Every accessor resolves env at call time (CLAUDE.md core
# rule #11) so an operator can flip the toggle without a rebuild.
# ---------------------------------------------------------------------------


def scanoss_enabled() -> bool:
    """Whether the SCANOSS vendored-OSS stage runs at all.

    Default ``false`` — this feature sends file fingerprints to an EXTERNAL API,
    so it is disabled unless an operator explicitly turns it on. Only the exact
    truthy tokens ``true`` / ``1`` / ``yes`` (case-insensitive) enable it; any
    other value (including typos, ``on``, ``enabled``) reads as OFF, so a
    mis-set variable fails closed to "no egress" rather than open. Read at call
    time (rule #11).
    """
    return os.getenv("SCANOSS_ENABLED", "false").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def scanoss_api_url() -> str:
    """SCANOSS matching API endpoint (``--apiurl``).

    Default ``https://api.osskb.org`` (the free Open Source Knowledge Base
    endpoint). An operator can point this at a self-hosted SCANOSS server to
    keep fingerprints on-premises. Read at call time (rule #11).
    """
    return os.getenv("SCANOSS_API_URL", "https://api.osskb.org")


def scanoss_api_key() -> str:
    """SCANOSS API key (``--key``), empty when unset.

    The public osskb.org endpoint needs no key; a commercial / self-hosted
    SCANOSS deployment may require one. Empty string means "send no ``--key``
    flag". Read at call time (rule #11). NEVER log this value.
    """
    return os.getenv("SCANOSS_API_KEY", "")


def scanoss_timeout_seconds() -> int:
    """Hard wall-clock limit (seconds) for one ``scanoss-py scan`` invocation.

    SCANOSS walks the first-party tree, fingerprints files, and round-trips
    them to the matching API; on a large tree this can take a few minutes.
    Default 300s (5 min). A non-integer / non-positive value falls back to the
    default so a mis-set variable cannot make the stage hang forever or abort
    on a parse error. Read at call time (rule #11).
    """
    raw = os.getenv("SCANOSS_TIMEOUT_SECONDS")
    if raw is None or raw.strip() == "":
        return 300
    try:
        value = int(raw)
    except ValueError:
        return 300
    return value if value > 0 else 300


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
        return "https://github.com/trustedoss/trusca/worker"
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
        # Not a placeholder version. This value is stated in three places — SLSA
        # provenance, the About surface, and every SBOM TRUSCA emits — and a
        # plausible-looking default made all three assert a release that does
        # not exist. "unknown" is also what the 2026 SBOM minimum elements ask
        # for when no version identifier is available. The release build injects
        # the real tag (see Dockerfile.prod ARG TRUSTEDOSS_VERSION).
        return "unknown"
    return raw.strip()


def cosign_public_key_path() -> str | None:
    """Filesystem path to the cosign PUBLIC key (key-based verification).

    v2.3-s3 — the SBOM signature download surface exposes the cosign PUBLIC key
    so a downstream consumer can run ``cosign verify-blob --key cosign.pub``
    *without* contacting the portal's private key. This accessor resolves, in
    order:

    1. ``COSIGN_PUBLIC_KEY_PATH`` when explicitly set (an operator who keeps the
       public key somewhere other than next to the private key), else
    2. the cosign convention: the private key path with a ``.key`` suffix
       swapped for ``.pub`` (``cosign generate-key-pair`` emits ``cosign.key`` +
       ``cosign.pub`` side by side), else
    3. ``<private-key-path>.pub`` as a last resort.

    Returns ``None`` when neither an explicit public key nor a private key path
    is configured (keyless mode, or signing not configured) — the download
    surface then advises certificate-based verification instead. NEVER returns
    the private key path. Read at call time (CLAUDE.md core rule #11).
    """
    explicit = os.getenv("COSIGN_PUBLIC_KEY_PATH")
    if explicit is not None and explicit.strip() != "":
        return explicit.strip()

    # Derive from the private key path using the cosign generate-key-pair
    # naming convention (cosign.key -> cosign.pub). We only ever return a
    # ``.pub`` path here; the private key bytes are never read or exposed.
    private = cosign_key_path()
    if private is None:
        return None
    if private.endswith(".key"):
        return private[: -len(".key")] + ".pub"
    return private + ".pub"


def sbom_download_max_bytes() -> int:
    """Max bytes of a single SBOM signing artifact (or bundle total) we will serve.

    v2.3-s3 (security hardening): the signature download surface reads each
    artifact fully into memory and buffers the bundle zip in a ``BytesIO``. With
    no ceiling, a pathological / tampered ``ScanArtifact`` (e.g. a multi-GiB SBOM,
    or a ``storage_path`` swapped to a huge file inside the workspace) could OOM
    the API process — a denial-of-service. We gate the read on the persisted
    ``ScanArtifact.byte_size`` BEFORE touching disk, and re-check the actual byte
    length after reading (defense in depth, since ``byte_size`` is itself a row we
    treat as untrusted), and cap the bundle's running total. An over-cap artifact
    surfaces as a 413 (RFC 7807), never an OOM.

    Default 64 MiB comfortably covers any realistic SBOM + signature + cert +
    attestation while still bounding the request. Read at call time
    (CLAUDE.md core rule #11) so an operator can retune without a rebuild.
    """
    return int(os.getenv("SBOM_DOWNLOAD_MAX_BYTES", str(64 * 1024 * 1024)))


def sbom_author() -> str:
    """The entity that creates the SBOM data, for the SBOM author element.

    The 2026 SBOM minimum elements ask an SBOM to name its author — the entity
    operating the tool, which nothing here can discover. So it is declared, not
    guessed, and left unset means the field is omitted from the export rather
    than filled with a placeholder. A placeholder would satisfy the element
    while telling a recipient nothing, which is worse than an honest gap.

    Read at call time (CLAUDE.md core rule #11). Not a secret.
    """
    return (os.getenv("SBOM_AUTHOR") or "").strip()


def sbom_ingest_max_bytes() -> int:
    """Hard ceiling on an externally-ingested CycloneDX SBOM upload.

    The SBOM-ingest endpoint (``POST /v1/projects/{id}/sbom-ingest``) accepts an
    attacker-controllable JSON document. We read the upload through a bounded,
    chunked loop and abort the instant the running total crosses this cap — the
    body is NEVER buffered in full first, so an oversized upload cannot exhaust
    memory before the size check fires. Over-cap surfaces as a 413 (RFC 7807).

    Default 32 MiB comfortably covers a large real-world CycloneDX SBOM (tens of
    thousands of components) while still bounding the request. Read at call time
    (CLAUDE.md core rule #11) so an operator can retune without a rebuild.
    """
    return int(os.getenv("SBOM_INGEST_MAX_BYTES", str(32 * 1024 * 1024)))


def sbom_ingest_max_components() -> int:
    """Max number of ``components`` entries an ingested CycloneDX SBOM may carry.

    A second, structural DoS guard layered on top of ``sbom_ingest_max_bytes``:
    even a within-size document could declare a pathological component count that
    the downstream Celery persister would loop over. The synchronous validation
    only checks ``len(components)`` (it never deep-traverses the elements), so the
    check is O(1) on the already-parsed list. Over-cap surfaces as a 422.

    Default 50,000 mirrors the source-archive member ceiling. Read at call time
    (CLAUDE.md core rule #11).
    """
    return int(os.getenv("SBOM_INGEST_MAX_COMPONENTS", "50000"))


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

    M2 (security review): we *enforce* the ``hard > soft`` invariant at read
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


# Fixed grace window the Redis broker's visibility timeout must sit ABOVE the
# scan hard time limit, in seconds. Not operator-configurable (unlike the
# scan time limits themselves); it exists only so
# ``broker_visibility_timeout_seconds()`` never lands exactly on the hard
# limit boundary, mirroring ``SCAN_TIMEOUT_MIN_GRACE_SECONDS`` above. 300s
# covers the time between the hard-limit SIGKILL landing and the worker
# actually acking (or the task-supervisor process observing the kill and
# letting Celery's own bookkeeping settle) before the broker would otherwise
# consider the message due for redelivery.
BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS = 300


def broker_visibility_timeout_seconds() -> int:
    """Redis transport ``visibility_timeout`` (seconds) for the Celery broker.

    concurrency-scaling-plan-2026-08-22.md §1.1 / §3.2 (S1): Redis' own
    transport default is 3600s, but this deployment's scan hard time limit
    defaults to 3900s (``scan_hard_time_limit_seconds()``); the timeout was
    never set explicitly, so the shorter Redis default silently won.
    Combined with ``task_acks_late=True`` (celery_app.py), a scan that runs
    past the visibility timeout gets redelivered to a second worker while the
    first worker is still running it, and the same scan occupies two slots.

    Derived from ``scan_hard_time_limit_seconds()`` plus a fixed margin
    (never a hard-coded literal here) so retuning
    ``SCAN_HARD_TIME_LIMIT_SECONDS`` moves this value automatically and the
    invariant (visibility timeout > hard limit) cannot drift out of sync.
    Read at call time (CLAUDE.md core rule #11); both this and the value it
    derives from re-read the environment on every call.
    """
    return scan_hard_time_limit_seconds() + BROKER_VISIBILITY_TIMEOUT_MARGIN_SECONDS


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


def dependency_graph_max_nodes() -> int:
    """Max nodes the dependency-graph endpoint will serialize (BomLens H-1).

    ``GET /v1/projects/{id}/dependency-graph`` ships a scan's whole node/edge
    adjacency to the browser for an interactive render. A pathological scan
    (tens of thousands of components) would freeze that render, so when a scan's
    node count exceeds this ceiling the endpoint returns ``truncated=true`` with
    EMPTY nodes/edges (the frontend then renders its tree fallback). The exact
    counts are always returned regardless.

    Default 5000 comfortably covers real-world graphs while bounding the payload.
    A non-numeric or non-positive value falls back to 5000 so a fat-finger cannot
    disable the guard or ship an empty graph for every scan. Read at call time
    per CLAUDE.md core rule #11 so an operator can retune without a rebuild.
    """
    raw = os.getenv("DEPENDENCY_GRAPH_MAX_NODES", "5000")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 5000
    return value if value > 0 else 5000


# ---------------------------------------------------------------------------
# W6-#42 — vulnerability rematch beat tuning.
#
# Two knobs the operator can retune at runtime per CLAUDE.md core rule #11:
#   - interval: how stale a scan's ``last_rematched_at`` must be before the
#     beat re-enqueues it (default 6h — Trivy DB refreshes weekly, but a 6h
#     cadence keeps the per-scan latency between an upstream NVD publish and
#     a new finding under a day for the typical scan corpus).
#   - batch size: cap on how many scans one beat tick fans out so a sudden
#     deploy onto a corpus of thousands of succeeded scans does not flood the
#     worker pool. The next tick picks up the remainder.
# ---------------------------------------------------------------------------


def vuln_rematch_interval_hours() -> int:
    """Minimum hours between rematch runs for a given scan. Default 6h.

    A scan's ``last_rematched_at`` must be NULL or older than ``now - this``
    for the beat to consider it due. Floor 1h to keep the beat from
    degenerating into a continuous Trivy re-run loop in dev; ceiling 168h
    (one week) — anything longer makes the feature pointless given Trivy's
    weekly DB refresh cadence.
    """
    return _int_env(
        "VULN_REMATCH_INTERVAL_HOURS",
        default=6,
        minimum=1,
        maximum=168,
    )


def vuln_rematch_batch_size() -> int:
    """Max scans the rematch beat enqueues in one tick. Default 50.

    Bounded ``[1, 5000]``. The beat enumerates due scans ORDER BY
    last_rematched_at NULLS FIRST so the never-rematched cohort drains first;
    the next 6h tick picks up wherever this one stopped. The default is sized
    so a worker can clear the batch (~50 × ≤5 min Trivy run = ≤4h) before the
    next tick would otherwise overlap.
    """
    return _int_env(
        "VULN_REMATCH_BATCH_SIZE",
        default=50,
        minimum=1,
        maximum=5000,
    )


def vuln_rematch_lock_skew_seconds() -> int:
    """Slack between the beat's "due" cutoff and the per-scan SKIP LOCKED ts.

    The beat selects due rows with ``last_rematched_at < now - interval``; the
    individual rematch task uses ``SELECT FOR UPDATE SKIP LOCKED`` to avoid two
    workers double-processing the same scan. This skew (default 30s) is the
    cushion subtracted from the cutoff so a scan that was JUST written by
    another worker is not immediately re-enqueued by the next beat tick.

    Bounded ``[0, 600]``. Read at call time (rule #11).
    """
    return _int_env(
        "VULN_REMATCH_LOCK_SKEW_SECONDS",
        default=30,
        minimum=0,
        maximum=600,
    )


# ---------------------------------------------------------------------------
# X1 — vulnerability SLA/aging tracking.
#
# Per-severity remediation windows measured from a finding's PROJECT-level
# first detection (``vulnerability_findings.first_detected_at``, carried
# forward across re-scans / re-matches by
# ``services.vulnerability_matching.persist_trivy_findings``). The list API
# computes ``sla_due_date`` / ``sla_status`` from these windows in SQL so
# sort / filter run in the database.
# ---------------------------------------------------------------------------


def vuln_sla_days(severity: str) -> int | None:
    """Remediation SLA window (days) for a finding severity (X1 SLA/aging).

    The window starts at the finding's project-level first detection
    (``vulnerability_findings.first_detected_at``, COALESCEd to ``created_at``
    for pre-0041 rows) and the due date is ``first_detected + this many days``.
    Severity → env var → default:

      - critical → ``VULN_SLA_DAYS_CRITICAL`` (default 7)
      - high     → ``VULN_SLA_DAYS_HIGH``     (default 30)
      - medium   → ``VULN_SLA_DAYS_MEDIUM``   (default 90)
      - low      → ``VULN_SLA_DAYS_LOW``      (default 180)

    ``info`` / ``unknown`` (and any unrecognised value) return ``None`` —
    those findings carry NO SLA: informational rows are not remediation work,
    and an unknown severity must not be silently assigned a deadline.

    A non-numeric or non-positive override falls back to the severity's
    default so a fat-fingered env value cannot disable (0) or invert (-N) the
    SLA clock. Read at call time per CLAUDE.md core rule #11 so operators can
    retune per-severity windows without a rebuild.
    """
    defaults = {"critical": 7, "high": 30, "medium": 90, "low": 180}
    sev = str(severity or "").strip().lower()
    default = defaults.get(sev)
    if default is None:
        return None
    raw = os.getenv(f"VULN_SLA_DAYS_{sev.upper()}")
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def vuln_sla_alerts_enabled() -> bool:
    """Master switch for the daily SLA-breach notification sweep (X1 step 2).

    Default ``true`` — the sweep is a pure internal computation over rows we
    already hold (no egress, no scanner run) and its output is in-app rows
    the per-user notification preferences already gate, so correct-by-default
    is ON. Only the exact falsy tokens ``false`` / ``0`` / ``no`` disable
    (LICENSE_FETCH_ENABLED parsing rule: a typo fails OPEN to the default
    behaviour instead of silently muting SLA alerts). Read at call time
    (rule #11) so operators can mute without a rebuild.
    """
    return os.getenv("VULN_SLA_ALERTS_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
    }


# ---------------------------------------------------------------------------
# W6-#44 — Trivy DB lifecycle (worker bootstrap + weekly refresh beat).
#
# The W6-#43e admin/health panel reads the on-disk Trivy DB metadata; this
# section owns the WRITE path that puts the DB there in the first place and
# refreshes it on a Celery Beat schedule. ``integrations.trivy`` already
# exposes ``trivy_db_repository()`` / ``trivy_db_refresh_interval_hours()`` /
# ``trivy_cache_dir()`` — they stay there so the panel and the
# lifecycle code share a single resolution path. The accessors below cover
# only the lifecycle-specific knobs the panel does not need.
#
# Every accessor reads the env at call time per CLAUDE.md core rule #11 so
# operators can flip the toggle without rebuilding the worker image.
# ---------------------------------------------------------------------------


def trivy_db_bootstrap_on_start() -> bool:
    """Whether the worker downloads / refreshes the Trivy DB on boot.

    Default ``true``. The bootstrap hook (registered on Celery's
    ``worker_ready`` signal) runs ``trivy --download-db-only`` once shortly
    after the worker starts accepting tasks. Set to ``false`` on air-gapped
    deployments where the DB is mirrored to a host volume by a separate
    process and the worker should never attempt a network pull, or in tests
    that drive the lifecycle hook directly.

    Truthy: ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive).
    Anything else → ``false``.
    """
    raw = os.getenv("TRIVY_DB_BOOTSTRAP_ON_START", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def trivy_db_bootstrap_timeout_seconds() -> int:
    """Per-invocation timeout for the worker-boot ``trivy --download-db-only`` call.

    The first download is ~500 MiB and typically completes in 1-3 minutes on
    a fast link. We bound at 15 minutes by default to absorb a slow corporate
    proxy without blocking the worker forever (the call is dispatched on a
    background thread so a hung subprocess never stalls Celery task
    consumption, but we still cap the subprocess itself so a zombie ``trivy``
    cannot retain a file lock on the cache dir indefinitely).
    """
    return _int_env(
        "TRIVY_DB_BOOTSTRAP_TIMEOUT_SECONDS",
        default=15 * 60,
        minimum=30,
        maximum=60 * 60,
    )


def trivy_db_refresh_timeout_seconds() -> int:
    """Per-invocation timeout for the weekly Celery beat refresh call.

    Refreshes are incremental (Trivy only fetches the delta layers since the
    last manifest) so they typically finish well under the bootstrap cap, but
    we keep a 15-minute ceiling for symmetry with the bootstrap path. A
    timeout becomes a WARNING log + notification — the prior DB stays in
    place because Trivy swaps the manifest only after a successful download.
    """
    return _int_env(
        "TRIVY_DB_REFRESH_TIMEOUT_SECONDS",
        default=15 * 60,
        minimum=30,
        maximum=60 * 60,
    )


# ---------------------------------------------------------------------------
# CISA KEV (Known Exploited Vulnerabilities) catalog refresh.
#
# A daily Celery beat (``tasks.kev_catalog_refresh``) pulls the public CISA
# KEV JSON feed and flags catalog ``vulnerabilities`` rows that appear in it
# (``kev`` / ``kev_date_added`` / ``kev_due_date`` — migration 0034). The KEV
# signal feeds the Vulnerabilities tab's ``sort=priority`` ranking (KEV →
# severity → EPSS, BomLens parity).
#
# Every accessor reads the env at call time per CLAUDE.md core rule #11 —
# style mirrors the ``VULN_REMATCH_*`` block above.
# ---------------------------------------------------------------------------


def kev_feed_url() -> str:
    """URL of the CISA KEV catalog JSON feed.

    Default is CISA's public feed. Override for an air-gapped mirror the same
    way ``TRIVY_DB_REPOSITORY`` points Trivy at an internal registry. The
    value is operator-controlled env configuration only — there is NO user
    write path to it, so it is not routed through ``core.url_guard`` (same
    trust model as the env-only notification webhook URLs).
    """
    return os.getenv(
        "KEV_FEED_URL",
        "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
    )


def kev_refresh_enabled() -> bool:
    """Whether the daily KEV catalog refresh beat actually fetches the feed.

    Default ``true``. Set ``false`` on air-gapped deployments with no mirror —
    the beat then logs a skip and exits without any network attempt (existing
    ``kev`` flags stay as-is; they are never cleared by a disabled refresh).

    Truthy: ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive).
    Anything else → ``false``.
    """
    raw = os.getenv("KEV_REFRESH_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def kev_refresh_timeout_seconds() -> int:
    """HTTP timeout (seconds) for the KEV feed download. Default 30s.

    The feed is a single ~10 MiB JSON document from a CDN; 30 seconds absorbs
    a slow corporate proxy. Bounded ``[1, 600]``. Read at call time (rule #11).
    """
    return _int_env(
        "KEV_REFRESH_TIMEOUT_SECONDS",
        default=30,
        minimum=1,
        maximum=600,
    )


# ---------------------------------------------------------------------------
# Phase M — end-of-life (EOL) component flagging (endoflife.date).
#
# The default path is FULLY offline: verdicts come from a snapshot vendored
# into the repo (services/eol/eol_snapshot.json, refreshed per release by
# scripts/refresh_eol_snapshot.py), so EOL_ENABLED defaults ON — reading a
# local file has zero egress (contrast SCANOSS). The optional live-refresh
# beat (PR M-3) is a SEPARATE, default-OFF toggle because that one does
# introduce new egress. Accessors resolve env at call time (rule #11).
# ---------------------------------------------------------------------------


def malicious_enabled() -> bool:
    """Whether components are stamped with known-malicious verdicts (#26).

    Default ``true`` — the check is offline (a vendored OSV snapshot), additive
    and never fatal, exactly like EOL. It stays on by default because a
    malicious package is an active attack rather than a schedulable defect;
    an operator who turns it off gets NULL columns, which the surfaces render
    as "not assessed" rather than as a clean bill.

    Only the exact falsy tokens ``false`` / ``0`` / ``no`` disable. Read at
    call time (rule #11).
    """
    return os.getenv("MALICIOUS_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
    }


def malicious_refresh_enabled() -> bool:
    """Whether the weekly beat may rebuild the malicious snapshot from OSV.

    Default OFF, like every other new egress target in this codebase. The
    re-stamp half of that beat always runs — it is local — so leaving this
    alone still lets an image upgrade reach existing rows. An air-gapped
    install never turns it on and works from the snapshot in the release.

    Read at call time (rule #11).
    """
    return os.getenv("MALICIOUS_REFRESH_ENABLED", "").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def malicious_snapshot_stale_days() -> int:
    """Age at which the admin panel calls the malicious snapshot stale.

    60 days, shorter than the EOL panel's 180. End-of-life dates move on a
    product's release calendar; malicious advisories are published daily, so
    a snapshot two months old has stopped answering the question it is asked.
    """
    raw = os.getenv("MALICIOUS_SNAPSHOT_STALE_DAYS", "").strip()
    if not raw:
        return 60
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 60
    return value if value > 0 else 60


def eol_enabled() -> bool:
    """Whether components are stamped with endoflife.date EOL verdicts.

    Default ``true`` — offline, additive, never fatal (a missing/corrupt
    dataset just skips stamping with one WARNING per scan). Only the exact
    falsy tokens ``false`` / ``0`` / ``no`` disable. Read at call time
    (rule #11).
    """
    return os.getenv("EOL_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
    }


def eol_snapshot_path() -> str:
    """Operator override for the endoflife.date snapshot file.

    Empty (default) → the vendored ``services/eol/eol_snapshot.json``.
    Air-gapped installs can mount a fresher snapshot (built with
    ``scripts/refresh_eol_snapshot.py`` on a connected host) and point this
    at it. Read at call time (rule #11).
    """
    return os.getenv("EOL_SNAPSHOT_PATH", "").strip()


def eol_refresh_enabled() -> bool:
    """Whether the weekly beat FETCHES fresh data from endoflife.date.

    Default ``false`` — this is NEW egress to a third-party host the product
    has never contacted, so it follows the SCANOSS fail-closed posture (only
    the exact truthy tokens enable), NOT the KEV default-on one: unlike KEV
    — where a stale catalog misses actively-exploited CVEs daily — EOL dates
    churn quarterly and the per-release vendored snapshot already bounds
    staleness. The beat's RE-STAMP pass runs regardless of this toggle (it
    is pure-local). Read at call time (rule #11).
    """
    return os.getenv("EOL_REFRESH_ENABLED", "false").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def eol_feed_url_template() -> str:
    """URL template for the endoflife.date per-product API.

    ``{product}`` is substituted with each mapped product slug. Env-only
    trust model (same as ``KEV_FEED_URL`` — no user write path, so no
    ``core.url_guard``); point it at an internal mirror to keep the egress
    inside your network. Read at call time (rule #11).
    """
    return os.getenv(
        "EOL_FEED_URL_TEMPLATE", "https://endoflife.date/api/{product}.json"
    ).strip()


def eol_refresh_timeout_seconds() -> int:
    """HTTP timeout (seconds) per product request. Default 15s.

    Each product document is a few KB; 15 seconds absorbs a slow corporate
    proxy. Bounded ``[1, 120]``. Read at call time (rule #11).
    """
    return _int_env(
        "EOL_REFRESH_TIMEOUT_SECONDS",
        default=15,
        minimum=1,
        maximum=120,
    )


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
# P2 #8c — tool stdout/stderr streaming over the scan WebSocket.
#
# The cdxgen / scancode subprocesses emit per-line progress to stdout/stderr.
# We stream those lines over the scan WebSocket as separate "log" frames so the
# scan drawer can render a live tool trace. Each line is capped (memory guard)
# and we cap total lines per scan (broker volume guard) — both are env-tunable
# so an operator can dial up the verbosity without a rebuild.
# ---------------------------------------------------------------------------


def scan_log_line_max_len() -> int:
    """Maximum length (chars) of a single streamed log line.

    A subprocess that emits a pathological multi-MB single line (e.g. a hung
    progress bar that never sends \\n) would otherwise sit in RAM until the
    process exits AND flood the Redis pubsub channel as one giant payload. We
    truncate at this cap with a trailing ``…(truncated)`` marker so the
    consumer can see a line happened without it bloating wire size. Default
    2000 — typical tool lines are < 200 chars. Read at call time (rule #11).
    """
    return int(os.getenv("SCAN_LOG_LINE_MAX_LEN", "2000"))


def scan_log_max_lines_per_scan() -> int:
    """Cap on the number of log lines we publish for a single scan.

    A pathological subprocess (infinite progress bar, runaway warning) could
    flood the WS channel and the Redis pubsub backlog. Past this many lines we
    silently drop further publishes for that scan (the subprocess still runs;
    we just stop forwarding). 0 disables streaming entirely (kill switch).
    Default 20000 — generous for normal scans AND verbose (``--debug`` /
    ``--verbose``) runs that the per-scan verbosity toggle can request, while
    still hostile to runaways. Read at call time (rule #11).
    """
    return int(os.getenv("SCAN_LOG_MAX_LINES_PER_SCAN", "20000"))


def scan_log_persist_enabled() -> bool:
    """Whether to also append every published log line to a per-scan disk file.

    When True (the default) ``tasks._progress.publish_log`` mirrors every line
    onto ``{WORKSPACE_HOST_PATH}/{scan_id}/scan.log`` so users can come back to
    a 30-60 minute scan (or a CI-triggered scan from days ago) and re-read the
    tool trace via ``GET /v1/scans/{scan_id}/log``. The file rides along with
    the workspace and is reclaimed by ``workspace_cleaner`` once the parent
    scan reaches a terminal status (same lifecycle as cdxgen/scancode/trivy
    artifacts on disk).

    Disable (``SCAN_LOG_PERSIST_ENABLED=false``) in a degraded-disk or
    air-gapped operator scenario, or in unit tests that do not want disk-IO
    side effects. Read at call time per CLAUDE.md core rule #11.
    """
    raw = os.getenv("SCAN_LOG_PERSIST_ENABLED", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


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


def oidc_issuer() -> str | None:
    """Issuer URL of the deployment's own identity provider.

    One generic provider, not a list. An organisation runs one identity
    provider, and every endpoint is discovered from this URL, so naming the
    issuer is the whole of the wiring. Read at call time (rule #11).
    """
    raw = os.getenv("OIDC_ISSUER", "").strip()
    return raw.rstrip("/") or None


def oidc_client_id() -> str | None:
    raw = os.getenv("OIDC_CLIENT_ID", "").strip()
    return raw or None


def oidc_client_secret() -> str | None:
    # Stripped, unlike the other two provider secrets: this one is typically
    # mounted from a file, and a trailing newline reaches the provider as part
    # of the credential and comes back as an opaque invalid_client.
    raw = os.getenv("OIDC_CLIENT_SECRET", "").strip()
    return raw or None


def oidc_scopes() -> str:
    """Scopes requested at the authorisation endpoint.

    ``openid`` is mandatory and is added back if an operator drops it, since
    without it the provider is not doing OpenID Connect and the userinfo
    endpoint has no subject to return.
    """
    raw = os.getenv("OIDC_SCOPES", "").strip()
    scopes = raw.split() if raw else ["openid", "email", "profile"]
    if "openid" not in scopes:
        scopes.insert(0, "openid")
    return " ".join(scopes)


def oidc_groups_claim() -> str:
    """Userinfo claim listing the groups the person belongs to.

    Providers disagree on the name (``groups`` is common, some send ``roles``),
    and unlike the address there is no standard claim to insist on: nothing
    vouches for a group list, so reading a different name changes only which
    unvouched list is read. Empty disables mapping entirely.
    """
    raw = os.getenv("OIDC_GROUPS_CLAIM", "").strip()
    return raw or "groups"


def default_member_role() -> str | None:
    """The grade a deployment has chosen for people nobody graded, or None.

    Bulk registration rows that name no role, and auto-registration through
    the deployment's own identity provider, both consult this. One setting
    rather than one per surface, because a deployment that has decided what a
    new person may do has decided it once.

    ``None`` means no choice was made, and each caller keeps the grade it
    granted before this setting existed: ``developer`` for an account an
    administrator adds, and the personal-team ``team_admin`` for a first
    sign-in through a hosted provider. Collapsing those two into one fallback
    here would move somebody's grade on a deployment that never asked, which
    is the change this setting exists to let them make deliberately.

    ``super_admin`` is refused even if written. That grade administers the
    deployment, and a setting that hands it out on arrival would make the
    first person through the door an administrator of everybody else.
    """
    raw = os.getenv("DEFAULT_MEMBER_ROLE", "").strip().lower()
    if not raw:
        return None
    if raw in {"viewer", "developer", "team_admin"}:
        return raw
    # Set to something this does not recognise. Answering None here would send
    # both callers to their historical fallback, which is a higher grade than
    # whatever the operator was reaching for by writing the setting at all, and
    # nothing would say so. The floor is the safe reading of an unreadable
    # instruction.
    import structlog

    structlog.get_logger("config").warning(
        "config.default_member_role_unrecognised",
        env_var="DEFAULT_MEMBER_ROLE",
        value=raw,
        fell_back_to="viewer",
    )
    return "viewer"


def ticket_webhook_url() -> str | None:
    """Where to post an event worth raising a ticket for, or None.

    Unset, and off. Off means nothing is called: no request, no queued task,
    no log line saying a delivery was skipped. A deployment that has not
    written a URL should be indistinguishable from one built before this
    existed.

    Generic on purpose. The portal posts a structured event and the
    organisation's own adapter turns it into a ticket in whatever tracker they
    run, because the mapping from an event to a ticket is where organisations
    differ most: which project, which issue type, which fields are mandatory,
    who it is assigned to. Shipping an adapter for one tracker would serve one
    organisation and mislead the rest.
    """
    raw = os.getenv("TICKET_WEBHOOK_URL", "").strip()
    return raw or None


def ticket_webhook_token() -> str | None:
    """A bearer token sent with the post, or None.

    Separate from the URL so a token can be rotated without touching the
    endpoint, and so a URL that already carries a secret in its path (the
    shape most trackers hand out) needs no second one.
    """
    raw = os.getenv("TICKET_WEBHOOK_TOKEN", "").strip()
    return raw or None


def ticket_webhook_events() -> frozenset[str]:
    """Which event kinds are worth a ticket. Empty means all of them.

    A ticket is raised for work somebody has to do, and not everything the
    portal announces is that: a finished scan is worth a line in a channel and
    is not worth a ticket anybody will close. Deployments disagree about where
    that line sits, so the list is theirs to write.

    Empty meaning "all" matches the notification routing rules, where an
    absent condition matches everything. One convention across the settings
    that filter by kind is worth more than each one being separately
    defensible.
    """
    raw = os.getenv("TICKET_WEBHOOK_EVENTS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())
def audit_export_url() -> str | None:
    """Where to hand the audit trail as it accumulates, or None.

    Unset, and off. The audit API is unchanged either way: this adds a way to
    push the trail somewhere, and takes nothing away from the way an
    administrator reads it in the portal.

    The receiver is whatever the organisation collects logs with. The portal
    posts batches of the same rows the audit screen shows, with the same
    columns masked, and does not care what happens next.
    """
    raw = os.getenv("AUDIT_EXPORT_URL", "").strip()
    return raw or None


def audit_export_token() -> str | None:
    """A bearer token sent with each batch, or None."""
    raw = os.getenv("AUDIT_EXPORT_TOKEN", "").strip()
    return raw or None


def audit_export_batch_size() -> int:
    """Rows per post. Bounded so one run cannot build an unbounded body.

    Five hundred is the same order as the other paged reads in this codebase,
    and a deployment that has fallen a day behind catches up over several runs
    rather than in one request the receiver may refuse.
    """
    return _int_env("AUDIT_EXPORT_BATCH_SIZE", 500, minimum=1, maximum=5000)


def audit_export_lag_seconds() -> int:
    """How far behind now the export stays, in seconds.

    Not a throttle. A row is stamped when its transaction commits, and a
    transaction that began earlier can commit later, so ordering by the stamp
    alone can place a row behind a position the export has already passed. The
    row would then never be sent, and nothing anywhere would say so.

    Staying a few seconds behind the present means the export only reads
    stretches of time no open transaction can still write into. The default
    covers a request far longer than any this codebase makes; a deployment with
    long transactions raises it and pays with a slightly older trail at the
    collector.
    """
    return _int_env("AUDIT_EXPORT_LAG_SECONDS", 30, minimum=0, maximum=3600)


def metrics_enabled() -> bool:
    """Whether the deployment publishes an operational metrics endpoint.

    Off by default, and off means the route does not exist rather than
    answering 403. A monitoring endpoint that announces itself to everybody
    who asks tells an outsider what this host is and who runs it, and a
    deployment that has not asked for a scrape target should not have one.

    What it publishes is a fixed list of aggregate counts, held to a shared
    fixture so a metric cannot be added without somebody deciding it is safe
    to publish. There is no free-form label carrying a project or a person's
    name in it, which is the way this kind of endpoint usually leaks.
    """
    return os.getenv("METRICS_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def metrics_token() -> str | None:
    """A bearer token a scraper must present, or None for an open endpoint.

    Optional because the usual deployment keeps the endpoint off the public
    ingress and lets the monitoring system reach it on the internal network,
    where a shared token adds a secret to rotate and nothing else. Set it when
    the endpoint is reachable from somewhere you do not control.

    Compared in constant time, so a wrong token cannot be found one character
    at a time by timing the refusals.
    """
    raw = os.getenv("METRICS_TOKEN", "").strip()
    return raw or None


def permission_cache_ttl_seconds() -> int:
    """How long a resolved principal may be reused before it is read again.

    Zero, and off, unless a deployment says otherwise. Every authenticated
    request currently costs two queries to rebuild the same answer, which is
    the shape worth caching; what makes it dangerous is that the answer is
    somebody's permissions, and a stale one keeps a demoted person at their
    old grade or a deactivated one signed in.

    So the number is the contract: whatever is written here is the longest a
    revocation can take to be felt, and the tests pin exactly that. An
    operator choosing a value is choosing how long they are willing to wait
    for "you are no longer an administrator" to be true.

    Off rather than a small positive default, because a deployment that has
    not thought about that trade has not agreed to it, and the cost this saves
    is one an installation only feels at a scale it can measure. The
    connection pool is the first thing to tune, and this is the second.
    """
    raw = os.getenv("PERMISSION_CACHE_TTL_SECONDS", "").strip()
    if not raw:
        return 0
    try:
        seconds = int(raw)
    except ValueError:
        import structlog

        structlog.get_logger("config").warning(
            "config.permission_cache_ttl_invalid",
            env_var="PERMISSION_CACHE_TTL_SECONDS",
            value=raw,
            fell_back_to=0,
        )
        return 0
    if seconds <= 0:
        return 0
    # An upper bound as well as a lower one. A cache measured in hours is not
    # a cache, it is a second copy of the permission model that nobody
    # updates, and the operator who wrote it will not remember it when they
    # deactivate somebody.
    return min(seconds, _PERMISSION_CACHE_TTL_MAX)


#: The longest lifetime this accepts, in seconds. Five minutes: long enough
#: that a busy deployment stops re-reading the same rows, short enough that a
#: revocation is felt inside the window an operator will sit and watch.
_PERMISSION_CACHE_TTL_MAX = 300


def self_registration_enabled() -> bool:
    """Whether anybody may create their own account at the sign-up form.

    On by default, because that form is how the hosted signup works and
    turning it off would break it.

    It exists next to ``auto_register_enabled`` because the two are one
    question asked at two doors, and closing one alone closes nothing: with
    auto-registration off but this on, somebody signs up under their work
    address, then signs in through the company provider, and the callback
    links the identity to the account they just made for themselves. They end
    up holding exactly the account the other setting was withholding. An
    enterprise deployment turns both off.
    """
    return os.getenv("AUTH_SELF_REGISTRATION", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def auto_register_enabled() -> bool:
    """Whether an unknown person who authenticates becomes a user.

    Off by default, and scoped to the deployment's own identity provider. The
    hosted providers keep creating accounts on first sign-in because that is
    what a demo signup is; an enterprise that points the portal at its own
    directory is in the opposite position, where everybody in the company can
    authenticate and only some of them are meant to have a portal account.

    Off means an unknown person is refused rather than silently created, and
    an administrator adds them (one at a time or in bulk). It is not a
    security boundary on its own: whoever can authenticate could be added by
    an administrator anyway. It is the difference between a roster somebody
    maintains and one that grows by itself.

    Closing the roster takes this and ``AUTH_SELF_REGISTRATION`` together. See
    that setting for why one alone closes nothing.
    """
    return os.getenv("AUTH_AUTO_REGISTER", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def oidc_group_role_map() -> dict[str, str]:
    """Group to grade, as ``group:grade`` pairs separated by commas.

    Unset means every arriving person gets the floor, which is the safe
    reading: a deployment that has not said what a group means has not said
    that it means privilege.

    ``super_admin`` is refused here even if written. That grade administers the
    whole deployment, and honouring it would mean anyone who can create a group
    in the identity provider can mint a portal administrator. It stays a
    decision an existing administrator makes in the portal.
    """
    raw = os.getenv("OIDC_GROUP_ROLE_MAP", "").strip()
    if not raw:
        return {}
    mapping: dict[str, str] = {}
    for pair in raw.split(","):
        group, _, grade = pair.partition(":")
        group = group.strip()
        grade = grade.strip()
        if not group or grade not in {"viewer", "developer", "team_admin"}:
            continue
        mapping[group] = grade
    return mapping


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
    H-3 (security review blocker): CORS bootstrap guard.

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


# ---------------------------------------------------------------------------
# feat/demo-sandbox-scan (security review finding) — boot-time safe-limit guard.
#
# The public-demo sandbox carve-out (DEMO_ALLOW_SANDBOX_SCANS) opens a public
# write surface: an anonymous visitor can run a live source scan and ingest an
# SBOM against the seeded "Demo Sandbox" project. What keeps that surface small
# and safe is a SET OF SEPARATE env knobs (input size, per-team concurrency,
# scancode off) applied by the docker-compose.demo.yml overlay / the demo env —
# NOT the carve-out flag itself. M-1: because the flag and the bounds are
# decoupled, an operator who sets DEMO_ALLOW_SANDBOX_SCANS=true WITHOUT layering
# the overlay would silently run the sandbox with PRODUCTION defaults (512 MiB
# source download, per-team concurrency 10, scancode on, 32 MiB / 50k-component
# SBOM ingest) — a much larger public surface than intended.
#
# We refuse to couple the flag to the knobs implicitly (that would surprise an
# operator who deliberately retuned one value); instead we FAIL THE BOOT when the
# flag is on but any safe ceiling is not actually in effect. Production (flag off)
# and the plain read-only demo (flag off) are unaffected — the check is a no-op.
# ---------------------------------------------------------------------------

_DEMO_SANDBOX_MAX_SOURCE_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB
_DEMO_SANDBOX_MAX_CONCURRENCY = 2
_DEMO_SANDBOX_MAX_SBOM_INGEST_BYTES = 10 * 1024 * 1024  # 10 MiB
_DEMO_SANDBOX_MAX_SBOM_COMPONENTS = 5000


def validate_demo_sandbox_limits() -> None:
    """Fail-fast when the demo sandbox carve-out is on without its safe bounds.

    security review finding. No-op unless ``demo_allow_sandbox_scans()`` is true;
    then every safe ceiling below MUST already be in effect (via the demo env /
    docker-compose.demo.yml overlay) or the process refuses to start with a
    ``RuntimeError``. Every value is read at call time through the existing
    accessors (CLAUDE.md core rule #11) so it reflects the LIVE env, not an
    import-time snapshot. Called from ``main.py``'s lifespan startup alongside
    ``secret_key()``.

    Ceilings (overlay values):
      * ``scan_source_raw_download_max_bytes()`` ≤ 10 MiB
      * ``scan_concurrency_cap_per_team()`` in ``[1, 2]`` — note a value ≤ 0 is
        the "unlimited" sentinel (:func:`scan_concurrency_cap_per_team` disables
        the cap on 0/negative), which is the OPPOSITE of a bound, so it is a
        violation here even though it is numerically ≤ 2.
      * ``scancode_enabled()`` is ``False``
      * ``sbom_ingest_max_bytes()`` ≤ 10 MiB
      * ``sbom_ingest_max_components()`` ≤ 5000
    """
    if not demo_allow_sandbox_scans():
        return

    violations: list[str] = []

    raw_dl = scan_source_raw_download_max_bytes()
    if raw_dl > _DEMO_SANDBOX_MAX_SOURCE_DOWNLOAD_BYTES:
        violations.append(
            f"SCAN_SOURCE_RAW_DOWNLOAD_MAX_BYTES={raw_dl} > "
            f"{_DEMO_SANDBOX_MAX_SOURCE_DOWNLOAD_BYTES}"
        )

    cap = scan_concurrency_cap_per_team()
    # A cap ≤ 0 disables the per-team limit entirely (unlimited concurrency) —
    # unsafe for a public sandbox, so reject it alongside an over-ceiling value.
    if cap < 1 or cap > _DEMO_SANDBOX_MAX_CONCURRENCY:
        violations.append(
            f"SCAN_CONCURRENCY_CAP_PER_TEAM={cap} not in "
            f"[1, {_DEMO_SANDBOX_MAX_CONCURRENCY}] "
            "(≤0 means unlimited — set 1 for the demo)"
        )

    if scancode_enabled():
        violations.append(
            "SCANCODE_ENABLED must be false (scancode disabled) in the demo sandbox"
        )

    ingest_bytes = sbom_ingest_max_bytes()
    if ingest_bytes > _DEMO_SANDBOX_MAX_SBOM_INGEST_BYTES:
        violations.append(
            f"SBOM_INGEST_MAX_BYTES={ingest_bytes} > {_DEMO_SANDBOX_MAX_SBOM_INGEST_BYTES}"
        )

    ingest_components = sbom_ingest_max_components()
    if ingest_components > _DEMO_SANDBOX_MAX_SBOM_COMPONENTS:
        violations.append(
            f"SBOM_INGEST_MAX_COMPONENTS={ingest_components} > "
            f"{_DEMO_SANDBOX_MAX_SBOM_COMPONENTS}"
        )

    if violations:
        raise RuntimeError(
            "DEMO_ALLOW_SANDBOX_SCANS is on but safe limits are not applied — "
            "layer docker-compose.demo.yml / set the demo env. Violations: "
            + "; ".join(violations)
        )
