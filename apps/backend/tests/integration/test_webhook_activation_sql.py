# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The activation SQL in the guide actually activates a webhook (ER70).

Webhook activation is operator-only in this release: the guide hands out an
UPDATE and the operator runs it. That makes the statement part of the product,
and it was wrong. It set `webhook_secret` and left `webhook_provider` NULL,
while the gateway looks a project up by git URL AND secret AND provider. Every
delivery was refused. The operator had done exactly what the page said, and the
only symptom was that scans never started.

Hardening rule 4: the guide is an oracle. So this reads the statement out of
the page, runs it, and asks the gateway's own lookup whether the project is now
found. A page that drifts from what the gateway needs fails here rather than in
somebody's deployment.

Reading the SQL from the document rather than restating it is the point. A copy
here would be a second thing to keep right, and the one the operator pastes is
the one on the page.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.webhook_service import _find_project_by_git_url
from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_project, make_team

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


def _activation_sql(page: Path) -> str:
    """The UPDATE the page tells an operator to run.

    Found by its docs-uat id rather than by position, so reordering the page
    does not silently pick up a different block.
    """
    text_ = page.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- docs-uat: id=webhooks-secret-sql[^>]*-->\s*```sql\n(.*?)```",
        text_,
        re.S,
    )
    assert match, f"{page.name} no longer carries a block tagged webhooks-secret-sql"
    return match.group(1)


def test_the_two_mirrors_give_the_same_statement() -> None:
    """A Korean-speaking operator has to get a working webhook too."""
    assert _activation_sql(GUIDE) == _activation_sql(GUIDE_KO), (
        "the activation SQL differs between the English and Korean pages, so "
        "one set of operators is following a different procedure"
    )


@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_running_the_documented_sql_makes_the_gateway_find_the_project(
    session: AsyncSession, provider: str
) -> None:
    """The whole point: paste, run, and deliveries are accepted.

    Both providers, because the statement carries the value as a comment
    alternative and a page that only worked for one would leave the other set
    of operators exactly where this started.
    """
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    slug = uuid.uuid4().hex[:12]
    project = await make_project(
        session, team=team, git_url=f"https://example.com/acme/{slug}"
    )
    await session.commit()

    assert project.webhook_secret is None
    assert project.webhook_provider is None
    assert (
        await _find_project_by_git_url(
            session, project.git_url, expected_provider=provider
        )
        is None
    ), "the project matched before activation, so the check below proves nothing"

    statement = _activation_sql(GUIDE)
    if provider == "gitlab":
        statement = statement.replace("'github'", "'gitlab'")
    await session.execute(
        text(statement.replace("'<project-uuid>'", f"'{project.id}'"))
    )
    await session.commit()

    found = await _find_project_by_git_url(
        session, project.git_url, expected_provider=provider
    )
    assert found is not None, (
        "an operator who ran the documented statement still has a project the "
        "gateway cannot find, so every delivery is refused and the only "
        "symptom is that scans never start"
    )
    assert found.id == project.id


async def test_the_statement_sets_both_columns(session: AsyncSession) -> None:
    """Named separately from the lookup so a failure says which half is wrong.

    The lookup test above would also fail if the gateway's query changed. This
    one fails only if the statement stopped writing what it has to write.
    """
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(
        session, team=team, git_url=f"https://example.com/acme/{uuid.uuid4().hex[:12]}"
    )
    await session.commit()

    await session.execute(
        text(_activation_sql(GUIDE).replace("'<project-uuid>'", f"'{project.id}'"))
    )
    await session.commit()
    await session.refresh(project)

    assert project.webhook_secret, "the statement no longer sets a secret"
    assert project.webhook_provider == "github", (
        "the statement no longer sets a provider, which is the half that was "
        f"missing and made every delivery fail; got {project.webhook_provider!r}"
    )
