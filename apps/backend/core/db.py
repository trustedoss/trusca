# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Database engine and session factory wiring.

The engine is created during the FastAPI lifespan and stored on app.state so
environment variables are read once per process startup (CLAUDE.md core rule
#11 — no module-level caching). Request handlers acquire sessions via the
get_db dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from .config import (
    database_url,
    database_url_sync,
    db_max_overflow,
    db_pool_recycle_seconds,
    db_pool_size,
    db_pool_timeout_seconds,
    db_sync_max_overflow,
    db_sync_pool_recycle_seconds,
    db_sync_pool_size,
    db_sync_pool_timeout_seconds,
)


def build_engine() -> AsyncEngine:
    """Create a fresh async engine using the current DATABASE_URL value.

    B1: pool sizing is read from the environment at construction time
    (CLAUDE.md core rule #11). The async engine serves the FastAPI request
    handlers, so it gets the larger pool (default 20 + 10 overflow). See
    core.config for the env var names and sizing guidance.
    """
    return create_async_engine(
        database_url(),
        pool_pre_ping=True,
        future=True,
        pool_size=db_pool_size(),
        max_overflow=db_max_overflow(),
        pool_timeout=db_pool_timeout_seconds(),
        pool_recycle=db_pool_recycle_seconds(),
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _ensure_state(app: Any) -> async_sessionmaker[AsyncSession]:
    """
    Return the app's session factory, building it lazily if the lifespan has
    not run.

    The FastAPI lifespan is the canonical place to construct the engine, but
    httpx's `ASGITransport` does not trigger lifespan events by default. To
    keep the integration tests simple we fall back to building on first
    access — and we install the audit listener at the same time so audit
    logs work even outside the normal startup path. This is idempotent.
    """
    state = app.state
    factory = getattr(state, "session_factory", None)
    if factory is None:
        # Local import to avoid circular dependency between db <-> audit.
        from .audit import install_audit_listeners

        engine = build_engine()
        state.engine = engine
        factory = build_session_factory(engine)
        state.session_factory = factory
        install_audit_listeners(factory)
    return factory


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = _ensure_state(request.app)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Sync session — Celery worker context.
#
# Celery tasks run in a synchronous greenlet/thread; mixing them with our
# asyncpg engine would require a running event loop per task and complicates
# transaction semantics. Phase 2 PR #8 introduces a dedicated psycopg2-backed
# engine for scan tasks, dt_resync, and the orphan cleaner. The sync engine is
# constructed lazily on first call so the FastAPI process (which never imports
# the Celery task modules) does not pay for it.
#
# We deliberately do NOT install audit listeners on the sync session: Celery
# tasks run as system actors with no request_id / actor_user_id context, and
# the scan pipeline writes thousands of ScanComponent rows per scan — auditing
# every row would balloon the audit_logs table. Tasks that need an audit trail
# emit explicit AuditLog rows from inside the service layer.
# ---------------------------------------------------------------------------


_sync_engine: Engine | None = None
_sync_session_factory: sessionmaker[Session] | None = None


def build_sync_engine() -> Engine:
    """Create a fresh sync engine using the current DATABASE_URL value.

    B1: the sync engine backs Celery worker tasks (scan pipeline, dt_resync,
    orphan cleaner). Worker concurrency is low, so it uses the smaller sync
    pool (default 5 + 5 overflow) tuned via the DB_SYNC_* env vars — separate
    from the FastAPI pool so the two can be sized independently against the
    shared Postgres `max_connections` budget.
    """
    return create_engine(
        database_url_sync(),
        pool_pre_ping=True,
        future=True,
        pool_size=db_sync_pool_size(),
        max_overflow=db_sync_max_overflow(),
        pool_timeout=db_sync_pool_timeout_seconds(),
        pool_recycle=db_sync_pool_recycle_seconds(),
    )


def get_sync_session_factory() -> sessionmaker[Session]:
    """
    Return a lazily-built sync sessionmaker.

    The first call inside a worker process initializes the engine; subsequent
    calls reuse the cached factory so SQLAlchemy's connection pool can do its
    job. The cache lives on module-level globals (Celery worker = one process)
    rather than app.state because there is no FastAPI app object here.
    """
    global _sync_engine, _sync_session_factory
    if _sync_session_factory is None:
        _sync_engine = build_sync_engine()
        _sync_session_factory = sessionmaker(
            bind=_sync_engine,
            expire_on_commit=False,
            class_=Session,
            future=True,
        )
    return _sync_session_factory


# ER39: "this session wrote something and nobody committed it".
#
# ``sync_session_scope`` does not auto-commit (see its docstring for why), so a
# task that forgets ``session.commit()`` mutates rows in memory, reports the
# work in its summary, and leaves the database untouched. Nothing raises and
# nothing is logged: two catalog backfill tasks were entirely inert this way
# and their summaries said otherwise.
#
# A static check cannot answer this. The write may be in the task body or
# inside a service the task calls, and the commit may be in either place too,
# so "does this file contain .commit()" flags every task that correctly
# delegates its writes. What we actually want to ask is of the session at the
# moment it closes: did you emit DML that was never committed. These three
# listeners carry that one bit.
_UNCOMMITTED_DML = "_uncommitted_dml"


@event.listens_for(Session, "after_flush")
def _mark_uncommitted_dml(session: Session, _ctx: Any) -> None:
    """A flush emitted INSERT/UPDATE/DELETE; it is not durable until commit."""
    session.info[_UNCOMMITTED_DML] = True


@event.listens_for(Session, "after_commit")
def _clear_on_commit(session: Session) -> None:
    session.info.pop(_UNCOMMITTED_DML, None)


@event.listens_for(Session, "after_soft_rollback")
def _clear_on_rollback(session: Session, _txn: SessionTransaction) -> None:
    """A deliberate discard is not a lost write.

    This is what keeps the dry-run paths quiet: they mutate for counting and
    roll back on purpose, and rolling back is an answer to the question.
    """
    session.info.pop(_UNCOMMITTED_DML, None)


def _warn_if_uncommitted(session: Session) -> None:
    """WARNING, never an exception.

    Raising would turn every task that has been silently inert into a loudly
    failing one at the moment this lands, which is a change to production
    behaviour that belongs with the fix for each task rather than with the
    detector. The test suite escalates this to a failure instead
    (``tests/conftest.py``), where a new task pays for the mistake in CI.
    """
    if not (
        session.info.get(_UNCOMMITTED_DML)
        or session.new
        or session.dirty
        or session.deleted
    ):
        return
    structlog.get_logger("core.db").warning(
        "session_scope_closed_with_uncommitted_writes",
        new=len(session.new),
        dirty=len(session.dirty),
        deleted=len(session.deleted),
        flushed=bool(session.info.get(_UNCOMMITTED_DML)),
    )


@contextmanager
def sync_session_scope() -> Iterator[Session]:
    """
    Context manager yielding a sync `Session` with commit/rollback semantics.

    Usage from a Celery task::

        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            ...
            session.commit()

    Exceptions trigger rollback before propagating; the session is always
    closed in `finally`. Tasks that need to commit mid-pipeline (per-stage
    progress updates) should call `session.commit()` explicitly inside the
    block — this helper does NOT auto-commit on exit because the scan
    pipeline mixes intermediate commits with terminal status updates.

    Because that rule is easy to forget, the scope reports a block that wrote
    and never committed (ER39). See ``_warn_if_uncommitted``.
    """
    factory = get_sync_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        _warn_if_uncommitted(session)
    finally:
        session.close()
