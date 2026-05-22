"""
Integration tests for the source-tree viewer HTTP surface — G3.2.

Endpoints:
  - GET /v1/projects/{project_id}/source-tree?path=&page=&size=&scan_id=
  - GET /v1/projects/{project_id}/source-file?path=&scan_id=

These hit the real FastAPI app + a real Postgres (seeded via the helper
factories) and a real gzip tarball written at the workspace path the service
resolves from the seeded project/scan UUIDs. Pins:
  - happy path tree listing + file read + per-line matches,
  - anonymous → 401,
  - outsider → 404 (existence-hide, never 403),
  - hostile ``?path=`` → 400 problem+json,
  - the 404 / 400 envelopes are RFC 7807 ``application/problem+json``.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from services.source_preservation_service import (
    SCANCODE_MEMBER_NAME,
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
        pytest.skip("DATABASE_URL not set — skip source-tree API tests")
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
            f"alembic upgrade head failed; source-tree API tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point workspace_root() at a clean tmp dir for every test (rule #11)."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed(client: AsyncClient, *, role: str = "developer", is_superuser: bool = False):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
    return team, user, project, scan.id


def _write_tarball(project_id: uuid.UUID, scan_id: uuid.UUID) -> None:
    """Write a small preserved-source tarball with a folded scancode JSON."""
    dest = scan_source_tarball_path(project_id, scan_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "README.md": b"# project\n",
        "src/main.py": b"print('hi')\n",
        "LICENSE": b"MIT License\n\nCopyright\n",
    }
    scancode = {
        "files": [
            {
                "path": "LICENSE",
                "license_detections": [
                    {
                        "matches": [
                            {
                                "license_expression_spdx": "MIT",
                                "start_line": 1,
                                "end_line": 3,
                                "score": 99.0,
                            }
                        ]
                    }
                ],
            }
        ]
    }
    with tarfile.open(dest, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(body))
        sc = json.dumps(scancode).encode("utf-8")
        info = tarfile.TarInfo(name=SCANCODE_MEMBER_NAME)
        info.size = len(sc)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(sc))


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_source_tree_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/source-tree")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_source_tree_lists_root(client: AsyncClient) -> None:
    _, user, project, scan_id = await _seed(client)
    _write_tarball(project.id, scan_id)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/source-tree", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scan_id"] == str(scan_id)
    assert body["path"] == ""
    names = [(e["name"], e["is_dir"]) for e in body["entries"]]
    assert ("src", True) in names
    assert ("README.md", False) in names
    assert ("LICENSE", False) in names
    assert all(not n.startswith(".trustedoss") for n, _ in names)


async def test_source_file_reads_content_and_line_matches(client: AsyncClient) -> None:
    _, user, project, scan_id = await _seed(client)
    _write_tarball(project.id, scan_id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/source-file",
        headers=headers,
        params={"path": "LICENSE"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["encoding"] == "utf-8"
    assert body["content"].startswith("MIT License")
    assert body["truncated"] is False
    assert body["license_matches"] == [
        {"spdx_id": "MIT", "start_line": 1, "end_line": 3, "score": 99.0}
    ]


# ---------------------------------------------------------------------------
# IDOR / RBAC + existence-hide
# ---------------------------------------------------------------------------


async def test_source_tree_other_team_returns_404(client: AsyncClient) -> None:
    _, _, target, scan_id = await _seed(client)
    _write_tarball(target.id, scan_id)
    _, outsider, _, _ = await _seed(client)
    headers = _bearer_for(outsider)

    response = await client.get(
        f"/v1/projects/{target.id}/source-tree", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_source_tree_super_admin_bypasses_team(client: AsyncClient) -> None:
    _, _, target, scan_id = await _seed(client)
    _write_tarball(target.id, scan_id)
    _, admin, _, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{target.id}/source-tree", headers=headers
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Path-traversal rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", ["../etc/passwd", "/etc/passwd", "..\\..\\x"])
async def test_source_file_rejects_hostile_path(
    client: AsyncClient, hostile: str
) -> None:
    _, user, project, scan_id = await _seed(client)
    _write_tarball(project.id, scan_id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/source-file",
        headers=headers,
        params={"path": hostile},
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
