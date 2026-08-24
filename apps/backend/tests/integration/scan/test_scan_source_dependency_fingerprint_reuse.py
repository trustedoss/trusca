# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
S8 (concurrency-scaling-plan-2026-08-22.md §3.2): dependency-set-fingerprint
scan reuse, wired end to end.

``models.scan_fingerprint.compute_scan_fingerprint`` (schema + pure function,
db-designer) is unit-tested in isolation
(``tests/unit/test_scan_fingerprint.py``). What is still missing, and what
this file pins, is the WIRING inside ``tasks.scan_source``: does a scan
actually consult the prior succeeded scan's fingerprint, actually skip
cdxgen when it matches, actually re-run vulnerability matching regardless,
and actually fall back to the full pipeline when the scanner version (or the
manifest set) changed?

Drives ``tasks.scan_source.scan_source_task`` directly (NOT through Celery's
broker) against a real Postgres, exactly like
``tests/integration/scan/test_scan_source_pipeline_mock.py``. cdxgen runs in
``TRUSTEDOSS_SCAN_BACKEND=mock`` mode (a deterministic single-component SBOM,
see ``integrations.cdxgen._write_mock_sbom``); a counting wrapper around
``cdxgen_adapter.run_cdxgen`` is the oracle for "did cdxgen actually run".
``scan_inputs.collect_manifest_inventory`` is monkeypatched to a fixed
inventory so the fingerprint comparison is deterministic across scans without
needing a real git tree with real lockfiles.

Regression contract asserted here (plan §4 S8 row, hardening rule 5,
lifecycle sequence, not single-action):

  1. Same lockfile set, same scanner version, same scan-time config, twice on
     the same (project, ref) → the SECOND scan takes the reuse path (cdxgen
     is NOT re-invoked) and still runs Trivy matching.
  2. A scanner-version bump between the two scans → the fingerprint differs →
     the second scan re-runs the full pipeline (cdxgen IS re-invoked).
  3. The reused scan's own SBOM artifact lives inside ITS OWN workspace
     (never a path under the prior scan's already-reclaimed workspace), and
     the reused scan's own workspace is cleaned up in `finally` exactly like
     a full-pipeline scan's.
  4. The reuse path and the full path leave indistinguishable scan-row shape
     (``status='succeeded'``, ``progress_percent=100``,
     ``current_step='finalize'``, ``completed_at`` set): a client cannot
     tell which path a succeeded scan took from the row alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
        pytest.skip("DATABASE_URL not set, skip S8 reuse integration")
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
            f"alembic upgrade head failed; S8 reuse integration cannot run\n"
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


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_project() -> uuid.UUID:
    """One project, reused by every scan in a test (S8 compares WITHIN a
    project + ref, so every scan under test must share one)."""
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
            # git_url=None: same no-source placeholder path
            # test_scan_source_pipeline_mock.py uses. The mock cdxgen backend
            # emits its SBOM regardless of what _fetch_source produced.
            project = await make_project(s, team=team, git_url=None)
            project_id = project.id
        await engine.dispose()
        return project_id

    return asyncio.run(_build())


def _seed_queued_scan(
    session: Session, *, project_id: uuid.UUID, ref: str | None
) -> uuid.UUID:
    """A fresh queued Scan row for an EXISTING project (sync insert, the scan
    task itself only needs the row to exist, not the project/team graph)."""
    scan = Scan(
        project_id=project_id,
        kind="source",
        status="queued",
        progress_percent=0,
        scan_metadata={},
        ref=ref,
    )
    session.add(scan)
    session.commit()
    session.refresh(scan)
    return scan.id


# ---------------------------------------------------------------------------
# Determinism helpers: fixed manifest inventory, counted cdxgen, stub Trivy
# ---------------------------------------------------------------------------


def _fixed_manifest_inventory() -> dict[str, Any]:
    """The exact shape ``services.scan_inputs.collect_manifest_inventory``
    returns. Identical across every call in a test ⇒ the fingerprint's
    manifest input never varies on its own (only ``CDXGEN_VERSION`` /
    scan-config toggles are allowed to move the fingerprint in these tests.
    """
    return {
        "files": [
            {"path": "package-lock.json", "size": 512, "sha256": "b" * 64},
            {"path": "package.json", "size": 128, "sha256": "c" * 64},
        ],
        "count": 2,
        "truncated": False,
    }


def _pin_manifest_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.scan_inputs as scan_inputs_module

    monkeypatch.setattr(
        scan_inputs_module,
        "collect_manifest_inventory",
        lambda _project_root: _fixed_manifest_inventory(),
    )


def _count_cdxgen_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Wrap ``cdxgen_adapter.run_cdxgen`` to record each real invocation
    while still delegating to the mock backend, so a reused scan's call
    count stays at the PRIOR scan's total (proof cdxgen never ran again)."""
    from integrations import cdxgen as cdxgen_adapter

    calls: list[int] = []
    original = cdxgen_adapter.run_cdxgen

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr("tasks.scan_source.cdxgen_adapter.run_cdxgen", _wrapped)
    return calls


def _stub_trivy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Same empty-report stub as test_scan_source_pipeline_mock.py's
    ``_stub_trivy_empty``, plus a call counter. S8's accuracy contract is
    that Trivy matching reruns on EVERY scan, reused SBOM or not."""
    from integrations.trivy import TrivyResult

    calls: list[int] = []

    def _fake_run(
        sbom_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: int = 0,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> TrivyResult:
        calls.append(1)
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
    return calls


def _component_purls(session: Session, scan_id: uuid.UUID) -> set[str]:
    from models import Component, ComponentVersion

    rows = session.execute(
        select(Component.purl)
        .join(ComponentVersion, ComponentVersion.component_id == Component.id)
        .join(ScanComponent, ScanComponent.component_version_id == ComponentVersion.id)
        .where(ScanComponent.scan_id == scan_id)
    ).all()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# 1. Same fingerprint twice → second scan reuses, cdxgen not re-invoked
# ---------------------------------------------------------------------------


def test_second_scan_with_unchanged_fingerprint_reuses_prior_sbom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("CDXGEN_VERSION", "12.3.3")
    _pin_manifest_inventory(monkeypatch)
    cdxgen_calls = _count_cdxgen_calls(monkeypatch)
    trivy_calls = _stub_trivy(monkeypatch)

    project_id = _seed_project()

    from tasks.scan_source import scan_source_task

    scan1_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    result1 = scan_source_task.apply(args=[str(scan1_id)])
    assert result1.successful(), f"scan1 failed: {result1.traceback}"

    sync_session.expire_all()
    scan1 = sync_session.execute(select(Scan).where(Scan.id == scan1_id)).scalar_one()
    assert scan1.status == "succeeded"
    assert scan1.dependency_fingerprint is not None
    assert len(cdxgen_calls) == 1
    assert len(trivy_calls) == 1

    scan2_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    result2 = scan_source_task.apply(args=[str(scan2_id)])
    assert result2.successful(), f"scan2 failed: {result2.traceback}"

    sync_session.expire_all()
    scan2 = sync_session.execute(select(Scan).where(Scan.id == scan2_id)).scalar_one()
    assert scan2.status == "succeeded"
    # cdxgen must NOT have run a second time: the reuse path took over.
    assert len(cdxgen_calls) == 1, "cdxgen re-ran on scan2 despite an unchanged fingerprint"
    # Trivy matching MUST still have run on scan2: reuse only skips cdxgen.
    assert len(trivy_calls) == 2, "vulnerability matching must re-run on every scan"

    # Same fingerprint value (the reused scan's own bytes are identical).
    assert scan2.dependency_fingerprint == scan1.dependency_fingerprint

    # The reused document produced the SAME component graph.
    assert _component_purls(sync_session, scan1_id) == _component_purls(
        sync_session, scan2_id
    )
    assert _component_purls(sync_session, scan2_id) == {"pkg:npm/example"}

    # scan2's own SBOM artifact lives under scan2's OWN id, never scan1's,
    # workspace isolation even on a reuse extraction.
    scan2_sbom = sync_session.execute(
        select(ScanArtifact).where(
            ScanArtifact.scan_id == scan2_id, ScanArtifact.kind == "sbom_cyclonedx"
        )
    ).scalar_one()
    assert str(scan2_id) in scan2_sbom.storage_path
    assert str(scan1_id) not in scan2_sbom.storage_path

    # finally: shutil.rmtree(workspace) reclaimed scan2's workspace exactly
    # like a full-pipeline scan's. Reuse must not leak a workspace dir.
    assert not (tmp_path / str(scan2_id)).exists()

    # Row-shape parity: a reused scan looks exactly like a full-pipeline scan
    # from the outside.
    assert scan2.progress_percent == 100
    assert scan2.current_step == "finalize"
    assert scan2.completed_at is not None
    assert scan2.error_message is None


# ---------------------------------------------------------------------------
# 2. Scanner version bump → fingerprint differs → full pipeline re-runs
# ---------------------------------------------------------------------------


def test_scanner_version_bump_forces_the_full_pipeline_to_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _pin_manifest_inventory(monkeypatch)
    cdxgen_calls = _count_cdxgen_calls(monkeypatch)
    _stub_trivy(monkeypatch)

    project_id = _seed_project()

    from tasks.scan_source import scan_source_task

    monkeypatch.setenv("CDXGEN_VERSION", "12.3.3")
    scan1_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    result1 = scan_source_task.apply(args=[str(scan1_id)])
    assert result1.successful()
    assert len(cdxgen_calls) == 1

    sync_session.expire_all()
    scan1 = sync_session.execute(select(Scan).where(Scan.id == scan1_id)).scalar_one()
    assert scan1.dependency_fingerprint is not None

    # Simulate a worker image upgrade between the two scans: same manifest
    # inventory, different pinned cdxgen version.
    monkeypatch.setenv("CDXGEN_VERSION", "13.0.0")
    scan2_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    result2 = scan_source_task.apply(args=[str(scan2_id)])
    assert result2.successful()

    sync_session.expire_all()
    scan2 = sync_session.execute(select(Scan).where(Scan.id == scan2_id)).scalar_one()
    assert scan2.status == "succeeded"
    # cdxgen DID run again: the version bump must never read as "unchanged".
    assert len(cdxgen_calls) == 2, (
        "a scanner-version bump must force cdxgen to re-run, not reuse the "
        "prior version's SBOM"
    )
    assert scan2.dependency_fingerprint is not None
    assert scan2.dependency_fingerprint != scan1.dependency_fingerprint


# ---------------------------------------------------------------------------
# 3. Different ref → no reuse (S8 compares within (project, ref) only)
# ---------------------------------------------------------------------------


def test_different_ref_on_the_same_project_does_not_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("CDXGEN_VERSION", "12.3.3")
    _pin_manifest_inventory(monkeypatch)
    cdxgen_calls = _count_cdxgen_calls(monkeypatch)
    _stub_trivy(monkeypatch)

    project_id = _seed_project()

    from tasks.scan_source import scan_source_task

    scan1_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    scan_source_task.apply(args=[str(scan1_id)])
    assert len(cdxgen_calls) == 1

    # A PR branch scan on the SAME project, DIFFERENT ref: no prior succeeded
    # scan exists for (project_id, "pr-7") yet, so this must run the full
    # pipeline even though the manifest/scanner/config are all identical.
    scan2_id = _seed_queued_scan(sync_session, project_id=project_id, ref="pr-7")
    result2 = scan_source_task.apply(args=[str(scan2_id)])
    assert result2.successful()
    assert len(cdxgen_calls) == 2, "a different ref must never reuse another ref's SBOM"


# ---------------------------------------------------------------------------
# 4. Idempotent retry: re-running an already-succeeded reused scan is a no-op
# ---------------------------------------------------------------------------


def test_rerunning_a_succeeded_reused_scan_is_still_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("CDXGEN_VERSION", "12.3.3")
    _pin_manifest_inventory(monkeypatch)
    cdxgen_calls = _count_cdxgen_calls(monkeypatch)
    _stub_trivy(monkeypatch)

    project_id = _seed_project()

    from tasks.scan_source import scan_source_task

    scan1_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    scan_source_task.apply(args=[str(scan1_id)])

    scan2_id = _seed_queued_scan(sync_session, project_id=project_id, ref="main")
    scan_source_task.apply(args=[str(scan2_id)])
    sync_session.expire_all()
    scan2_first = sync_session.execute(
        select(Scan).where(Scan.id == scan2_id)
    ).scalar_one()
    assert scan2_first.status == "succeeded"
    completed_at_first = scan2_first.completed_at
    components_first = _component_purls(sync_session, scan2_id)

    # Celery acks_late + worker-restart re-entry on the SAME (already-
    # succeeded) reused scan: task-level idempotency short-circuits before
    # the reuse decision even runs again.
    scan_source_task.apply(args=[str(scan2_id)])
    sync_session.expire_all()
    scan2_again = sync_session.execute(
        select(Scan).where(Scan.id == scan2_id)
    ).scalar_one()
    assert scan2_again.status == "succeeded"
    assert scan2_again.completed_at == completed_at_first
    assert _component_purls(sync_session, scan2_id) == components_first
    # No extra cdxgen invocation, and no extra components accumulated.
    assert len(cdxgen_calls) == 1
