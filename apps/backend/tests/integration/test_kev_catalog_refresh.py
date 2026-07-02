"""
Integration tests for the CISA KEV catalog refresh task against real Postgres.

Drives ``tasks.kev_catalog_refresh.refresh_kev_catalog`` with the REAL
captured CISA feed excerpt (``tests/fixtures/kev/cisa-kev-excerpt.json`` —
hardening rule 3: no hand-built minimal JSON) wired in through a mocked
``fetch_kev_catalog``, and asserts against the ``vulnerabilities`` table:

  * **Lifecycle sequence** (hardening rule 5): list → delist → re-list. A
    single-direction test would miss the delist arm (CISA does occasionally
    remove entries) and the re-list arm (dates must come back, not stay NULL).
  * **Idempotency**: a second run against the same feed reports zero writes.
  * **Disabled / feed-unavailable skips**: no network attempt when
    ``KEV_REFRESH_ENABLED=false``; a feed outage leaves existing flags
    untouched (never mass-delists).

CLAUDE.md compliance:
  - PostgreSQL only — no SQLite. Skips cleanly when ``DATABASE_URL`` is unset.
  - ``alembic upgrade head`` once per module (sibling pattern to
    ``test_vulnerability_rematch_db.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from integrations.kev_feed import KevEntry, KevFeedUnavailable, parse_kev_catalog
from models import Vulnerability

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_PATH = BACKEND_ROOT / "tests" / "fixtures" / "kev" / "cisa-kev-excerpt.json"

LOG4SHELL = "CVE-2021-44228"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip KEV catalog refresh tests")
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
            "alembic upgrade head failed; KEV refresh tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app() -> Any:
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient) -> Any:
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _fixture_catalog() -> dict[str, KevEntry]:
    """Parse the real captured excerpt into the task's input shape."""
    return parse_kev_catalog(json.loads(FIXTURE_PATH.read_text()))


async def _get_or_create_vuln(
    client: AsyncClient,
    *,
    external_id: str,
    severity: str = "high",
    kev: bool = False,
    kev_date_added: date | None = None,
    kev_due_date: date | None = None,
) -> uuid.UUID:
    """Idempotent seed for a catalog row.

    The integration DB is shared across runs (no transactional rollback) and
    ``external_id`` is unique — the fixture feed carries FIXED real CVE ids,
    so a plain INSERT would trip the unique constraint on the second run. We
    get-or-create and RESET the KEV columns to the requested starting state
    so each test starts deterministic.
    """
    factory = await _factory(client)
    async with factory() as session:
        row: Vulnerability | None = (
            await session.execute(
                select(Vulnerability).where(Vulnerability.external_id == external_id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = Vulnerability(
                external_id=external_id,
                source="NVD",
                severity=severity,
            )
            session.add(row)
        row.kev = kev
        row.kev_date_added = kev_date_added
        row.kev_due_date = kev_due_date
        await session.commit()
        await session.refresh(row)
        return row.id


async def _read_kev_state(
    client: AsyncClient, vuln_id: uuid.UUID
) -> tuple[bool, date | None, date | None]:
    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(
                select(Vulnerability).where(Vulnerability.id == vuln_id)
            )
        ).scalar_one()
        return bool(row.kev), row.kev_date_added, row.kev_due_date


def _run_with_catalog(
    monkeypatch: pytest.MonkeyPatch,
    catalog: dict[str, KevEntry],
    *,
    lower_floor: bool = True,
) -> dict[str, Any]:
    """Invoke the task body directly (no broker) with a mocked feed fetch.

    ``lower_floor=True`` (default) drops the mass-delist sanity floor to 1 so
    the 12-entry REAL fixture excerpt can drive the write passes — the floor
    is calibrated against the full ~1,600-entry production catalog. The floor
    behaviour itself is exercised with the DEFAULT value in the dedicated
    sanity-floor tests below (``lower_floor=False``).
    """
    from tasks import kev_catalog_refresh as task_module

    if lower_floor:
        monkeypatch.setattr(task_module, "_FEED_SANITY_FLOOR", 1)
    monkeypatch.setattr(task_module, "fetch_kev_catalog", lambda: catalog)
    result = task_module.refresh_kev_catalog.run()
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# Lifecycle sequence — list → idempotent re-run → delist → re-list
# ---------------------------------------------------------------------------


async def test_kev_lifecycle_list_delist_relist(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KEV_REFRESH_ENABLED", "true")
    catalog = _fixture_catalog()
    assert LOG4SHELL in catalog  # fixture sanity — the excerpt carries log4shell

    log4shell_id = await _get_or_create_vuln(
        client, external_id=LOG4SHELL, severity="critical"
    )
    # A CVE the feed does NOT list — must never be touched by any pass.
    bystander_cve = f"CVE-2099-{uuid.uuid4().hex[:8].upper()}"
    bystander_id = await _get_or_create_vuln(
        client, external_id=bystander_cve, severity="critical"
    )

    # (1) Listing pass — full fixture feed flags log4shell with CISA's dates.
    summary = _run_with_catalog(monkeypatch, catalog)
    assert summary["skipped"] is False, summary
    assert summary["feed_count"] == 12
    assert summary["listed"] >= 1

    kev, added, due = await _read_kev_state(client, log4shell_id)
    assert kev is True
    assert added == date(2021, 12, 10)
    assert due == date(2021, 12, 24)

    kev, added, due = await _read_kev_state(client, bystander_id)
    assert (kev, added, due) == (False, None, None)

    # (2) Idempotency — the same feed again is a pure read: zero writes.
    summary = _run_with_catalog(monkeypatch, catalog)
    assert summary["skipped"] is False, summary
    assert summary["listed"] == 0
    assert summary["delisted"] == 0

    # (3) Delist — CISA drops log4shell from the feed; the flag AND both
    # dates must clear (a stale due date would drive a phantom SLA).
    without_log4shell = {k: v for k, v in catalog.items() if k != LOG4SHELL}
    summary = _run_with_catalog(monkeypatch, without_log4shell)
    assert summary["skipped"] is False, summary
    assert summary["delisted"] >= 1

    kev, added, due = await _read_kev_state(client, log4shell_id)
    assert (kev, added, due) == (False, None, None)

    # (4) Re-list — the CVE returns to the feed; flag and dates come back.
    summary = _run_with_catalog(monkeypatch, catalog)
    assert summary["skipped"] is False, summary
    assert summary["listed"] >= 1

    kev, added, due = await _read_kev_state(client, log4shell_id)
    assert kev is True
    assert added == date(2021, 12, 10)
    assert due == date(2021, 12, 24)


# ---------------------------------------------------------------------------
# Mass-delist sanity floor (security-reviewer MAJOR) — a valid-JSON but
# empty / gutted feed must NEVER reach the delist pass. Both cases run with
# the DEFAULT floor (500), not the lowered test floor.
# ---------------------------------------------------------------------------


async def test_empty_feed_skips_and_preserves_flags(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``{"vulnerabilities": []}`` parses cleanly to an empty catalog — the
    task must treat it like an outage (skip), not delist every kev=true row."""
    monkeypatch.setenv("KEV_REFRESH_ENABLED", "true")
    flagged_id = await _get_or_create_vuln(
        client,
        external_id=LOG4SHELL,
        severity="critical",
        kev=True,
        kev_date_added=date(2021, 12, 10),
        kev_due_date=date(2021, 12, 24),
    )

    empty_catalog = parse_kev_catalog({"vulnerabilities": []})
    assert empty_catalog == {}
    summary = _run_with_catalog(monkeypatch, empty_catalog, lower_floor=False)
    assert summary["skipped"] is True, summary
    assert summary["skipped_reason"] == "feed_below_sanity_floor"
    assert summary["feed_count"] == 0
    assert summary["delisted"] == 0

    kev, added, due = await _read_kev_state(client, flagged_id)
    assert kev is True
    assert added == date(2021, 12, 10)
    assert due == date(2021, 12, 24)


async def test_below_floor_feed_skips_and_preserves_flags(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drastically truncated feed (3 entries parsed vs a ~1,600-entry real
    catalog) is below the sanity floor — skip, flags untouched."""
    monkeypatch.setenv("KEV_REFRESH_ENABLED", "true")
    flagged_id = await _get_or_create_vuln(
        client,
        external_id=LOG4SHELL,
        severity="critical",
        kev=True,
        kev_date_added=date(2021, 12, 10),
        kev_due_date=date(2021, 12, 24),
    )

    full = _fixture_catalog()
    truncated = dict(list(full.items())[:3])
    assert len(truncated) == 3
    summary = _run_with_catalog(monkeypatch, truncated, lower_floor=False)
    assert summary["skipped"] is True, summary
    assert summary["skipped_reason"] == "feed_below_sanity_floor"
    assert summary["feed_count"] == 3
    assert summary["delisted"] == 0

    kev, added, due = await _read_kev_state(client, flagged_id)
    assert kev is True
    assert added == date(2021, 12, 10)
    assert due == date(2021, 12, 24)


# ---------------------------------------------------------------------------
# Skip paths — disabled toggle / feed outage
# ---------------------------------------------------------------------------


async def test_disabled_toggle_skips_without_fetching(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks import kev_catalog_refresh as task_module

    monkeypatch.setenv("KEV_REFRESH_ENABLED", "false")

    def _must_not_be_called() -> dict[str, KevEntry]:
        raise AssertionError("fetch_kev_catalog must not be called when disabled")

    monkeypatch.setattr(task_module, "fetch_kev_catalog", _must_not_be_called)
    summary = task_module.refresh_kev_catalog.run()
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"


async def test_feed_unavailable_leaves_existing_flags_untouched(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient CISA outage must NOT mass-delist previously flagged rows."""
    from tasks import kev_catalog_refresh as task_module

    monkeypatch.setenv("KEV_REFRESH_ENABLED", "true")
    flagged_id = await _get_or_create_vuln(
        client,
        external_id=LOG4SHELL,
        severity="critical",
        kev=True,
        kev_date_added=date(2021, 12, 10),
        kev_due_date=date(2021, 12, 24),
    )

    def _feed_down() -> dict[str, KevEntry]:
        raise KevFeedUnavailable("simulated outage")

    monkeypatch.setattr(task_module, "fetch_kev_catalog", _feed_down)
    summary = task_module.refresh_kev_catalog.run()
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "feed_unavailable"

    kev, added, due = await _read_kev_state(client, flagged_id)
    assert kev is True
    assert added == date(2021, 12, 10)
    assert due == date(2021, 12, 24)
