"""The verify-specs baseline's webhook secrets are readable back out (E-401).

tests/verify-specs/specs/integrations.json:TC-INTG-05-000-secret-setup checks,
with raw SQL against the encrypted column, that fx-appr (github) and
scan-pipeline (gitlab) both carry a webhook secret after a default seed. That
SQL lives in a vendored spec we don't edit (PROVENANCE.md), so this is the
guard on our side: it exercises the same seeding path through the ORM and
decrypts the result, rather than trusting that a raw column check would still
mean the same thing after the next schema change to this area.

Regression this pins: the plaintext -> encrypted migration (0084-0086, PR
#379) moved these two projects from a column the spec's SQL could read
directly to one it can't (tests/verify-specs/excluded.json,
"integrations:TC-INTG-05-000-secret-setup"). If a future change to
seed_demo._seed_verify_baseline stops setting webhook_provider/
webhook_secret_encrypted on either project, this fails here instead of only
surfacing as a nightly SQL exclusion nobody is looking at.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


async def test_fx_appr_and_scan_pipeline_carry_a_readable_webhook_secret(
    db_factory: async_sessionmaker[Any],
) -> None:
    from core.crypto import decrypt_secret
    from models import Project
    from scripts import seed_demo

    await seed_demo._seed()

    async with db_factory() as session:
        fx = (
            await session.execute(
                select(Project).where(Project.name == "fx-appr")
            )
        ).scalar_one()
        pipeline = (
            await session.execute(
                select(Project).where(Project.name == "scan-pipeline")
            )
        ).scalar_one()

    assert fx.webhook_provider == "github"
    assert fx.webhook_secret_encrypted is not None
    assert decrypt_secret(fx.webhook_secret_encrypted) == "whsec_github_fxappr_seed_001"

    assert pipeline.webhook_provider == "gitlab"
    assert pipeline.webhook_secret_encrypted is not None
    assert decrypt_secret(pipeline.webhook_secret_encrypted) == "whsec_gitlab_intg_test_001"
