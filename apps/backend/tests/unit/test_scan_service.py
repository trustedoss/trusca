"""
Service-layer tests for `services/scan_service.py` — Phase 2 PR #7.

PR #7 only persists the `scans` row with status='queued' and celery_task_id=
None — there is no Celery enqueue yet. These tests pin:

  - happy-path trigger persists status='queued' + progress_percent=0 + audit log
  - the partial unique index `ix_scans_project_active` produces a 409 on a
    second concurrent trigger (and that the gate releases when the first scan
    moves to a terminal status)
  - cross-team guards on trigger and read (IDOR)
  - super_admin bypass
  - list pagination

We drive the service directly (no HTTP); the API surface is covered in
`tests/integration/test_scans_api.py`.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip scan service tests")
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
            f"alembic upgrade head failed; scan service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# trigger_scan — happy path
# ---------------------------------------------------------------------------


async def test_trigger_scan_persists_queued_row_and_writes_audit_log(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ScanCreate
    from services.scan_service import trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    scan = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(kind="source", metadata={"git_ref": "main"}),
        actor=actor,
    )

    assert scan.id is not None
    assert scan.project_id == project.id
    assert scan.status == "queued"
    assert scan.progress_percent == 0
    assert scan.kind == "source"
    # PR #8 contract: trigger_scan now enqueues via tasks.enqueue_scan and
    # records the returned task id (UUID string from Celery).
    assert isinstance(scan.celery_task_id, str)
    assert len(scan.celery_task_id) > 0
    assert scan.requested_by_user_id == user.id
    assert scan.scan_metadata == {"git_ref": "main"}

    # Audit log row exists for the scan create. As with projects, the listener
    # fires before gen_random_uuid() resolves the id, so target_id is None.
    # Match by diff containment instead.
    rows = (
        await db_session.execute(
            text(
                "SELECT action, target_table, diff "
                "FROM audit_logs "
                "WHERE target_table = 'scans' "
                "  AND diff @> CAST(:match AS jsonb)"
            ),
            {"match": f'{{"project_id": "{project.id}"}}'},
        )
    ).all()
    assert rows, "expected an audit_logs row for the scan create"
    assert any(r.action == "create" for r in rows)


# ---------------------------------------------------------------------------
# trigger_scan — partial unique index gate
# ---------------------------------------------------------------------------


async def test_trigger_scan_second_trigger_while_active_raises_conflict(
    db_session: AsyncSession,
) -> None:
    """
    The partial unique index `ix_scans_project_active` (UNIQUE on project_id
    WHERE status IN ('queued','running')) is the canonical "scan already in
    progress" signal. The service translates the IntegrityError to
    ScanInProgressConflict (409).
    """
    from schemas.scan import ScanCreate
    from services.scan_service import ScanInProgressConflict, trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    first = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(),
        actor=actor,
    )
    assert first.status == "queued"

    with pytest.raises(ScanInProgressConflict):
        await trigger_scan(
            db_session,
            project_id=project.id,
            payload=ScanCreate(),
            actor=actor,
        )


async def test_trigger_scan_succeeds_after_previous_scan_terminates(
    db_session: AsyncSession,
) -> None:
    """
    The partial unique index only covers status IN ('queued','running'). Once
    the first scan transitions to a terminal status ('succeeded' here), a new
    scan must be triggerable. Verifies the index is partial, not absolute.
    """
    from schemas.scan import ScanCreate
    from services.scan_service import trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    first = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(),
        actor=actor,
    )

    # Move the first scan out of the active set
    first.status = "succeeded"
    await db_session.commit()

    second = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(kind="container"),
        actor=actor,
    )
    assert second.id != first.id
    assert second.status == "queued"
    assert second.kind == "container"


# ---------------------------------------------------------------------------
# trigger_scan — RBAC / IDOR
# ---------------------------------------------------------------------------


async def test_trigger_scan_other_team_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ScanCreate
    from services.scan_service import ScanForbidden, trigger_scan

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ScanForbidden):
        await trigger_scan(
            db_session,
            project_id=project.id,
            payload=ScanCreate(),
            actor=actor,
        )


async def test_trigger_scan_unknown_project_raises_not_found(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ScanCreate
    from services.scan_service import ProjectMissingForScan, trigger_scan

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user, role="super_admin")

    with pytest.raises(ProjectMissingForScan):
        await trigger_scan(
            db_session,
            project_id=uuid.uuid4(),
            payload=ScanCreate(),
            actor=actor,
        )


async def test_trigger_scan_super_admin_can_trigger_any_team(
    db_session: AsyncSession,
) -> None:
    from schemas.scan import ScanCreate
    from services.scan_service import trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, team_ids=[], role="super_admin")

    scan = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(),
        actor=actor,
    )
    assert scan.requested_by_user_id == admin.id


# ---------------------------------------------------------------------------
# get_scan — IDOR
# ---------------------------------------------------------------------------


async def test_get_scan_other_team_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.scan_service import ScanForbidden, get_scan

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)
    scan = await make_scan(db_session, project=project)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ScanForbidden):
        await get_scan(db_session, scan_id=scan.id, actor=actor)


async def test_get_scan_same_team_returns_row(db_session: AsyncSession) -> None:
    from services.scan_service import get_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    fetched = await get_scan(db_session, scan_id=scan.id, actor=actor)
    assert fetched.id == scan.id


async def test_get_scan_super_admin_bypasses_team_check(
    db_session: AsyncSession,
) -> None:
    from services.scan_service import get_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    fetched = await get_scan(db_session, scan_id=scan.id, actor=actor)
    assert fetched.id == scan.id


async def test_get_scan_unknown_id_raises_not_found(
    db_session: AsyncSession,
) -> None:
    from services.scan_service import ScanNotFound, get_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(ScanNotFound):
        await get_scan(db_session, scan_id=uuid.uuid4(), actor=actor)


# ---------------------------------------------------------------------------
# list_scans_for_project — pagination + RBAC
# ---------------------------------------------------------------------------


async def test_list_scans_for_project_pagination(
    db_session: AsyncSession,
) -> None:
    from services.scan_service import list_scans_for_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    # Create a handful of terminal-status scans so the partial unique
    # index does not block the inserts.
    for _ in range(5):
        await make_scan(db_session, project=project, status="succeeded")

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    actor = principal_for(user, team_ids=[team.id], role="developer")

    rows, total = await list_scans_for_project(
        db_session, project_id=project.id, actor=actor, page=1, size=2
    )
    assert len(rows) == 2
    assert total == 5

    rows_page2, _ = await list_scans_for_project(
        db_session, project_id=project.id, actor=actor, page=2, size=2
    )
    assert len(rows_page2) == 2
    page1_ids = {r.id for r in rows}
    page2_ids = {r.id for r in rows_page2}
    assert page1_ids.isdisjoint(page2_ids)


async def test_list_scans_for_project_outsider_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.scan_service import ScanForbidden, list_scans_for_project

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ScanForbidden):
        await list_scans_for_project(db_session, project_id=project.id, actor=actor)


async def test_list_scans_for_project_orders_most_recent_first(
    db_session: AsyncSession,
) -> None:
    """The query orders by created_at DESC; verify ordering of three scans."""
    import asyncio

    from services.scan_service import list_scans_for_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    s1 = await make_scan(db_session, project=project, status="succeeded")
    # tiny sleep so created_at is monotonic at the microsecond level —
    # Postgres TIMESTAMPTZ resolution is microsecond.
    await asyncio.sleep(0.01)
    s2 = await make_scan(db_session, project=project, status="succeeded")
    await asyncio.sleep(0.01)
    s3 = await make_scan(db_session, project=project, status="succeeded")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    rows, _ = await list_scans_for_project(db_session, project_id=project.id, actor=actor)
    ids = [r.id for r in rows]
    assert ids[0] == s3.id
    assert ids[1] == s2.id
    assert ids[2] == s1.id


# ---------------------------------------------------------------------------
# trigger_scan — upload source type (feat/zip-upload)
# ---------------------------------------------------------------------------


async def test_trigger_scan_upload_missing_archive_raises_404(
    db_session: AsyncSession,
) -> None:
    """source_type='upload' with no archive file on disk must 404 before enqueue."""
    from schemas.scan import ScanCreate
    from services.scan_service import ScanArchiveMissing, trigger_scan

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ScanArchiveMissing) as ei:
        await trigger_scan(
            db_session,
            project_id=project.id,
            payload=ScanCreate(
                kind="source",
                metadata={"source_type": "upload", "archive_id": str(uuid.uuid4())},
            ),
            actor=actor,
        )
    assert ei.value.status_code == 404


async def test_trigger_scan_upload_with_existing_archive_queues(
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uploaded archive that exists on disk lets the upload scan queue."""
    import zipfile

    from schemas.scan import ScanCreate
    from services.scan_service import trigger_scan
    from services.source_archive_service import archive_path

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    archive_id = uuid.uuid4()
    path = archive_path(project.id, str(archive_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    import io as _io

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("src/main.py", b"print('hi')\n")
    path.write_bytes(buf.getvalue())

    scan = await trigger_scan(
        db_session,
        project_id=project.id,
        payload=ScanCreate(
            kind="source",
            metadata={"source_type": "upload", "archive_id": str(archive_id)},
        ),
        actor=actor,
    )
    assert scan.status == "queued"
    assert scan.scan_metadata["source_type"] == "upload"
    assert scan.scan_metadata["archive_id"] == str(archive_id)
