"""
Backend test bootstrap.

Adds the backend root to sys.path so tests can import top-level packages
(`main`, `core`, `tasks`) when pytest is invoked from anywhere.

Also installs autouse fixtures that:
  - reset slowapi's in-memory rate-limit storage so policy state never
    leaks across tests, and
  - dispose the FastAPI app's async engine after every test so asyncpg's
    connection pool does not get reused under a different event loop.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _stub_enqueue_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `tasks.enqueue_scan` with a deterministic stub by default.

    PR #8 wired `services.scan_service.trigger_scan` to call
    `tasks.enqueue_scan(scan)`, which submits a real Celery task. With a
    healthy broker + worker the worker picks the task up and flips
    `scan.status` from 'queued' to 'running' before the test reads the
    response — racing the assertions in PR #7's pre-Celery contract.

    The stub returns a static UUID-shaped string. Tests that need to
    observe the real dispatcher (`tests/integration/scan/test_trigger_scan_enqueues_celery.py`)
    re-monkeypatch `services.scan_service.enqueue_scan` themselves; this
    fixture only affects the default path.
    """
    try:
        import services.scan_service as scan_service_mod
    except Exception:  # pragma: no cover - tests that don't import service layer
        return
    monkeypatch.setattr(
        scan_service_mod,
        "enqueue_scan",
        lambda scan: "00000000-0000-0000-0000-000000000001",
    )


@pytest.fixture(autouse=True)
def _fail_on_uncommitted_session_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escalate ER39's warning to a test failure.

    ``core.db.sync_session_scope`` warns when a block emitted DML and closed
    without committing; in production it stays a warning, because raising
    would turn tasks that have been silently inert into loudly failing ones.
    Here the same condition is an error, so a task written without its commit
    fails in CI instead of shipping and doing nothing.

    This only fires for tests that actually run the task. A write task with no
    test that executes it is still invisible, which is why the two tasks fixed
    alongside this guard each gained one.
    """
    try:
        import core.db as core_db
    except Exception:  # pragma: no cover - tests that don't import the db layer
        return

    def _raise(session: object) -> None:
        info = getattr(session, "info", {})
        if not (
            info.get(core_db._UNCOMMITTED_DML)
            or session.new  # type: ignore[attr-defined]
            or session.dirty  # type: ignore[attr-defined]
            or session.deleted  # type: ignore[attr-defined]
        ):
            return
        raise AssertionError(
            "a sync_session_scope block wrote and closed without committing "
            "(ER39). sync_session_scope does not auto-commit: call "
            "session.commit() inside the block, or session.rollback() if the "
            "discard is deliberate."
        )

    monkeypatch.setattr(core_db, "_warn_if_uncommitted", _raise)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Clear slowapi's in-memory storage so rate-limit state never leaks."""
    try:
        from core.ratelimit import limiter
    except Exception:  # pragma: no cover - slowapi optional in some envs
        return
    limiter.reset()


@pytest.fixture(autouse=True)
async def _isolate_engine_per_test() -> AsyncIterator[None]:
    """
    Dispose the FastAPI app's async engine after every test.

    pytest-asyncio creates a fresh event loop per test by default. asyncpg
    connections bind to whatever loop opened them, so reusing the engine
    across tests crashes with "got Future <...> attached to a different
    loop". We dispose after each test; the next test triggers
    core.db._ensure_state to rebuild it under the new loop.
    """
    yield
    try:
        from main import app
    except Exception:  # pragma: no cover
        return
    engine = getattr(app.state, "engine", None)
    if engine is None:
        return
    await engine.dispose()
    if "engine" in app.state.__dict__:
        del app.state.__dict__["engine"]
    if "session_factory" in app.state.__dict__:
        del app.state.__dict__["session_factory"]
