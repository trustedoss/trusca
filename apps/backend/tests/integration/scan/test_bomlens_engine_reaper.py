"""The engine-container reaper against real scan rows (security review on #522, M1, N-M1).

The container list is a test double; which scans exist and run comes from the
real database, so the reaper's query is what decides.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from integrations.bomlens.runner import DockerCli, EngineContainer
from models import Scan
from tasks.bomlens_engine_reaper import reap_engine_containers
from tests._bomlens_engine import FakeDocker
from tests.integration.scan.test_bomlens_result import _seed_running_source_scan

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    from tests._db_required import migrate_to_head

    migrate_to_head()


@pytest.fixture(autouse=True)
def _instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSCA_INSTANCE_ID", "this-one")


def _set_status(session: Session, scan_id: uuid.UUID, status: str) -> None:
    scan = session.get(Scan, scan_id)
    assert scan is not None
    scan.status = status
    session.commit()


def _c(cid: str, scan_hex: str, deadline: int | None, instance: str = "this-one") -> Any:
    return EngineContainer(id=cid, scan_hex=scan_hex, deadline=deadline, instance=instance)


def test_containers_no_running_scan_needs_are_removed(sync_session: Session) -> None:
    now = time.time()
    soon, past = int(now) + 600, int(now) - 1
    running = _seed_running_source_scan()
    cancelled = _seed_running_source_scan()
    _set_status(sync_session, cancelled, "cancelled")
    unknown = uuid.uuid4()
    docker = FakeDocker()
    docker.engine_list = [
        _c("keep", running.hex, soon),
        _c("late", running.hex, past),
        _c("cancelled", cancelled.hex, soon),
        # A scan this database does not know: kept until its deadline ...
        _c("unknown-scan", unknown.hex, soon),
        # ... and removed after it.
        _c("unknown-scan-late", unknown.hex, past),
        _c("no-labels", "", None),
        _c("bad-scan-label", "not-a-uuid", past),
    ]

    counts = reap_engine_containers(docker, now=now)

    assert sorted(docker.removed) == sorted(
        ["late", "cancelled", "unknown-scan-late", "no-labels", "bad-scan-label"]
    )
    assert counts == {"seen": 7, "removed": 5}


def test_another_installations_containers_are_never_touched(sync_session: Session) -> None:
    """Even when the listing returns them, and even past their deadline."""
    now = time.time()
    cancelled = _seed_running_source_scan()
    _set_status(sync_session, cancelled, "cancelled")
    docker = FakeDocker()
    docker.engine_list = [
        _c("theirs-running", uuid.uuid4().hex, int(now) + 600, instance="other"),
        _c("theirs-late", uuid.uuid4().hex, int(now) - 1, instance="other"),
        _c("theirs-same-scan-id", cancelled.hex, int(now) + 600, instance="other"),
    ]

    assert reap_engine_containers(docker, now=now) == {"seen": 0, "removed": 0}
    assert docker.removed == []


def test_the_listing_asks_the_daemon_for_this_installation_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"abc\tdef\t123\tthis-one\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    listed = DockerCli().engine_containers()
    assert listed == [EngineContainer(id="abc", scan_hex="def", deadline=123, instance="this-one")]
    assert "label=trusca.instance=this-one" in seen[0]
    assert "label=trusca.role=bomlens-engine" in seen[0]


def test_a_listing_failure_removes_nothing() -> None:
    from integrations.bomlens.runner import BomLensEngineError

    class Broken(FakeDocker):
        def engine_containers(self) -> list[EngineContainer]:  # type: ignore[override]
            raise BomLensEngineError("cannot list")

    docker = Broken()
    assert reap_engine_containers(docker) == {"seen": 0, "removed": 0}
    assert docker.removed == []
