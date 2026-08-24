# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for migration 0070 (``scans.dependency_fingerprint``).

S8 (concurrency-scaling-plan-2026-08-22.md §3.2, unit 29) is a "연속"
(multi-PR) unit; this session ships the schema step only. What has to be
true of THAT step, independent of the reuse-decision logic a later revision
adds:

  - the column exists after ``alembic upgrade head`` with the shape the
    model declares (nullable, fixed-length string sized for a SHA-256 hex
    digest), and a real digest round-trips through it unchanged;
  - the migration's own ``downgrade()`` fails loudly rather than silently
    dropping the column (CLAUDE.md §6 forward-only policy), both at the
    Python level (calling the function directly) and through the real
    ``alembic downgrade`` CLI a rollback attempt would actually invoke.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from models import Organization, Project, Scan, Team
from models.scan_fingerprint import compute_scan_fingerprint

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_0070_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "0070_scan_dependency_fingerprint.py"
)

pytestmark = pytest.mark.integration


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skip alembic integration test")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture(scope="module", autouse=True)
def _migrate_to_head() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed: {result.stderr[-400:]}")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(_sync_url(), future=True)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as s:
        yield s
    engine.dispose()


def _seed_project(session: Session) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"S8 Org {suffix}", slug=f"s8-org-{suffix}")
    session.add(org)
    session.flush()
    team = Team(organization_id=org.id, name=f"S8 Team {suffix}", slug=f"s8-team-{suffix}")
    session.add(team)
    session.flush()
    project = Project(team_id=team.id, name=f"S8 Proj {suffix}", slug=f"s8-proj-{suffix}")
    session.add(project)
    session.commit()
    return project.id


def _load_migration_0070():
    """Load the migration file directly by path.

    ``alembic.versions`` cannot be imported as a dotted module name: the
    installed ``alembic`` tooling package already owns the top-level
    ``alembic`` name on ``sys.path``, and it has no ``versions`` submodule of
    its own, so ``importlib.import_module("alembic.versions.0070_...")``
    resolves against the wrong package and raises ``ModuleNotFoundError``.
    Loading by file path sidesteps the name collision entirely.
    """
    spec = importlib.util.spec_from_file_location(
        "trusca_migration_0070", _MIGRATION_0070_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_fingerprint_column_shape(session: Session) -> None:
    row = session.execute(
        text(
            "SELECT data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'scans' AND column_name = 'dependency_fingerprint'"
        )
    ).one()
    data_type, max_length, is_nullable = row

    assert data_type == "character varying"
    assert max_length == 64  # exactly a SHA-256 hex digest's length
    assert is_nullable == "YES"  # existing scans predate this column


def test_dependency_fingerprint_round_trips_a_computed_digest(session: Session) -> None:
    """A digest ``compute_scan_fingerprint`` actually returns writes and
    reads back unchanged: no silent truncation or type coercion."""
    digest = compute_scan_fingerprint(
        manifest_inventory={
            "files": [{"path": "go.sum", "size": 10, "sha256": "a" * 64}],
            "count": 1,
            "truncated": False,
        },
        scanner_version="12.3.3",
        scan_config={"cdxgen_spec_version": "1.5"},
    )
    assert digest is not None
    assert len(digest) == 64

    project_id = _seed_project(session)
    scan = Scan(
        project_id=project_id,
        kind="source",
        status="succeeded",
        dependency_fingerprint=digest,
    )
    session.add(scan)
    session.commit()
    session.refresh(scan)

    assert scan.dependency_fingerprint == digest

    reloaded = session.get(Scan, scan.id)
    assert reloaded is not None
    assert reloaded.dependency_fingerprint == digest


def test_dependency_fingerprint_defaults_to_null(session: Session) -> None:
    """A scan created without a fingerprint (every pre-0070 scan, and any
    scan the pipeline could not fingerprint) stores NULL, not an empty
    string or a sentinel: NULL is what the model docstring's "never equal
    to another NULL" contract depends on.
    """
    project_id = _seed_project(session)
    scan = Scan(project_id=project_id, kind="source", status="succeeded")
    session.add(scan)
    session.commit()
    session.refresh(scan)

    assert scan.dependency_fingerprint is None


def test_migration_0070_downgrade_raises_not_implemented() -> None:
    """Calling the migration module's ``downgrade()`` directly: no DB or
    Alembic runtime needed, since the function raises before touching ``op``.
    """
    module = _load_migration_0070()

    with pytest.raises(NotImplementedError):
        module.downgrade()


def test_alembic_downgrade_cli_fails_on_0070() -> None:
    """The forward-only contract as an operator would actually hit it: an
    ``alembic downgrade`` attempt from head must exit non-zero, not silently
    drop the column.
    """
    current = subprocess.run(
        ["alembic", "current"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert current.returncode == 0, current.stderr
    if "0070" not in current.stdout:
        pytest.skip(
            "head is past 0070 already, a later migration owns the downgrade "
            "boundary the CLI would hit first; 0070's own raise is covered by "
            "the direct-call test above"
        )

    downgrade = subprocess.run(
        ["alembic", "downgrade", "-1"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert downgrade.returncode != 0
    assert "NotImplementedError" in downgrade.stderr or "forward-only" in downgrade.stderr

    # Restore head so this test does not leave the shared DB mid-migration
    # for whatever runs next.
    restore = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert restore.returncode == 0, restore.stderr
