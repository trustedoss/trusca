"""
Unit tests for BUG-010 — conditional-license auto-approval in the source scan
pipeline.

Two layers are covered here:

  1. ``tasks.scan_source._conditional_component_ids`` — the selector that decides
     which components in a scan are approval targets (effective license category
     == conditional, "most restrictive wins", forbidden excluded).
  2. ``services.component_approval_service.auto_create_pending_approvals`` — the
     sync, system-context (actor-less) approval creator: NULL requester,
     idempotent on re-run, per-row SAVEPOINT isolation.

Both touch real rows (the selector is a GROUP BY / HAVING query, and the
idempotency guard rides on a partial unique index), so these are DB-backed and
carry the ``integration`` marker — they ``pytest.skip`` when DATABASE_URL is
unset. The seed graph is built directly through a sync session (no event loop)
mirroring the ``sync_session`` fixture in
``tests/integration/scan/test_scan_source_pipeline_mock.py``.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import (
    Component,
    ComponentApproval,
    ComponentVersion,
    License,
    LicenseFinding,
    Organization,
    Project,
    Scan,
    Team,
)
from models.component_approval import ApprovalStatus
from services.component_approval_service import auto_create_pending_approvals
from tasks.scan_source import _CATEGORY_RANK, _CONDITIONAL_RANK, _conditional_component_ids

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Pure-unit guard — the literal rank in scan_source must track _CATEGORY_RANK
# ---------------------------------------------------------------------------


def test_conditional_rank_literal_matches_category_rank() -> None:
    """``_CONDITIONAL_RANK`` is a hand-typed literal (forward-reference dodge);
    pin it to the canonical ``_CATEGORY_RANK`` so a future re-rank can't drift.
    """
    assert _CONDITIONAL_RANK == _CATEGORY_RANK["conditional"]


# ---------------------------------------------------------------------------
# DB harness
# ---------------------------------------------------------------------------

_ALEMBIC_RAN = False


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    import os

    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skip auto-approval DB tests")
    global _ALEMBIC_RAN
    if _ALEMBIC_RAN:
        return
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed:\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    _ALEMBIC_RAN = True


@pytest.fixture
def session() -> Iterator[Session]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers (sync)
# ---------------------------------------------------------------------------


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _seed_project(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an org → team → project; return (project_id, team_id)."""
    suffix = _suffix()
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}")
    session.add(org)
    session.flush()
    team = Team(organization_id=org.id, name=f"Team {suffix}", slug=f"team-{suffix}")
    session.add(team)
    session.flush()
    project = Project(
        team_id=team.id,
        name=f"Project {suffix}",
        slug=f"project-{suffix}",
        visibility="team",
    )
    session.add(project)
    session.flush()
    return project.id, team.id


def _seed_scan(session: Session, project_id: uuid.UUID) -> uuid.UUID:
    # status='succeeded' (not queued/running) so multiple scans can coexist on
    # one project — the partial unique index ``ix_scans_project_active`` only
    # gates the active set, and the selector reads only LicenseFinding.scan_id.
    scan = Scan(
        project_id=project_id,
        kind="source",
        status="succeeded",
        progress_percent=100,
        scan_metadata={},
    )
    session.add(scan)
    session.flush()
    return scan.id


def _make_license(session: Session, category: str) -> License:
    """Create a License row with a per-call-unique spdx_id.

    The selector under test reads ``License.category`` directly, so the spdx_id
    value is irrelevant to the assertions — we namespace it (``LicenseRef-...``)
    so these tests never collide on the global ``uq_licenses_spdx_id`` index nor
    pollute the shared ``licenses`` table for sibling suites.
    """
    spdx_id = f"LicenseRef-test-{category}-{_suffix()}"
    lic = License(spdx_id=spdx_id, name=spdx_id, category=category)
    session.add(lic)
    session.flush()
    return lic


def _seed_component_with_licenses(
    session: Session,
    *,
    scan_id: uuid.UUID,
    licenses: list[tuple[str, str]],
) -> uuid.UUID:
    """Create a component + version + a license finding per (label, category).

    ``licenses`` is a list of (label, category) tuples; only the category drives
    the selector, the label is documentation. Returns the component id. Every
    seeded row (component purl, license spdx_id) is suffix-namespaced so these
    tests do not pollute the shared DB for sibling suites.
    """
    suffix = _suffix()
    purl = f"pkg:npm/comp-autoapprove-{suffix}"
    component = Component(purl=purl, name=f"comp-{suffix}", package_type="npm")
    session.add(component)
    session.flush()
    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    session.flush()
    for _label, category in licenses:
        lic = _make_license(session, category)
        session.add(
            LicenseFinding(
                scan_id=scan_id,
                component_version_id=cv.id,
                license_id=lic.id,
                kind="declared",
                source_path=None,
                raw_data={"source": "test"},
            )
        )
    session.flush()
    return component.id


def _open_approval_count(
    session: Session, *, component_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    return len(
        session.execute(
            select(ComponentApproval.id).where(
                ComponentApproval.component_id == component_id,
                ComponentApproval.project_id == project_id,
                ComponentApproval.status.in_(
                    [ApprovalStatus.pending, ApprovalStatus.under_review]
                ),
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# _conditional_component_ids — selection logic
# ---------------------------------------------------------------------------


def test_selector_picks_conditional_only(session: Session) -> None:
    """allowed → excluded, conditional → included, forbidden → excluded."""
    project_id, _team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)

    allowed_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("MIT", "allowed")]
    )
    conditional_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("LGPL-3.0-only", "conditional")]
    )
    forbidden_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("GPL-3.0-only", "forbidden")]
    )
    session.commit()

    selected = set(_conditional_component_ids(session, scan_uuid=scan_id))
    assert conditional_id in selected
    assert allowed_id not in selected
    assert forbidden_id not in selected


def test_selector_most_restrictive_wins(session: Session) -> None:
    """A component with allowed + conditional licenses is conditional; a
    component with conditional + forbidden is forbidden (excluded — build-gate's
    job, not the approval queue).
    """
    project_id, _team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)

    # allowed + conditional → effective conditional → selected
    mixed_conditional = _seed_component_with_licenses(
        session,
        scan_id=scan_id,
        licenses=[("MIT", "allowed"), ("MPL-2.0", "conditional")],
    )
    # conditional + forbidden → effective forbidden → NOT selected
    mixed_forbidden = _seed_component_with_licenses(
        session,
        scan_id=scan_id,
        licenses=[("EPL-2.0", "conditional"), ("AGPL-3.0-only", "forbidden")],
    )
    session.commit()

    selected = set(_conditional_component_ids(session, scan_uuid=scan_id))
    assert mixed_conditional in selected
    assert mixed_forbidden not in selected


def test_selector_is_scoped_to_scan(session: Session) -> None:
    """A conditional finding from a *different* scan does not leak in."""
    project_id, _team_id = _seed_project(session)
    scan_a = _seed_scan(session, project_id)
    scan_b = _seed_scan(session, project_id)

    comp_b = _seed_component_with_licenses(
        session, scan_id=scan_b, licenses=[("LGPL-3.0-only", "conditional")]
    )
    session.commit()

    selected = set(_conditional_component_ids(session, scan_uuid=scan_a))
    assert comp_b not in selected
    assert selected == set()


# ---------------------------------------------------------------------------
# auto_create_pending_approvals — system-context creation
# ---------------------------------------------------------------------------


def test_creates_pending_with_null_actor(session: Session) -> None:
    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    comp_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("LGPL-3.0-only", "conditional")]
    )
    session.commit()

    created = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_id],
        scan_id=scan_id,
    )
    session.commit()

    assert created == 1
    row = session.execute(
        select(ComponentApproval).where(
            ComponentApproval.component_id == comp_id,
            ComponentApproval.project_id == project_id,
        )
    ).scalar_one()
    assert row.status == ApprovalStatus.pending
    assert row.requested_by_user_id is None  # system-created
    assert row.team_id == team_id
    assert row.version == 1


def test_multiple_conditional_components_each_get_one(session: Session) -> None:
    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    ids = [
        _seed_component_with_licenses(
            session, scan_id=scan_id, licenses=[("MPL-2.0", "conditional")]
        )
        for _ in range(3)
    ]
    session.commit()

    created = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=ids,
        scan_id=scan_id,
    )
    session.commit()

    assert created == 3
    for comp_id in ids:
        assert _open_approval_count(
            session, component_id=comp_id, project_id=project_id
        ) == 1


def test_idempotent_double_call_no_duplicate(session: Session) -> None:
    """Calling twice with the same (scan, component) keeps exactly one open
    approval — the re-run safety BUG-010 relies on.
    """
    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    comp_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("CDDL-1.0", "conditional")]
    )
    session.commit()

    first = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_id],
        scan_id=scan_id,
    )
    session.commit()
    second = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_id],
        scan_id=scan_id,
    )
    session.commit()

    assert first == 1
    assert second == 0  # preflight skip — nothing new created
    assert _open_approval_count(
        session, component_id=comp_id, project_id=project_id
    ) == 1


def test_skips_when_under_review_already_open(session: Session) -> None:
    """An existing under_review approval blocks a new one (open = pending OR
    under_review).
    """
    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    comp_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("LGPL-2.1-only", "conditional")]
    )
    # Pre-seed an under_review approval for this component+project.
    session.add(
        ComponentApproval(
            component_id=comp_id,
            project_id=project_id,
            team_id=team_id,
            requested_by_user_id=None,
            status=ApprovalStatus.under_review,
            version=2,
        )
    )
    session.commit()

    created = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_id],
        scan_id=scan_id,
    )
    session.commit()

    assert created == 0
    assert _open_approval_count(
        session, component_id=comp_id, project_id=project_id
    ) == 1


def test_duplicate_ids_in_one_call_create_one(session: Session) -> None:
    """The same component id passed twice in one call inserts once (de-dupe)."""
    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    comp_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("MPL-2.0", "conditional")]
    )
    session.commit()

    created = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_id, comp_id],
        scan_id=scan_id,
    )
    session.commit()

    assert created == 1
    assert _open_approval_count(
        session, component_id=comp_id, project_id=project_id
    ) == 1


def test_integrity_error_race_caught_by_savepoint(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU race: a concurrent writer opens an approval *between* our preflight
    and our flush. The preflight misses it, the partial unique index fires on
    flush, and the per-row SAVEPOINT swallows the IntegrityError so the loop
    continues. We simulate the missed preflight by forcing it to return no rows
    while a real open approval exists in the DB.
    """
    import services.component_approval_service as svc

    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    comp_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("MPL-2.0", "conditional")]
    )
    # A real open approval already exists — the DB unique index will reject a
    # second one on flush.
    session.add(
        ComponentApproval(
            component_id=comp_id,
            project_id=project_id,
            team_id=team_id,
            requested_by_user_id=None,
            status=ApprovalStatus.pending,
            version=1,
        )
    )
    session.commit()

    # Blind the preflight: make its SELECT match an id that cannot exist, so the
    # helper believes no open approval is present and proceeds to INSERT — which
    # then trips the unique index on flush (the race we want to cover).
    real_select = svc.select  # type: ignore[attr-defined]
    state = {"first": True}

    def _blinded_select(*args, **kwargs):  # type: ignore[no-untyped-def]
        stmt = real_select(*args, **kwargs)
        if state["first"]:
            state["first"] = False
            return stmt.where(svc.ComponentApproval.id == uuid.uuid4())
        return stmt

    monkeypatch.setattr(svc, "select", _blinded_select)

    created = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_id],
        scan_id=scan_id,
    )
    session.commit()

    # The insert was rejected by the unique index and swallowed by the SAVEPOINT:
    # nothing new created, the original lone approval still stands.
    assert created == 0
    assert _open_approval_count(
        session, component_id=comp_id, project_id=project_id
    ) == 1


# ---------------------------------------------------------------------------
# _auto_create_conditional_approvals — best-effort stage wrapper
# ---------------------------------------------------------------------------


def test_stage_creates_approval_for_conditional_component(session: Session) -> None:
    """End-to-end through the stage wrapper (its own sync_session_scope): a
    conditional component is enrolled, an allowed one is not.
    """
    from tasks.scan_source import _auto_create_conditional_approvals

    project_id, _team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    cond_id = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("LGPL-3.0-only", "conditional")]
    )
    _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("MIT", "allowed")]
    )
    session.commit()

    _auto_create_conditional_approvals(scan_uuid=scan_id, project_id=project_id)

    session.expire_all()
    approvals = (
        session.execute(
            select(ComponentApproval).where(
                ComponentApproval.project_id == project_id
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 1
    assert approvals[0].component_id == cond_id
    assert approvals[0].requested_by_user_id is None


def test_stage_missing_project_is_noop(session: Session) -> None:
    """A project deleted mid-scan: the stage logs + returns without raising."""
    from tasks.scan_source import _auto_create_conditional_approvals

    # No project/scan seeded — a random project id has no row.
    _auto_create_conditional_approvals(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4()
    )  # must not raise


def test_stage_swallows_errors(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort: an unexpected error inside the stage NEVER propagates (it
    would otherwise sink an otherwise-succeeded scan).
    """
    import tasks.scan_source as ss

    project_id, _team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    session.commit()

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated selector failure")

    monkeypatch.setattr(ss, "_conditional_component_ids", _boom)

    # Must NOT raise — best-effort swallow. (The function returns None; we only
    # assert it does not propagate the RuntimeError.)
    ss._auto_create_conditional_approvals(scan_uuid=scan_id, project_id=project_id)


def test_savepoint_isolation_one_open_does_not_block_others(session: Session) -> None:
    """One component already-open must not poison the inserts for the rest.

    We seed two conditional components, pre-open an approval for the first, then
    call the helper with BOTH ids. The first is skipped (preflight), the second
    still inserts — and crucially a stale preflight (TOCTOU) would still be
    caught by the per-row SAVEPOINT, so the loop never aborts midway.
    """
    project_id, team_id = _seed_project(session)
    scan_id = _seed_scan(session, project_id)
    comp_open = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("MPL-2.0", "conditional")]
    )
    comp_fresh = _seed_component_with_licenses(
        session, scan_id=scan_id, licenses=[("EPL-2.0", "conditional")]
    )
    session.add(
        ComponentApproval(
            component_id=comp_open,
            project_id=project_id,
            team_id=team_id,
            requested_by_user_id=None,
            status=ApprovalStatus.pending,
            version=1,
        )
    )
    session.commit()

    created = auto_create_pending_approvals(
        session,
        project_id=project_id,
        team_id=team_id,
        component_ids=[comp_open, comp_fresh],
        scan_id=scan_id,
    )
    session.commit()

    assert created == 1
    assert _open_approval_count(
        session, component_id=comp_open, project_id=project_id
    ) == 1
    assert _open_approval_count(
        session, component_id=comp_fresh, project_id=project_id
    ) == 1
