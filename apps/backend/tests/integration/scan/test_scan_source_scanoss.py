"""
SCANOSS vendored-OSS pipeline integration — enabled vs disabled (Phase J / P3-11).

Drives ``tasks.scan_source.scan_source_task`` directly (mock cdxgen / scancode +
stub Trivy, same harness as ``test_scan_source_pipeline_mock``) and pins the two
paths that matter:

  - ENABLED (``SCANOSS_ENABLED=true``): the ``scanoss`` stage runs; each returned
    VendoredComponent is persisted as a ``ScanComponent`` carrying
    ``raw_data.source == "scanoss"`` plus ``detected`` ``LicenseFinding`` rows.
    We stub ``run_scanoss`` so no subprocess / egress happens.
  - DISABLED (default): the pipeline never even CALLS ``run_scanoss`` (the
    ``if scanoss_enabled()`` gate short-circuits), so zero scanoss components are
    added. We prove "never called" with a sentinel that fails the test if hit —
    this is the pipeline-level guarantee of no egress on a default deployment.
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

from models import LicenseFinding, Scan, ScanComponent
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
        pytest.skip("DATABASE_URL not set — skip scanoss pipeline integration")
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
            f"alembic upgrade head failed; scanoss integration cannot run\n"
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


def _seed_queued_scan(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team, git_url=None)
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


def _stub_trivy_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from integrations.trivy import TrivyResult

    def _fake_run(
        sbom_path: Path,
        output_dir: Path,
        **_kwargs: object,
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report = {
            "SchemaVersion": 2,
            "ArtifactName": str(sbom_path),
            "ArtifactType": "cyclonedx",
            "Results": [],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake_run)


def _stub_scanoss_vendored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``run_scanoss`` with a stub returning two full-file matches — no
    subprocess, no egress. One purl collides with a cdxgen component to exercise
    the de-dup path; the other is a genuinely vendored component."""
    from integrations.scanoss import ScanossResult, VendoredComponent

    def _fake(
        *,
        source_dir: Path,  # noqa: ARG001
        output_dir: Path,
        **_kwargs: object,
    ) -> ScanossResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report = output_dir / "scanoss.json"
        report.write_text("{}", encoding="utf-8")
        return ScanossResult(
            vendored=[
                VendoredComponent(
                    purl="pkg:github/kgabis/parson@1.5.2",
                    name="parson",
                    version="1.5.2",
                    licenses=["MIT"],
                ),
                VendoredComponent(
                    purl="pkg:github/madler/zlib@1.3.1",
                    name="zlib",
                    version="1.3.1",
                    licenses=["Zlib"],
                ),
            ],
            result_path=report,
        )

    monkeypatch.setattr("tasks.scan_source.scanoss_adapter.run_scanoss", _fake)


# ---------------------------------------------------------------------------
# ENABLED — vendored components persist with source=scanoss + detected licenses
# ---------------------------------------------------------------------------


def test_scanoss_enabled_persists_vendored_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SCANOSS_ENABLED", "true")
    _stub_trivy_empty(monkeypatch)
    _stub_scanoss_vendored(monkeypatch)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    # ScanComponents tagged with raw_data.source == "scanoss".
    scanoss_components = (
        sync_session.execute(
            select(ScanComponent).where(
                ScanComponent.scan_id == scan_id,
                ScanComponent.raw_data["source"].astext == "scanoss",
            )
        )
        .scalars()
        .all()
    )
    assert len(scanoss_components) == 2, (
        f"expected 2 scanoss-sourced components; got {len(scanoss_components)}"
    )
    for sc in scanoss_components:
        assert sc.direct is False

    # Each vendored component carries a detected LicenseFinding sourced scanoss.
    scanoss_cv_ids = {sc.component_version_id for sc in scanoss_components}
    detected = (
        sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.scan_id == scan_id,
                LicenseFinding.kind == "detected",
                LicenseFinding.component_version_id.in_(scanoss_cv_ids),
            )
        )
        .scalars()
        .all()
    )
    assert len(detected) == 2
    assert all(
        isinstance(f.raw_data, dict) and f.raw_data.get("source") == "scanoss"
        for f in detected
    )


# ---------------------------------------------------------------------------
# DISABLED — stage skipped, run_scanoss never called, zero scanoss components
# ---------------------------------------------------------------------------


def test_scanoss_disabled_skips_stage_and_never_calls_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sync_session: Session,
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.delenv("SCANOSS_ENABLED", raising=False)  # default OFF
    _stub_trivy_empty(monkeypatch)

    # Sentinel: if the pipeline calls the adapter at all on a disabled
    # deployment, the test fails — this is the "no egress" guarantee.
    def _must_not_run(*_a: object, **_k: object) -> object:  # pragma: no cover
        raise AssertionError("run_scanoss must not be called when disabled")

    monkeypatch.setattr("tasks.scan_source.scanoss_adapter.run_scanoss", _must_not_run)

    scan_id, _ = _seed_queued_scan(sync_session)

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    scanoss_components = (
        sync_session.execute(
            select(ScanComponent).where(
                ScanComponent.scan_id == scan_id,
                ScanComponent.raw_data["source"].astext == "scanoss",
            )
        )
        .scalars()
        .all()
    )
    assert scanoss_components == []
