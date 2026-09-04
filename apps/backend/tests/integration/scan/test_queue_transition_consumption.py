# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4, S3 row): transition
consumption.

Lifecycle sequence (repo hardening rule 5): a message published under the
pre-split single-queue name -> a worker configured for the split's default
(subscribed to both the new scan queue and the old/shared default queue) ->
that message still gets consumed to completion. A single-action test ("the
worker's `-Q` argument lists both queue names") would not catch the actual
regression this unit guards against: a rolling upgrade during which the new
``task_routes`` sends *newly dispatched* scan tasks to ``trustedoss.scan``
while a message published *before* the routing existed is still sitting on
``trustedoss.default`` (the exact name ``task_default_queue`` already used
pre-split, per ``tests/contracts/queue-names.json``'s own comment). If the
transition-default worker only listened to ``trustedoss.scan``, that old
message would sit forever.

This spawns a REAL ``celery -A tasks.celery_app worker`` subprocess consuming
BOTH queue names (mirroring
``worker.transitionSubscribeBothQueues: true`` / Compose's default
``-Q trustedoss.scan,trustedoss.default``, devops-owned, see
``charts/trustedoss/values.yaml`` and ``docker-compose.yml``) and force-
publishes a scan task directly onto ``trustedoss.default`` via
``apply_async(queue=...)``, bypassing ``task_routes`` entirely to fabricate
exactly the "leftover pre-split message" scenario. It reuses M1's load-test
delay mode so the fabricated scan finishes in well under a second without a
real cdxgen/scancode/Trivy toolchain, the same way
``test_scan_source_worker_shutdown_grace.py`` (S4) and
``test_scan_source_load_test_delay.py`` (M1) do for their own assertions.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import Scan
from tests._db_required import migrate_to_head
from tests._helpers import make_membership, make_organization, make_project, make_team, make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration

# Fabricated "scan" duration for the load-test delay mode. Short enough that
# this test is not the slowest thing in the suite, long enough that "the scan
# reached running" and "the scan reached succeeded" are two distinguishable
# polls rather than a single instantaneous transition.
_DELAY_SECONDS = 0.3

_WORKER_READY_TIMEOUT_SECONDS = 60.0

# Matches tests/contracts/queue-names.json (the shared oracle both this test
# and tasks/celery_app.py's task_routes are held to; see
# tests/unit/tasks/test_queue_routing_contract.py for the vocabulary check).
_SCAN_QUEUE = "trustedoss.scan"
_DEFAULT_QUEUE = "trustedoss.default"


def _require_redis_url() -> str:
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set: skip queue transition-consumption integration")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def sync_session() -> Iterator[Session]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_queued_scan() -> uuid.UUID:
    """Seed a project + queued source scan, sync-visible (mirrors the sibling
    load-test-delay / worker-shutdown-grace integration tests' helper)."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
            scan = Scan(
                project_id=project.id,
                kind="source",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _poll_scan_status(
    session: Session, scan_id: uuid.UUID, *, want: set[str], timeout: float
) -> str:
    deadline = time.monotonic() + timeout
    status = "queued"
    while time.monotonic() < deadline:
        session.expire_all()
        scan = session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
        status = scan.status
        if status in want:
            return status
        time.sleep(0.2)
    return status


def test_transition_worker_drains_a_message_left_on_the_old_default_queue(
    sync_session: Session,
) -> None:
    """Lifecycle: message published on ``trustedoss.default`` (bypassing
    ``task_routes``, exactly what a message enqueued by a pre-split backend
    process would look like) -> transition-default worker subscribed to BOTH
    queues -> the scan reaches ``running`` and then ``succeeded``.

    The regression this guards against is silent: a worker started with only
    ``-Q trustedoss.scan`` after the split would leave that message sitting
    on the broker forever, and a test that only checks the `-Q` argument
    string (or that `task_routes` maps the four scan task names) would stay
    green while that happened, because neither assertion touches an actual
    broker queue.
    """
    redis_url = _require_redis_url()
    scan_id = _seed_queued_scan()

    env = {
        **os.environ,
        "DATABASE_URL": os.environ["DATABASE_URL"],
        "REDIS_URL": redis_url,
        "APP_ENV": "dev",
        "SCAN_LOAD_TEST_DELAY_ENABLED": "true",
        "SCAN_LOAD_TEST_DELAY_SECONDS": str(_DELAY_SECONDS),
        "PYTHONUNBUFFERED": "1",
    }

    proc = subprocess.Popen(
        [
            "celery",
            "-A",
            "tasks.celery_app",
            "worker",
            "--loglevel=info",
            "--concurrency=1",
            "--pool=prefork",
            "--without-mingle",
            "--without-gossip",
            # Transition default (worker.transitionSubscribeBothQueues: true /
            # Compose's default WORKER_*_QUEUES): subscribes to both the new
            # scan queue and the pre-split single queue by its original name.
            "-Q",
            f"{_SCAN_QUEUE},{_DEFAULT_QUEUE}",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        from tasks.scan_source import scan_source_task

        # Publish directly onto the OLD queue name, bypassing task_routes
        # entirely: this is what a message already sitting on the broker
        # before the split shipped looks like, not a fresh dispatch.
        scan_source_task.apply_async(args=[str(scan_id)], queue=_DEFAULT_QUEUE)

        running_status = _poll_scan_status(
            sync_session,
            scan_id,
            want={"running", "succeeded", "failed"},
            timeout=_WORKER_READY_TIMEOUT_SECONDS,
        )
        if running_status not in {"running", "succeeded"}:
            # End the worker before reading it. ``read()`` waits for EOF, EOF
            # arrives when the child closes its pipe, and the child closes it
            # when it exits -- which the ``finally`` below only arranges once
            # this line has returned. So the branch written to report a failure
            # waited forever instead, and the failure never reached anybody:
            # the run burned its whole time budget with no message. Terminating
            # first keeps the worker log, which is the only account of why the
            # message was never drained.
            proc.terminate()
            try:
                worker_output, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                worker_output, _ = proc.communicate(timeout=10)
            pytest.fail(
                f"scan never left {running_status!r} within "
                f"{_WORKER_READY_TIMEOUT_SECONDS}s: the transition worker likely "
                f"did not drain trustedoss.default:\n{worker_output}"
            )

        final_status = _poll_scan_status(
            sync_session,
            scan_id,
            want={"succeeded", "failed"},
            timeout=_WORKER_READY_TIMEOUT_SECONDS,
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    assert final_status == "succeeded", (
        f"scan ended in status={final_status!r} after being published on the old "
        f"{_DEFAULT_QUEUE!r} queue to a worker subscribed to both queues "
        "(transition default): the S3 regression contract "
        "(concurrency-scaling-plan-2026-08-22.md §4, S3 row) is that a message "
        "left on the pre-split queue is still consumed during the transition"
    )

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.started_at is not None
    assert scan.completed_at is not None
