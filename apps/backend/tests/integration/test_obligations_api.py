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
import re
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


async def _seed_mit_finding(client: AsyncClient, *, scan_id: uuid.UUID) -> None:
    """Attach a license_finding for the REAL ``MIT`` license (get-or-create —
    ``licenses.spdx_id`` is UNIQUE and MIT may already exist from seeds or
    earlier runs). Used by the Phase B license-text assertions: only a
    catalogued id has a bundled full text."""
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Component, ComponentVersion, License, LicenseFinding

        suffix = uuid.uuid4().hex[:10]
        cname = f"pkg-mit-{suffix}"
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

        lic = (
            await session.execute(select(License).where(License.spdx_id == "MIT"))
        ).scalar_one_or_none()
        if lic is None:
            lic = License(spdx_id="MIT", name="MIT License", category="allowed")
            session.add(lic)
            await session.commit()
            await session.refresh(lic)

        session.add(
            LicenseFinding(
                scan_id=scan_id,
                component_version_id=cv.id,
                license_id=lic.id,
                kind="concluded",
                source_path=f"path/{suffix}",
                raw_data={},
            )
        )
        await session.commit()


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


async def test_list_cross_team_existence_hide_returns_404(client) -> None:
    """Cross-team list reads existence-hide as 404 (security review, low severity),
    uniform with the detail / report / source-tree endpoints."""
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
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_notice_cross_team_existence_hide_returns_404(client) -> None:
    """Cross-team NOTICE reads existence-hide as 404 (security review, low severity)."""
    _, team_a, _ = await _seed_team_with_user(client)
    project_id, _, _ = await _seed_scanned_project(client, team_id=team_a.id)

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_b = await make_team(session, organization=org)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=_bearer_for(outsider),
    )
    assert response.status_code == 404
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


async def test_notice_html_format_uses_text_html_media_type(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    spdx = f"OBL-HT-MIT-{uuid.uuid4().hex[:8]}"
    await _seed_obligation(client, scan_id=scan_id, spdx_id=spdx, kind="attribution")
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
        params={"format": "html", "download": "true"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # download=true → attachment with the .html extension.
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert ".html" in disposition
    body = response.text
    assert body.startswith("<!DOCTYPE html>")
    assert f"<h2>{spdx}" in body
    # security review, low severity: the same-origin HTML NOTICE carries a restrictive
    # CSP so any future escaping regression cannot execute against the API origin.
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "style-src 'unsafe-inline'" in csp


async def test_notice_csp_absent_for_non_html_formats(client) -> None:
    """The CSP header is HTML-specific — text/markdown NOTICEs don't carry it."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    spdx = f"OBL-NOCSP-{uuid.uuid4().hex[:8]}"
    await _seed_obligation(client, scan_id=scan_id, spdx_id=spdx, kind="attribution")
    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=_bearer_for(user),
        params={"format": "text"},
    )
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


async def test_notice_invalid_format_is_rejected_by_query_validation(client) -> None:
    """The ``format`` query is constrained to text|markdown|html (422 else)."""
    _, team, user = await _seed_team_with_user(client)
    project_id, _, _ = await _seed_scanned_project(client, team_id=team.id)
    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=_bearer_for(user),
        params={"format": "pdf"},
    )
    assert response.status_code == 422


async def test_notice_download_includes_bundled_license_full_text(client) -> None:
    """Phase B wire contract: a downloaded NOTICE for a scan that surfaced a
    catalogued license (MIT) carries the License Texts section with the
    bundled standard full text, the per-component copyright attribution line
    (honest fallback here — no copyright captured), and unchanged inspection
    headers."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _ = await _seed_scanned_project(client, team_id=team.id)
    await _seed_mit_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
        params={"download": True},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    body = response.text
    # License Texts section: BomLens-parity divider + the standard MIT text.
    assert "License Texts" in body
    assert "----- MIT -----" in body
    assert "Permission is hereby granted" in body
    # Copyright attribution never renders blank — honest fallback + the
    # npm registry URL inferred from the component's purl.
    assert "Copyright: holders not captured in SBOM — see source (" in body
    # Inspection headers regression: counts are licenses/obligations, not
    # inflated by the appended text sections.
    assert response.headers["x-notice-license-count"] == "1"


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


# ---------------------------------------------------------------------------
# N15: recording that an obligation was actually met
#
# Two contracts shape these. The reads above keep their shape, with the record
# attached alongside rather than folded into the query that finds obligations:
# a join would drop every obligation nobody has started, which is most of them
# on the day this ships, and the list would read as the work having shrunk.
#
# And nothing recorded here reaches the generated notice. The notice is the
# licence's words about the components; a fulfilment that could edit it would
# be a way to make a compliance artefact say what somebody wished were true.
# ---------------------------------------------------------------------------


async def _record(
    client: AsyncClient,
    *,
    user: User,
    project_id: uuid.UUID,
    obligation_id: uuid.UUID,
    status: str = "done",
    **extra: object,
) -> dict:
    response = await client.put(
        f"/v1/projects/{project_id}/obligations/{obligation_id}/fulfilment",
        headers=_bearer_for(user),
        json={"status": status, **extra},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _without_the_stamp(notice: str) -> list[str]:
    """The notice minus the line saying when it was generated.

    That line differs between any two calls a second apart, so comparing the
    whole text would pass or fail on the clock rather than on the content.
    """
    return [
        line
        for line in notice.splitlines()
        if not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", line)
    ]


async def test_an_obligation_nobody_has_touched_reads_as_null(client) -> None:
    """Null, not a fabricated 'not started' row.

    Nothing recorded and a deliberate "this does not apply to us" are
    different answers, and only one of them means somebody looked.
    """
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    await _seed_obligation(client, scan_id=scan_id)

    listed = await client.get(
        f"/v1/projects/{project_id}/obligations", headers=_bearer_for(user)
    )

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"], "the fixture should produce one obligation"
    assert all(item["fulfilment"] is None for item in listed.json()["items"])


async def test_recording_does_not_shrink_the_obligation_list(client) -> None:
    """The failure this unit had to avoid.

    Attaching the record with a join would drop every obligation nobody has
    started. Recording against one obligation and finding the others gone is
    the shape that would pass every test written about the record itself.
    """
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic_a, ob_a = await _seed_obligation(client, scan_id=scan_id)
    _lic_b, _ob_b = await _seed_obligation(client, scan_id=scan_id)
    before = await client.get(
        f"/v1/projects/{project_id}/obligations", headers=_bearer_for(user)
    )

    await _record(client, user=user, project_id=project_id, obligation_id=ob_a)

    after = await client.get(
        f"/v1/projects/{project_id}/obligations", headers=_bearer_for(user)
    )

    assert before.json()["total"] == after.json()["total"]
    assert {i["id"] for i in before.json()["items"]} == {
        i["id"] for i in after.json()["items"]
    }
    recorded = next(i for i in after.json()["items"] if i["id"] == str(ob_a))
    untouched = next(i for i in after.json()["items"] if i["id"] != str(ob_a))
    assert recorded["fulfilment"]["status"] == "done"
    assert untouched["fulfilment"] is None


async def test_the_notice_does_not_change_when_work_is_recorded(client) -> None:
    """The record says somebody acted. It does not edit what they acted on.

    A fulfilment that could change the notice would be a way to make a
    compliance artefact say what somebody wished were true.
    """
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    headers = _bearer_for(user)
    before = await client.get(f"/v1/projects/{project_id}/notice", headers=headers)

    await _record(
        client,
        user=user,
        project_id=project_id,
        obligation_id=ob_id,
        status="not_applicable",
        evidence_note="we do not distribute this service",
    )

    after = await client.get(f"/v1/projects/{project_id}/notice", headers=headers)

    assert before.status_code == 200, before.text
    assert after.status_code == 200, after.text
    # Not a vacuous comparison: the notice has to have said something in the
    # first place, or two empty documents would match and prove nothing.
    assert len(_without_the_stamp(before.text)) > 3
    # Everything but the generation stamp, which moves on its own.
    assert _without_the_stamp(before.text) == _without_the_stamp(after.text)


async def test_the_detail_read_carries_the_record_too(client) -> None:
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    await _record(
        client,
        user=user,
        project_id=project_id,
        obligation_id=ob_id,
        evidence_url="https://example.com/releases/1.0",
    )

    detail = await client.get(
        f"/v1/projects/{project_id}/obligations/{ob_id}", headers=_bearer_for(user)
    )

    assert detail.status_code == 200, detail.text
    assert detail.json()["fulfilment"]["status"] == "done"
    assert detail.json()["fulfilment"]["evidence_url"] == (
        "https://example.com/releases/1.0"
    )


async def test_marking_done_records_when_and_who(client) -> None:
    """A row that says done without saying when is not a record of anything."""
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)

    row = await _record(client, user=user, project_id=project_id, obligation_id=ob_id)

    assert row["completed_at"] is not None
    assert row["completed_by_user_id"] == str(user.id)


async def test_reopening_clears_the_completion(client) -> None:
    """Otherwise the row says "not done, finished on Tuesday"."""
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    done = await _record(client, user=user, project_id=project_id, obligation_id=ob_id)

    reopened = await client.put(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers={**_bearer_for(user), "If-Match": f'"{done["version"]}"'},
        json={"status": "in_progress"},
    )

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["completed_at"] is None
    assert reopened.json()["completed_by_user_id"] is None


async def test_a_stale_version_is_refused(client) -> None:
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    await _record(client, user=user, project_id=project_id, obligation_id=ob_id)

    response = await client.put(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers={**_bearer_for(user), "If-Match": '"999"'},
        json={"status": "in_progress"},
    )

    assert response.status_code == 412, response.text


async def test_an_assignee_from_another_team_is_refused(client) -> None:
    """A name that makes the row look owned while nobody has been asked.

    Worse than leaving it unassigned, which at least reads as waiting for
    somebody.
    """
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    _org2, _team2, outsider = await _seed_team_with_user(client)

    response = await client.put(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers=_bearer_for(user),
        json={"status": "in_progress", "assignee_user_id": str(outsider.id)},
    )

    assert response.status_code == 422, response.text


async def test_clearing_puts_it_back_to_nothing_recorded(client) -> None:
    """Distinct from not-applicable, which is a judgement worth keeping."""
    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    await _record(client, user=user, project_id=project_id, obligation_id=ob_id)

    cleared = await client.delete(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers=_bearer_for(user),
    )
    listed = await client.get(
        f"/v1/projects/{project_id}/obligations", headers=_bearer_for(user)
    )

    assert cleared.status_code == 204, cleared.text
    assert all(item["fulfilment"] is None for item in listed.json()["items"])


async def test_another_teams_project_cannot_be_recorded_against(client) -> None:
    _org, team, owner = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    _org2, _team2, outsider = await _seed_team_with_user(client)

    response = await client.put(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers=_bearer_for(outsider),
        json={"status": "done"},
    )

    assert response.status_code == 404, response.text


async def test_a_viewer_may_read_but_not_record(client) -> None:
    """Recording is the engineer doing the release, not only their manager.

    A viewer is the one grade that is explicitly read-only, so it is where the
    line sits.
    """
    _org, team, viewer = await _seed_team_with_user(client, role="viewer")
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)

    listed = await client.get(
        f"/v1/projects/{project_id}/obligations", headers=_bearer_for(viewer)
    )
    recorded = await client.put(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers=_bearer_for(viewer),
        json={"status": "done"},
    )

    assert listed.status_code == 200, listed.text
    assert recorded.status_code == 403, recorded.text


async def test_recording_leaves_an_audit_row(client) -> None:
    """Who said the obligation was met, and when they said it.

    This is the half of the record an auditor reads. The row itself says the
    obligation is done; the audit trail says somebody put it there.
    """
    from sqlalchemy import select

    from models import AuditLog

    _org, team, user = await _seed_team_with_user(client)
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)

    await _record(client, user=user, project_id=project_id, obligation_id=ob_id)

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.target_table == "obligation_fulfilments")
            )
        ).scalars().all()

    assert rows, "the write should have produced an audit row"
    assert rows[-1].actor_user_id == user.id


async def test_a_developer_elsewhere_is_still_a_viewer_here(client) -> None:
    """The grade that counts is the one held on this project's team.

    Somebody who is a developer on one team and a viewer on another passes the
    route gate on their highest grade. If the service also judged them by that
    grade, being trusted anywhere would make them trusted everywhere, which is
    cross-team escalation rather than a permission.
    """
    factory = await _factory(client)
    _org_a, team_a, user = await _seed_team_with_user(client, role="developer")
    _org_b, team_b, _other = await _seed_team_with_user(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team
        from models import User as UserModel

        team = (
            await session.execute(select(Team).where(Team.id == team_b.id))
        ).scalar_one()
        actor = (
            await session.execute(select(UserModel).where(UserModel.id == user.id))
        ).scalar_one()
        await make_membership(session, user=actor, team=team, role="viewer")
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team_b.id)
    _lic, ob_id = await _seed_obligation(client, scan_id=scan_id)
    assert team_a.id != team_b.id

    response = await client.put(
        f"/v1/projects/{project_id}/obligations/{ob_id}/fulfilment",
        headers=_bearer_for(user),
        json={"status": "done"},
    )

    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# NOTICE templates (D10, N21)
# ---------------------------------------------------------------------------


async def test_notice_template_write_requires_super_admin(client) -> None:
    org, _team, user = await _seed_team_with_user(client, role="team_admin")

    response = await client.put(
        f"/v1/notice-templates/org/{org.id}/text",
        headers=_bearer_for(user),
        json={"preface": "Internal only."},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_notice_template_requires_preface_or_footer(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)

    response = await client.put(
        f"/v1/notice-templates/org/{org.id}/text",
        headers=_bearer_for(admin),
        json={},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_notice_template_unknown_format_is_refused(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)

    response = await client.put(
        f"/v1/notice-templates/org/{org.id}/pdf",
        headers=_bearer_for(admin),
        json={"preface": "x"},
    )
    assert response.status_code == 422


async def test_notice_template_read_404_when_none_written(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        user = await make_user(session)

    response = await client.get(
        f"/v1/notice-templates/org/{org.id}/text",
        headers=_bearer_for(user),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_notice_template_round_trips_and_appears_in_the_document(client) -> None:
    """A super admin's template reaches the NOTICE a developer downloads."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    org, team, user = await _seed_team_with_user(client, role="developer")
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    await _seed_obligation(client, scan_id=scan_id, kind="notice")

    put_response = await client.put(
        f"/v1/notice-templates/org/{org.id}/text",
        headers=_bearer_for(admin),
        json={"preface": "Internal distribution only.", "footer": "Example Corp."},
    )
    assert put_response.status_code == 200, put_response.text

    notice_response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=_bearer_for(user),
    )
    assert notice_response.status_code == 200, notice_response.text
    body = notice_response.text
    assert "Internal distribution only." in body
    assert "Example Corp." in body


async def test_notice_template_deleted_reverts_to_the_untemplated_document(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    org, team, user = await _seed_team_with_user(client, role="developer")
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team.id)
    await _seed_obligation(client, scan_id=scan_id, kind="notice")

    await client.put(
        f"/v1/notice-templates/org/{org.id}/text",
        headers=_bearer_for(admin),
        json={"preface": "Internal distribution only."},
    )
    delete_response = await client.delete(
        f"/v1/notice-templates/org/{org.id}/text",
        headers=_bearer_for(admin),
    )
    assert delete_response.status_code == 204, delete_response.text

    notice_response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=_bearer_for(user),
    )
    assert "Internal distribution only." not in notice_response.text


async def test_notice_template_scoped_to_its_own_organization(client) -> None:
    """A template written for one organization must not leak into another's
    NOTICE, the whole point of scoping this to `organization_id`."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    org_a, _team_a, _user_a = await _seed_team_with_user(client)
    _org_b, team_b, user_b = await _seed_team_with_user(client, role="developer")
    project_id, scan_id, _name = await _seed_scanned_project(client, team_id=team_b.id)
    await _seed_obligation(client, scan_id=scan_id, kind="notice")

    await client.put(
        f"/v1/notice-templates/org/{org_a.id}/text",
        headers=_bearer_for(admin),
        json={"preface": "Org A only."},
    )

    notice_response = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=_bearer_for(user_b),
    )
    assert "Org A only." not in notice_response.text
