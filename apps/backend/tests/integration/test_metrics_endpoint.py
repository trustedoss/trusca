"""
The operational metrics endpoint (N10).

Two things are worth pinning and the rest follows from them.

Off is the default, and off means the route is not there. A monitoring
endpoint that answers "not allowed" tells an outsider what this host is; one
that answers 404 looks like a deployment without the feature, which is what a
deployment without the feature should look like.

And what it publishes is a decision somebody made, held to
``tests/contracts/metrics-series.json``. The named risk for this unit is a
metric added later without anybody re-reading the whole output, which is how
an endpoint ends up saying how many people work at the company and what their
projects are called.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import make_organization, make_project, make_scan, make_team

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACT = (
    BACKEND_ROOT.parent.parent / "tests" / "contracts" / "metrics-series.json"
)

QUEUE_BACKLOG_SERIES = {"trusca_broker_queue_backlog", "trusca_scan_queue_wait_seconds"}

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping metrics tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
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
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


def _contract() -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = json.loads(CONTRACT.read_text(encoding="utf-8"))[
        "series"
    ]
    return series


def _emitted_series(body: str) -> list[str]:
    """Series names in the order the document declares them."""
    return re.findall(r"^# TYPE (\S+) ", body, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Off, which is the default
# ---------------------------------------------------------------------------


async def test_the_endpoint_is_not_there_unless_a_deployment_asks(
    client, monkeypatch
) -> None:
    monkeypatch.delenv("METRICS_ENABLED", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 404, response.text


async def test_a_wrong_token_looks_exactly_like_switched_off(
    client, monkeypatch
) -> None:
    """A 401 would confirm that this deployment publishes metrics and that the
    caller only needs the token."""
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "the-real-one")

    refused = await client.get(
        "/metrics", headers={"Authorization": "Bearer not-the-real-one"}
    )
    monkeypatch.setenv("METRICS_ENABLED", "false")
    switched_off = await client.get("/metrics")

    assert refused.status_code == switched_off.status_code == 404
    assert refused.text == switched_off.text


# ---------------------------------------------------------------------------
# On
# ---------------------------------------------------------------------------


async def test_turning_it_on_publishes_the_series_the_contract_lists(
    client, monkeypatch
) -> None:
    """In the order the contract lists them, so a diff of two scrapes reads as
    a change in values rather than a reshuffle.

    M2 (concurrency plan 2026-08-22 §3.1) added two series behind their own,
    separately-off toggle, so "everything the contract lists" now also needs
    that toggle on (see test_the_queue_backlog_series_are_absent_when_
    its_own_toggle_is_off below for the default state where it is not).
    """
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text
    assert _emitted_series(response.text) == [s["name"] for s in _contract()]


async def test_nothing_is_published_that_the_contract_does_not_list(
    client, monkeypatch
) -> None:
    """The named silent break for this unit.

    A metric added to the code without a line in the contract file is one
    nobody decided was safe to publish.
    """
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    declared = {s["name"] for s in _contract()}
    for name in _emitted_series(response.text):
        assert name in declared, f"{name} is published but not declared"


async def test_every_label_is_from_a_closed_vocabulary(client, monkeypatch) -> None:
    """No project, person, package or repository name can reach the output.

    Checked against the contract's declared label keys rather than by reading
    the values, because the risk is a label whose key is fine and whose value
    is somebody's project name.
    """
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    allowed = {
        series["name"]: set(series["labels"]) for series in _contract()
    }

    response = await client.get("/metrics")

    for line in response.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        match = re.match(r"^(\w+)(?:\{([^}]*)\})? ", line)
        assert match, line
        name, labels = match.group(1), match.group(2) or ""
        keys = {pair.split("=")[0] for pair in labels.split(",") if pair}
        assert keys <= allowed[name], f"{name} carries undeclared labels {keys}"


async def test_the_values_are_counts_and_nothing_else(client, monkeypatch) -> None:
    """An aggregate, never a row. A count of scans is fine, a scan id is not."""
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    for line in response.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        value = line.rsplit(" ", 1)[1]
        float(value)  # raises if anything but a number reached the output


async def test_a_scraper_with_the_token_is_served(client, monkeypatch) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "the-real-one")

    response = await client.get(
        "/metrics", headers={"Authorization": "Bearer the-real-one"}
    )

    assert response.status_code == 200, response.text


async def test_the_bare_token_is_accepted_too(client, monkeypatch) -> None:
    """Scrapers differ and neither shape is more correct than the other."""
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "the-real-one")

    response = await client.get("/metrics", headers={"Authorization": "the-real-one"})

    assert response.status_code == 200, response.text


async def test_no_token_configured_means_no_token_required(
    client, monkeypatch
) -> None:
    """The usual deployment keeps this off the public ingress and reaches it on
    the internal network, where a shared secret is one more thing to rotate."""
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text


async def test_the_document_is_served_in_the_format_a_scraper_expects(
    client, monkeypatch
) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")  # M2, see above
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]
    for series in _contract():
        assert f"# HELP {series['name']} " in response.text
        assert f"# TYPE {series['name']} {series['type']}\n" in response.text


async def test_reading_metrics_needs_no_account(client, monkeypatch) -> None:
    """It is a scrape target, not a screen.

    Requiring a portal session would mean the monitoring system holds one,
    which is a worse credential to leave lying in a config file than the
    optional token.
    """
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# M2, the queue-backlog series (concurrency plan 2026-08-22 §3.1/§4)
#
# Its own switch, off by default and independent of METRICS_ENABLED: turning
# the endpoint on does not by itself turn the broker round trip on too.
# ---------------------------------------------------------------------------


async def test_the_route_stays_gone_with_the_queue_backlog_toggle_on_alone(
    client, monkeypatch
) -> None:
    """Neither toggle alone opens the route.

    QUEUE_BACKLOG_METRICS_ENABLED cannot substitute for METRICS_ENABLED: the
    default-off state described in the M2 contract holds even if an operator
    sets only the newer of the two flags.
    """
    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")

    response = await client.get("/metrics")

    assert response.status_code == 404, response.text


async def test_the_queue_backlog_series_are_absent_when_its_own_toggle_is_off(
    client, monkeypatch
) -> None:
    """The endpoint's default behaviour is unchanged by M2 existing.

    METRICS_ENABLED on and QUEUE_BACKLOG_METRICS_ENABLED at its default (off)
    is exactly the pre-M2 six-series-shape, not six-plus-two-empty.
    """
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.delenv("QUEUE_BACKLOG_METRICS_ENABLED", raising=False)
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text
    emitted = set(_emitted_series(response.text))
    assert emitted.isdisjoint(QUEUE_BACKLOG_SERIES)
    assert emitted == {s["name"] for s in _contract()} - QUEUE_BACKLOG_SERIES


async def test_the_queue_backlog_series_are_explicitly_off_too(client, monkeypatch) -> None:
    """The same absence when the toggle is explicitly false, not only unset."""
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "false")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text
    assert set(_emitted_series(response.text)).isdisjoint(QUEUE_BACKLOG_SERIES)


async def test_turning_on_both_toggles_publishes_the_queue_backlog_series(
    client, monkeypatch
) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text
    emitted = set(_emitted_series(response.text))
    assert emitted == {s["name"] for s in _contract()}
    # In contract order, same guarantee the base six already carry.
    assert _emitted_series(response.text) == [s["name"] for s in _contract()]


async def test_the_broker_backlog_value_reflects_a_real_redis_list(
    client, monkeypatch
) -> None:
    """Not a stub: pushing a raw message onto the broker's queue moves the
    published number, without a worker ever consuming it (none is running in
    this test)."""
    import redis as redis_lib

    from core.config import redis_url

    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    from tasks.celery_app import celery_app

    queue = str(celery_app.conf.task_default_queue)
    conn = redis_lib.Redis.from_url(os.getenv("REDIS_URL") or redis_url())
    try:
        before = int(conn.llen(queue))  # type: ignore[arg-type]
        conn.lpush(queue, b'{"probe": "m2-broker-backlog-test"}')
        try:
            response = await client.get("/metrics")
            assert response.status_code == 200, response.text
            match = re.search(
                r"^trusca_broker_queue_backlog\{queue=\"" + re.escape(queue) + r"\"\} (\S+)$",
                response.text,
                flags=re.MULTILINE,
            )
            assert match, response.text
            assert float(match.group(1)) == before + 1
        finally:
            conn.lrem(queue, 1, b'{"probe": "m2-broker-backlog-test"}')
    finally:
        conn.close()


async def test_the_broker_backlog_series_covers_the_scan_queue_too(
    client, monkeypatch
) -> None:
    """S3 split the single queue this series used to read into two. A
    deployment that upgraded past S3 and only ever read the (unchanged)
    ``trustedoss.default`` label would believe the scan queue - the one
    §1.1's slot-capacity math is actually about - never backs up."""
    import redis as redis_lib

    from core.config import redis_url
    from tasks.celery_app import _SCAN_QUEUE

    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    conn = redis_lib.Redis.from_url(os.getenv("REDIS_URL") or redis_url())
    try:
        before = int(conn.llen(_SCAN_QUEUE))  # type: ignore[arg-type]
        conn.lpush(_SCAN_QUEUE, b'{"probe": "m2-scan-queue-backlog-test"}')
        try:
            response = await client.get("/metrics")
            assert response.status_code == 200, response.text
            match = re.search(
                r"^trusca_broker_queue_backlog\{queue=\"" + re.escape(_SCAN_QUEUE) + r"\"\} (\S+)$",
                response.text,
                flags=re.MULTILINE,
            )
            assert match, response.text
            assert float(match.group(1)) == before + 1
        finally:
            conn.lrem(_SCAN_QUEUE, 1, b'{"probe": "m2-scan-queue-backlog-test"}')
    finally:
        conn.close()


async def test_the_scan_wait_value_reflects_the_oldest_queued_scan(
    client, monkeypatch, db_session
) -> None:
    from datetime import UTC, datetime, timedelta

    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("QUEUE_BACKLOG_METRICS_ENABLED", "true")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    old_enough = datetime.now(tz=UTC) - timedelta(seconds=120)
    await make_scan(db_session, project=project, status="queued", created_at=old_enough)

    response = await client.get("/metrics")

    assert response.status_code == 200, response.text
    match = re.search(
        r"^trusca_scan_queue_wait_seconds (\S+)$", response.text, flags=re.MULTILINE
    )
    assert match, response.text
    # >= 120s minus a few seconds of scheduling slack, never negative, never
    # the zero this series would report with nothing queued.
    assert float(match.group(1)) >= 110.0


# The zero-baseline case ("nothing queued reports 0, not None") is pinned
    # in tests/unit/test_metrics_service.py against a fake session instead of
    # here: this integration database is shared with the rest of the suite,
    # and other modules' fixtures default new scans to 'queued'
    # (tests/_helpers.py make_scan), so "nothing is queued right now" is not
    # a state this file can put the shared database into without either
    # racing concurrently-running tests or mutating rows another test still
    # expects to find in that status.
