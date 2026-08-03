"""
Integration tests for the daily SLA-breach sweep (real Postgres) — X1 step 2.

Drives ``tasks.vuln_sla_sweep._run_sweep`` end to end against seeded rows:
the latest-succeeded-scan candidate query, the window selection, the team
membership fan-out, and the ACTUAL in-app row creation through
``tasks.notify`` (``send_notification_task.delay`` is monkeypatched to a
synchronous ``.apply`` so the notify task's prefs filter + insert run in-line
without a broker/worker — ``channels=[]`` means no outbound egress exists on
this path at all).

Negative controls pin the selection contract: outside-window breaches,
closed statuses, the master toggle, and a member who muted in-app.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import (
    Component,
    ComponentVersion,
    Notification,
    NotificationPreferences,
    Vulnerability,
    VulnerabilityFinding,
)
from tasks.vuln_sla_sweep import _run_sweep

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip vuln SLA sweep integration")
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
            "alembic upgrade head failed; vuln SLA sweep integration cannot run\n"
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


@pytest.fixture
def _inline_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route ``send_notification_task.delay`` through a synchronous ``.apply``
    so the notify task body (prefs filter → in-app INSERT) runs in-line in
    the test process. ``channels=[]`` short-circuits before any dispatcher /
    egress code, so this exercises exactly the production in-app path."""
    import tasks.notify as notify_module

    def _inline(*args: Any, **kwargs: Any) -> None:
        notify_module.send_notification_task.apply(args=args, kwargs=kwargs)

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _inline)


def _seed_team_project_scan(
    n_members: int,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID], str]:
    """Seed org/team(+members)/project/succeeded-scan.

    Returns ``(team_id, project_id, scan_id, member_user_ids, project_name)``.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from tests._helpers import (
        make_membership,
        make_organization,
        make_project,
        make_scan,
        make_team,
        make_user,
    )

    async def _build() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID], str]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            members = []
            for _ in range(n_members):
                user = await make_user(s)
                await make_membership(s, user=user, team=team, role="developer")
                members.append(user.id)
            project = await make_project(s, team=team, git_url=None)
            scan = await make_scan(s, project=project, status="succeeded")
            out = (team.id, project.id, scan.id, members, project.name)
        await engine.dispose()
        return out

    return asyncio.run(_build())


def _seed_finding(
    session: Session,
    *,
    scan_id: uuid.UUID,
    severity: str = "critical",
    status: str = "new",
    first_detected_at: datetime | None = None,
) -> uuid.UUID:
    """One component_version + vulnerability + finding tied to ``scan_id``."""
    suffix = uuid.uuid4().hex[:10]
    purl = f"pkg:npm/sla-sweep-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"sla-sweep-{suffix}")
    session.add(component)
    session.flush()
    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    session.flush()
    vuln = Vulnerability(
        external_id=f"CVE-2099-SLA-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"sweep fixture {suffix}",
    )
    session.add(vuln)
    session.flush()
    finding = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv.id,
        vulnerability_id=vuln.id,
        status=status,
        analysis_state=status,
        first_detected_at=first_detected_at,
    )
    session.add(finding)
    session.commit()
    return finding.id


def _notifications_for(
    session: Session, user_ids: list[uuid.UUID]
) -> list[Notification]:
    session.expire_all()
    return list(
        session.execute(
            select(Notification).where(
                Notification.user_id.in_(user_ids),
                Notification.kind == "vuln_sla_breach",
            )
        ).scalars()
    )


def _first_detected_for_breach(severity_days: int, *, hours_overdue: int) -> datetime:
    """A first_detected that puts the due date ``hours_overdue`` hours ago —
    inside the trailing 24h window for 0 < hours_overdue < 24."""
    return datetime.now(UTC) - timedelta(days=severity_days, hours=hours_overdue)


# ---------------------------------------------------------------------------
# Happy path — two members, one aggregated in-app row each
# ---------------------------------------------------------------------------


def test_sweep_fans_out_one_in_app_row_per_member(
    sync_session: Session, _inline_notify: None
) -> None:
    _team_id, project_id, scan_id, members, project_name = _seed_team_project_scan(2)
    # critical (7d window), due crossed 2h ago → inside the 24h window.
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="critical",
        first_detected_at=_first_detected_for_breach(7, hours_overdue=2),
    )
    # second breached finding on the SAME project — must aggregate, not
    # produce a second notification.
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="high",
        first_detected_at=_first_detected_for_breach(30, hours_overdue=3),
    )

    summary = _run_sweep()

    assert summary["skipped"] is False
    assert summary["findings_crossed"] >= 2
    assert summary["notifications_enqueued"] >= 2

    rows = _notifications_for(sync_session, members)
    assert len(rows) == 2, "exactly ONE aggregated row per member (no per-finding spam)"
    assert {r.user_id for r in rows} == set(members)
    for row in rows:
        assert row.kind == "vuln_sla_breach"
        assert row.title == f"SLA breach: 2 findings overdue in {project_name}"
        assert "critical 1, high 1" in row.body
        assert row.link == f"/projects/{project_id}?tab=vulnerabilities&sla=overdue"
        assert row.target_table == "projects"
        assert row.target_id == project_id
        assert row.read_at is None


def test_sweep_never_notifies_other_teams(
    sync_session: Session, _inline_notify: None
) -> None:
    """Tenant-isolation guard (security review, low severity, X1 step 2): a breach in
    team A's project fans out to team A's members ONLY. Team B — a different
    team with its own project, no membership overlap — must receive ZERO
    rows: the fact that a vulnerability exists in another team's project is
    itself information that must not leak. This pins the fan-out boundary so
    a future "org-wide visibility" style expansion has to change this test
    consciously."""
    _team_a, project_a, scan_a, members_a, _name_a = _seed_team_project_scan(2)
    _team_b, _project_b, _scan_b, members_b, _name_b = _seed_team_project_scan(2)
    _seed_finding(
        sync_session,
        scan_id=scan_a,
        severity="critical",
        first_detected_at=_first_detected_for_breach(7, hours_overdue=2),
    )

    _run_sweep()

    rows_a = _notifications_for(sync_session, members_a)
    assert {r.user_id for r in rows_a} == set(members_a)
    rows_b = _notifications_for(sync_session, members_b)
    assert rows_b == [], "cross-team members must never receive SLA breach rows"


# ---------------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------------


def test_sweep_ignores_breach_outside_window(
    sync_session: Session, _inline_notify: None
) -> None:
    """An aged breach (due 3 days ago) was another tick's alert — silent now."""
    _team_id, _project_id, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="critical",
        first_detected_at=datetime.now(UTC) - timedelta(days=10),  # due 3d ago
    )

    _run_sweep()

    assert _notifications_for(sync_session, members) == []


def test_sweep_ignores_closed_status(
    sync_session: Session, _inline_notify: None
) -> None:
    _team_id, _project_id, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="critical",
        status="fixed",  # closed per the gate vocabulary
        first_detected_at=_first_detected_for_breach(7, hours_overdue=2),
    )

    _run_sweep()

    assert _notifications_for(sync_session, members) == []


def test_sweep_toggle_off_skips_everything(
    sync_session: Session, _inline_notify: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _team_id, _project_id, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="critical",
        first_detected_at=_first_detected_for_breach(7, hours_overdue=2),
    )
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "false")

    summary = _run_sweep()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"
    assert _notifications_for(sync_session, members) == []


def test_sweep_respects_in_app_muted_member(
    sync_session: Session, _inline_notify: None
) -> None:
    """A member whose prefs row has ``in_app_enabled=False`` gets NO row while
    teammates still do — the per-user gate lives in ``_apply_prefs_filter``."""
    _team_id, _project_id, scan_id, members, _name = _seed_team_project_scan(2)
    muted, listening = members
    sync_session.add(
        NotificationPreferences(
            user_id=muted,
            email_enabled=False,
            slack_enabled=False,
            teams_enabled=False,
            in_app_enabled=False,
        )
    )
    sync_session.commit()
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="critical",
        first_detected_at=_first_detected_for_breach(7, hours_overdue=2),
    )

    _run_sweep()

    assert _notifications_for(sync_session, [muted]) == []
    rows = _notifications_for(sync_session, [listening])
    assert len(rows) == 1


def test_sweep_only_reads_latest_succeeded_scan(
    sync_session: Session, _inline_notify: None
) -> None:
    """A breach on an OLDER scan is superseded state — only the project's
    latest succeeded scan feeds the sweep (same anchor every current-state
    reader uses)."""
    _team_id, project_id, old_scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=old_scan_id,
        severity="critical",
        first_detected_at=_first_detected_for_breach(7, hours_overdue=2),
    )

    # A NEWER succeeded scan with no findings supersedes the breached one.
    import asyncio

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from models import Project
    from tests._helpers import make_scan

    async def _newer_scan() -> None:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one()
            await make_scan(
                s,
                project=project,
                status="succeeded",
                created_at=datetime.now(UTC) + timedelta(seconds=5),
            )
        await engine.dispose()

    asyncio.run(_newer_scan())

    _run_sweep()

    assert _notifications_for(sync_session, members) == []
