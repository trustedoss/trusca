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
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

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

    What it does NOT cover: this looks once, at session start. An index that
    is empty now can be joined a second later by a run that starts after this
    one, and nothing here notices. So a quiet start means "nobody was here
    when I began", not "this run has the index to itself". The convention of
    one index per session is what provides the second half; this only catches
    the case where somebody is already there.

    Skipped entirely when ``REDIS_URL`` is unset, matching the tests that
    need a broker: they skip on the same condition, so a run without one is
    not made to fail here for a resource it was never going to use.

    CI does not hit this. Its redis is a fresh container per job, so the
    index is empty and the check passes without comment.

    A limit worth knowing, and how to see whether it still holds.

    This check reads ``REDIS_URL`` once, at session start.
    ``core/ratelimit.py`` binds its storage when the module is imported. If
    anything changed ``REDIS_URL`` between those two moments, the two would
    watch different indexes and this check would guard the wrong one.

    As of 2026-09-04 nothing does, for two independent reasons. Seven places
    write ``REDIS_URL`` (three in ``tests/unit/test_celery_app.py``, four in
    ``tests/unit/tasks/test_progress_publisher.py``), and all seven are
    ``monkeypatch.setenv`` inside plain test functions rather than fixtures,
    so they revert at teardown. Every value they write points at a host that
    does not exist - ``example-a``, ``primary.local``, ``broker-a.local``,
    ``only-one-url.local`` - or at ``localhost:6390``, which is not this
    Redis. Either reason alone would be enough.

    To check whether that is still true, parse the test tree and find calls
    that WRITE the variable - ``monkeypatch.setenv``, ``os.environ[...] =``,
    ``setdefault``, ``delenv`` - with ``"REDIS_URL"`` as the name; reading it
    is harmless. For each, ask two things: is it inside a fixture (which would
    outlive the test), and does its value name a host that actually exists. A
    count other than seven means somebody added one, which is the signal to
    look rather than assume.
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
        "index nobody else is using.\n"
        "Note that this is checked only at session start: an index empty now "
        "can still be joined by a run that begins after this one.",
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
    # The sign-in throttle keeps one Redis client per event loop for the same
    # reason the engine is per-loop. Nothing closes it when the loop ends, so
    # the connection is collected later against a loop that is already shut
    # and prints "Event loop is closed" from a destructor, which is noise a
    # reader has to rule out before trusting a run.
    try:
        from core.login_throttle import close_client
    except Exception:  # pragma: no cover - the module is always importable
        pass
    else:
        await close_client()
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


@pytest.fixture(scope="session", autouse=True)
def _schema_is_left_where_the_migration_put_it():
    """Notice a test that changes the schema and does not change it back.

    The migration now runs once per process and the result is remembered, so
    a module that damages the schema is no longer repaired by the next
    module's own `alembic upgrade head`. Nobody designed that repair - it was
    a side effect of every module migrating for itself - and removing it took
    nothing away that anyone was relying on. But it did remove a safety net,
    and the way that shows up is a failure in an unrelated module much later,
    with the cause far behind it.

    A comment would not help: the person who writes a schema-mutating test is
    not the person reading this file. So the session checks instead. It costs
    one query, and it cannot name the guilty test - it only says the schema
    moved during this run. That is still worth much more than chasing an
    unrelated failure next week.

    Both current mutators put things back themselves: `test_health_ready.py`
    rewinds `alembic_version` and restores it in `finally`, and
    `test_backup_task_round_trip.py` drops and restores from its own dump.
    """
    yield
    from tests import _db_required

    expected = _db_required.revision_after_migrating()
    if expected is None:
        return  # this run never migrated; there is nothing to compare against
    actual = _db_required.head_revision()
    if actual is None:
        return  # the database went away; that is its own, louder problem
    assert actual == expected, (
        f"the schema revision changed during this run: {expected} -> {actual}. "
        "Some test moved it and did not move it back. Because the migration is "
        "cached per process, nothing repairs that afterwards, and later "
        "failures in unrelated modules are the symptom."
    )

# Fixtures live here when there is nothing left to decide about them: one
# body, one scope, and nothing that changes who receives them. Everything
# else stays where it is.
#
# Counted across the test tree on 2026-09-04, comparing bodies rather than
# names, with string literals and decorators KEPT - erasing either makes
# different fixtures compare equal, since the strings carry the payload
# (which variable, which team) and the decorator carries the lifetime.
#
# Lifted:
#   sync_session   23 files, one body, function scope, not autouse
#   db_factory      3 files, one body, function scope, not autouse
#
# Considered and left alone, with the reason, so the next person counting
# does not repeat the investigation:
#
#   _workspace   12 files, one body, one scope - but autouse, and spread over
#       four directories (integration, unit, unit/tasks, unit/services). The
#       only conftest covering all four is this one, so lifting it would hand
#       an autouse fixture to the whole backend suite: 12 files receiving it
#       becomes thousands. Its cost is trivial (one monkeypatch.setenv), but
#       cheapness is not a reason to apply something everywhere - the real
#       cost is checking whether any of those thousands depends on
#       WORKSPACE_HOST_PATH being unset, and that exceeds the value of
#       removing 12 duplicates.
#
#   client (89 files, 14 bodies), app (79, 5), db_session (66, 11) - a
#       majority body exists in each, but lifting it means the minority
#       silently inherits unless every one of them overrides. pytest allows
#       that override without any sign of which file diverges or why. Visible
#       duplication beats invisible inheritance: duplicates might drift,
#       whereas these have already drifted and the drift would stop showing.
#
#   session (15 files, 9 bodies), _clean_env (12, 10) - as many shapes as
#       files. There is no common version to lift.
#
#   Naming drift, out of scope here because the gain is small and the diff
#       wide, but recorded so somebody editing one finds the other:
#       temp_backups / temp_backups_root, anon_client / client,
#       _patch_async_client / patch_async_client.


@pytest.fixture
def sync_session() -> Iterator[Session]:
    """A synchronous session against the configured database."""
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
async def db_factory() -> AsyncIterator[async_sessionmaker[Any]]:
    """An async session factory against the configured database."""
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
