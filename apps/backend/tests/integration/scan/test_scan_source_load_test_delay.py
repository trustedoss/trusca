# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
M1/M2 (concurrency-scaling plan) load-test delay wiring, end-to-end.

``tests/unit/tasks/test_scan_source_load_test_delay.py`` pins the dispatch
logic against a fake session. This file drives ``scan_source_task`` against
the real Postgres the worker uses, so the fields M1's measurement script
(``tests/load/scan_queue_wait.py``) reads back (``started_at``,
``completed_at``, ``status``, ``progress_percent``) are asserted against
actual committed rows, not mocked writer calls.
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

from models import Scan, ScanArtifact, ScanComponent
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip scan_source load-test-delay integration")
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
            f"alembic upgrade head failed; load-test-delay integration cannot run\n"
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
    """Seed a project + queued source scan via the async helpers, sync-visible."""
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
            # git_url=None: the delay path never calls _fetch_source, so there
            # is nothing to clone either way; kept None so a regression that
            # accidentally fell through to the real pipeline would fail loudly
            # (no git binary / no real repo to clone in the test image) rather
            # than silently succeeding for an unrelated reason.
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


# ---------------------------------------------------------------------------
# Regression: disabled (default), real pipeline untouched
# ---------------------------------------------------------------------------


def test_delay_disabled_leaves_the_real_pipeline_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """No load-test env set ⇒ the scan still runs the full mock cdxgen /
    scancode / Trivy chain and produces real artifacts + components; the
    M1 wiring must not divert a normal scan."""
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_ENABLED", raising=False)
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_SECONDS", raising=False)
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    import json

    from integrations.trivy import TrivyResult

    def _fake_run_trivy(
        sbom_path: Path,  # noqa: ARG001
        output_dir: Path,
        *,
        timeout_seconds: int = 0,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report = {"SchemaVersion": 2, "ArtifactType": "cyclonedx", "Results": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake_run_trivy)

    scan_id = _seed_queued_scan()

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100

    artifacts = (
        sync_session.execute(select(ScanArtifact).where(ScanArtifact.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert "sbom_cyclonedx" in {
        a.kind for a in artifacts
    }, "the real pipeline must have produced an SBOM artifact"
    components = (
        sync_session.execute(select(ScanComponent).where(ScanComponent.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert len(components) >= 1, "the mock cdxgen SBOM must have persisted components"


# ---------------------------------------------------------------------------
# Activated: dev + enabled, actually sleeps, marks succeeded, no output
# ---------------------------------------------------------------------------


def test_delay_enabled_in_dev_sleeps_the_requested_duration_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    """This is the case tests/load/scan_queue_wait.py needs: a scan that
    holds its worker slot busy for a measurable, fixed duration and then
    reports success, without a git binary, cdxgen, scancode or Trivy ever
    running (WORKSPACE_HOST_PATH is deliberately left unset / whatever the
    ambient default is; the delay path never touches it)."""
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "0.3")

    scan_id = _seed_queued_scan()

    from tasks.scan_source import scan_source_task

    wall_start = time.monotonic()
    result = scan_source_task.apply(args=[str(scan_id)])
    wall_elapsed = time.monotonic() - wall_start
    assert result.successful(), f"task failed: {result.traceback}"
    assert (
        wall_elapsed >= 0.3
    ), f"task returned after only {wall_elapsed:.3f}s: the configured delay was not honoured"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100
    assert scan.current_step == "finalize"
    assert scan.error_message is None
    assert scan.started_at is not None
    assert scan.completed_at is not None
    assert scan.completed_at >= scan.started_at
    # The DB-recorded gap between started_at and completed_at must reflect
    # the actual sleep, not just the wall-clock call above; this is the
    # exact pair tests/load/scan_queue_wait.py measures.
    db_elapsed = (scan.completed_at - scan.started_at).total_seconds()
    assert (
        db_elapsed >= 0.25
    ), f"started_at→completed_at gap was only {db_elapsed:.3f}s, expected >= ~0.3s"

    # No real toolchain ran: no SBOM/scancode/Trivy artifacts, no components.
    artifacts = (
        sync_session.execute(select(ScanArtifact).where(ScanArtifact.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert artifacts == [], "load-test delay mode must not produce any scan artifact"
    components = (
        sync_session.execute(select(ScanComponent).where(ScanComponent.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert components == [], "load-test delay mode must not produce any component"


# ---------------------------------------------------------------------------
# Safety: enabled but outside dev, refused; real pipeline still runs
# ---------------------------------------------------------------------------


def test_delay_enabled_outside_dev_is_refused_and_real_pipeline_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """The safety gate lives in ``scan_load_test_delay_seconds()`` itself, but
    this pins that the TASK actually honours a 0.0 return by running the real
    pipeline: a large configured delay (99s) must NOT be slept in prod."""
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "99")
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    import json

    from integrations.trivy import TrivyResult

    def _fake_run_trivy(
        sbom_path: Path,  # noqa: ARG001
        output_dir: Path,
        *,
        timeout_seconds: int = 0,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report = {"SchemaVersion": 2, "ArtifactType": "cyclonedx", "Results": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake_run_trivy)

    scan_id = _seed_queued_scan()

    from tasks.scan_source import scan_source_task

    wall_start = time.monotonic()
    result = scan_source_task.apply(args=[str(scan_id)])
    wall_elapsed = time.monotonic() - wall_start
    assert result.successful(), f"task failed: {result.traceback}"
    # A real (mock-backend) pipeline run finishes in well under a second;
    # a mistakenly-honoured 99s delay would fail this bound immediately.
    assert (
        wall_elapsed < 30
    ), f"task took {wall_elapsed:.1f}s: looks like the 99s prod delay was honoured"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    artifacts = (
        sync_session.execute(select(ScanArtifact).where(ScanArtifact.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert "sbom_cyclonedx" in {
        a.kind for a in artifacts
    }, "prod must always run the real pipeline, never the fabricated-success delay path"
