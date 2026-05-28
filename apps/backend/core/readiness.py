"""
Schema-readiness check for the ``GET /health/ready`` probe (v2.1 / Track B B1).

``/health`` is *liveness* — it answers the moment the uvicorn process is up,
proving nothing about the database. ``/health/ready`` is *readiness*: it returns
200 only when the Postgres schema is at the Alembic HEAD revision(s). Orchestrators
(docker-compose ``service_healthy`` gates, Kubernetes ``readinessProbe``) use this
so dependents (Celery worker / beat) start only after migrations have landed.

Interaction with ``AUTO_MIGRATE`` (see ``docker-entrypoint.sh``):

  * ``AUTO_MIGRATE=true``  — the backend container runs ``alembic upgrade head``
    before uvicorn answers, so ``/health/ready`` flips to 200 once that finishes.
  * ``AUTO_MIGRATE=false`` (the role-separated / out-of-band stack) — uvicorn
    answers ``/health`` immediately, but ``/health/ready`` stays 503 until an
    external ``alembic upgrade head`` (run as the owner role) brings the schema
    to HEAD. This is the intended gate: workers wait for the schema, not just
    for the process.

Design for testability: the two facts this needs — the *expected* HEAD revisions
(from the Alembic script directory) and the *current* DB revisions (from the
``alembic_version`` table) — are computed by small, independently-injectable
functions so unit tests can drive both the at-head (200) and behind/missing (503)
branches without a live Postgres. ``check_schema_readiness`` accepts overrides
for both via keyword arguments.

Security: the 503 detail summarises a revision *mismatch* (short revision ids
only) and never echoes the DSN, credentials, or a raw driver traceback — those
go to the structured log at WARNING/ERROR, not the HTTP body.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic.config import Config
from alembic.script import ScriptDirectory

log = structlog.get_logger("readiness")

# Backend root holds alembic.ini + the alembic/ script tree. readiness.py lives
# in apps/backend/core/, so the root is one directory up from this file's parent.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"


@dataclass(frozen=True)
class ReadinessResult:
    """Outcome of a schema-readiness check.

    ``ready`` is True only when the DB revision set equals the script HEAD set.
    ``expected`` / ``current`` are short revision ids (sorted) used to build a
    non-sensitive 503 detail string. ``error`` carries a terse, already-masked
    reason when the check could not even be performed (table missing / DB
    unreachable) — never a raw driver message.
    """

    ready: bool
    expected: tuple[str, ...]
    current: tuple[str, ...]
    error: str | None = None

    def summary(self) -> str:
        """Human-readable, non-sensitive one-liner for the 503 ``detail`` field."""
        if self.error is not None:
            return self.error
        exp = ", ".join(self.expected) or "(none)"
        cur = ", ".join(self.current) or "(none)"
        return (
            f"database schema is not at the expected Alembic HEAD "
            f"(expected: {exp}; current: {cur})"
        )


def compute_expected_heads(*, config_path: Path | None = None) -> tuple[str, ...]:
    """Return the Alembic script HEAD revision(s), sorted.

    Mirrors how ``alembic/env.py`` loads configuration (alembic.ini next to the
    backend root) but is callable from the FastAPI runtime — it touches no DB,
    only the on-disk ``alembic/versions`` tree, so it is cheap and connection-free.

    A well-formed linear history yields exactly one head; a (transient) branch
    yields several. We return the whole set so the comparison is correct either
    way. Raises if the script directory cannot be loaded — the caller treats that
    as "not ready" rather than letting it bubble into a 500.
    """
    ini = config_path or _ALEMBIC_INI
    cfg = Config(str(ini))
    # script_location in alembic.ini is the relative "alembic"; resolve it
    # against the backend root so the lookup works regardless of CWD.
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    return tuple(sorted(script.get_heads()))


async def fetch_db_revisions(session: AsyncSession) -> tuple[str, ...]:
    """Return the revision(s) recorded in the DB ``alembic_version`` table, sorted.

    A read-only ``SELECT`` — the runtime DML-only role (``trustedoss_app``) has
    ample privilege for this. An empty table (migrations never run) yields an
    empty tuple, which will not equal a non-empty HEAD set → 503. A missing table
    (brand-new DB) raises; the caller maps that to a 503 with a generic reason.
    """
    result = await session.execute(text("SELECT version_num FROM alembic_version"))
    return tuple(sorted(row[0] for row in result.fetchall()))


async def check_schema_readiness(
    session: AsyncSession,
    *,
    expected_heads: Callable[[], tuple[str, ...]] | None = None,
    db_revisions: Callable[[AsyncSession], Awaitable[tuple[str, ...]]] | None = None,
) -> ReadinessResult:
    """Compare expected Alembic HEAD(s) against the DB's recorded revision(s).

    The two data sources are injectable so unit tests can exercise both the
    at-head (200) and behind / missing-table / DB-error (503) branches without a
    live Postgres:

    * ``expected_heads`` — defaults to :func:`compute_expected_heads`.
    * ``db_revisions``   — defaults to :func:`fetch_db_revisions`.

    Any exception from either source is caught and folded into a non-ready result
    with a generic, non-sensitive ``error`` reason (the real exception is logged
    at WARNING with the path of failure, not returned to the caller).
    """
    heads_fn = expected_heads or compute_expected_heads
    db_fn = db_revisions or fetch_db_revisions

    try:
        expected = heads_fn()
    except Exception as exc:  # pragma: no cover - defensive; script tree is in-image
        log.error("readiness.expected_heads_failed", error=str(exc))
        return ReadinessResult(
            ready=False,
            expected=(),
            current=(),
            error="could not determine the expected schema revision",
        )

    try:
        current = await db_fn(session)
    except Exception as exc:
        # alembic_version missing (fresh DB) or DB unreachable. Do NOT echo the
        # driver message (it can carry host / role detail) — log it, return a
        # terse reason. WARNING because this is an expected pre-migration state,
        # not a server defect.
        log.warning("readiness.db_revision_failed", error=str(exc))
        return ReadinessResult(
            ready=False,
            expected=expected,
            current=(),
            error="database schema revision is unavailable (not yet migrated?)",
        )

    ready = bool(expected) and expected == current
    if not ready:
        log.warning(
            "readiness.schema_behind_head",
            expected=list(expected),
            current=list(current),
        )
    return ReadinessResult(ready=ready, expected=expected, current=current)


__all__ = [
    "ReadinessResult",
    "check_schema_readiness",
    "compute_expected_heads",
    "fetch_db_revisions",
]
