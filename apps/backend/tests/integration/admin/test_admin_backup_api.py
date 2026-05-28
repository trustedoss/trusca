"""
Integration tests for ``/v1/admin/backup/*`` — Phase 6 chore PR #19.

Coverage:
  - 4-role auth matrix on every endpoint (anon → 401, developer → 404,
    team_admin → 404, super_admin → 2xx).
  - X-Confirm-Restore header gate on POST /restore.
  - DELETE auto-* → 409 with type=auto-protected.
  - Path-traversal name → 404 (existence-hide via the regex).
  - GET / lists what we wrote on disk (size + db_revision populated).
  - POST / enqueues the Celery task and emits a `backup.requested` audit row.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin backup API tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(  # noqa: S603, S607
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; admin backup API tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def temp_backups_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``BACKUPS_ROOT`` at a fresh tmp dir for the duration of a test."""
    monkeypatch.setenv("BACKUPS_ROOT", str(tmp_path))
    return tmp_path


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


def _seed_backup(
    root: Path,
    *,
    name: str,
    alembic_head: str | None = "abc123",
) -> Path:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "postgres.sql.gz").write_bytes(b"\x1f\x8bfake")
    (target / "workspace.tar.gz").write_bytes(b"\x1f\x8bfake")
    manifest: dict[str, object] = {"timestamp": "2026-05-09"}
    if alembic_head is not None:
        manifest["alembic_head"] = alembic_head
    (target / "manifest.json").write_text(json.dumps(manifest))
    return target


# ---------------------------------------------------------------------------
# 4-role auth matrix
# ---------------------------------------------------------------------------


_AUTH_MATRIX_ENDPOINTS = [
    ("GET", "/v1/admin/backup"),
    ("POST", "/v1/admin/backup"),
    ("GET", f"/v1/admin/backup/manual-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}/download"),
    ("DELETE", f"/v1/admin/backup/manual-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"),
]


@pytest.mark.parametrize("method,path", _AUTH_MATRIX_ENDPOINTS)
async def test_anonymous_returns_401(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


@pytest.mark.parametrize("method,path", _AUTH_MATRIX_ENDPOINTS)
async def test_developer_returns_404_existence_hide(
    client: AsyncClient, method: str, path: str
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.request(method, path, headers=_bearer_for(user))
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


@pytest.mark.parametrize("method,path", _AUTH_MATRIX_ENDPOINTS)
async def test_team_admin_returns_404_existence_hide(
    client: AsyncClient, method: str, path: str
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

    response = await client.request(method, path, headers=_bearer_for(user))
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/admin/backup
# ---------------------------------------------------------------------------


async def test_list_backups_super_admin_sees_seeded_entries(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    _seed_backup(temp_backups_root, name="manual-20260509T120000Z", alembic_head="rev42")
    _seed_backup(temp_backups_root, name="auto-20260508T000000Z", alembic_head="rev42")

    response = await client.get("/v1/admin/backup", headers=_bearer_for(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    names = [item["name"] for item in body["items"]]
    assert sorted(names) == sorted(["manual-20260509T120000Z", "auto-20260508T000000Z"])
    assert body["total"] == 2
    assert all(item["db_revision"] == "rev42" for item in body["items"])
    assert all(item["size_bytes"] > 0 for item in body["items"])


# ---------------------------------------------------------------------------
# POST /v1/admin/backup
# ---------------------------------------------------------------------------


async def test_trigger_manual_backup_enqueues_task_and_audits(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    class _FakeAsync:
        id = "fake-backup-task-id"

    with patch("api.v1.admin.backup.run_backup_task") as mock_task:
        mock_task.delay = lambda **_kw: _FakeAsync()
        response = await client.post("/v1/admin/backup", headers=_bearer_for(admin))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["task_id"] == "fake-backup-task-id"
    assert body["name"].startswith("manual-")

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id=:a AND target_table='backups' "
                    "  AND action='backup.requested'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


# ---------------------------------------------------------------------------
# DELETE /v1/admin/backup/{name}
# ---------------------------------------------------------------------------


async def test_delete_auto_backup_returns_409_problem(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    auto_dir = _seed_backup(temp_backups_root, name="auto-20260509T120000Z")

    response = await client.delete(
        "/v1/admin/backup/auto-20260509T120000Z", headers=_bearer_for(admin)
    )
    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["type"].endswith("/backup-auto-protected")
    # The directory survives.
    assert auto_dir.is_dir()


async def test_delete_manual_backup_succeeds_and_audits(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    target = _seed_backup(temp_backups_root, name="manual-20260509T120000Z")

    response = await client.delete(
        "/v1/admin/backup/manual-20260509T120000Z", headers=_bearer_for(admin)
    )
    assert response.status_code == 204, response.text
    assert not target.exists()

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id=:a AND target_table='backups' "
                    "  AND action='backup.deleted'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_delete_path_traversal_name_returns_404(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # FastAPI keeps the path segment intact when it does not contain '/'; a
    # crafted ".." prefix on a backup-style name fails the regex gate.
    response = await client.delete(
        "/v1/admin/backup/manual-../etc-passwd", headers=_bearer_for(admin)
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_delete_missing_backup_returns_404(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        "/v1/admin/backup/manual-20260509T120000Z", headers=_bearer_for(admin)
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# POST /v1/admin/backup/restore
# ---------------------------------------------------------------------------


async def test_restore_without_confirm_header_returns_412(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    """A2 (sys-bug-bkp-1): missing X-Confirm-Restore is a precondition
    failure (412), not a malformed request (400). The request body itself
    is valid; what is missing is the operator's explicit confirmation.
    """
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    files = {"archive": ("upload.tar.gz", b"\x1f\x8bfake", "application/gzip")}
    response = await client.post(
        "/v1/admin/backup/restore", headers=_bearer_for(admin), files=files
    )
    assert response.status_code == 412, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["type"] == "urn:trustedoss:problem:restore_confirmation_required"
    assert body["title"] == "Restore confirmation header missing"
    assert body["status"] == 412


async def test_restore_with_confirm_header_enqueues_task(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    """A valid tar.gz upload + confirm header dispatches the restore task."""
    import tarfile
    import tempfile

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Build a valid backup tar.gz: top-level dir contains the three artifacts.
    src = temp_backups_root / "src" / "manual-20260509T120000Z"
    src.mkdir(parents=True)
    (src / "postgres.sql.gz").write_bytes(b"\x1f\x8bfake")
    (src / "workspace.tar.gz").write_bytes(b"\x1f\x8bfake")
    (src / "manifest.json").write_text(json.dumps({"alembic_head": "rev42"}))

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tar_path = Path(tmp.name)
    try:
        with tarfile.open(tar_path, mode="w:gz") as tar:
            tar.add(str(src), arcname="manual-20260509T120000Z")

        class _FakeAsync:
            id = "fake-restore-task-id"

        with patch("api.v1.admin.backup.restore_backup_task") as mock_task:
            mock_task.delay = lambda **_kw: _FakeAsync()
            with tar_path.open("rb") as fh:
                files = {"archive": ("upload.tar.gz", fh.read(), "application/gzip")}
            response = await client.post(
                "/v1/admin/backup/restore",
                headers={
                    **_bearer_for(admin),
                    "X-Confirm-Restore": "yes",
                },
                files=files,
            )
    finally:
        tar_path.unlink(missing_ok=True)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["task_id"] == "fake-restore-task-id"

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id=:a AND target_table='backups' "
                    "  AND action='backup.restore_requested'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_restore_with_invalid_archive_returns_400(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Random bytes — not a valid tar.gz.
    files = {"archive": ("upload.tar.gz", b"definitely-not-a-tar", "application/gzip")}
    response = await client.post(
        "/v1/admin/backup/restore",
        headers={**_bearer_for(admin), "X-Confirm-Restore": "yes"},
        files=files,
    )
    assert response.status_code == 400, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["type"].endswith("/backup-invalid-archive")


# ---------------------------------------------------------------------------
# Download (super-admin path uses a real seed; auth matrix already covers
# 401/404 above).
# ---------------------------------------------------------------------------


async def test_download_streams_tar_gz_for_super_admin(
    client: AsyncClient, temp_backups_root: Path
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    name = f"manual-{uuid.uuid4().hex[:8]}-placeholder"
    # The seed name must match the regex; substitute with a regex-conforming
    # name and seed accordingly.
    name = "manual-20260509T120000Z"
    _seed_backup(temp_backups_root, name=name)

    response = await client.get(
        f"/v1/admin/backup/{name}/download", headers=_bearer_for(admin)
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/gzip"
    assert response.headers["content-disposition"].startswith("attachment;")
    # Body is non-trivial gzip data.
    assert len(response.content) > 0
