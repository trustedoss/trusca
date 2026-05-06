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

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from .config import database_url, database_url_sync


def build_engine() -> AsyncEngine:
    """Create a fresh async engine using the current DATABASE_URL value."""
    return create_async_engine(database_url(), pool_pre_ping=True, future=True)


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
    """Create a fresh sync engine using the current DATABASE_URL value."""
    return create_engine(database_url_sync(), pool_pre_ping=True, future=True)


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
    """
    factory = get_sync_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
