"""
Integration tests for the obligations HTTP surface — Phase 3 PR #13.

Endpoints:

  - GET /v1/projects/{project_id}/obligations
  - GET /v1/projects/{project_id}/obligations/{obligation_id}
  - GET /v1/projects/{project_id}/notice

Pins the wire format (RFC 7807 envelope on errors), the auth gate, and the
3xx vs 4xx contract. Heavier behavioural coverage (filter combinations,
search escape, sort) lives in :file:`tests/unit/test_obligation_service.py`.

The NOTICE endpoint also emits inspection headers (X-Notice-Generated-At /
License-Count / Obligation-Count) and surfaces a Content-Disposition header
when ``download=true`` — both contracts are pinned here.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
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
        pytest.skip("DATABASE_URL not set — skip obligations API tests")
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
            "alembic upgrade head failed; obligations API tests cannot run\n"
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


async def _seed_team_with_user(
    client: AsyncClient, *, role: str = "developer", is_superuser: bool = False
):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
    return org, team, user


async def _seed_scanned_project(
    client: AsyncClient,
    *,
    team_id: uuid.UUID,
    project_name: str | None = None,
):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        project = await make_project(session, team=team, name=project_name)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
        return project.id, scan.id, project.name


async def _seed_obligation(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    spdx_id: str | None = None,
    license_name: str | None = None,
    category: str = "allowed",
    kind: str = "attribution",
    text: str = "preserve the attribution notice",
    link: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns ``(license_id, obligation_id)`` and attaches a license_finding
    so the obligation's parent license is visible in the scan."""
    factory = await _factory(client)
    async with factory() as session:
        from models import Component, ComponentVersion, License, LicenseFinding, Obligation

        suffix = uuid.uuid4().hex[:10]
        cname = f"pkg-{suffix}"
        purl = f"pkg:npm/{cname}"
        component = Component(purl=purl, package_type="npm", name=cname)
        session.add(component)
        await session.commit()
        await session.refresh(component)

        cv = ComponentVersion(
            component_id=component.id,
            version="1.0.0",
            purl_with_version=f"{purl}@1.0.0",
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)

        lic = License(
            spdx_id=spdx_id or f"SPDX-{suffix}",
            name=license_name or f"License {suffix}",
            category=category,
        )
        session.add(lic)
        await session.commit()
        await session.refresh(lic)

        lf = LicenseFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            license_id=lic.id,
            kind="concluded",
            source_path=f"path/{suffix}",
            raw_data={},
        )
        session.add(lf)

        ob = Obligation(license_id=lic.id, kind=kind, text=text, link=link)
        session.add(ob)
        await session.commit()
        await session.refresh(ob)
        return lic.id, ob.id


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_list_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/obligations")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/obligations
# ---------------------------------------------------------------------------


async def test_list_happy_path_empty(client) -> None:
    """Project with no obligations → 200 with empty items + total 0."""
    _, team, user = await _seed_team_with_user(client)
    project_id, _, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/obligations",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["distribution"] == {}


async def test_list_returns_seeded_obligations(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    _, ob_id = await _seed_obligation(
        client,
        scan_id=scan_id,
        spdx_id=f"OBL-API-MIT-{uuid.uuid4().hex[:8]}",
        kind="attribution",
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/obligations",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(ob_id)
    assert body["items"][0]["kind"] == "attribution"
    assert body["distribution"]["attribution"] == 1


async def test_list_multivalue_kind_query_param(client) -> None:
    """FastAPI binds repeated `kind` query params to a list[str]."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    await _seed_obligation(client, scan_id=scan_id, kind="attribution")
    await _seed_obligation(client, scan_id=scan_id, kind="copyleft")
    await _seed_obligation(client, scan_id=scan_id, kind="notice")
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/obligations",
        headers=headers,
        params=[("kind", "attribution"), ("kind", "copyleft")],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    kinds = {row["kind"] for row in body["items"]}
    assert kinds == {"attribution", "copyleft"}


async def test_list_invalid_sort_returns_422_problem(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/obligations",
        headers=headers,
        params={"sort": "BOGUS"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_cross_team_returns_403(client) -> None:
    _, team_a, _ = await _seed_team_with_user(client)
    project_id, _, _ = await _seed_scanned_project(client, team_id=team_a.id)

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_b = await make_team(session, organization=org)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    headers = _bearer_for(outsider)
    response = await client.get(
        f"/v1/projects/{project_id}/obligations",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/obligations/{obligation_id}
# ---------------------------------------------------------------------------


async def test_detail_cross_team_existence_hide_returns_404(client) -> None:
    """An obligation visible to team A is 404 (not 403) for team B."""
    _, team_a, _ = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team_a.id)
    _, ob_id = await _seed_obligation(client, scan_id=scan_id)

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_b = await make_team(session, organization=org)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    headers = _bearer_for(outsider)
    response = await client.get(
        f"/v1/projects/{project_id}/obligations/{ob_id}",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_detail_obligation_not_visible_in_scan_returns_404(client) -> None:
    """Obligation exists, but its parent license isn't in this project's scan."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)

    # Seed an obligation whose license is in a *different* scan (no finding
    # rows tying it to this project's scan).
    factory = await _factory(client)
    async with factory() as session:
        from models import License, Obligation

        suffix = uuid.uuid4().hex[:10]
        lic = License(
            spdx_id=f"DETACHED-{suffix}",
            name=f"detached {suffix}",
            category="allowed",
        )
        session.add(lic)
        await session.commit()
        await session.refresh(lic)
        ob = Obligation(license_id=lic.id, kind="attribution", text="t")
        session.add(ob)
        await session.commit()
        await session.refresh(ob)
        ob_id = ob.id

    headers = _bearer_for(user)
    response = await client.get(
        f"/v1/projects/{project_id}/obligations/{ob_id}",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/notice
# ---------------------------------------------------------------------------


async def test_notice_text_inline_returns_plain_body_with_inspection_headers(
    client,
) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    spdx = f"OBL-NOTICE-MIT-{uuid.uuid4().hex[:8]}"
    await _seed_obligation(
        client,
        scan_id=scan_id,
        spdx_id=spdx,
        kind="attribution",
        text="please preserve attribution",
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # Inline (no Content-Disposition by default).
    assert "content-disposition" not in {k.lower() for k in response.headers.keys()}
    # Inspection headers present + parse as ints.
    assert response.headers["x-notice-license-count"] == "1"
    assert response.headers["x-notice-obligation-count"] == "1"
    assert response.headers["x-notice-generated-at"]  # ISO8601 string
    body = response.text
    assert spdx in body
    # Body length is non-trivial (header + divider + license + obligation).
    assert len(body) > 100


async def test_notice_markdown_format_uses_text_markdown_media_type(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    spdx = f"OBL-MD-MIT-{uuid.uuid4().hex[:8]}"
    await _seed_obligation(client, scan_id=scan_id, spdx_id=spdx, kind="attribution")
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
        params={"format": "markdown"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    # Markdown variant — H1 header + H2 license heading.
    assert body.startswith("# Third-party Licenses for ")
    assert f"## {spdx}" in body


async def test_notice_download_attaches_filename_with_safe_token(client) -> None:
    """`download=true` adds an RFC 6266 ``Content-Disposition: attachment``
    header with both the ASCII ``filename="NOTICE-<token>.txt"`` fallback
    and the UTF-8 ``filename*=UTF-8''…`` extended parameter. The ASCII
    fallback's project name segment is sanitised to ``[A-Za-z0-9._-]``."""
    _, team, user = await _seed_team_with_user(client)
    # Use a tricky project name so the sanitiser has something to do.
    project_id, scan_id, _ = await _seed_scanned_project(
        client, team_id=team.id, project_name="Hello / World!  alpha"
    )
    await _seed_obligation(
        client,
        scan_id=scan_id,
        spdx_id=f"OBL-DL-MIT-{uuid.uuid4().hex[:8]}",
        kind="attribution",
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
        params={"download": True},
    )
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # Pick out the ASCII fallback (filename="..."). It must not carry any
    # of `/`, `!`, or whitespace from the project name.
    ascii_part = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert " " not in ascii_part
    assert "/" not in ascii_part
    assert "!" not in ascii_part
    # ASCII fallback starts with `NOTICE-` and ends with `.txt`.
    assert ascii_part.startswith("NOTICE-")
    assert ascii_part.endswith(".txt")
    # RFC 6266 extended parameter must also be present so non-ASCII names
    # round-trip on browsers that understand it.
    assert "filename*=UTF-8''" in disposition


async def test_notice_download_filename_carries_utf8_round_trip_for_non_ascii_project(
    client,
) -> None:
    """RFC 6266 ``filename*=UTF-8''…`` must percent-encode the original
    project name (including non-ASCII characters) so a browser can decode it
    back to a human-readable name. The ASCII fallback drops them safely."""
    import urllib.parse as _up

    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, project_name = await _seed_scanned_project(
        client, team_id=team.id, project_name="한글-프로젝트"
    )
    await _seed_obligation(
        client,
        scan_id=scan_id,
        spdx_id=f"OBL-KR-MIT-{uuid.uuid4().hex[:8]}",
        kind="attribution",
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
        params={"download": True},
    )
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    # Extract the extended-parameter value (everything after the marker).
    marker = "filename*=UTF-8''"
    assert marker in disposition
    encoded = disposition.split(marker, 1)[1]
    # Round-trip via percent-decoding must reproduce the original name.
    decoded = _up.unquote(encoded)
    assert decoded == f"NOTICE-{project_name}.txt"
    # ASCII fallback must remain free of non-ASCII characters so legacy
    # clients can still save the file.
    ascii_part = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_part.isascii()
    assert ascii_part.startswith("NOTICE-")
    assert ascii_part.endswith(".txt")
