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

from integrations.bomlens.engine import EngineContainer
from models import Scan
from tasks import bomlens_engine_reaper
from tasks.bomlens_engine_reaper import reap_engine_containers
from tests._bomlens_engine import FakeBackend
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


def _c(name: str, scan_id: str | None, deadline: int | None, running: bool = True) -> Any:
    return EngineContainer(
        name=name, scan_id=scan_id, component="engine-stage1", deadline=deadline, running=running
    )


def test_containers_no_running_scan_needs_are_removed(sync_session: Session) -> None:
    now = time.time()
    soon, past = int(now) + 600, int(now) - 1
    running = _seed_running_source_scan()
    cancelled = _seed_running_source_scan()
    _set_status(sync_session, cancelled, "cancelled")
    unknown = uuid.uuid4()
    backend = FakeBackend()
    backend.engine_list = [
        _c("keep", running.hex, soon),
        _c("late", running.hex, past),
        _c("cancelled", cancelled.hex, soon),
        # A scan this database does not know: kept until its deadline ...
        _c("unknown-scan", unknown.hex, soon),
        # ... and removed after it.
        _c("unknown-scan-late", unknown.hex, past),
        _c("no-labels", None, None),
        _c("no-deadline", running.hex, None),
        _c("bad-scan-label", "not-a-uuid", past),
    ]

    counts = reap_engine_containers(backend, now=now)

    assert sorted(backend.removed_engine) == sorted(
        ["late", "cancelled", "unknown-scan-late", "no-labels", "no-deadline", "bad-scan-label"]
    )
    assert counts == {"seen": 8, "removed": 6}


def test_a_container_the_backend_will_not_remove_is_not_counted(
    sync_session: Session,
) -> None:
    """The backend refuses a name outside its scope and says so."""
    cancelled = _seed_running_source_scan()
    _set_status(sync_session, cancelled, "cancelled")
    backend = FakeBackend()
    backend.engine_list = [_c("cancelled", cancelled.hex, int(time.time()) + 600)]
    backend.remove_engine_container = lambda name: False  # type: ignore[method-assign]

    assert reap_engine_containers(backend) == {"seen": 1, "removed": 0}


def test_the_listing_is_narrowed_to_this_installation() -> None:
    """The backend's own scope is the instance label too; asking for it by
    name keeps the reaper from ever seeing another installation's containers."""
    backend = FakeBackend()

    assert reap_engine_containers(backend) == {"seen": 0, "removed": 0}
    assert backend.listed_labels == [{"trusca.instance": "this-one"}]


def test_a_listing_failure_removes_nothing() -> None:
    class Broken(FakeBackend):
        def list_engine_containers(self, labels: Any = None) -> list[Any]:
            raise RuntimeError("cannot list")

    backend = Broken()
    assert reap_engine_containers(backend) == {"seen": 0, "removed": 0}
    assert backend.removed_engine == []


def test_containers_of_the_runner_before_the_package_are_removed_by_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        out = "abc\ndef\n" if argv[1] == "ps" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(bomlens_engine_reaper.subprocess, "run", fake_run)

    assert bomlens_engine_reaper.remove_legacy_containers() == 2

    listing, *removals = calls
    assert "label=trusca.role=bomlens-engine" in listing
    assert "label=trusca.instance=this-one" in listing
    assert [r[-1] for r in removals] == ["abc", "def"]
    assert all(r[:3] == ["docker", "rm", "--force"] for r in removals)


def test_a_daemon_that_cannot_be_asked_leaves_the_legacy_containers_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "cannot connect")

    monkeypatch.setattr(bomlens_engine_reaper.subprocess, "run", refused)
    assert bomlens_engine_reaper.remove_legacy_containers() == 0

    def missing(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(bomlens_engine_reaper.subprocess, "run", missing)
    assert bomlens_engine_reaper.remove_legacy_containers() == 0
