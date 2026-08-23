# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S4 (concurrency-scaling-plan-2026-08-22.md §3.2 / §4): worker shutdown grace.

Lifecycle sequence (repo hardening rule 5): scan starts -> worker process
receives SIGTERM -> scan row's final state. A single-action test ("SIGTERM
handler is installed") cannot catch the actual regression this unit guards
against: `task_acks_late=True` + `task_reject_on_worker_lost=True`
(``tasks/celery_app.py``) mean a worker that is SIGKILLed mid-scan does not
lose the message: it gets *redelivered* to another worker and starts over
from zero. On a long scan (up to the hard time limit,
``scan_hard_time_limit_seconds()``, default 3900s / 65 min) that is exactly
the throughput paradox S4 exists to avoid: an autoscaler scaling the worker
pool DOWN under a growing queue should never make the queue longer by
discarding in-flight progress.

This spawns a REAL ``celery -A tasks.celery_app worker`` subprocess (not
``.apply()`` in-process, which never exercises OS signal handling at all) and
sends it an actual ``SIGTERM``, mirroring exactly what a Kubernetes pod
deletion (or `docker-compose stop`) delivers to the container's PID 1. It
reuses M1's load-test delay mode (``SCAN_LOAD_TEST_DELAY_ENABLED`` /
``SCAN_LOAD_TEST_DELAY_SECONDS``, ``core.config.scan_load_test_delay_seconds``)
to fabricate a "long-running scan" without a real cdxgen/scancode/Trivy
toolchain, the same way
``tests/integration/scan/test_scan_source_load_test_delay.py`` does for its
own (in-process, no-signal) assertions.

What this test relies on, verified directly in ``tasks/celery_app.py`` /
Celery's own source rather than assumed:

- ``REMAP_SIGTERM`` is unset in this image (``apps/backend/Dockerfile.worker``
  never sets it), so Celery's default SIGTERM mapping applies: SIGTERM is a
  *warm* shutdown (stop consuming new tasks, let the currently-executing task
  run to completion), see ``celery/apps/worker.py``'s
  ``install_worker_term_handler`` branch. A *cold* shutdown (SIGQUIT, or a
  second SIGTERM) would instead terminate the in-flight task immediately,
  which is the failure mode this test asserts against (the "still alive 1s
  after SIGTERM" check below).
- The worker subprocess is started with the exec-form `celery` command
  directly as the process being signalled (matching
  ``Dockerfile.worker``'s ``CMD`` and ``deployment-worker-scan.yaml``'s
  ``command:``, no shell wrapper eating the signal).

S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4, S3 row) note: the worker
subprocess below is started with ``-Q trustedoss.scan,trustedoss.default``
(the deployments' own transition default,
``worker.transitionSubscribeBothQueues`` / Compose's ``WORKER_SCAN_QUEUES``),
not with no ``-Q`` at all as it was pre-split. A worker started with no
``-Q`` only consumes ``app.amqp.queues``, which is exactly
``task_default_queue`` unless ``task_queues`` is set explicitly. Celery does
NOT auto-subscribe a running worker to a queue that only appears in
``task_routes``. Since ``scan_source`` now routes to ``trustedoss.scan``
(``tasks/celery_app.py``'s ``task_routes``), a worker consuming only
``trustedoss.default`` would never see the dispatched message and this test
would hang until its own timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import Scan
from tests._helpers import make_membership, make_organization, make_project, make_team, make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration

# Fabricated "long scan" duration for the load-test delay mode. Long enough
# that a cold shutdown (worker exits within ~1s of SIGTERM) is unmistakably
# distinguishable from a warm one, short enough that the test does not become
# the slowest thing in the suite.
_DELAY_SECONDS = 4.0

# How long to wait for the worker subprocess to boot, consume the dispatched
# message, and flip the scan row to "running". Generous because the worker
# process imports every module in ``tasks.celery_app``'s ``_TASK_INCLUDES``
# (a much longer import chain than any single test module needs), which is
# slower on a cold CI runner than importing just ``tasks.scan_source``.
_WORKER_READY_TIMEOUT_SECONDS = 60.0


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip worker shutdown-grace integration")
    return url


def _require_redis_url() -> str:
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set: skip worker shutdown-grace integration")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    _require_redis_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; worker shutdown-grace integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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
    load-test-delay integration test's helper of the same shape)."""
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
    """Poll until the scan's status lands in ``want`` or ``timeout`` elapses.

    Returns the final observed status (which may not be in ``want`` if the
    timeout expired). The caller asserts on it so a timeout produces a clear
    failure message rather than a bare TimeoutError.
    """
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


def test_worker_sigterm_lets_the_in_flight_scan_finish_instead_of_losing_it(
    sync_session: Session,
) -> None:
    """Lifecycle: scan starts -> worker gets SIGTERM -> scan row's final state.

    A worker sized with S4's grace period (or, here, simply given enough wall
    time to demonstrate the mechanism) must let the already-running scan run
    to completion rather than abandoning it. The regression this guards
    against is silent: acks_late + reject_on_worker_lost means a SIGKILLed
    worker's scan does not error out loudly, it just quietly restarts on
    another worker, and a test that only checks "the worker has a SIGTERM
    handler" would stay green while that still happened.
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
            # S3: transition default (both worker kinds subscribe to both
            # queue names). See the module docstring's S3 note for why a
            # worker consuming only trustedoss.default would never see the
            # scan_source task task_routes now sends to trustedoss.scan.
            "-Q",
            "trustedoss.scan,trustedoss.default",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # Dispatch through the real broker (not `.apply()`, which never
        # leaves the calling process) so the worker subprocess is the one
        # that actually executes the task. A Redis-backed broker is durable
        # across the worker's own boot time, so publishing before the worker
        # finishes starting up is safe, it just waits in the queue.
        from tasks.scan_source import scan_source_task

        scan_source_task.apply_async(args=[str(scan_id)])

        running_status = _poll_scan_status(
            sync_session,
            scan_id,
            want={"running", "succeeded", "failed"},
            timeout=_WORKER_READY_TIMEOUT_SECONDS,
        )
        if running_status != "running" and proc.poll() is not None and proc.stdout:
            worker_output = proc.stdout.read()
        else:
            worker_output = "(worker still running, no output captured)"
        assert running_status == "running", (
            f"scan never reached 'running' within {_WORKER_READY_TIMEOUT_SECONDS}s "
            f"(last observed: {running_status!r}): worker subprocess likely failed "
            f"to boot or consume the task:\n{worker_output}"
        )

        sigterm_sent_at = time.monotonic()
        proc.send_signal(signal.SIGTERM)

        # Warm-vs-cold check: a cold shutdown (SIGQUIT, or a REMAP_SIGTERM
        # misconfiguration flipping SIGTERM to cold) kills the in-flight task
        # near-instantly. Give the process well under the fabricated scan
        # delay and assert it is still alive, proof SIGTERM triggered warm
        # shutdown, not cold.
        time.sleep(min(1.5, _DELAY_SECONDS / 2))
        assert proc.poll() is None, (
            "worker process exited within "
            f"{min(1.5, _DELAY_SECONDS / 2)}s of SIGTERM while the scan was still "
            "sleeping: this looks like a cold shutdown (in-flight task killed), "
            "not the warm shutdown S4's terminationGracePeriodSeconds relies on"
        )

        returncode = proc.wait(timeout=_DELAY_SECONDS + 30)
        elapsed_since_sigterm = time.monotonic() - sigterm_sent_at
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    output = proc.stdout.read() if proc.stdout else ""
    assert (
        returncode == 0
    ), f"worker did not exit cleanly after warm shutdown (exit code {returncode})\n{output}"
    assert elapsed_since_sigterm >= _DELAY_SECONDS - 0.5, (
        "worker process exited before the in-flight scan's configured delay "
        f"elapsed ({elapsed_since_sigterm:.1f}s since SIGTERM, expected >= "
        f"{_DELAY_SECONDS}s): the running task was cut short instead of "
        "completing"
    )

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded", (
        f"scan ended in status={scan.status!r} (error={scan.error_message!r}) "
        "after the worker received SIGTERM mid-scan: the regression contract "
        "(S4, concurrency-scaling-plan-2026-08-22.md §4) is that an in-flight "
        "scan on a SIGTERM'd worker completes, retries, or fails explicitly; it "
        "must never silently vanish"
    )
    assert scan.completed_at is not None
    assert scan.started_at is not None
    # Exactly one execution attempt: started_at is set once, not reset by a
    # requeue-and-restart. A worker that lost the task to SIGKILL instead of
    # completing it via warm shutdown would show this scan back in `queued`
    # (picked up again by nobody, since this test only runs one worker) rather
    # than `succeeded` with a single coherent started_at/completed_at pair.
    assert scan.completed_at >= scan.started_at
