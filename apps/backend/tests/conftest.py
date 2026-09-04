"""
Backend test bootstrap.

Adds the backend root to sys.path so tests can import top-level packages
(`main`, `core`, `tasks`) when pytest is invoked from anywhere.

Also installs autouse fixtures that:
  - reset slowapi's in-memory rate-limit storage so policy state never
    leaks across tests, and
  - dispose the FastAPI app's async engine after every test so asyncpg's
    connection pool does not get reused under a different event loop.

And checks once, at session start, that this run has the Redis index to
itself. See :func:`_redis_index_is_not_shared`.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))



def _redis_occupancy(url: str) -> tuple[int, list[str]]:
    """How many keys the Redis index holds, and a sample of their names.

    The count comes from ``DBSIZE`` rather than from the length of the
    sample: reporting "40 keys" for an index holding 112 would understate
    exactly the thing the reader is being asked to judge.

    Returns ``(0, [])`` when redis is unreachable or the URL is unusable. A
    broken connection is the individual test's problem to report, and turning
    it into a session-wide exit here would replace a specific failure with a
    vague one.
    """
    try:
        import redis
    except ImportError:  # pragma: no cover - redis is a runtime dependency
        return 0, []
    try:
        client = redis.Redis.from_url(url)
        total = int(client.dbsize())
        if total == 0:
            return 0, []
        sample = [
            k.decode("utf-8", "replace")
            for _, k in zip(range(20), client.scan_iter(count=100), strict=False)
        ]
        return total, sample
    except Exception:  # noqa: BLE001 - see docstring
        return 0, []


@pytest.fixture(scope="session", autouse=True)
def _redis_index_is_not_shared() -> None:
    """Refuse to run when somebody else is already using this Redis index.

    Redis has no per-run isolation here. `DATABASE_URL` gets a database per
    branch by convention, but `REDIS_URL` is whatever the invoking command
    passed, and two runs that land on the same index share the broker. Then
    one run's worker consumes the other's message: the task never arrives,
    the test waits out its timeout, and the failure reads as a defect in the
    code under test. That is not hypothetical; it cost an afternoon.

    The symptom is silence, which is why this is worth a hard stop at session
    start rather than a note somewhere. Thirty seconds here beats an hour of
    reading a worker log that says nothing is wrong.

    It reports what it found rather than deciding for you. A leftover from
    your own earlier run looks identical to a live collision from the
    outside, and only you know which it is. Nothing is deleted: on a shared
    index, clearing it would break whoever else is mid-run.

    CI does not hit this. Its redis is a fresh container per job, so the
    index is empty and the check passes without comment.
    """
    import os

    url = os.getenv("REDIS_URL")
    if not url:
        return

    total, sample = _redis_occupancy(url)
    if total == 0:
        return

    shown = ", ".join(sorted(sample)[:10])
    more = "" if total <= 10 else f" (and {total - 10} more)"
    pytest.exit(
        f"REDIS_URL points at an index that already holds {total} key(s): "
        f"{shown}{more}\n"
        "Another run may be using it, in which case both will misbehave in "
        "ways that look like code defects: a worker there will consume "
        "messages published here.\n"
        "If these are leftovers from your own earlier run, clear them "
        "yourself and re-run. If they are not yours, point REDIS_URL at an "
        "index nobody else is using.",
        returncode=3,
    )

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
