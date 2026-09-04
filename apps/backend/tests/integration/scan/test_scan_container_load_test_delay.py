# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
M1/M2 (concurrency-scaling plan) load-test delay wiring, ``scan_container_task``, end-to-end.

Mirrors ``tests/integration/scan/test_scan_source_load_test_delay.py`` for
the container pipeline against the real Postgres the worker uses. See that
file's module docstring for the full rationale.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Scan, ScanArtifact, ScanComponent
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _seed_queued_container_scan() -> uuid.UUID:
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from tests._helpers import (
        make_membership,
        make_organization,
        make_project,
        make_team,
        make_user,
    )

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            scan = Scan(
                project_id=project.id,
                kind="container",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                # required by the real pipeline's early validation, even
                # though the delay path never reads it; kept realistic so a
                # regression that fell through to the real pipeline would hit
                # an actual (mocked) Trivy call instead of failing for an
                # unrelated missing-field reason.
                scan_metadata={"image_ref": "alpine:3.19"},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _stub_trivy_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.trivy import TrivyResult

    def _fake_run(
        *,
        image_ref: str,  # noqa: ARG001
        output_dir: Path,
        **_kwargs: object,
    ) -> TrivyResult:
        import json

        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-image.json"
        report = {"SchemaVersion": 2, "ArtifactType": "container_image", "Results": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_container.trivy_adapter.run_trivy_image", _fake_run)


# ---------------------------------------------------------------------------
# Regression: disabled (default), real pipeline untouched
# ---------------------------------------------------------------------------


def test_delay_disabled_leaves_the_real_pipeline_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_ENABLED", raising=False)
    monkeypatch.delenv("SCAN_LOAD_TEST_DELAY_SECONDS", raising=False)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_image(monkeypatch)

    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import scan_container_task

    result = scan_container_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    artifacts = (
        sync_session.execute(select(ScanArtifact).where(ScanArtifact.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert "trivy_json" in {
        a.kind for a in artifacts
    }, "the real pipeline must have produced a Trivy report artifact"


# ---------------------------------------------------------------------------
# Activated: dev + enabled, actually sleeps, marks succeeded, no output
# ---------------------------------------------------------------------------


def test_delay_enabled_in_dev_sleeps_the_requested_duration_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "0.3")

    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import scan_container_task

    wall_start = time.monotonic()
    result = scan_container_task.apply(args=[str(scan_id)])
    wall_elapsed = time.monotonic() - wall_start
    assert result.successful(), f"task failed: {result.traceback}"
    assert wall_elapsed >= 0.3

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100
    assert scan.current_step == "finalize"
    assert scan.error_message is None
    assert scan.started_at is not None
    assert scan.completed_at is not None
    db_elapsed = (scan.completed_at - scan.started_at).total_seconds()
    assert db_elapsed >= 0.25

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
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SCAN_LOAD_TEST_DELAY_SECONDS", "99")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy_image(monkeypatch)

    scan_id = _seed_queued_container_scan()

    from tasks.scan_container import scan_container_task

    wall_start = time.monotonic()
    result = scan_container_task.apply(args=[str(scan_id)])
    wall_elapsed = time.monotonic() - wall_start
    assert result.successful(), f"task failed: {result.traceback}"
    assert wall_elapsed < 30

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    artifacts = (
        sync_session.execute(select(ScanArtifact).where(ScanArtifact.scan_id == scan_id))
        .scalars()
        .all()
    )
    assert "trivy_json" in {a.kind for a in artifacts}
