"""The EPSS sync must leave real scores in the catalog (integration).

The defect this feature closes was invisible for a release because the tests
around it asserted the wrong thing. ``_extract_epss`` had unit coverage built
from hand-written dicts carrying an ``EPSS`` key; the parser was correct and
the scanner never emitted that key, so every EPSS surface read NULL while the
suite stayed green. A test that only proves a code path runs cannot notice
that its input never arrives.

So the assertion here is the one that was missing: after a sync, read the rows
back on a separate connection and check that ``epss_score`` is not NULL and
carries the published value. Anything weaker passes against a task that writes
nothing, which is the failure mode worth guarding.

Runs against the real Postgres: the task's work IS the UPDATE, and the
question is whether it is durable.
"""

from __future__ import annotations

import gzip
import os
import subprocess
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from models import EpssSyncState, Vulnerability

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    BACKEND_ROOT / "tests" / "fixtures" / "epss" / "epss-scores-excerpt.csv.gz"
)

# From the captured feed. Asserted as published values, not as "something".
LOG4SHELL = "CVE-2021-44228"
LOG4SHELL_SCORE = Decimal("0.99999")
LOG4SHELL_PERCENTILE = Decimal("1.00000")
CURL_CVE = "CVE-2015-3153"
CURL_SCORE = Decimal("0.07247")


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping EPSS sync integration")
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
            f"alembic upgrade head failed; EPSS sync integration cannot run\n"
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


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sync is off by default; every test here turns it on explicitly."""
    monkeypatch.setenv("EPSS_REFRESH_ENABLED", "true")


def _feed_bytes(rows: int | None = None) -> bytes:
    """The captured feed, optionally padded to look like a full-size document.

    The task refuses a document with implausibly few rows, which the twelve-row
    excerpt is. Padding with synthetic CVEs that match nothing in the catalog
    keeps the twelve REAL rows the assertions read while getting the row count
    past the floor, so the sanity floor is exercised as itself rather than
    being turned off for the tests.
    """
    text = gzip.decompress(FIXTURE_PATH.read_bytes()).decode("utf-8").rstrip("\n")
    if rows is not None:
        padding = "\n".join(
            f"CVE-1900-{i:07d},0.00001,0.00001" for i in range(rows)
        )
        text = f"{text}\n{padding}"
    return gzip.compress((text + "\n").encode("utf-8"))


@pytest.fixture
def stub_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the captured document through the real client and real parser.

    The transport is stubbed, not the parser: a test that replaced
    ``fetch_epss_scores`` would assert the task can store whatever it is
    handed, which is not the thing that was broken.
    """
    import integrations.epss_feed as feed_module

    real_fetch = feed_module.fetch_epss_scores
    payload = _feed_bytes(rows=200_000)

    def _fetch(**kwargs: object) -> object:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            return real_fetch(http=http, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("tasks.epss_catalog_refresh.fetch_epss_scores", _fetch)


def _seed(factory: sessionmaker[Session], *external_ids: str) -> None:
    session = factory()
    try:
        session.execute(
            delete(Vulnerability).where(Vulnerability.external_id.in_(external_ids))
        )
        for external_id in external_ids:
            session.add(
                Vulnerability(external_id=external_id, source="trivy", severity="high")
            )
        session.commit()
    finally:
        session.close()


def _read(
    factory: sessionmaker[Session], external_id: str
) -> tuple[Decimal | None, Decimal | None]:
    session = factory()
    try:
        row = session.execute(
            select(Vulnerability.epss_score, Vulnerability.epss_percentile).where(
                Vulnerability.external_id == external_id
            )
        ).one()
        return row.epss_score, row.epss_percentile
    finally:
        session.close()


def test_the_sync_fills_scores_that_were_null(
    session_factory: sessionmaker[Session], enabled: None, stub_feed: None
) -> None:
    """The assertion the old tests never made: the column is no longer NULL."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    _seed(session_factory, LOG4SHELL, CURL_CVE)
    assert _read(session_factory, LOG4SHELL) == (None, None)

    summary = refresh_epss_scores.run()

    assert summary["skipped"] is False
    assert summary["updated"] >= 2

    score, percentile = _read(session_factory, LOG4SHELL)
    assert score == LOG4SHELL_SCORE
    assert percentile == LOG4SHELL_PERCENTILE
    # A second, mid-range CVE, so a task that wrote one constant everywhere
    # would not pass on the headline value alone.
    assert _read(session_factory, CURL_CVE)[0] == CURL_SCORE


def test_a_second_tick_over_an_unchanged_feed_writes_nothing(
    session_factory: sessionmaker[Session], enabled: None, stub_feed: None
) -> None:
    """Idempotent, and cheaply so: only rows whose value moved are touched."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    _seed(session_factory, LOG4SHELL)
    first = refresh_epss_scores.run()
    assert first["updated"] >= 1

    second = refresh_epss_scores.run()

    assert second["skipped"] is False
    assert second["matched"] >= 1, "the CVE is still matched"
    assert second["updated"] == 0, "an unchanged score must not be rewritten"
    assert _read(session_factory, LOG4SHELL)[0] == LOG4SHELL_SCORE


def test_a_cve_outside_the_catalog_is_not_created(
    session_factory: sessionmaker[Session], enabled: None, stub_feed: None
) -> None:
    """367,000 feed rows must not become 367,000 catalog rows."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    _seed(session_factory, LOG4SHELL)
    absent = "CVE-2016-0800"
    session = session_factory()
    try:
        session.execute(
            delete(Vulnerability).where(Vulnerability.external_id == absent)
        )
        session.commit()
    finally:
        session.close()

    refresh_epss_scores.run()

    session = session_factory()
    try:
        assert (
            session.execute(
                select(Vulnerability.id).where(Vulnerability.external_id == absent)
            ).scalar_one_or_none()
            is None
        ), "the sync scores what the deployment has seen; it does not import a corpus"
    finally:
        session.close()


def test_a_truncated_feed_leaves_existing_scores_alone(
    session_factory: sessionmaker[Session],
    enabled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad publish upstream must not overwrite good scores.

    The unpadded excerpt is twelve rows, far under the sanity floor, which is
    what a truncated or placeholder document looks like.
    """
    import integrations.epss_feed as feed_module
    from tasks.epss_catalog_refresh import refresh_epss_scores

    real_fetch = feed_module.fetch_epss_scores
    payload = _feed_bytes()  # no padding: below the floor

    def _fetch(**kwargs: object) -> object:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            return real_fetch(http=http, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("tasks.epss_catalog_refresh.fetch_epss_scores", _fetch)

    _seed(session_factory, LOG4SHELL)
    session = session_factory()
    try:
        row = session.execute(
            select(Vulnerability).where(Vulnerability.external_id == LOG4SHELL)
        ).scalar_one()
        row.epss_score = Decimal("0.50000")
        row.epss_percentile = Decimal("0.50000")
        session.commit()
    finally:
        session.close()

    summary = refresh_epss_scores.run()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "feed_below_sanity_floor"
    assert _read(session_factory, LOG4SHELL)[0] == Decimal("0.50000")


def test_the_sync_is_off_unless_the_operator_turns_it_on(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default off, and the skip happens before any network attempt."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    monkeypatch.delenv("EPSS_REFRESH_ENABLED", raising=False)

    def _must_not_be_called(**kwargs: object) -> object:
        raise AssertionError("a disabled sync must not reach the network")

    monkeypatch.setattr(
        "tasks.epss_catalog_refresh.fetch_epss_scores", _must_not_be_called
    )
    _seed(session_factory, LOG4SHELL)

    summary = refresh_epss_scores.run()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"
    assert _read(session_factory, LOG4SHELL) == (None, None)


def test_the_status_row_records_the_model_that_produced_the_scores(
    session_factory: sessionmaker[Session], enabled: None, stub_feed: None
) -> None:
    """Which model run is in the catalog is not inferable from the numbers."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    _seed(session_factory, LOG4SHELL)
    refresh_epss_scores.run()

    session = session_factory()
    try:
        state = session.execute(select(EpssSyncState)).scalar_one()
    finally:
        session.close()

    assert state.last_result == "synced"
    assert state.skipped_reason is None
    assert state.model_version == "v2026.06.15"
    assert state.score_date is not None
    assert state.last_synced_at is not None
    assert state.matched is not None and state.matched >= 1


def test_a_skipped_tick_keeps_the_last_good_success_stamp(
    session_factory: sessionmaker[Session], enabled: None, stub_feed: None
) -> None:
    """The panel must be able to show last attempt and last success separately."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    _seed(session_factory, LOG4SHELL)
    refresh_epss_scores.run()

    session = session_factory()
    try:
        good = session.execute(select(EpssSyncState)).scalar_one()
        synced_at = good.last_synced_at
        model_version = good.model_version
    finally:
        session.close()
    assert synced_at is not None

    def _unavailable(**kwargs: object) -> object:
        from integrations.epss_feed import EpssFeedUnavailable

        raise EpssFeedUnavailable("upstream is down")

    import pytest as _pytest  # local: this test switches the stub mid-way

    mp = _pytest.MonkeyPatch()
    try:
        mp.setattr("tasks.epss_catalog_refresh.fetch_epss_scores", _unavailable)
        summary = refresh_epss_scores.run()
    finally:
        mp.undo()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "feed_unavailable"

    session = session_factory()
    try:
        after = session.execute(select(EpssSyncState)).scalar_one()
    finally:
        session.close()

    assert after.last_result == "skipped"
    assert after.skipped_reason == "feed_unavailable"
    assert after.last_synced_at == synced_at, "a skip must not move the success stamp"
    assert after.model_version == model_version


def test_an_empty_catalog_does_not_download_the_feed(
    session_factory: sessionmaker[Session],
    enabled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing scanned yet means nothing to score, and no reason to fetch 2.6 MiB."""
    from tasks.epss_catalog_refresh import refresh_epss_scores

    session = session_factory()
    try:
        session.execute(delete(Vulnerability))
        session.commit()
    finally:
        session.close()

    def _must_not_be_called(**kwargs: object) -> object:
        raise AssertionError("an empty catalog must not trigger a download")

    monkeypatch.setattr(
        "tasks.epss_catalog_refresh.fetch_epss_scores", _must_not_be_called
    )

    summary = refresh_epss_scores.run()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "empty_catalog"
    assert summary["catalog_size"] == 0
