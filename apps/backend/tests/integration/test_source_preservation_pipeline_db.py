"""
End-to-end source-preservation pipeline integration test against real Postgres —
chore/scan-gaps-hardening (G3 follow-up #5).

The existing G3 coverage is unit-level: ``services/source_preservation_service``
and ``services/source_tree_service`` are exercised with in-test tar fixtures, and
the cleaner has a sibling DB test for the *archive* (zip-upload) path. This test
closes the gap by driving the REAL preservation → read → retention chain end to
end against a live database + a real on-disk tarball written by the production
``preserve_scan_source`` (no hand-rolled tar fixture):

  1. seed a project + a succeeded source scan,
  2. lay down a real source tree + scancode JSON on disk,
  3. call ``preserve_scan_source`` (the production tar writer) and persist a
     ``source_tarball`` ScanArtifact pointing at the retained tar,
  4. read it back through the source-tree + source-file (+ raw=true) endpoints,
  5. run ``scan_source_cleaner_task`` and assert the latest tarball is RETAINED
     while a superseded one is reclaimed.

Per CLAUDE.md: integration tests hit the real Postgres (no mocks for our own
infra); the test skips cleanly when ``DATABASE_URL`` is unset or alembic cannot
reach the DB so the unit lane / a DB-less env stays green.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import ScanArtifact, User
from services.source_preservation_service import (
    preserve_scan_source,
    scan_source_tarball_path,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip source-preservation pipeline test")
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
            f"alembic upgrade head failed; pipeline test cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point workspace_root() at a clean tmp dir for every test (rule #11)."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def app() -> Any:
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient) -> Any:
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _lay_down_source_tree(workspace: Path) -> tuple[Path, Path]:
    """Create a real source dir + scancode JSON on disk (as the scan would)."""
    source_dir = workspace / "source"
    (source_dir / "src").mkdir(parents=True, exist_ok=True)
    (source_dir / "README.md").write_bytes(b"# project\n")
    (source_dir / "LICENSE").write_bytes(b"MIT License\n\nCopyright\n")
    (source_dir / "src" / "main.py").write_bytes(b"print('hi')\n")
    # A larger file so the raw download has something the viewer cap could clip.
    (source_dir / "big.txt").write_bytes(b"A" * 4096)

    scancode_dir = workspace / "scancode"
    scancode_dir.mkdir(parents=True, exist_ok=True)
    scancode_json = scancode_dir / "result.json"
    scancode_json.write_text(
        '{"files": [{"path": "LICENSE", "license_detections": '
        '[{"matches": [{"license_expression_spdx": "MIT", '
        '"start_line": 1, "end_line": 3, "score": 99.0}]}]}]}'
    )
    return source_dir, scancode_json


async def test_preserve_then_read_then_retain_pipeline(
    client: AsyncClient, _workspace: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
        project_id = project.id
        scan_id = scan.id

    # --- Step 1+2: real source tree + the PRODUCTION preservation writer -----
    source_dir, scancode_json = _lay_down_source_tree(_workspace)
    tar_path = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=source_dir,
        scancode_json_path=scancode_json,
    )
    assert tar_path is not None, "preservation must produce a tarball"
    assert tar_path == scan_source_tarball_path(project_id, scan_id)
    assert tar_path.is_file()

    # --- Step 3: persist the source_tarball ScanArtifact (as the task does) --
    async with factory() as session:
        session.add(
            ScanArtifact(
                scan_id=scan_id,
                kind="source_tarball",
                storage_path=str(tar_path),
                byte_size=tar_path.stat().st_size,
            )
        )
        await session.commit()
        row = (
            await session.execute(
                ScanArtifact.__table__.select().where(
                    ScanArtifact.scan_id == scan_id,
                    ScanArtifact.kind == "source_tarball",
                )
            )
        ).first()
        assert row is not None

    headers = _bearer_for(user)

    # --- Step 4: read it back through the endpoints -------------------------
    tree = await client.get(
        f"/v1/projects/{project_id}/source-tree", headers=headers
    )
    assert tree.status_code == 200, tree.text
    names = {(e["name"], e["is_dir"]) for e in tree.json()["entries"]}
    assert ("src", True) in names
    assert ("README.md", False) in names
    assert ("LICENSE", False) in names
    assert all(not n.startswith(".trustedoss") for n, _ in names)

    file_resp = await client.get(
        f"/v1/projects/{project_id}/source-file",
        headers=headers,
        params={"path": "LICENSE"},
    )
    assert file_resp.status_code == 200, file_resp.text
    file_body = file_resp.json()
    assert file_body["encoding"] == "utf-8"
    assert file_body["content"].startswith("MIT License")
    assert file_body["license_matches"] == [
        {"spdx_id": "MIT", "start_line": 1, "end_line": 3, "score": 99.0}
    ]

    # Raw full-file download (G3.3) streams octet-stream with a disposition.
    raw_resp = await client.get(
        f"/v1/projects/{project_id}/source-file",
        headers=headers,
        params={"path": "big.txt", "raw": "true"},
    )
    assert raw_resp.status_code == 200, raw_resp.text
    assert raw_resp.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in raw_resp.headers["content-disposition"]
    assert raw_resp.content == b"A" * 4096  # the WHOLE member

    # --- Step 5: retention sweep keeps the latest, reclaims a superseded one --
    # Drop a stale tarball for a DIFFERENT (non-latest) scan id under the same
    # project; the cleaner must reclaim it while keeping the latest_scan tarball.
    stale_scan_id = uuid.uuid4()
    stale_path = scan_source_tarball_path(project_id, stale_scan_id)
    stale_path.write_bytes(b"PK\x00stale-not-the-latest")

    from tasks.scan_source_cleaner import scan_source_cleaner_task

    result = scan_source_cleaner_task()

    assert tar_path.is_file(), "the latest_scan tarball must be retained"
    assert not stale_path.is_file(), "a superseded tarball must be reclaimed"
    assert result["deleted"] >= 1

    # The read endpoints still work after the sweep (latest tarball survived).
    tree_after = await client.get(
        f"/v1/projects/{project_id}/source-tree", headers=headers
    )
    assert tree_after.status_code == 200, tree_after.text


async def test_pipeline_other_team_cannot_read_preserved_source(
    client: AsyncClient, _workspace: Path
) -> None:
    """The preserved source is team-scoped: an outsider gets 404 existence-hide."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        owner = await make_user(session)
        await make_membership(session, user=owner, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        await session.commit()
        await session.refresh(project)
        project_id = project.id
        scan_id = scan.id

        # An unrelated user in a different team.
        other_org = await make_organization(session)
        other_team = await make_team(session, organization=other_org)
        outsider = await make_user(session)
        await make_membership(
            session, user=outsider, team=other_team, role="developer"
        )

    source_dir, scancode_json = _lay_down_source_tree(_workspace)
    tar_path = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=source_dir,
        scancode_json_path=scancode_json,
    )
    assert tar_path is not None

    resp = await client.get(
        f"/v1/projects/{project_id}/source-tree",
        headers=_bearer_for(outsider),
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)
