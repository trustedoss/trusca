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


def migrate_to_head(timeout: float = 120) -> None:
    """Bring the database to head, or say why these tests cannot run.

    The stderr of a failed migration is carried into the message because that
    text is the whole diagnosis: without it the reader sees tests that did not
    run and no reason, and looks for the fault in the tests.
    """
    result = _attempt_migration(timeout)
    if result.returncode != 0:
        _unavailable(
            "alembic upgrade head failed, so the schema these tests need does "
            f"not exist.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _reset_for_testing() -> None:
    """Forget every attempt. For this module's own tests only."""
    _ATTEMPTS.clear()
