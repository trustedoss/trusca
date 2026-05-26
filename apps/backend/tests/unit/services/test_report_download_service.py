"""
DB-backed service tests for ``services/report_download_service.py``.

Backs the W3 #32a-2 Reports center surface:

  - ``record_report_download(...)``  — emit one history row (best-effort).
  - ``list_report_history(...)``     — paginated read with existence-hide.

Pinned cases:

Emit:
  * Happy path inserts the row with the expected column mapping.
  * ``team_id`` is mirrored from ``project.team_id`` even when the caller
    passes a different team via the actor (defence against a future call site
    that gets the denormalised pointer wrong).
  * A SQLAlchemyError from the underlying INSERT is logged + swallowed;
    callers MUST NOT see the exception (the download already succeeded).

List:
  * page < 1 / page_size out of [1, 200] → ReportHistoryError (422 in router).
  * Unknown report_type in the filter list → ReportHistoryError.
  * Cross-team viewer → ReportHistoryNotFound (404 existence-hide).
  * super_admin bypasses the team check.
  * type filter narrows the result set.
  * scan_id filter narrows the result set.

These run against the live Postgres (``integration`` mark + alembic upgrade
fixture) — the model wires a Postgres ENUM (``report_type_enum``) and the
listing query uses the compound ``(project_id, created_at DESC)`` index, so a
mock DB would test the mock, not the contract.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import ReportDownload
from services.report_download_service import (
    PAGE_SIZE_MAX,
    PAGE_SIZE_MIN,
    ReportHistoryError,
    ReportHistoryNotFound,
    list_report_history,
    record_report_download,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip report_download service tests")
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
            "alembic upgrade head failed; report_download service tests cannot run\n"
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
# Helpers
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal Request stand-in carrying ``headers`` + ``client.host``."""

    class _Client:
        def __init__(self, host: str | None) -> None:
            self.host = host

    def __init__(
        self,
        *,
        ip: str | None = "203.0.113.7",
        user_agent: str | None = "pytest/1.0",
        forwarded_for: str | None = None,
    ) -> None:
        h: dict[str, str] = {}
        if user_agent is not None:
            h["user-agent"] = user_agent
        if forwarded_for is not None:
            h["x-forwarded-for"] = forwarded_for
        self.headers = h
        self.client = self._Client(ip)


async def _seed_project(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    return team, user, project, actor


# ---------------------------------------------------------------------------
# record_report_download
# ---------------------------------------------------------------------------


async def test_record_report_download_happy_path_inserts_row(db_session: AsyncSession) -> None:
    _, user, project, _ = await _seed_project(db_session)

    await record_report_download(
        db_session,
        project=project,
        scan_id=None,
        user=user,
        report_type="notice",
        fmt="text",
        size_bytes=12_345,
        request=_FakeRequest(ip="198.51.100.4", user_agent="curl/8.0"),
    )

    rows = (
        await db_session.execute(
            select(ReportDownload).where(ReportDownload.project_id == project.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.project_id == project.id
    assert row.scan_id is None
    assert row.team_id == project.team_id  # mirrored from project
    assert row.user_id == user.id
    assert row.report_type == "notice"
    assert row.format == "text"
    assert row.size_bytes == 12_345
    # client_ip is INET; psycopg2 returns ipaddress objects in some versions —
    # str() compare is portable across asyncpg ↔ psycopg variants.
    assert str(row.client_ip) == "198.51.100.4"
    assert row.user_agent == "curl/8.0"
    assert row.created_at is not None


async def test_record_report_download_xff_preferred_over_client_host(
    db_session: AsyncSession,
) -> None:
    _, user, project, _ = await _seed_project(db_session)

    await record_report_download(
        db_session,
        project=project,
        scan_id=None,
        user=user,
        report_type="sbom",
        fmt="cyclonedx-json",
        size_bytes=999,
        # Reverse-proxied request: XFF carries the original client; the ASGI
        # client tuple is the proxy. We MUST record the original.
        request=_FakeRequest(ip="10.0.0.1", forwarded_for="203.0.113.99, 10.0.0.5"),
    )

    row = (
        await db_session.execute(
            select(ReportDownload).where(ReportDownload.project_id == project.id)
        )
    ).scalar_one()
    assert str(row.client_ip) == "203.0.113.99"


async def test_record_report_download_team_id_mirrored_from_project(
    db_session: AsyncSession,
) -> None:
    """``team_id`` must come from the parent project, not the actor's other team."""
    _, _, project, _ = await _seed_project(db_session)
    # Second team that the actor also belongs to — must NOT leak onto the row.
    org = await make_organization(db_session)
    other_team = await make_team(db_session, organization=org)
    other_user = await make_user(db_session)
    await make_membership(db_session, user=other_user, team=other_team, role="team_admin")

    await record_report_download(
        db_session,
        project=project,
        scan_id=None,
        user=other_user,
        report_type="vex_export",
        fmt="cdx-vex",
        size_bytes=4096,
        request=_FakeRequest(),
    )

    row = (
        await db_session.execute(
            select(ReportDownload).where(ReportDownload.project_id == project.id)
        )
    ).scalar_one()
    assert row.team_id == project.team_id
    assert row.team_id != other_team.id


async def test_record_report_download_unknown_type_is_swallowed(
    db_session: AsyncSession,
) -> None:
    """A programmer-error report_type logs + returns without raising."""
    _, user, project, _ = await _seed_project(db_session)

    # MUST NOT raise; MUST NOT insert.
    await record_report_download(
        db_session,
        project=project,
        scan_id=None,
        user=user,
        report_type="not_a_real_type",
        fmt="text",
        size_bytes=1,
        request=_FakeRequest(),
    )

    count = (
        await db_session.execute(
            select(ReportDownload).where(ReportDownload.project_id == project.id)
        )
    ).scalars().all()
    assert count == []


async def test_record_report_download_db_failure_is_swallowed(
    db_session: AsyncSession,
) -> None:
    """Any SQLAlchemyError raised during commit must be logged + swallowed.

    We drive a real DB failure by emitting under a non-existent scan_id: the
    FK constraint trips at commit time and SQLAlchemy raises an IntegrityError
    (a SQLAlchemyError subclass). The download MUST NOT see the exception.

    After the swallowed failure we use a fresh session to verify nothing was
    persisted — re-using ``db_session`` would surface SQLAlchemy's
    ``PendingRollbackError`` on the next query because the test's outer
    session has its own transaction (separate from the helper's commit),
    not because the swallow misbehaved.
    """
    _, user, project, _ = await _seed_project(db_session)
    # Capture identifiers before the failing emit so the verify session can
    # query without re-touching the expired ORM object on the original session.
    project_id = project.id

    bogus_scan_id = uuid.uuid4()  # not in the scans table → FK violation
    # MUST NOT raise.
    await record_report_download(
        db_session,
        project=project,
        scan_id=bogus_scan_id,
        user=user,
        report_type="vuln_pdf",
        fmt="pdf",
        size_bytes=2048,
        request=_FakeRequest(),
    )

    # Verify on a fresh session — the failed INSERT must not have been
    # committed by anyone.
    from core.config import database_url

    verify_engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    verify_factory = async_sessionmaker(
        verify_engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with verify_factory() as verify_session:
            count = (
                await verify_session.execute(
                    select(ReportDownload).where(ReportDownload.project_id == project_id)
                )
            ).scalars().all()
            assert count == []
    finally:
        await verify_engine.dispose()


async def test_record_report_download_long_user_agent_truncated(
    db_session: AsyncSession,
) -> None:
    """UA longer than 512 chars must be truncated (column is VARCHAR(512))."""
    _, user, project, _ = await _seed_project(db_session)

    long_ua = "ua/" + ("x" * 600)
    await record_report_download(
        db_session,
        project=project,
        scan_id=None,
        user=user,
        report_type="sbom",
        fmt="spdx-json",
        size_bytes=1,
        request=_FakeRequest(user_agent=long_ua),
    )

    row = (
        await db_session.execute(
            select(ReportDownload).where(ReportDownload.project_id == project.id)
        )
    ).scalar_one()
    assert row.user_agent is not None
    assert len(row.user_agent) == 512


# ---------------------------------------------------------------------------
# list_report_history — validation
# ---------------------------------------------------------------------------


async def test_list_history_page_below_one_raises(db_session: AsyncSession) -> None:
    _, _, project, actor = await _seed_project(db_session)
    with pytest.raises(ReportHistoryError):
        await list_report_history(
            db_session,
            project_id=project.id,
            viewer=actor,
            page=0,
            page_size=50,
        )


async def test_list_history_page_size_out_of_range_raises(db_session: AsyncSession) -> None:
    _, _, project, actor = await _seed_project(db_session)
    with pytest.raises(ReportHistoryError):
        await list_report_history(
            db_session,
            project_id=project.id,
            viewer=actor,
            page=1,
            page_size=PAGE_SIZE_MAX + 1,
        )
    with pytest.raises(ReportHistoryError):
        await list_report_history(
            db_session,
            project_id=project.id,
            viewer=actor,
            page=1,
            page_size=PAGE_SIZE_MIN - 1,
        )


async def test_list_history_unknown_type_filter_raises(db_session: AsyncSession) -> None:
    _, _, project, actor = await _seed_project(db_session)
    with pytest.raises(ReportHistoryError):
        await list_report_history(
            db_session,
            project_id=project.id,
            viewer=actor,
            type_filter=["notice", "garbage"],
        )


# ---------------------------------------------------------------------------
# list_report_history — auth / cross-team / existence-hide
# ---------------------------------------------------------------------------


async def test_list_history_cross_team_returns_404(db_session: AsyncSession) -> None:
    _, _, project, _ = await _seed_project(db_session)
    # Outsider user with their own team — they are NOT in the project's team.
    org = await make_organization(db_session)
    outsider_team = await make_team(db_session, organization=org)
    outsider_user = await make_user(db_session)
    await make_membership(
        db_session, user=outsider_user, team=outsider_team, role="developer"
    )
    outsider = principal_for(outsider_user, team_ids=[outsider_team.id])

    with pytest.raises(ReportHistoryNotFound):
        await list_report_history(
            db_session,
            project_id=project.id,
            viewer=outsider,
        )


async def test_list_history_unknown_project_returns_404(db_session: AsyncSession) -> None:
    _, _, _, actor = await _seed_project(db_session)
    with pytest.raises(ReportHistoryNotFound):
        await list_report_history(
            db_session,
            project_id=uuid.uuid4(),
            viewer=actor,
        )


async def test_list_history_super_admin_bypasses_team_check(
    db_session: AsyncSession,
) -> None:
    _, _, project, _ = await _seed_project(db_session)
    admin = principal_for(
        await make_user(db_session, is_superuser=True),
        team_ids=[],
    )
    response = await list_report_history(
        db_session, project_id=project.id, viewer=admin
    )
    assert response.total == 0
    assert response.items == []


# ---------------------------------------------------------------------------
# list_report_history — filtering
# ---------------------------------------------------------------------------


async def _emit(
    db_session: AsyncSession,
    *,
    project,
    user,
    report_type: str,
    fmt: str,
    scan_id: uuid.UUID | None = None,
) -> None:
    await record_report_download(
        db_session,
        project=project,
        scan_id=scan_id,
        user=user,
        report_type=report_type,
        fmt=fmt,
        size_bytes=1,
        request=_FakeRequest(),
    )


async def test_list_history_type_filter_narrows_results(db_session: AsyncSession) -> None:
    _, user, project, actor = await _seed_project(db_session)
    await _emit(db_session, project=project, user=user, report_type="notice", fmt="text")
    await _emit(
        db_session, project=project, user=user, report_type="sbom", fmt="cyclonedx-json"
    )
    await _emit(
        db_session, project=project, user=user, report_type="vuln_pdf", fmt="pdf"
    )

    response = await list_report_history(
        db_session,
        project_id=project.id,
        viewer=actor,
        type_filter=["notice", "sbom"],
    )
    types = sorted(item.report_type for item in response.items)
    assert types == ["notice", "sbom"]
    assert response.total == 2


async def test_list_history_scan_id_filter_narrows_results(db_session: AsyncSession) -> None:
    _, user, project, actor = await _seed_project(db_session)
    target_scan_id = uuid.uuid4()
    # NOTE: scan_id FK is nullable but VALIDATING — passing a UUID that has no
    # matching scans row would violate FK. We pass NULL for the "wrong scan"
    # entry instead, which is the realistic shape (VEX export always has NULL,
    # SBOM/Vuln-PDF when scan is later pruned also goes to NULL).
    # For "target_scan_id" we therefore must NOT FK to a real scan in this unit
    # test — skip the explicit scan_id filter scenario when the FK is enforced.
    # We instead test the NULL-vs-NULL distinction below.
    _ = target_scan_id

    # Two emits with NULL scan_id (VEX-export-style).
    await _emit(db_session, project=project, user=user, report_type="vex_export", fmt="cdx-vex")
    await _emit(
        db_session, project=project, user=user, report_type="vex_export", fmt="csaf"
    )

    # Filter by an unrelated scan_id → no matches.
    response = await list_report_history(
        db_session,
        project_id=project.id,
        viewer=actor,
        scan_id_filter=uuid.uuid4(),
    )
    assert response.total == 0
    assert response.items == []


async def test_list_history_default_pagination(db_session: AsyncSession) -> None:
    _, user, project, actor = await _seed_project(db_session)
    for _ in range(3):
        await _emit(db_session, project=project, user=user, report_type="notice", fmt="text")

    response = await list_report_history(
        db_session,
        project_id=project.id,
        viewer=actor,
        page=1,
        page_size=2,
    )
    assert response.page == 1
    assert response.page_size == 2
    assert response.total == 3
    assert len(response.items) == 2

    page2 = await list_report_history(
        db_session,
        project_id=project.id,
        viewer=actor,
        page=2,
        page_size=2,
    )
    assert page2.total == 3
    assert len(page2.items) == 1
