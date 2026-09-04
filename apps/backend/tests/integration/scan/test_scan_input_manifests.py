# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Scan provenance — the manifests a source scan had in front of it (gap #31).

Driven through ``scan_source_task`` rather than the collector directly: what is
being pinned is that the record survives a real pipeline run and lands on the
column the scan-detail path reads. The collector's own behaviour (what it skips,
its bounds, its determinism) is in
``tests/unit/services/test_scan_inputs.py``.

``_fetch_source`` is replaced with one that writes a tree, because the mock
pipeline's placeholder fetch produces an empty workspace — which is a legitimate
case of its own and is asserted here too, since "not recorded" and "looked and
found nothing" have to stay distinguishable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from integrations.trivy import TrivyResult
from models import Scan
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration


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
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

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


def _stub_trivy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(
        sbom_path: Path,  # noqa: ARG001
        output_dir: Path,
        **_kwargs: object,
    ) -> TrivyResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "trivy-sbom.json"
        report: dict[str, object] = {"SchemaVersion": 2, "Results": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return TrivyResult(report_path=report_path, report=report)

    monkeypatch.setattr("tasks.scan_source.run_trivy_sbom", _fake_run)


def _ingest_trivy_stub(
    sbom_path: Path,  # noqa: ARG001
    output_dir: Path,
    **_kwargs: object,
) -> TrivyResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "trivy-sbom.json"
    report: dict[str, object] = {"SchemaVersion": 2, "Results": []}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return TrivyResult(report_path=report_path, report=report)


def _fetch_writing(tree: dict[str, str]) -> object:
    """Replace the fetch stage with one that materialises ``tree``."""

    def _fake_fetch(*, scan_uuid: uuid.UUID, workspace: Path, **_kwargs: object) -> Path:  # noqa: ARG001
        source_dir = workspace / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        for relative, content in tree.items():
            path = source_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return source_dir

    return _fake_fetch


def test_a_source_scan_records_the_manifests_it_was_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy(monkeypatch)
    monkeypatch.setattr(
        "tasks.scan_source._fetch_source",
        _fetch_writing(
            {
                "package.json": '{"name": "demo"}',
                "package-lock.json": '{"lockfileVersion": 3}',
                "services/api/go.mod": "module demo\n",
                "node_modules/lodash/package.json": '{"name": "lodash"}',
                "README.md": "docs",
            }
        ),
    )

    scan_id = _seed_queued_scan()

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    inventory = scan.input_manifests
    assert inventory is not None, "a tree with manifests must be recorded"
    paths = [entry["path"] for entry in inventory["files"]]
    assert paths == ["package-lock.json", "package.json", "services/api/go.mod"]
    assert inventory["count"] == 3
    assert inventory["truncated"] is False
    # The installed dependency's own manifest is not the project's declaration.
    assert not any("node_modules" in path for path in paths)
    for entry in inventory["files"]:
        assert entry["sha256"] is not None
        assert entry["size"] > 0


def test_a_tree_without_manifests_leaves_the_column_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """NULL reads as "not recorded", which is what a scan with nothing to record
    should leave — an empty inventory would claim a measurement was taken."""
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy(monkeypatch)
    monkeypatch.setattr(
        "tasks.scan_source._fetch_source",
        _fetch_writing({"README.md": "docs only", "src/main.c": "int main(){}"}),
    )

    scan_id = _seed_queued_scan()

    from tasks.scan_source import scan_source_task

    result = scan_source_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.input_manifests is None


def test_a_rerun_records_the_same_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """Re-scanning the same source must not produce a different record.

    ``_reset_scan_for_rerun`` wipes the scan's prior rows; the inventory is a
    column rather than a row, so what matters is that the second run overwrites
    it with an equal value instead of leaving a stale or diverging one.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    _stub_trivy(monkeypatch)
    monkeypatch.setattr(
        "tasks.scan_source._fetch_source",
        _fetch_writing({"pom.xml": "<project/>", "go.mod": "module demo\n"}),
    )

    scan_id = _seed_queued_scan()

    from tasks.scan_source import scan_source_task

    scan_source_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    first = sync_session.execute(
        select(Scan.input_manifests).where(Scan.id == scan_id)
    ).scalar_one()

    # Re-running a succeeded scan is a no-op, so reset it to queued the way a
    # retry would arrive.
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    scan.status = "queued"
    sync_session.commit()

    scan_source_task.apply(args=[str(scan_id)])
    sync_session.expire_all()
    second = sync_session.execute(
        select(Scan.input_manifests).where(Scan.id == scan_id)
    ).scalar_one()

    assert first == second
    assert first is not None


# ---------------------------------------------------------------------------
# The other half: what an ingest scan was handed (gap #31, step 2)
# ---------------------------------------------------------------------------


def _seed_queued_ingest(workspace: Path, sbom_bytes: bytes, filename: str) -> uuid.UUID:
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

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
                kind="sbom",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={"source_type": "sbom"},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id, project_id = scan.id, project.id
            ingest_dir = workspace / "sbom-ingest" / str(project_id)
            ingest_dir.mkdir(parents=True, exist_ok=True)
            dest = ingest_dir / f"{scan_id}.cdx.json"
            dest.write_bytes(sbom_bytes)
            scan.scan_metadata = {
                "source_type": "sbom",
                "sbom_path": str(dest),
                "original_filename": filename,
            }
            await s.commit()
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def test_an_ingest_records_what_the_document_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """The counterpart to the source scan's manifest inventory.

    An upload has no tree to inventory, so what is recorded is the document's
    own account of itself — read from the ORIGINAL bytes, the same ones the
    conformance verdict is scored on, so the summary describes the supplier's
    document rather than our conversion of it.
    """
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr("tasks.ingest_sbom.run_trivy_sbom", _ingest_trivy_stub)

    sbom = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:11111111-2222-3333-4444-555555555555",
            "version": 1,
            "metadata": {
                "timestamp": "2026-08-01T10:00:00Z",
                "tools": {
                    "components": [
                        {"type": "application", "name": "cdxgen", "version": "12.3.3"}
                    ]
                },
                "supplier": {"name": "Example Corp"},
                "component": {
                    "type": "application",
                    "name": "supplier-app",
                    "version": "4.2.0",
                },
            },
            "components": [
                {
                    "type": "library",
                    "name": "lodash",
                    "version": "4.17.21",
                    "purl": "pkg:npm/lodash@4.17.21",
                }
            ],
        }
    ).encode()

    scan_id = _seed_queued_ingest(tmp_path, sbom, "supplier.cdx.json")

    from tasks.ingest_sbom import ingest_sbom_task

    result = ingest_sbom_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"

    document = scan.input_document
    assert document is not None
    assert document["format"] == "cyclonedx"
    assert document["spec_version"] == "1.6"
    assert document["created"] == "2026-08-01T10:00:00Z"
    assert document["tools"] == [{"name": "cdxgen", "version": "12.3.3"}]
    assert document["supplier"] == "Example Corp"
    assert document["subject"] == "supplier-app"
    assert document["component_count"] == 1
    assert document["original_filename"] == "supplier.cdx.json"
    assert document["byte_size"] == len(sbom)

    # An ingest has no tree, so the source-scan half stays NULL. The two
    # questions are asked of different scan kinds and must not be conflated.
    assert scan.input_manifests is None


def test_an_unsummarisable_upload_leaves_the_column_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sync_session: Session
) -> None:
    """SPDX Tag-Value ingests fine but is not summarised.

    Recording a spec version nobody parsed would give a reader a value to
    compare against that was never read.
    """
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setattr("tasks.ingest_sbom.run_trivy_sbom", _ingest_trivy_stub)

    tag_value = (
        b"SPDXVersion: SPDX-2.3\n"
        b"DataLicense: CC0-1.0\n"
        b"SPDXID: SPDXRef-DOCUMENT\n"
        b"DocumentName: portal\n"
        b"PackageName: lodash\n"
        b"SPDXID: SPDXRef-Package-1\n"
        b"PackageVersion: 4.17.21\n"
    )
    scan_id = _seed_queued_ingest(tmp_path, tag_value, "portal.spdx")

    from tasks.ingest_sbom import ingest_sbom_task

    result = ingest_sbom_task.apply(args=[str(scan_id)])
    assert result.successful(), f"task failed: {result.traceback}"

    sync_session.expire_all()
    scan = sync_session.execute(select(Scan).where(Scan.id == scan_id)).scalar_one()
    assert scan.status == "succeeded"
    assert scan.input_document is None
