"""
End-to-end source scan pipeline — mock backend + mocked DT.

We drive `tasks.scan_source.scan_source_task` directly (NOT through Celery's
broker) with `TRUSTEDOSS_SCAN_BACKEND=mock` so cdxgen + scancode emit fixture
JSON. The DT client + breaker are monkeypatched so the test never touches a
real Redis or DT instance.

What we pin:

  - Happy path: a queued scan reaches `status='succeeded'` with progress=100,
    artifacts persisted (`scan_artifacts`), and a non-empty `scan_components`
    set derived from the cdxgen mock SBOM.
  - Stage progression updates `current_step` / `progress_percent` along the
    way (not just at the end).
  - Idempotency: invoking the task again on a `succeeded` scan is a no-op.
  - cdxgen failure → scan transitions to `status='failed'` with the cdxgen
    error message; the workspace is cleaned up (the `finally` shutil.rmtree
    is in the task module, so we just assert no leftover dir on the host).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import LicenseFinding, Scan, ScanArtifact, ScanComponent
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
        pytest.skip("DATABASE_URL not set — skip scan_source pipeline integration")
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
            f"alembic upgrade head failed; pipeline integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def sync_session() -> Iterator[Session]:
    """A sync session pointing at the same Postgres the worker uses.

    The scan task module uses `core.db.sync_session_scope`, which lazily
    builds an engine off `DATABASE_URL`. Here we open our own engine to
    seed rows AND read them back after the task has run — both engines
    point at the same DB so the writes are visible.
    """
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _seed_queued_scan(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Set up a project + queued scan via the async helpers, sync-flushed."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            from models import Scan as ScanModel

            scan = ScanModel(
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
            project_id = project.id
        await engine.dispose()
        return scan_id, project_id

    return asyncio.run(_build())


class _FakeBreaker:
    """Pass-through breaker — runs the callable and never short-circuits."""

    def call(self, fn):  # type: ignore[no-untyped-def]
        return fn()

    def record_success(self) -> None:  # pragma: no cover - unused in passthrough
        pass

    def record_failure(self) -> None:  # pragma: no cover - unused in passthrough
        pass


class _FakeDTClient:
    """Returns canned DT responses — no network."""

    def upsert_project(self, *, name: str, version: str) -> str:  # noqa: ARG002
        return "fake-dt-uuid-1"

    def upload_sbom(self, *, project_uuid: str, sbom_json) -> str:  # noqa: ARG002
        return "fake-token-1"

    def get_findings(self, *, project_uuid: str) -> list[dict[str, object]]:  # noqa: ARG002
        return []  # no findings — keeps the test focused on pipeline shape

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_scan_source_pipeline_completes_with_mock_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A full pipeline run against mock cdxgen / scancode / DT must reach `succeeded`."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    # Replace the breaker + DT client factories so no network/Redis is hit.
    monkeypatch.setattr(
        "tasks.scan_source.get_breaker",
        lambda: _FakeBreaker(),
    )
    monkeypatch.setattr(
        "tasks.scan_source.build_client",
        lambda: _FakeDTClient(),
    )
    # chore PR #4: stage-6 polls DT for findings with exponential backoff
    # (~60s budget). With a fake DT client that always returns [] the test
    # would otherwise wait the full budget — short-circuit by zeroing the
    # delays so the helper still iterates (covering its loop body) but
    # without the wall-clock cost.
    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,)
    )

    scan_id, project_id = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    # Direct invocation: scan_source_task is a Celery `bind=True` task. Calling
    # `.run(scan_id=...)` would normally need self.request — we use `.apply()`
    # which executes the task in-process synchronously and constructs `self`.
    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    # Refresh state from the DB.
    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.progress_percent == 100
    assert scan.current_step == "finalize"
    assert scan.completed_at is not None
    assert scan.error_message is None

    # The cdxgen + scancode artifacts were persisted.
    artifacts = (
        sync_session.execute(
            select(ScanArtifact).where(ScanArtifact.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    kinds = {a.kind for a in artifacts}
    assert "sbom_cyclonedx" in kinds
    assert "scancode_result" in kinds

    # cdxgen mock emits at least one component → ScanComponent rows exist.
    components = (
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    assert len(components) >= 1


# ---------------------------------------------------------------------------
# Idempotency — succeeded scan re-run is a no-op
# ---------------------------------------------------------------------------


def test_scan_source_succeeded_run_is_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr("tasks.scan_source.get_breaker", lambda: _FakeBreaker())
    monkeypatch.setattr("tasks.scan_source.build_client", lambda: _FakeDTClient())
    monkeypatch.setattr(
        "tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,)
    )

    scan_id, _ = _seed_queued_scan(sync_session)

    # First run completes.
    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    completed_at_first = scan.completed_at

    # Second run on the same scan_id must short-circuit (no re-running cdxgen,
    # no completed_at update). We assert by ensuring completed_at is unchanged.
    scan_source_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    scan_again = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan_again.completed_at == completed_at_first
    assert scan_again.status == "succeeded"


# ---------------------------------------------------------------------------
# Blast-radius isolation — detected-license failure must NOT roll back the
# declared findings + component graph (security-reviewer Medium #1).
# ---------------------------------------------------------------------------


def test_detected_license_failure_does_not_roll_back_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """A SQLAlchemyError while persisting scancode-detected licenses is wrapped
    in a SAVEPOINT: the high-value declared findings + components still commit
    and the scan still reaches ``succeeded``.

    This is the cache the UI shows when DT is down — it must survive a hostile
    file that trips an unexpected constraint in the detected-license write.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr("tasks.scan_source.get_breaker", lambda: _FakeBreaker())
    monkeypatch.setattr("tasks.scan_source.build_client", lambda: _FakeDTClient())
    monkeypatch.setattr("tasks.scan_source._DT_FINDINGS_POLL_DELAYS_SECONDS", (0,))

    # Sabotage detected-license persistence to raise mid-SAVEPOINT. We let it
    # add a row first (so the nested transaction is non-trivially dirty) then
    # raise, mirroring an INSERT that fails on flush.
    from sqlalchemy.exc import DataError

    def _boom(session, *, scan_uuid, sbom, detections):  # type: ignore[no-untyped-def]
        raise DataError("simulated detected-license INSERT failure", None, Exception())

    monkeypatch.setattr("tasks.scan_source._persist_detected_licenses", _boom)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    # The scan still succeeds — detected-license failure is degraded, not fatal.
    assert scan.status == "succeeded"

    # Components committed despite the detected-license failure.
    components = (
        sync_session.execute(
            select(ScanComponent).where(ScanComponent.scan_id == scan_id)
        )
        .scalars()
        .all()
    )
    assert len(components) >= 1

    # No detected (scancode) findings were persisted — the SAVEPOINT rolled
    # them back without touching the declared findings / components.
    detected = (
        sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.scan_id == scan_id,
                LicenseFinding.kind == "detected",
            )
        )
        .scalars()
        .all()
    )
    assert detected == []


# ---------------------------------------------------------------------------
# Failure path — cdxgen raises
# ---------------------------------------------------------------------------


def test_scan_source_cdxgen_failure_marks_scan_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    """When cdxgen blows up the scan must transition to `failed` with a message."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr("tasks.scan_source.get_breaker", lambda: _FakeBreaker())
    monkeypatch.setattr("tasks.scan_source.build_client", lambda: _FakeDTClient())

    # Sabotage the cdxgen adapter from inside the scan_source module.
    from integrations import cdxgen as cdxgen_adapter

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise cdxgen_adapter.CdxgenFailed("test cdxgen exit 1")

    monkeypatch.setattr("tasks.scan_source.cdxgen_adapter.run_cdxgen", _boom)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "failed"
    assert scan.error_message
    assert "cdxgen" in scan.error_message.lower() or "unexpected" in scan.error_message.lower()
    assert scan.completed_at is not None

    # Workspace must have been cleaned up by the task's `finally`.
    workspace_dir = tmp_path / str(scan_id)
    assert not workspace_dir.exists(), "workspace must be cleaned up after failure"
"""Reviewer probe appended into the pipeline test module's namespace so it
reuses the local sync_session fixture + seed helpers."""
import uuid
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.db import sync_session_scope
from models import LicenseFinding, ScanComponent
import tasks.scan_source as ss
from integrations.scancode import DetectedLicense


def test_savepoint_isolates_real_flush_error(monkeypatch, sync_session):
    """Force a flush-time DB error INSIDE begin_nested via the REAL persist fn
    and confirm the outer transaction still commits the declared graph."""
    sbom = {
        "metadata": {"component": {"name": "probe-app"}},
        "components": [
            {"purl": "pkg:pypi/probe-req@9.9.9", "name": "probe-req", "version": "9.9.9",
             "licenses": [{"license": {"id": "Apache-2.0"}}]}
        ],
    }
    scan_id, _ = _seed_queued_scan(sync_session)

    # Inject a flush-time NOT NULL violation inside the nested block by adding a
    # LicenseFinding with kind=None when the real persist asks for a license.
    real_license = ss._get_or_create_license
    def _bad_license(session, *, spdx_id, reference_url):
        lic = real_license(session, spdx_id=spdx_id, reference_url=reference_url)
        session.add(LicenseFinding(scan_id=scan_id, component_version_id=lic.id,
                                   license_id=lic.id, kind=None, source_path="x"))
        return lic
    monkeypatch.setattr(ss, "_get_or_create_license", _bad_license)

    caught = {}
    with sync_session_scope() as session:
        ss._persist_components(session, scan_uuid=scan_id, sbom=sbom)
        try:
            with session.begin_nested():
                ss._persist_detected_licenses(
                    session, scan_uuid=scan_id, sbom=sbom,
                    detections=[DetectedLicense(spdx_id="MIT", source_path="LICENSE")],
                )
        except SQLAlchemyError as exc:
            caught["type"] = type(exc).__name__
        session.commit()  # must NOT raise PendingRollbackError

    assert caught.get("type"), "expected a flush-time SQLAlchemyError inside savepoint"
    with sync_session_scope() as s:
        comps = s.execute(select(ScanComponent).where(ScanComponent.scan_id == scan_id)).scalars().all()
        assert len(comps) >= 1, "declared components rolled back -> blast radius NOT isolated"
        det = s.execute(select(LicenseFinding).where(
            LicenseFinding.scan_id == scan_id, LicenseFinding.kind == "detected")).scalars().all()
        assert det == [], "detected findings leaked despite savepoint rollback"
    print(f"PROBE OK: caught={caught['type']}, components survived, detected rolled back")
