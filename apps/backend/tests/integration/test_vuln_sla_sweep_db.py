"""
Integration tests for the daily SLA-breach sweep (real Postgres + Redis), X1
step 2 and #569.

Drives ``tasks.vuln_sla_sweep._run_sweep`` end to end against seeded rows:
the latest-succeeded-scan aggregate, the window selection (in SQL since
#569), the team membership fan-out, the Redis window anchor, and the ACTUAL
in-app row creation through ``tasks.notify`` (``send_notification_task.delay``
is monkeypatched to a synchronous ``.apply`` so the notify task's prefs filter
+ insert run in-line without a worker; ``channels=[]`` means no outbound
egress exists on this path at all).

Every run here passes an explicit ``now`` so a deadline can sit exactly on a
window bound, and the lifecycle tests step through days without waiting.

Negative controls pin the selection contract: outside-window breaches,
closed statuses, archived projects, inactive members, the master toggle, and
a member who muted in-app.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import (
    Component,
    ComponentVersion,
    Notification,
    NotificationPreferences,
    Organization,
    Team,
    Vulnerability,
    VulnerabilityFinding,
)
from tasks.vuln_sla_sweep import _ANCHOR_KEY, _run_sweep, vuln_sla_sweep
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration

# Whole seconds, so ISO round trips through Redis are exact.
_T = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _redis() -> Any:
    import redis as redis_lib

    from core.config import redis_url

    return redis_lib.Redis.from_url(redis_url(), decode_responses=True)


@pytest.fixture(autouse=True)
def _fresh_anchor() -> Iterator[None]:
    """Each test starts with no anchor, i.e. the trailing-24h window, and
    leaves none behind for the next one."""
    client = _redis()
    client.delete(_ANCHOR_KEY)
    yield
    client.delete(_ANCHOR_KEY)


@pytest.fixture
def _inline_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route ``send_notification_task.delay`` through a synchronous ``.apply``
    so the notify task body (prefs filter -> in-app INSERT) runs in-line in
    the test process. ``channels=[]`` short-circuits before any dispatcher /
    egress code, so this exercises exactly the production in-app path."""
    import tasks.notify as notify_module

    def _inline(*args: Any, **kwargs: Any) -> None:
        notify_module.send_notification_task.apply(args=args, kwargs=kwargs)

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _inline)


def _seed_team_project_scan(
    n_members: int,
    *,
    archived: bool = False,
    inactive_members: int = 0,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID], str]:
    """Seed org/team(+members)/project/succeeded-scan.

    Returns ``(team_id, project_id, scan_id, member_user_ids, project_name)``.
    ``member_user_ids`` lists the active members first, then the
    ``inactive_members`` deactivated ones.
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
            for i in range(n_members + inactive_members):
                user = await make_user(s, is_active=i < n_members)
                await make_membership(s, user=user, team=team, role="developer")
                members.append(user.id)
            project = await make_project(s, team=team, git_url=None, archived=archived)
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
    due_on: date | None = None,
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
        due_on=due_on,
    )
    session.add(finding)
    session.commit()
    return finding.id


_SLA_DAYS = {"critical": 7, "high": 30, "medium": 90, "low": 180}


def _detected_for_due(due: datetime, severity: str = "critical") -> datetime:
    """The first_detected_at that puts the policy due date at ``due``."""
    return due - timedelta(days=_SLA_DAYS[severity])


def _notifications_for(
    session: Session, user_ids: list[uuid.UUID]
) -> list[Notification]:
    session.expire_all()
    return list(
        session.execute(
            select(Notification)
            .where(
                Notification.user_id.in_(user_ids),
                Notification.kind == "vuln_sla_breach",
            )
            .order_by(Notification.created_at)
        ).scalars()
    )


def _one_breach_project(
    session: Session, *, due: datetime, **seed: Any
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A one-member project with a single critical finding due at ``due``."""
    _team, project_id, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        session, scan_id=scan_id, first_detected_at=_detected_for_due(due), **seed
    )
    return project_id, members


# ---------------------------------------------------------------------------
# Happy path: two members, one aggregated in-app row each
# ---------------------------------------------------------------------------


def test_sweep_fans_out_one_in_app_row_per_member(
    sync_session: Session, _inline_notify: None
) -> None:
    _team_id, project_id, scan_id, members, project_name = _seed_team_project_scan(2)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="critical",
        first_detected_at=_detected_for_due(_T - timedelta(hours=2)),
    )
    # second breached finding on the SAME project: must aggregate, not
    # produce a second notification.
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="high",
        first_detected_at=_detected_for_due(_T - timedelta(hours=3), "high"),
    )

    summary = _run_sweep(now=_T)

    assert summary["skipped"] is False
    assert summary["findings_crossed"] >= 2
    assert summary["notifications_enqueued"] >= 2

    rows = _notifications_for(sync_session, members)
    assert len(rows) == 2, "exactly ONE aggregated row per member (no per-finding spam)"
    assert {r.user_id for r in rows} == set(members)
    since = (_T - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M UTC")
    for row in rows:
        assert row.kind == "vuln_sla_breach"
        assert row.title == f"SLA breach: 2 findings overdue in {project_name}"
        assert row.body == (
            f"2 open findings crossed the remediation SLA since {since} "
            "(critical 1, high 1). Review the overdue list and triage or remediate."
        )
        assert row.link == f"/projects/{project_id}?tab=vulnerabilities&sla=overdue"
        assert row.target_table == "projects"
        assert row.target_id == project_id
        assert row.read_at is None


def test_sweep_never_notifies_other_teams(
    sync_session: Session, _inline_notify: None
) -> None:
    """Tenant-isolation guard (security review, low severity, X1 step 2): a
    breach in team A's project fans out to team A's members ONLY. Team B, a
    different team with its own project and no membership overlap, must
    receive ZERO rows: the fact that a vulnerability exists in another team's
    project is itself information that must not leak."""
    _team_a, _project_a, scan_a, members_a, _name_a = _seed_team_project_scan(2)
    _team_b, _project_b, _scan_b, members_b, _name_b = _seed_team_project_scan(2)
    _seed_finding(
        sync_session,
        scan_id=scan_a,
        first_detected_at=_detected_for_due(_T - timedelta(hours=2)),
    )

    _run_sweep(now=_T)

    rows_a = _notifications_for(sync_session, members_a)
    assert {r.user_id for r in rows_a} == set(members_a)
    rows_b = _notifications_for(sync_session, members_b)
    assert rows_b == [], "cross-team members must never receive SLA breach rows"


# ---------------------------------------------------------------------------
# Window bounds, evaluated in SQL: window_start <= due < now
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("due_offset", "alerted"),
    [
        pytest.param(-timedelta(hours=1), True, id="inside"),
        pytest.param(-timedelta(hours=24), True, id="exactly-window-start"),
        pytest.param(timedelta(0), False, id="exactly-now"),
        pytest.param(-timedelta(hours=24, seconds=1), False, id="before-window"),
        pytest.param(timedelta(hours=1), False, id="future"),
        pytest.param(-timedelta(days=3), False, id="aged-breach"),
    ],
)
def test_window_bounds(
    sync_session: Session,
    _inline_notify: None,
    due_offset: timedelta,
    alerted: bool,
) -> None:
    """A due date at exactly the window start belongs to this tick; one at
    exactly ``now`` has not crossed and belongs to the next. Without the
    open upper bound a due date landing on a tick would alert twice."""
    _project, members = _one_breach_project(sync_session, due=_T + due_offset)

    _run_sweep(now=_T)

    assert len(_notifications_for(sync_session, members)) == (1 if alerted else 0)


# ---------------------------------------------------------------------------
# Status, severity and written deadlines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "alerted"),
    [
        ("new", True),
        ("analyzing", True),
        ("exploitable", True),
        # Deliberately OPEN: the gate and upgrade engine count it as work.
        ("suppressed", True),
        ("not_affected", False),
        ("fixed", False),
        ("false_positive", False),
    ],
)
def test_status_vocabulary(
    sync_session: Session, _inline_notify: None, status: str, alerted: bool
) -> None:
    _project, members = _one_breach_project(
        sync_session, due=_T - timedelta(hours=2), status=status
    )

    _run_sweep(now=_T)

    assert len(_notifications_for(sync_session, members)) == (1 if alerted else 0)


@pytest.mark.parametrize("severity", ["info", "unknown"])
def test_no_sla_severity_without_a_written_date_never_alerts(
    sync_session: Session, _inline_notify: None, severity: str
) -> None:
    _team, _project, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity=severity,
        first_detected_at=_T - timedelta(days=400),
    )

    _run_sweep(now=_T)

    assert _notifications_for(sync_session, members) == []


def test_a_written_deadline_is_swept_even_without_an_sla_window(
    sync_session: Session, _inline_notify: None
) -> None:
    """ER28a: a date written on an info finding is its deadline. It expires
    at the END of the named day (UTC organization), so "yesterday" crossed
    at midnight, inside the window."""
    now = datetime.combine(_T.date(), datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=2, minutes=45
    )
    _team, _project, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="info",
        first_detected_at=now - timedelta(days=400),
        due_on=(now - timedelta(days=1)).date(),
    )

    _run_sweep(now=now)

    rows = _notifications_for(sync_session, members)
    assert len(rows) == 1
    assert "(info 1)" in rows[0].body


def test_an_earlier_written_date_beats_the_policy(
    sync_session: Session, _inline_notify: None
) -> None:
    """Critical by policy is due in 7 days; a date written for yesterday is
    earlier, so the sweep sees yesterday (the list's precedence rule)."""
    now = datetime.combine(_T.date(), datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=2, minutes=45
    )
    _team, _project, scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        first_detected_at=now,
        due_on=(now - timedelta(days=1)).date(),
    )

    _run_sweep(now=now)

    assert len(_notifications_for(sync_session, members)) == 1


def test_a_written_date_expires_in_the_organization_timezone(
    sync_session: Session, _inline_notify: None
) -> None:
    """ER55 through the sweep's own join: in Asia/Seoul a date of D expires
    at 15:00 UTC on D. A window of 14:00 to 16:00 UTC on D catches it; the
    same finding in a UTC organization expires at midnight and is silent."""
    day = _T.date() - timedelta(days=2)
    now = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=16)
    _redis().set(_ANCHOR_KEY, (now - timedelta(hours=2)).isoformat())

    team_seoul, _p1, scan_seoul, members_seoul, _n1 = _seed_team_project_scan(1)
    _team_utc, _p2, scan_utc, members_utc, _n2 = _seed_team_project_scan(1)
    org_id = sync_session.execute(
        select(Team.organization_id).where(Team.id == team_seoul)
    ).scalar_one()
    sync_session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(timezone="Asia/Seoul")
    )
    sync_session.commit()
    for scan_id in (scan_seoul, scan_utc):
        _seed_finding(
            sync_session,
            scan_id=scan_id,
            severity="info",
            first_detected_at=now - timedelta(days=400),
            due_on=day,
        )

    _run_sweep(now=now)

    assert len(_notifications_for(sync_session, members_seoul)) == 1
    assert _notifications_for(sync_session, members_utc) == []


def test_env_override_moves_the_due_date(
    sync_session: Session, _inline_notify: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SQL window reads ``vuln_sla_days`` at call time (rule #11)."""
    _team, _project, scan_id, members, _name = _seed_team_project_scan(1)
    # Detected 10d + 1h ago: under a 10-day high window it crossed 1h ago.
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="high",
        first_detected_at=_T - timedelta(days=10, hours=1),
    )

    _run_sweep(now=_T)
    assert _notifications_for(sync_session, members) == []

    monkeypatch.setenv("VULN_SLA_DAYS_HIGH", "10")
    _redis().delete(_ANCHOR_KEY)
    _run_sweep(now=_T)
    assert len(_notifications_for(sync_session, members)) == 1


def test_aggregation_is_per_project_and_severity(
    sync_session: Session, _inline_notify: None
) -> None:
    _team, _project, scan_id, members, _name = _seed_team_project_scan(1)
    for sev, hours in (("critical", 2), ("critical", 3), ("low", 4)):
        _seed_finding(
            sync_session,
            scan_id=scan_id,
            severity=sev,
            first_detected_at=_detected_for_due(_T - timedelta(hours=hours), sev),
        )
    # Noise on the same project: aged, closed.
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        first_detected_at=_detected_for_due(_T - timedelta(days=3)),
    )
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        status="fixed",
        first_detected_at=_detected_for_due(_T - timedelta(hours=1)),
    )

    _run_sweep(now=_T)

    rows = _notifications_for(sync_session, members)
    assert len(rows) == 1
    assert "3 open findings" in rows[0].body
    assert "(critical 2, low 1)" in rows[0].body


# ---------------------------------------------------------------------------
# Who is told, and about what (#569)
# ---------------------------------------------------------------------------


def test_sweep_skips_archived_projects(
    sync_session: Session, _inline_notify: None
) -> None:
    _team, _project, scan_id, members, _name = _seed_team_project_scan(
        1, archived=True
    )
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        first_detected_at=_detected_for_due(_T - timedelta(hours=2)),
    )

    summary = _run_sweep(now=_T)

    assert _notifications_for(sync_session, members) == []
    assert summary["skipped"] is False


def test_sweep_skips_inactive_members(
    sync_session: Session, _inline_notify: None
) -> None:
    _team, _project, scan_id, members, _name = _seed_team_project_scan(
        1, inactive_members=1
    )
    active, inactive = members
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        first_detected_at=_detected_for_due(_T - timedelta(hours=2)),
    )

    _run_sweep(now=_T)

    assert len(_notifications_for(sync_session, [active])) == 1
    assert _notifications_for(sync_session, [inactive]) == []


def test_sweep_toggle_off_skips_everything(
    sync_session: Session, _inline_notify: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project, members = _one_breach_project(sync_session, due=_T - timedelta(hours=2))
    monkeypatch.setenv("VULN_SLA_ALERTS_ENABLED", "false")

    summary = _run_sweep(now=_T)

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"
    assert _notifications_for(sync_session, members) == []
    assert _redis().get(_ANCHOR_KEY) is None


def test_sweep_respects_in_app_muted_member(
    sync_session: Session, _inline_notify: None
) -> None:
    """A member whose prefs row has ``in_app_enabled=False`` gets NO row while
    teammates still do; the per-user gate lives in ``_apply_prefs_filter``."""
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
        first_detected_at=_detected_for_due(_T - timedelta(hours=2)),
    )

    _run_sweep(now=_T)

    assert _notifications_for(sync_session, [muted]) == []
    assert len(_notifications_for(sync_session, [listening])) == 1


def test_sweep_only_reads_latest_succeeded_scan(
    sync_session: Session, _inline_notify: None
) -> None:
    """A breach on an OLDER scan is superseded state: only the project's
    latest succeeded scan feeds the sweep (same anchor every current-state
    reader uses)."""
    _team_id, project_id, old_scan_id, members, _name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=old_scan_id,
        first_detected_at=_detected_for_due(_T - timedelta(hours=2)),
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

    _run_sweep(now=_T)

    assert _notifications_for(sync_session, members) == []


# ---------------------------------------------------------------------------
# Lifecycle: the window anchor across runs (hardening rule #5)
# ---------------------------------------------------------------------------


def test_missed_day_is_caught_up_by_the_next_run_exactly_once(
    sync_session: Session, _inline_notify: None
) -> None:
    """Day 0 runs, day 1 is missed, day 2 runs, day 3 runs.

    ``early`` crosses before day 0 and is told on day 0. ``missed`` crosses
    10h after day 0, inside the span the missed day-1 tick owned and outside
    day 2's trailing 24h: before #569 nobody was ever told. Day 2 must tell
    exactly that one, and day 3 nothing.
    """
    day0 = _T - timedelta(days=3)
    _team, _project, scan_id, members, project_name = _seed_team_project_scan(1)
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        first_detected_at=_detected_for_due(day0 - timedelta(hours=2)),
    )
    _seed_finding(
        sync_session,
        scan_id=scan_id,
        severity="high",
        first_detected_at=_detected_for_due(day0 + timedelta(hours=10), "high"),
    )

    _run_sweep(now=day0)
    rows = _notifications_for(sync_session, members)
    assert [r.body.split(" (")[1] for r in rows] == [
        "critical 1). Review the overdue list and triage or remediate."
    ]
    assert _redis().get(_ANCHOR_KEY) == day0.isoformat()

    # Day 1: no run.

    day2 = day0 + timedelta(days=2)
    summary = _run_sweep(now=day2)
    assert summary["window_start"] == day0.isoformat()
    rows = _notifications_for(sync_session, members)
    assert len(rows) == 2
    assert rows[1].title == f"SLA breach: 1 finding overdue in {project_name}"
    since = day0.strftime("%Y-%m-%d %H:%M UTC")
    assert f"since {since} (high 1)" in rows[1].body

    day3 = day0 + timedelta(days=3)
    _run_sweep(now=day3)
    assert len(_notifications_for(sync_session, members)) == 2


def test_a_second_run_on_the_same_day_does_not_repeat_alerts(
    sync_session: Session, _inline_notify: None
) -> None:
    """An operator re-running the task by hand, or beat jitter putting two
    ticks close together, must not alert the same crossing twice."""
    _project, members = _one_breach_project(sync_session, due=_T - timedelta(hours=2))

    _run_sweep(now=_T)
    _run_sweep(now=_T + timedelta(hours=1))

    assert len(_notifications_for(sync_session, members)) == 1


def test_a_failed_run_leaves_its_span_to_the_next_one(
    sync_session: Session, _inline_notify: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Day 0 runs; day 1 fails inside the read (through the Celery entry
    point, which swallows the error); day 2 still tells about the crossing
    the failed run owned."""
    import tasks.vuln_sla_sweep as sweep_module

    day0 = _T - timedelta(days=3)
    _project, members = _one_breach_project(
        sync_session, due=day0 + timedelta(hours=10)
    )
    _run_sweep(now=day0)
    assert _notifications_for(sync_session, members) == []

    real = sweep_module._breached_counts

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("database went away")

    monkeypatch.setattr(sweep_module, "_breached_counts", _boom)
    summary = vuln_sla_sweep.run()
    assert summary["skipped_reason"] == "unexpected:RuntimeError"
    assert _redis().get(_ANCHOR_KEY) == day0.isoformat()
    monkeypatch.setattr(sweep_module, "_breached_counts", real)

    _run_sweep(now=day0 + timedelta(days=2))
    assert len(_notifications_for(sync_session, members)) == 1


def test_catch_up_reaches_back_a_week_and_no_further(
    sync_session: Session, _inline_notify: None
) -> None:
    """An anchor a month old (sweep re-enabled after a long pause) is clamped
    to seven days: a crossing six days ago is told, one ten days ago is not."""
    _redis().set(_ANCHOR_KEY, (_T - timedelta(days=30)).isoformat())
    _p1, within = _one_breach_project(sync_session, due=_T - timedelta(days=6))
    _p2, beyond = _one_breach_project(sync_session, due=_T - timedelta(days=10))

    summary = _run_sweep(now=_T)

    assert summary["window_start"] == (_T - timedelta(days=7)).isoformat()
    assert len(_notifications_for(sync_session, within)) == 1
    assert _notifications_for(sync_session, beyond) == []
    assert _redis().get(_ANCHOR_KEY) == _T.isoformat()
