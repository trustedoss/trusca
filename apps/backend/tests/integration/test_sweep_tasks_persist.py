# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The two sweeps write what they report writing.

Both tasks take an optional caller-owned session, and every test they had used
it. That path is documented not to commit, so the production path, which opens
its own scoped session per batch, had never run under test. It did not commit
either: `sync_session_scope` leaves that to the caller. The sweeps walked the
catalog, counted what they would change, mutated rows in memory, and rolled
everything back at scope exit while returning a summary that said work had
been done.

Nothing could see it. The tasks raised nothing, logged a successful
completion, and reported non-zero counts. Re-running produced the same counts
again, which is what an idempotent sweep looks like from the outside.

These tests call each task with no session, then read the rows back through a
connection of their own. A row that was never committed is invisible to a
second connection, which is the only vantage point from which this failure is
distinguishable from success.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _sync_url() -> str:
    """psycopg2, which is what the requirements pin."""
    return os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr[-400:]}")


@pytest.fixture
def session() -> Iterator[Session]:
    """A connection of our own, separate from the one the task uses.

    The separation is the point. Reading through the task's own session would
    see its uncommitted work and the test would pass against the bug.
    """
    engine = create_engine(_sync_url(), future=True)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as s:
        yield s
    engine.dispose()


def test_the_licence_backfill_commits_what_it_counts(session: Session) -> None:
    """Run the sweep on its own sessions, then read the row back on ours."""
    from tasks.license_review_flag_backfill import backfill_license_review_flags

    key = f"TEST-COMMIT-{uuid.uuid4().hex[:8]}"
    session.execute(
        text(
            "INSERT INTO licenses (spdx_id, name, classification, review_flag)"
            " VALUES (:k, :k, 'forbidden', NULL)"
        ),
        {"k": key},
    )
    session.commit()

    try:
        summary = backfill_license_review_flags.run(dry_run=False)
        assert summary["updated"] >= 1, "the sweep reported no work at all"

        session.expire_all()
        flag = session.execute(
            text("SELECT review_flag FROM licenses WHERE spdx_id = :k"),
            {"k": key},
        ).scalar_one()

        assert flag is not None, (
            "the sweep counted this row as updated but the value was never "
            "committed; a second connection still sees NULL"
        )
    finally:
        session.execute(
            text("DELETE FROM licenses WHERE spdx_id = :k"), {"k": key}
        )
        session.commit()


def test_a_dry_run_of_the_licence_backfill_writes_nothing(
    session: Session,
) -> None:
    """The counting path must stay observably read-only.

    Worth pinning next to the test above: the fix adds a commit on the branch
    the dry run does not take, and a fix that committed on both would look
    identical from the summary alone.
    """
    from tasks.license_review_flag_backfill import backfill_license_review_flags

    key = f"TEST-DRY-{uuid.uuid4().hex[:8]}"
    session.execute(
        text(
            "INSERT INTO licenses (spdx_id, name, classification, review_flag)"
            " VALUES (:k, :k, 'forbidden', NULL)"
        ),
        {"k": key},
    )
    session.commit()

    try:
        backfill_license_review_flags.run(dry_run=True)

        session.expire_all()
        flag = session.execute(
            text("SELECT review_flag FROM licenses WHERE spdx_id = :k"),
            {"k": key},
        ).scalar_one()

        assert flag is None, "a dry run wrote to the database"
    finally:
        session.execute(
            text("DELETE FROM licenses WHERE spdx_id = :k"), {"k": key}
        )
        session.commit()


def test_the_catalog_refresh_commits_what_it_counts(session: Session) -> None:
    """Same shape for the vulnerability catalog sweep.

    A row is stale here when its references still hold the legacy blob shape.
    The sweep rewrites them in place; the assertion is that the rewrite
    survives into another connection.
    """
    from tasks.vulnerability_catalog_refresh import refresh_stale_catalog_rows

    cve = f"CVE-9999-{uuid.uuid4().int % 100000:05d}"
    session.execute(
        text(
            "INSERT INTO vulnerabilities (cve_id, severity, description, "
            '"references") VALUES (:c, \'high\', :d, :r)'
        ),
        {
            "c": cve,
            "d": "seed row for the commit contract",
            "r": '["* [ADVISORY] (https://example.test/a)"]',
        },
    )
    session.commit()

    try:
        summary = refresh_stale_catalog_rows.run(dry_run=False)
        assert summary["scanned"] >= 1

        session.expire_all()
        stored = session.execute(
            text('SELECT "references" FROM vulnerabilities WHERE cve_id = :c'),
            {"c": cve},
        ).scalar_one()

        assert "[ADVISORY]" not in str(stored), (
            "the sweep counted this row as fixed but the rewrite was never "
            "committed; a second connection still sees the legacy blob"
        )
    finally:
        session.execute(
            text("DELETE FROM vulnerabilities WHERE cve_id = :c"), {"c": cve}
        )
        session.commit()
