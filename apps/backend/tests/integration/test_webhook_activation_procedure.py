# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Following the guide actually activates a webhook (ER70, then E22a).

The question this file asks has not changed: an operator who does what the
page says ends up with a project the gateway can find. What the page says has
changed twice, and the test follows it rather than the other way round.

It used to hand out an UPDATE. That statement set ``webhook_secret`` and left
``webhook_provider`` NULL, the gateway matches on git URL AND secret AND
provider, and so every delivery was refused: the operator had done exactly
what the page said and the only symptom was that scans never started (ER70).

The column now holds Fernet ciphertext (0084-0086), which SQL cannot produce,
so activation moved into the product as
``POST /v1/projects/{id}/webhook-secret``. The procedure being tested is the
new one. That is not the test being patched to keep passing: its subject is
whatever the page tells an operator to do, and the page changed.

Two things are pinned that the earlier version could not pin. The response is
the only place the plaintext appears, and what the page promises about the
provider (required, both set together) is what the endpoint does.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import create_access_token
from services.webhook_service import _find_project_by_git_url
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
GUIDE = REPO_ROOT / "docs-site/docs/ci-integration/webhooks.md"
GUIDE_KO = (
    REPO_ROOT
    / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/ci-integration/webhooks.md"
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def app():  # noqa: ANN201
    import main as m

    return m.app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _team_admin_of(session: AsyncSession, team) -> dict[str, str]:  # noqa: ANN001
    """A caller who may activate a webhook on ``team``.

    The membership is what decides: ``require_role`` reads the grade derived
    from memberships, not the claim in the token, so a token minted as
    team_admin for somebody with no membership does not reach the route.
    """
    user = await make_user(session, full_name="Tara Admin")
    await make_membership(session, user=user, team=team, role="team_admin")
    await session.commit()
    return {
        "Authorization": (
            f"Bearer {create_access_token(subject=str(user.id), role='team_admin')}"
        )
    }


def _documented_request(page: Path) -> str:
    """The call the page tells an operator to make.

    Found by its docs-uat id rather than by position, so reordering the page
    does not silently pick up a different block.
    """
    text_ = page.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- docs-uat: id=webhooks-secret-issue[^>]*-->\s*```http\n(.*?)```",
        text_,
        re.S,
    )
    assert match, f"{page.name} no longer carries a block tagged webhooks-secret-issue"
    return match.group(1)


def test_the_two_mirrors_give_the_same_procedure() -> None:
    """A Korean-speaking operator has to get a working webhook too.

    A defect in a page exists twice when the page exists twice, and which half
    gets fixed is decided by which language the person fixing it reads.
    """
    assert _documented_request(GUIDE) == _documented_request(GUIDE_KO), (
        "the activation call differs between the English and Korean pages, so "
        "one set of operators is following a different procedure"
    )


def test_the_documented_call_matches_the_route_that_exists() -> None:
    """The page names a real method and path.

    Separate from the round trip below so that a page naming a route nobody
    implemented fails here, with a message about the page, rather than as a
    404 in the middle of a longer test.
    """
    from api.v1.projects import router

    documented = _documented_request(GUIDE).splitlines()[0]
    method, path = documented.split()[0], documented.split()[1]

    paths = {
        (m, getattr(r, "path", ""))
        for r in router.routes
        for m in getattr(r, "methods", set())
    }
    # The page writes the FastAPI path template verbatim.
    assert (method, f"/v1/projects{path.split('/v1/projects')[1]}") in paths, (
        f"the guide tells operators to call {method} {path}, which is not a "
        f"route this application serves"
    )


@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_following_the_guide_makes_the_gateway_find_the_project(
    session: AsyncSession,
    client: AsyncClient,
    provider: str,
) -> None:
    """The whole point: issue, paste, and deliveries are accepted.

    Both providers, because the page documents the field as taking either and
    a procedure that only worked for one would leave the other set of
    operators exactly where ER70 started.
    """
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    slug = uuid.uuid4().hex[:12]
    project = await make_project(
        session, team=team, git_url=f"https://example.com/acme/{slug}"
    )
    headers = await _team_admin_of(session, team)

    assert project.webhook_secret_encrypted is None
    assert (
        await _find_project_by_git_url(
            session, project.git_url, expected_provider=provider
        )
        is None
    ), "the project matched before activation, so the check below proves nothing"

    response = await client.post(
        f"/v1/projects/{project.id}/webhook-secret",
        json={"provider": provider},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["secret"], "the response carried no secret to paste into the SCM"
    assert body["replaced_existing"] is False

    found = await _find_project_by_git_url(
        session, project.git_url, expected_provider=provider
    )
    assert found is not None, (
        "an operator who followed the documented procedure still has a project "
        "the gateway cannot find, so every delivery is refused and the only "
        "symptom is that scans never start"
    )
    assert found.id == project.id


async def test_the_call_sets_both_fields(
    session: AsyncSession,
    client: AsyncClient,
) -> None:
    """Named separately from the lookup so a failure says which half is wrong.

    The lookup test above would also fail if the gateway's query changed. This
    one fails only if activation stopped writing what it has to write, which
    is the half ER70 was about.
    """
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(
        session, team=team, git_url=f"https://example.com/acme/{uuid.uuid4().hex[:12]}"
    )
    headers = await _team_admin_of(session, team)

    response = await client.post(
        f"/v1/projects/{project.id}/webhook-secret",
        json={"provider": "github"},
        headers=headers,
    )
    assert response.status_code == 201, response.text

    await session.refresh(project)
    assert project.webhook_secret_encrypted, "activation no longer stores a secret"
    assert project.webhook_provider == "github", (
        "activation no longer sets a provider, which is the half that was "
        f"missing and made every delivery fail; got {project.webhook_provider!r}"
    )


async def test_the_stored_value_is_not_the_secret(
    session: AsyncSession,
    client: AsyncClient,
) -> None:
    """What the column holds is ciphertext, and it decrypts back.

    Both halves. Asserting only that the column differs from the plaintext
    would pass for any transformation including a broken one, and asserting
    only that it round-trips would pass if the column held the plaintext.
    """
    from core.crypto import decrypt_secret

    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(
        session, team=team, git_url=f"https://example.com/acme/{uuid.uuid4().hex[:12]}"
    )
    headers = await _team_admin_of(session, team)

    response = await client.post(
        f"/v1/projects/{project.id}/webhook-secret",
        json={"provider": "gitlab"},
        headers=headers,
    )
    secret = response.json()["secret"]

    await session.refresh(project)
    stored = project.webhook_secret_encrypted
    assert stored is not None
    assert secret not in stored, (
        "the plaintext secret is recoverable from the stored column by reading "
        "it, which is what encrypting it was supposed to stop"
    )
    assert decrypt_secret(stored) == secret, (
        "the stored ciphertext does not decrypt back to the issued secret, so "
        "the gateway will refuse every delivery for this project"
    )
