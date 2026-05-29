"""
Integration tests for DT-style scan retention — retire (Layer 1) + reclaim
(Layer 2) against live Postgres.

Covers the security-reviewer findings that drove the BLOCK:
  - ref-keyed retire supersedes prior same-ref succeeds, skips release-labelled
    snapshots, ignores other refs, and is a no-op for ref-less scans;
  - the reaper hard-deletes superseded-past-grace scans AND emits an AuditLog
    row per reclaim (Critical: bulk DELETE must not be audit-silent);
  - the keep-last/max-age sweep protects latest_scan_id / release / ref-live
    snapshots and re-asserts them in the DELETE predicate.

These exercise the sync-session code paths (retire runs in the scan finalize
path, the reaper in the beat), so the whole test drives a synchronous Session.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from models import AuditLog, Organization, Project, Scan, Team
from tasks.scan_retention import (
    _reclaim_aged,
    _reclaim_superseded,
    supersede_prior_ref_scans,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip scan retention tests")
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
            "alembic upgrade head failed; scan retention tests cannot run\n"
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
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_project(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Create org + team + project; return (project_id, team_id)."""
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}")
    session.add(org)
    session.flush()
    team = Team(organization_id=org.id, name=f"Team {suffix}", slug=f"team-{suffix}")
    session.add(team)
    session.flush()
    project = Project(team_id=team.id, name=f"Proj {suffix}", slug=f"proj-{suffix}")
    session.add(project)
    session.flush()
    return project.id, team.id


def _add_scan(
    session: Session,
    *,
    project_id: uuid.UUID,
    status: str = "succeeded",
    ref: str | None = None,
    release: str | None = None,
    created_at: datetime | None = None,
    superseded_at: datetime | None = None,
) -> uuid.UUID:
    metadata: dict[str, object] = {}
    if release is not None:
        metadata["release"] = release
    scan = Scan(
        project_id=project_id,
        kind="source",
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        scan_metadata=metadata,
        ref=ref,
        superseded_at=superseded_at,
    )
    session.add(scan)
    session.flush()
    if created_at is not None:
        scan.created_at = created_at
        session.flush()
    return scan.id


def _is_superseded(session: Session, scan_id: uuid.UUID) -> bool:
    row = session.execute(
        select(Scan.superseded_at).where(Scan.id == scan_id)
    ).scalar_one()
    return row is not None


def _exists(session: Session, scan_id: uuid.UUID) -> bool:
    return (
        session.execute(select(Scan.id).where(Scan.id == scan_id)).first() is not None
    )


def _audit_count_for(session: Session, scan_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.target_table == "scans",
                AuditLog.target_id == str(scan_id),
                AuditLog.action == "delete",
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Layer 1 — retire
# ---------------------------------------------------------------------------


def test_retire_supersedes_prior_same_ref(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    old = _add_scan(sync_session, project_id=pid, ref="main")
    winner = _add_scan(sync_session, project_id=pid, ref="main")
    sync_session.commit()

    n = supersede_prior_ref_scans(
        sync_session, project_id=pid, winner_scan_id=winner, ref="main"
    )
    sync_session.commit()

    assert n == 1
    assert _is_superseded(sync_session, old)
    assert not _is_superseded(sync_session, winner)  # winner stays live


def test_retire_skips_release_labelled(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    tagged = _add_scan(sync_session, project_id=pid, ref="main", release="v1.2.3")
    winner = _add_scan(sync_session, project_id=pid, ref="main")
    sync_session.commit()

    n = supersede_prior_ref_scans(
        sync_session, project_id=pid, winner_scan_id=winner, ref="main"
    )
    sync_session.commit()

    assert n == 0
    assert not _is_superseded(sync_session, tagged)  # release is immutable


def test_retire_ignores_other_ref(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    other = _add_scan(sync_session, project_id=pid, ref="develop")
    winner = _add_scan(sync_session, project_id=pid, ref="main")
    sync_session.commit()

    supersede_prior_ref_scans(
        sync_session, project_id=pid, winner_scan_id=winner, ref="main"
    )
    sync_session.commit()

    assert not _is_superseded(sync_session, other)


def test_retire_noop_when_ref_none(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    old = _add_scan(sync_session, project_id=pid, ref=None)
    winner = _add_scan(sync_session, project_id=pid, ref=None)
    sync_session.commit()

    n = supersede_prior_ref_scans(
        sync_session, project_id=pid, winner_scan_id=winner, ref=None
    )
    sync_session.commit()

    assert n == 0
    assert not _is_superseded(sync_session, old)


# ---------------------------------------------------------------------------
# Layer 2 — reclaim superseded
# ---------------------------------------------------------------------------


def test_reclaim_superseded_past_grace_deletes_and_audits(
    sync_session: Session,
) -> None:
    pid, _ = _seed_project(sync_session)
    now = datetime.now(UTC)
    stale = _add_scan(
        sync_session,
        project_id=pid,
        ref="main",
        superseded_at=now - timedelta(days=30),
    )
    sync_session.commit()

    reclaimed = _reclaim_superseded(sync_session, now=now, grace_days=7)

    assert reclaimed == 1
    assert not _exists(sync_session, stale)
    # Critical finding: the hard delete must leave an audit trail.
    assert _audit_count_for(sync_session, stale) == 1


def test_reclaim_superseded_within_grace_kept(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    now = datetime.now(UTC)
    fresh = _add_scan(
        sync_session,
        project_id=pid,
        ref="main",
        superseded_at=now - timedelta(days=1),
    )
    sync_session.commit()

    reclaimed = _reclaim_superseded(sync_session, now=now, grace_days=7)

    assert reclaimed == 0
    assert _exists(sync_session, fresh)


# ---------------------------------------------------------------------------
# Layer 2 — reclaim aged (keep-last / max-age + protections)
# ---------------------------------------------------------------------------


def test_reclaim_aged_keeps_last_n_and_protects_latest(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    now = datetime.now(UTC)
    old_time = now - timedelta(days=400)
    # Five ref-less failed scans, all well past max-age.
    ids = [
        _add_scan(
            sync_session,
            project_id=pid,
            status="failed",
            ref=None,
            created_at=old_time + timedelta(seconds=i),
        )
        for i in range(5)
    ]
    sync_session.commit()
    # Pin the newest as latest_scan_id — it must survive even past keep window.
    latest = ids[-1]
    sync_session.execute(
        update(Project).where(Project.id == pid).values(latest_scan_id=latest)
    )
    sync_session.commit()

    # keep_last=2 → 3 oldest are eligible; max_age=180 → all past cutoff.
    reclaimed = _reclaim_aged(sync_session, now=now, keep_last=2, max_age_days=180)

    # The 2 newest are kept by keep_last; one of the "kept" is also latest.
    surviving = {sid for sid in ids if _exists(sync_session, sid)}
    assert latest in surviving  # latest always protected
    assert len(surviving) == 2  # keep_last honored
    assert reclaimed == 3


def test_reclaim_aged_protects_release_and_ref_live(sync_session: Session) -> None:
    pid, _ = _seed_project(sync_session)
    now = datetime.now(UTC)
    old_time = now - timedelta(days=400)
    tagged = _add_scan(
        sync_session,
        project_id=pid,
        status="succeeded",
        ref=None,
        release="v9",
        created_at=old_time,
    )
    ref_live = _add_scan(
        sync_session,
        project_id=pid,
        status="succeeded",
        ref="main",  # ref-keyed live snapshot — retire owns it, not the sweep
        created_at=old_time,
    )
    sync_session.commit()

    reclaimed = _reclaim_aged(sync_session, now=now, keep_last=0, max_age_days=0)

    # Neither protected scan is reclaimed even with the most aggressive policy.
    assert _exists(sync_session, tagged)
    assert _exists(sync_session, ref_live)
    assert reclaimed == 0
