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

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACT = (
    BACKEND_ROOT.parent.parent / "tests" / "contracts" / "metrics-series.json"
)

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
    a change in values rather than a reshuffle."""
    monkeypatch.setenv("METRICS_ENABLED", "true")
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
