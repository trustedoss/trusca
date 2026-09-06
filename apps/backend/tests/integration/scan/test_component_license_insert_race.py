"""Component / ComponentVersion / License upsert races must not kill a scan
(#398-A).

``persist_sbom_components`` walks an entire cdxgen SBOM inside ONE caller
transaction, committed once at the end (``tasks/scan_source.py``). Its three
get-or-create helpers each do a plain "SELECT, then INSERT if missing". Two
workers scanning at once can both miss the same purl / spdx_id and both
INSERT; the loser's flush raises a unique violation and Postgres leaves the
transaction aborted. Recovering by rolling back the WHOLE caller transaction
(the naive fix) would silently discard every ScanComponent / LicenseFinding
the loop had already staged ahead of the one row that collided, exactly the
defect ER8 fixed for the vulnerability catalog in
``services/vulnerability_matching.py`` (PR #290). These three helpers get the
same SAVEPOINT treatment; this file proves it the same way
``test_vuln_catalog_insert_race.py`` does.

These run against the real Postgres because the defect IS a unique-constraint
violation across two live connections; no mock reproduces it. The second
session holds an uncommitted INSERT open while the first one runs, which is
what makes the first session's own INSERT block on the unique index and then
fail once the second session commits.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from structlog.testing import capture_logs

from models import Component, ComponentVersion, License
from tasks.scan_source import (
    _get_or_create_component,
    _get_or_create_component_version,
    _get_or_create_license,
)
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration

# How long the second session keeps its uncommitted INSERT open. The first
# session blocks on the unique index for this long, so it has to outlast the
# scheduling jitter of handing control back to the main thread.
HOLD_SECONDS = 3.0


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield factory
    finally:
        engine.dispose()


def _unique_purl() -> str:
    return f"pkg:npm/race-{uuid.uuid4().hex[:12]}"


def _steal_component(
    factory: sessionmaker[Session], purl: str, ready: threading.Event
) -> threading.Thread:
    """Second worker: INSERT the Component row, hold it uncommitted, then commit."""

    def _run() -> None:
        session = factory()
        try:
            session.add(Component(purl=purl, name="race-winner", package_type="npm"))
            session.flush()
            ready.set()
            time.sleep(HOLD_SECONDS)
            session.commit()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _steal_component_version(
    factory: sessionmaker[Session],
    *,
    component_id: uuid.UUID,
    purl_with_version: str,
    ready: threading.Event,
) -> threading.Thread:
    def _run() -> None:
        session = factory()
        try:
            session.add(
                ComponentVersion(
                    component_id=component_id,
                    version="1.0.0",
                    purl_with_version=purl_with_version,
                )
            )
            session.flush()
            ready.set()
            time.sleep(HOLD_SECONDS)
            session.commit()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _steal_license(
    factory: sessionmaker[Session], spdx_id: str, ready: threading.Event
) -> threading.Thread:
    def _run() -> None:
        session = factory()
        try:
            session.add(
                License(
                    spdx_id=spdx_id,
                    name=spdx_id,
                    category="allowed",
                )
            )
            session.flush()
            ready.set()
            time.sleep(HOLD_SECONDS)
            session.commit()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _assert_race_fired(entries: list[dict], event: str, **fields: object) -> None:
    """The race branch was entered, not merely survived (same guard as
    ``test_vuln_catalog_insert_race._assert_race_actually_fired``). A test
    that only checks the row count passes just as happily when the two
    sessions never actually collided (the second session commits first, the
    first session's SELECT finds the row, ordinary path). That green proves
    nothing."""
    matches = [e for e in entries if e.get("event") == event]
    assert matches, f"{event} never logged, the two sessions did not collide"
    for key, value in fields.items():
        assert matches[0].get(key) == value


def test_get_or_create_component_survives_concurrent_insert(
    session_factory: sessionmaker[Session],
) -> None:
    """Two workers racing on the same purl: the loser gets the winner's row,
    no exception propagates, and (the actual bug) a component staged EARLIER
    in the same transaction is not silently discarded by a session-wide
    rollback."""
    purl = _unique_purl()
    ready = threading.Event()
    thief = _steal_component(session_factory, purl, ready)
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        # A component the caller staged BEFORE the race: this is what a
        # whole-transaction rollback would have thrown away.
        staged_purl = _unique_purl()
        staged = _get_or_create_component(
            session, purl=staged_purl, name="staged-before-race", package_type="npm"
        )
        staged_id = staged.id

        with capture_logs() as logs:
            result = _get_or_create_component(
                session, purl=purl, name="race-loser", package_type="npm"
            )
        session.commit()
    finally:
        session.close()
    thief.join(10)

    _assert_race_fired(logs, "component_insert_race", purl=purl)
    assert result.name == "race-winner", "loser must resolve to the winner's row"

    verify = session_factory()
    try:
        count = verify.execute(
            select(func.count()).select_from(Component).where(Component.purl == purl)
        ).scalar_one()
        staged_row = verify.get(Component, staged_id)
    finally:
        verify.close()

    assert count == 1, "exactly one row must survive the race"
    assert staged_row is not None, (
        "the component staged before the race must still have committed, "
        "a whole-transaction rollback would have silently dropped it"
    )


def test_get_or_create_component_version_survives_concurrent_insert(
    session_factory: sessionmaker[Session],
) -> None:
    purl_with_version = f"{_unique_purl()}@1.0.0"

    seed = session_factory()
    try:
        component = Component(
            purl=purl_with_version.rsplit("@", 1)[0],
            name="race-component",
            package_type="npm",
        )
        seed.add(component)
        seed.commit()
        component_id = component.id
    finally:
        seed.close()

    ready = threading.Event()
    thief = _steal_component_version(
        session_factory,
        component_id=component_id,
        purl_with_version=purl_with_version,
        ready=ready,
    )
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        component_row = session.get(Component, component_id)
        assert component_row is not None
        # A version staged BEFORE the race, same component: proves the
        # caller's earlier work in this transaction survives.
        staged = _get_or_create_component_version(
            session,
            component=component_row,
            version="0.9.0",
            purl_with_version=f"{component_row.purl}@0.9.0",
        )
        staged_id = staged.id

        with capture_logs() as logs:
            result = _get_or_create_component_version(
                session,
                component=component_row,
                version="1.0.0",
                purl_with_version=purl_with_version,
            )
        session.commit()
    finally:
        session.close()
    thief.join(10)

    _assert_race_fired(
        logs, "component_version_insert_race", purl_with_version=purl_with_version
    )
    assert result.version == "1.0.0"

    verify = session_factory()
    try:
        count = verify.execute(
            select(func.count())
            .select_from(ComponentVersion)
            .where(ComponentVersion.purl_with_version == purl_with_version)
        ).scalar_one()
        staged_row = verify.get(ComponentVersion, staged_id)
    finally:
        verify.close()

    assert count == 1, "exactly one row must survive the race"
    assert staged_row is not None, (
        "the version staged before the race must still have committed"
    )


def test_get_or_create_license_survives_concurrent_insert(
    session_factory: sessionmaker[Session],
) -> None:
    spdx_id = f"Race-License-{uuid.uuid4().hex[:12]}"
    ready = threading.Event()
    thief = _steal_license(session_factory, spdx_id, ready)
    assert ready.wait(10), "second session never opened its insert"

    session = session_factory()
    try:
        # A license staged BEFORE the race.
        staged_spdx = f"Race-Staged-{uuid.uuid4().hex[:12]}"
        staged = _get_or_create_license(session, spdx_id=staged_spdx, reference_url=None)
        staged_id = staged.id

        with capture_logs() as logs:
            result = _get_or_create_license(session, spdx_id=spdx_id, reference_url=None)
        session.commit()
    finally:
        session.close()
    thief.join(10)

    _assert_race_fired(logs, "license_insert_race", spdx_id=spdx_id)
    assert result.category == "allowed", "loser must resolve to the winner's row"

    verify = session_factory()
    try:
        count = verify.execute(
            select(func.count()).select_from(License).where(License.spdx_id == spdx_id)
        ).scalar_one()
        staged_row = verify.get(License, staged_id)
    finally:
        verify.close()

    assert count == 1, "exactly one row must survive the race"
    assert staged_row is not None, (
        "the license staged before the race must still have committed"
    )
