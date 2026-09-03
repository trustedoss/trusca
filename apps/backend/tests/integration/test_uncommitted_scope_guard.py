"""``sync_session_scope`` reports a block that wrote and never committed (ER39).

Two sweep tasks corrected rows in memory, counted the corrections into their
summaries, and returned success while the database kept its old values, because
``sync_session_scope`` leaves the commit to the caller and neither caller made
one. Both are fixed. What is not fixed by fixing them is the next task somebody
writes the same way.

A static check cannot find this class. The write may sit in the task body or
inside a service the task calls, and the commit may sit in either place too, so
"does this file contain ``.commit()``" flags eight tasks that correctly delegate
their writes and catches nothing real. The session can answer the question at
the moment it closes, so the scope asks it there.

These tests drive the scope directly rather than a task. A task-shaped test
would only prove the guard fires while that particular task is broken, and the
point of the guard is the tasks nobody has written yet.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from core.db import _warn_if_uncommitted as _REAL_WARN
from models import Organization

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping uncommitted-scope guard tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; scope guard tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def unguarded(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Put the production warning back for the duration of one test.

    ``tests/conftest.py`` replaces ``_warn_if_uncommitted`` with a raising
    version for every test, which is what makes a forgotten commit fail CI.
    Two of the tests below need to observe the warning itself, so they restore
    the real function first.
    """
    import core.db as core_db

    monkeypatch.setattr(core_db, "_warn_if_uncommitted", _REAL_WARN)
    yield


def _new_org() -> Organization:
    token = uuid.uuid4().hex[:10]
    return Organization(name=f"guard-{token}", slug=f"guard-{token}")


def test_a_scope_that_writes_and_never_commits_is_reported(
    unguarded: None,
) -> None:
    """The shape both sweep tasks had: rows written, block closed, nothing said."""
    from core.db import sync_session_scope

    with capture_logs() as logs:
        with sync_session_scope() as session:
            session.add(_new_org())
            session.flush()

    events = [e["event"] for e in logs]
    assert "session_scope_closed_with_uncommitted_writes" in events


def test_a_scope_that_commits_is_not_reported(unguarded: None) -> None:
    from core.db import sync_session_scope

    with capture_logs() as logs:
        with sync_session_scope() as session:
            org = _new_org()
            session.add(org)
            session.commit()
            org_id = org.id

    events = [e["event"] for e in logs]
    assert "session_scope_closed_with_uncommitted_writes" not in events

    # And the row is really there, so the quiet answer is the true one.
    with sync_session_scope() as verify:
        assert (
            verify.execute(
                select(Organization.id).where(Organization.id == org_id)
            ).scalar_one_or_none()
            is not None
        )


def test_a_deliberate_rollback_is_not_reported(unguarded: None) -> None:
    """Discarding on purpose answers the question the guard asks.

    This is what keeps the sweeps' dry-run paths quiet: they mutate rows to
    count them and roll back, and a guard that could not tell that apart from a
    forgotten commit would be noise on every dry run.
    """
    from core.db import sync_session_scope

    with capture_logs() as logs:
        with sync_session_scope() as session:
            session.add(_new_org())
            session.flush()
            session.rollback()

    events = [e["event"] for e in logs]
    assert "session_scope_closed_with_uncommitted_writes" not in events


def test_a_read_only_scope_is_not_reported(unguarded: None) -> None:
    """Five of the tasks that never commit only read; none of them may trip this."""
    from core.db import sync_session_scope

    with capture_logs() as logs:
        with sync_session_scope() as session:
            session.execute(select(Organization.id).limit(1)).all()

    events = [e["event"] for e in logs]
    assert "session_scope_closed_with_uncommitted_writes" not in events


def test_the_test_suite_turns_the_warning_into_a_failure() -> None:
    """Without the escalation the guard is a log line nobody reads in CI.

    Deliberately does NOT take the ``unguarded`` fixture: this asserts the
    behaviour every other test in the suite runs under.
    """
    from core.db import sync_session_scope

    with pytest.raises(AssertionError, match="without committing"):
        with sync_session_scope() as session:
            session.add(_new_org())
            session.flush()


def test_a_commit_by_a_called_service_satisfies_the_guard(unguarded: None) -> None:
    """The case a file-level check gets wrong.

    ``stale_scan_reaper`` has no ``.commit()`` anywhere in its file and is
    correct: it writes through ``mark_failed``, which commits
    (``tasks/_scan_pipeline.py``). A guard that reads files would call that a
    defect. A guard that asks the session sees the commit wherever it happened.
    """
    from core.db import sync_session_scope

    def _service_that_commits(session: object) -> None:
        session.add(_new_org())  # type: ignore[attr-defined]
        session.commit()  # type: ignore[attr-defined]

    with capture_logs() as logs:
        with sync_session_scope() as session:
            _service_that_commits(session)

    events = [e["event"] for e in logs]
    assert "session_scope_closed_with_uncommitted_writes" not in events
