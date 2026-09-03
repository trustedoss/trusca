"""The two one-shot backfills must actually write (ER39).

Both tasks walk a catalog in batches, fix rows in place, and report what they
fixed in a summary dict. Neither committed. ``core.db.sync_session_scope`` does
not commit on exit and says so, so every batch was discarded at the end of its
block: the tasks reported ``stale=N`` / ``updated=N`` and the database kept its
old values. Nothing raised, so nothing surfaced.

They survived because nothing ran them. ``test_vulnerability_catalog_refresh``
covers only the row-level helpers, and the license backfill's existing test
drives the injected-session path (``session=``), which by contract does NOT
commit and leaves the transaction to the caller. The production path, the one
that opens its own scope, had no test at all. So these run the real entry point
with no session injected and read the rows back through a SEPARATE connection,
which is the only way to tell a durable write from a live transaction.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from models import License as LicenseModel
from models import Vulnerability
from services.license_flags import classify_review_flag

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# A license the classifier actually flags. The first attempt at this test used
# GPL-3.0-only, which classifies to None: the row already agreed, the task
# reported ``updated=0``, and the assertion passed against a task that wrote
# nothing. Ordinary copyleft is out of scope for the review flag by design.
FLAGGED_SPDX = "LLAMA-2"
FLAGGED_NAME = "Llama 2 Community License"


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping backfill persistence integration")
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
            f"alembic upgrade head failed; backfill persistence cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_stale_vulnerability(factory: sessionmaker[Session]) -> str:
    """A row matching ``_is_stale_catalog_row``: DT-era source, summary == details."""
    external_id = f"CVE-2094-{uuid.uuid4().hex[:8]}"
    session = factory()
    try:
        session.add(
            Vulnerability(
                external_id=external_id,
                source="NVD",  # not 'trivy' -> stale by provenance
                severity="high",
                summary="the same paragraph twice",
                details="the same paragraph twice",
                references=["https://nvd.nist.gov/vuln/detail/CVE-2094-0001"],
            )
        )
        session.commit()
    finally:
        session.close()
    return external_id


def _read_vulnerability(
    factory: sessionmaker[Session], external_id: str
) -> tuple[str, str | None]:
    session = factory()
    try:
        row = session.execute(
            select(Vulnerability.source, Vulnerability.details).where(
                Vulnerability.external_id == external_id
            )
        ).one()
        return row.source, row.details
    finally:
        session.close()


def test_catalog_refresh_persists_its_fixes(
    session_factory: sessionmaker[Session],
) -> None:
    """A stale row is still fixed after the task's own transaction is gone."""
    from tasks.vulnerability_catalog_refresh import refresh_stale_catalog_rows

    external_id = _seed_stale_vulnerability(session_factory)
    assert _read_vulnerability(session_factory, external_id) == (
        "NVD",
        "the same paragraph twice",
    )

    summary = refresh_stale_catalog_rows.run(dry_run=False)
    assert summary["stale"] >= 1
    assert summary["details_dropped"] >= 1

    # ``_refresh_row_inplace`` fixes ``references`` and drops a ``details``
    # that merely repeats ``summary``; it leaves ``source`` alone, so the row
    # stays "stale" by provenance and gets re-counted on the next sweep. What
    # matters here is that the change it did make is still there after the
    # task's transaction is gone.
    source, details = _read_vulnerability(session_factory, external_id)
    assert details is None, "the duplicated details paragraph did not stay dropped"
    assert source == "NVD"


def test_catalog_refresh_dry_run_changes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    """The counting path stays observably read-only.

    The commit added for ER39 sits in the ``else`` of the dry-run branch, so
    this is what holds it there.
    """
    from tasks.vulnerability_catalog_refresh import refresh_stale_catalog_rows

    external_id = _seed_stale_vulnerability(session_factory)
    before = _read_vulnerability(session_factory, external_id)

    summary = refresh_stale_catalog_rows.run(dry_run=True)
    assert summary["stale"] >= 1, "dry run must still count what it would touch"

    assert _read_vulnerability(session_factory, external_id) == before


def _seed_unflagged_license(factory: sessionmaker[Session]) -> None:
    session = factory()
    try:
        session.execute(delete(LicenseModel).where(LicenseModel.spdx_id == FLAGGED_SPDX))
        session.add(
            LicenseModel(
                spdx_id=FLAGGED_SPDX,
                name=FLAGGED_NAME,
                category="conditional",
                review_flag=None,
            )
        )
        session.commit()
    finally:
        session.close()


def _read_review_flag(factory: sessionmaker[Session]) -> str | None:
    session = factory()
    try:
        return session.execute(
            select(LicenseModel.review_flag).where(
                LicenseModel.spdx_id == FLAGGED_SPDX
            )
        ).scalar_one()
    finally:
        session.close()


def test_license_backfill_persists_its_fixes(
    session_factory: sessionmaker[Session],
) -> None:
    """The production path (no injected session) writes the flag durably."""
    from tasks.license_review_flag_backfill import backfill_license_review_flags

    expected = classify_review_flag(FLAGGED_SPDX, FLAGGED_NAME)
    assert expected is not None, "the fixture license must be one the classifier flags"

    _seed_unflagged_license(session_factory)
    assert _read_review_flag(session_factory) is None

    summary = backfill_license_review_flags.run(dry_run=False)
    assert summary["updated"] >= 1

    assert _read_review_flag(session_factory) == expected


def test_license_backfill_dry_run_changes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    from tasks.license_review_flag_backfill import backfill_license_review_flags

    _seed_unflagged_license(session_factory)

    summary = backfill_license_review_flags.run(dry_run=True)
    assert summary["updated"] >= 1, "dry run must still count what it would change"

    assert _read_review_flag(session_factory) is None
