"""The engine-container reaper against real scan rows (security review on #522, M1).

The container list is a test double; which scans are running comes from the
real database, so the reaper's query is what decides.
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy.orm import Session

from integrations.bomlens.runner import EngineContainer
from models import Scan
from tasks.bomlens_engine_reaper import reap_engine_containers
from tests._bomlens_engine import FakeDocker
from tests.integration.scan.test_bomlens_result import _seed_running_source_scan

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    from tests._db_required import migrate_to_head

    migrate_to_head()


def _set_status(session: Session, scan_id: uuid.UUID, status: str) -> None:
    scan = session.get(Scan, scan_id)
    assert scan is not None
    scan.status = status
    session.commit()


def test_containers_no_running_scan_needs_are_removed(sync_session: Session) -> None:
    now = time.time()
    running = _seed_running_source_scan()
    cancelled = _seed_running_source_scan()
    _set_status(sync_session, cancelled, "cancelled")
    gone = uuid.uuid4()
    docker = FakeDocker()
    docker.engine_list = [
        EngineContainer(id="keep", scan_hex=running.hex, deadline=int(now) + 600),
        EngineContainer(id="late", scan_hex=running.hex, deadline=int(now) - 1),
        EngineContainer(id="cancelled", scan_hex=cancelled.hex, deadline=int(now) + 600),
        EngineContainer(id="no-scan-row", scan_hex=gone.hex, deadline=int(now) + 600),
        EngineContainer(id="no-labels", scan_hex="", deadline=None),
        EngineContainer(id="bad-scan-label", scan_hex="not-a-uuid", deadline=int(now) + 600),
    ]

    counts = reap_engine_containers(docker, now=now)

    assert sorted(docker.removed) == sorted(
        ["late", "cancelled", "no-scan-row", "no-labels", "bad-scan-label"]
    )
    assert counts == {"seen": 6, "removed": 5}


def test_a_listing_failure_removes_nothing() -> None:
    from integrations.bomlens.runner import BomLensEngineError

    class Broken(FakeDocker):
        def engine_containers(self) -> list[EngineContainer]:  # type: ignore[override]
            raise BomLensEngineError("cannot list")

    docker = Broken()
    assert reap_engine_containers(docker) == {"seen": 0, "removed": 0}
    assert docker.removed == []
