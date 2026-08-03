"""
Service-layer tests for ``services.admin_scan_service`` — Phase 4 PR #14.

Drives list_scans + cancel_scan against a live Postgres so the JOIN +
SELECT FOR UPDATE actually run.

Coverage:
  - list_scans: cross-team join, status filter, default ordering,
    pagination envelope.
  - list_scans filters (M-35): kind equality, project name partial match
    (case-insensitive, LIKE metacharacters escaped), combined filters,
    count/total parity with the row filters.
  - cancel_scan: queued → cancelled, running → cancelled, terminal-state
    409, 404 on missing scan.
  - revoke is best-effort: a broker exception does NOT prevent the status
    update.
  - audit row written on cancel (listener captures the status mutation).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin_scan_service tests")
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
            f"alembic upgrade head failed; admin_scan_service tests cannot run\n"
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
# Fakes — Celery control surface
# ---------------------------------------------------------------------------


class _FakeControl:
    """Records revoke calls so tests can assert behaviour."""

    def __init__(self, *, raise_on_revoke: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_on_revoke = raise_on_revoke

    def revoke(self, task_id: str, *, terminate: bool = False, signal: str | None = None) -> None:
        if self.raise_on_revoke:
            # Model a real broker outage: kombu raises OperationalError, which
            # the service's best-effort catch is now narrowed to (Low #5). A
            # bare RuntimeError would (correctly) propagate as a programming
            # error rather than be swallowed as a transient broker hiccup.
            from kombu.exceptions import OperationalError

            raise OperationalError("broker unreachable")
        self.calls.append({"task_id": task_id, "terminate": terminate, "signal": signal})


class _FakeCeleryApp:
    def __init__(self, *, raise_on_revoke: bool = False) -> None:
        self.control = _FakeControl(raise_on_revoke=raise_on_revoke)


# ---------------------------------------------------------------------------
# list_scans
# ---------------------------------------------------------------------------


async def test_list_scans_returns_pagination_envelope(db_session: AsyncSession) -> None:
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    page = await list_scans(db_session, actor=actor, page=1, page_size=10)
    assert page.page == 1
    assert page.page_size == 10
    assert page.total >= 1
    assert any(item.id == scan.id for item in page.items)
    matching = next(item for item in page.items if item.id == scan.id)
    assert matching.team_id == team.id
    assert matching.team_name == team.name
    assert matching.project_name == project.name


async def test_list_scans_status_filter(db_session: AsyncSession) -> None:
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    project1 = await make_project(db_session, team=team)
    project2 = await make_project(db_session, team=team)

    queued = await make_scan(db_session, project=project1, status="queued")
    succeeded = await make_scan(db_session, project=project2, status="succeeded")

    page = await list_scans(db_session, actor=actor, status="queued", page_size=200)
    assert any(item.id == queued.id for item in page.items)
    assert all(item.status == "queued" for item in page.items)
    assert succeeded.id not in {item.id for item in page.items}


async def test_list_scans_kind_filter(db_session: AsyncSession) -> None:
    """M-35: `kind` filter narrows to source vs container scans, total agrees."""
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    # A unique project name lets us scope the cross-team global listing to
    # just the rows this test created.
    name = f"kindfilter-{unique_suffix()}"
    project = await make_project(db_session, team=team, name=name)

    # ix_scans_project_active allows at most one queued/running scan per
    # project — keep the second scan terminal.
    source = await make_scan(db_session, project=project, kind="source", status="succeeded")
    container = await make_scan(db_session, project=project, kind="container")

    page = await list_scans(
        db_session, actor=actor, kind="container", project=name, page_size=200
    )
    ids = {item.id for item in page.items}
    assert container.id in ids
    assert source.id not in ids
    assert all(item.kind == "container" for item in page.items)
    # Total reflects the same filters as the rows (count parity).
    assert page.total == len(page.items) == 1


async def test_list_scans_project_partial_match_case_insensitive(
    db_session: AsyncSession,
) -> None:
    """M-35: `project` is a case-insensitive substring match on project name."""
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    suffix = unique_suffix()
    hit_project = await make_project(db_session, team=team, name=f"Payments-{suffix}")
    miss_project = await make_project(db_session, team=team, name=f"Billing-{suffix}")
    hit = await make_scan(db_session, project=hit_project)
    miss = await make_scan(db_session, project=miss_project)

    # Substring, different case than the stored name.
    page = await list_scans(
        db_session, actor=actor, project=f"payments-{suffix}".upper(), page_size=200
    )
    ids = {item.id for item in page.items}
    assert hit.id in ids
    assert miss.id not in ids
    assert page.total == len(page.items) == 1


@pytest.mark.parametrize(
    ("needle_template", "decoy_name_template"),
    [
        # `%` must match literally, not as a multi-char wildcard: searching
        # "100%-<sfx>" must NOT match "100x-<sfx>".
        ("100%-{sfx}", "100x-{sfx}"),
        # `_` must match literally, not as a single-char wildcard: searching
        # "a_b-<sfx>" must NOT match "axb-<sfx>".
        ("a_b-{sfx}", "axb-{sfx}"),
        # A lone backslash must not break the pattern or act as an escape
        # prefix for the following character.
        ("c\\d-{sfx}", "cd-{sfx}"),
    ],
)
async def test_list_scans_project_filter_escapes_like_metacharacters(
    db_session: AsyncSession, needle_template: str, decoy_name_template: str
) -> None:
    """M-35 adversarial: LIKE metacharacters in `project` are literal."""
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    sfx = unique_suffix()
    needle = needle_template.format(sfx=sfx)
    decoy_name = decoy_name_template.format(sfx=sfx)

    literal_project = await make_project(db_session, team=team, name=needle)
    decoy_project = await make_project(db_session, team=team, name=decoy_name)
    literal_scan = await make_scan(db_session, project=literal_project)
    decoy_scan = await make_scan(db_session, project=decoy_project)

    page = await list_scans(db_session, actor=actor, project=needle, page_size=200)
    ids = {item.id for item in page.items}
    assert literal_scan.id in ids
    assert decoy_scan.id not in ids
    assert page.total == len(page.items) == 1


async def test_list_scans_combined_status_kind_project_filters(
    db_session: AsyncSession,
) -> None:
    """M-35: status + kind + project compose with AND semantics; total agrees."""
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    # ix_scans_project_active allows at most one queued/running scan per
    # project — split the two queued scans across two projects whose names
    # share the searchable fragment.
    fragment = f"combo-{unique_suffix()}"
    project_a = await make_project(db_session, team=team, name=f"{fragment}-a")
    project_b = await make_project(db_session, team=team, name=f"{fragment}-b")

    match = await make_scan(db_session, project=project_a, kind="source", status="queued")
    wrong_status = await make_scan(
        db_session, project=project_a, kind="source", status="succeeded"
    )
    wrong_kind = await make_scan(
        db_session, project=project_b, kind="container", status="queued"
    )

    page = await list_scans(
        db_session,
        actor=actor,
        status="queued",
        kind="source",
        project=fragment,
        page_size=200,
    )
    ids = {item.id for item in page.items}
    assert ids == {match.id}
    assert wrong_status.id not in ids
    assert wrong_kind.id not in ids
    assert page.total == 1


async def test_list_scans_total_matches_filtered_rows_across_pages(
    db_session: AsyncSession,
) -> None:
    """M-35: count query carries the same filters — total is the filtered total."""
    from services.admin_scan_service import list_scans

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    name = f"pagetotal-{unique_suffix()}"
    project = await make_project(db_session, team=team, name=name)

    # All terminal so ix_scans_project_active (one queued/running per
    # project) is not violated by stacking scans on one project.
    for _ in range(3):
        await make_scan(db_session, project=project, kind="source", status="succeeded")
    # Noise that the filters must exclude from the total.
    await make_scan(db_session, project=project, kind="container", status="succeeded")

    page = await list_scans(
        db_session, actor=actor, kind="source", project=name, page_size=2
    )
    assert page.total == 3
    assert len(page.items) == 2  # page 1 of 2

    page2 = await list_scans(
        db_session, actor=actor, kind="source", project=name, page=2, page_size=2
    )
    assert page2.total == 3
    assert len(page2.items) == 1


# ---------------------------------------------------------------------------
# cancel_scan — happy paths
# ---------------------------------------------------------------------------


async def test_cancel_scan_queued_to_cancelled(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    fake_celery = _FakeCeleryApp()
    item = await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=fake_celery,
    )
    assert item.status == "cancelled"
    assert item.error_message == "cancelled by admin"
    assert item.finished_at is not None


async def test_cancel_scan_running_revokes_celery_task(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")
    # Seed a celery task id so the revoke branch fires.
    scan.celery_task_id = "celery-task-xyz"
    await db_session.commit()

    fake_celery = _FakeCeleryApp()
    await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=fake_celery,
    )
    assert fake_celery.control.calls == [
        {"task_id": "celery-task-xyz", "terminate": True, "signal": "SIGTERM"}
    ]


async def test_cancel_scan_revoke_failure_does_not_block_status_update(
    db_session: AsyncSession,
) -> None:
    """A broker hiccup must not stop us from marking the scan cancelled."""
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="running")
    scan.celery_task_id = "celery-task-xyz"
    await db_session.commit()

    fake_celery = _FakeCeleryApp(raise_on_revoke=True)
    item = await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=fake_celery,
    )
    assert item.status == "cancelled"


# ---------------------------------------------------------------------------
# cancel_scan — error paths
# ---------------------------------------------------------------------------


async def test_cancel_scan_unknown_id_raises_404(db_session: AsyncSession) -> None:
    from services.admin_scan_service import AdminScanNotFound, cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(AdminScanNotFound):
        await cancel_scan(
            db_session,
            actor=actor,
            scan_id=uuid.uuid4(),
        )


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
async def test_cancel_scan_terminal_state_raises_409(
    db_session: AsyncSession, terminal_status: str
) -> None:
    from services.admin_scan_service import ScanAlreadyCancelled, cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status=terminal_status)

    with pytest.raises(ScanAlreadyCancelled):
        await cancel_scan(db_session, actor=actor, scan_id=scan.id)


# ---------------------------------------------------------------------------
# Audit trail — listener captures the cancel mutation
# ---------------------------------------------------------------------------


async def test_cancel_scan_writes_audit_row(db_session: AsyncSession) -> None:
    from services.admin_scan_service import cancel_scan

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="queued")

    # The audit listener requires the contextvar to be bound to the actor —
    # mirror what get_current_user does in the request path.
    from core.audit import audit_context

    audit_context.set({"user_id": str(actor.id)})

    await cancel_scan(
        db_session,
        actor=actor,
        scan_id=scan.id,
        celery_app_override=_FakeCeleryApp(),
    )

    rows = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE target_table='scans' AND action='update' "
                "  AND target_id=:tid"
            ),
            {"tid": str(scan.id)},
        )
    ).scalar_one()
    assert rows >= 1
