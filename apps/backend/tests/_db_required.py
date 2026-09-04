"""One place that decides whether an absent database is a skip or a failure.

ER66. 193 test modules gate on the database, and 376 of those gate sites skip
when ``DATABASE_URL`` is unset or when ``alembic upgrade head`` returns
non-zero. On a laptop that is right: not everybody has Postgres running, and a
suite that errors out on a clean checkout is a suite people stop running.

On CI it is wrong, and wrong in the worst direction. A pull request that breaks
a migration makes ``alembic upgrade head`` fail, which makes every test that
needs the schema skip, which lets the job exit 0. The check that exists to
catch a broken migration is silenced by the broken migration.

So the behaviour is kept and the venue decides it. With
``TRUSCA_TESTS_REQUIRE_DB`` set, an absent database and a failed migration are
failures. Without it, both stay skips.

Three call sites already did this correctly before the flag existed, in three
different ways - ``test_health_ready.py`` asserts on the return code,
``test_component_approval_service.py`` raises, and
``test_queue_transition_consumption.py`` calls ``pytest.fail``. This module is
not a new convention; it is those three collapsed into one that the other 376
can share.
These are plain functions, called from each module's own fixture, and NOT
fixtures themselves. That is deliberate twice over, and the second reason is
the one that is easy to lose:

1. Each module keeps its own fixture, with its own scope and its own position
   relative to that module's other fixtures. Nothing about ordering changes.
2. Sharing a *fixture* means importing it, and ruff rejects that. Measured on
   this tree: ``from tests._helpers import make_user`` followed by
   ``def test_x(make_user)`` produces both ``F401`` (imported but unused) and
   ``F811`` (redefinition of unused name). pytest understands that form; the
   linter does not.

So "make these fixtures, it is more idiomatic" is a change to decline: it
would trade a form the linter accepts for one it rejects, and the way out of
that is the copying this module exists to end.

What made the pattern spread to 193 files is not recorded and is not claimed
here. One case was traced: a file took two fixtures from a sibling, hit F811
on the import, and moved both into itself - and only one of the two had a
shared version to use instead. That is one file's history, not the history of
the other 192.

Putting the fixture in ``conftest.py`` avoids the import and so avoids F811
entirely. That is the orthodox answer and it stays open, but it needs one
fixture name and scope agreed across every module that uses it, which makes it
a design change rather than the mechanical move this is.

"""

"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent

REQUIRE_ENV = "TRUSCA_TESTS_REQUIRE_DB"
_TRUE = frozenset({"1", "true", "yes", "on"})


def database_is_required() -> bool:
    """Whether an unavailable database should fail the run rather than skip it."""
    return os.getenv(REQUIRE_ENV, "").strip().lower() in _TRUE


def _unavailable(reason: str) -> None:
    if database_is_required():
        pytest.fail(f"{reason}\n({REQUIRE_ENV} is set, so this is not a skip.)")
    pytest.skip(reason)


def require_database_url() -> str:
    """The configured database URL, or the venue's verdict on not having one."""
    url = os.getenv("DATABASE_URL")
    if not url:
        _unavailable("DATABASE_URL is not set, so database tests cannot run.")
        raise AssertionError("unreachable")  # pragma: no cover - pytest exits above
    return url


# What this process knows about migrating, keyed by the database it migrated.
# The key matters: without it, "we have migrated" loses track of WHICH database
# it migrated, and a test pointed at a second database in the same process
# would be handed somebody else's success. No test does that today; the key
# costs one dict lookup and means none ever can.
_ATTEMPTS: dict[str, subprocess.CompletedProcess[str]] = {}


def _attempt_migration(timeout: float) -> subprocess.CompletedProcess[str]:
    """Run `alembic upgrade head` once per process and remember the outcome.

    Caching the FAILURE is the point, not caching the success. Each of the 193
    modules used to run its own migration, so when one broke, the first module
    reported the real error and every module after it reported
    `relation "..." already exists` from re-running a chain that had stopped
    partway. The true cause appeared once, near the top of a log where the last
    words are about a duplicate table. Measured on a probe branch: even the one
    module that raises instead of skipping reported the duplicate, because it
    was not the first to try.

    A retry cannot succeed anyway - the migration that failed will fail again -
    so the only thing retrying produces is a second, worse explanation.

    Named for what the 193 copies of ``_migrate_once`` claimed to be. That name
    was true in its own scope and false in the one that mattered: each module
    migrated once, and a session migrated 193 times. Reading the name alone
    told you the opposite of what the suite did.

    Under pytest-xdist this cache is per worker, not per session. That is
    survivable because ``alembic/env.py`` takes ``pg_advisory_xact_lock`` on
    ``MIGRATION_ADVISORY_LOCK_KEY`` around ``run_migrations()``, so workers
    serialise rather than collide: the first migrates and the rest find nothing
    to do. The gap is the failure path - if the migration is broken, each
    worker attempts it once, and workers after the first can report the
    duplicate-table sequel rather than the original error. CI does not use
    xdist today (there is a ``serial`` marker in pyproject.toml left for
    whoever parallelises), so this is a note for that day, not a live defect.
    """
    key = require_database_url()
    if key not in _ATTEMPTS:
        _ATTEMPTS[key] = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    return _ATTEMPTS[key]


def connection_error() -> str | None:
    """``None`` if the database answers, else why it did not.

    This is what separates the two ways a migration can fail. Deciding it by
    reading the migration's stderr would mean matching driver wording, which
    changes between versions and between drivers; opening a connection asks
    the question directly.

    The reason is returned rather than discarded because a bare skip reads as
    "no database here" and the reader moves on, while a skip carrying the
    error is usually answered in half a minute.

    The case that prompted this: a database that had not been created yet.
    libpq reports one line per address it tried, and the first of them was
    ``connection to server at "localhost" (::1), port 5432 failed: Connection
    refused`` - true, since nothing listens on IPv6 here, but not the reason.
    The reason was three lines down: ``FATAL: database "..." does not exist``.
    Read through a ``tail``, the first line looked like the answer and an hour
    went into an IPv6 theory that explained nothing. So: keep the whole error,
    and remember that libpq's last line is the one that matters.
    """
    from core.config import database_url_owner_sync

    try:
        import psycopg2

        psycopg2.connect(database_url_owner_sync(), connect_timeout=5).close()
    except Exception as exc:  # noqa: BLE001 - any failure here means "cannot reach it"
        return str(exc).strip() or exc.__class__.__name__
    return None


def migrate_to_head(timeout: float = 120) -> None:
    """Bring the database to head, or say why these tests cannot run.

    Two different situations end up here and they deserve different verdicts.

    No database is an environment fact: on a laptop without Postgres running,
    skipping is right, and that is what the flag governs.

    A database that answers but will not migrate is a fault. It is the same
    fault whether it happens on CI or on somebody's laptop, and skipping it
    locally means the person who broke a migration finds out from CI instead
    of from the run they just did. So that one fails everywhere, flag or no
    flag.

    The stderr of a failed migration is carried into the message because that
    text is the whole diagnosis: without it the reader sees tests that did not
    run and no reason, and looks for the fault in the tests.
    """
    result = _attempt_migration(timeout)
    unreachable = connection_error() if result.returncode != 0 else None
    if result.returncode != 0 and unreachable is None:
        pytest.fail(
            "alembic upgrade head failed against a database that is up, so "
            "something is wrong with the migrations rather than with this "
            f"environment.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if result.returncode != 0:
        # Both, in that order. The connection error is what the reader can
        # act on; alembic's own output is the raw evidence and dropping it
        # would trade one silence for another.
        _unavailable(
            "the database could not be reached, so the schema these tests "
            f"need does not exist.\nconnecting said: {unreachable}\n"
            f"alembic said:\n{result.stderr}"
        )


def _reset_for_testing() -> None:
    """Forget every attempt. For this module's own tests only."""
    _ATTEMPTS.clear()
