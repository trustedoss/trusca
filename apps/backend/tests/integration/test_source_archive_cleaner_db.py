"""
Integration test for ``tasks.source_archive_cleaner`` against real Postgres —
feat/zip-upload (security H-fix).

The unit suite (``tests/unit/tasks/test_source_archive_cleaner.py``) stubs the
two DB-reading helpers so the filesystem decision logic is hermetic. This test
exercises those helpers (``_project_exists`` / ``_active_archive_ids``) against
the live database, per CLAUDE.md: integration tests must hit real Postgres, no
mocks for our own infra.

It proves the end-to-end retention policy:
  - an archive whose project exists but has a QUEUED upload-scan is kept,
  - an archive whose project exists but is stale + unreferenced is reclaimed,
  - an archive whose project no longer exists is reclaimed.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from models import Scan
from tests._helpers import make_organization, make_project, make_team

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip source archive cleaner DB test")
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
            f"alembic upgrade head failed; cleaner DB test cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


async def _session_factory() -> Any:
    from core.db import _ensure_state
    from main import app

    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        factory = _ensure_state(app)
    return factory


def _write_zip(project_id: uuid.UUID, archive_id: uuid.UUID, *, age_seconds: float = 0.0) -> Path:
    from services.source_archive_service import archive_path

    path = archive_path(project_id, str(archive_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


async def test_cleaner_against_real_db_applies_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "1")
    factory = await _session_factory()

    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)

        # An archive referenced by a QUEUED upload-scan — must be kept.
        referenced_aid = uuid.uuid4()
        scan = Scan(
            project_id=project.id,
            kind="source",
            status="queued",
            progress_percent=0,
            scan_metadata={"source_type": "upload", "archive_id": str(referenced_aid)},
        )
        session.add(scan)
        await session.commit()

    referenced = _write_zip(project.id, referenced_aid, age_seconds=999999)
    stale = _write_zip(project.id, uuid.uuid4(), age_seconds=7200)  # 2h old, TTL 1h
    # An archive under a project id that does not exist in the DB.
    orphan_project = uuid.uuid4()
    orphan = _write_zip(orphan_project, uuid.uuid4(), age_seconds=10)

    from tasks.source_archive_cleaner import source_archive_cleaner_task

    result = source_archive_cleaner_task()

    assert referenced.exists(), "an archive referenced by a queued scan must be kept"
    assert not stale.exists(), "a stale unreferenced archive must be reclaimed"
    assert not orphan.exists(), "an orphaned-project archive must be reclaimed"
    assert result["deleted"] >= 2
