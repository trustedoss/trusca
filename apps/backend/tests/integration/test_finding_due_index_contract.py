# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The due index's closed-status list is a copy, so it is compared (ER28a).

``services.policy_gate._CLOSED_FINDING_STATUSES`` is the vocabulary for "this
finding is finished". Migration 0081 needs the same list inside a partial index
predicate, and DDL cannot import Python. That makes it a copy, and rule 2 says
a copy gets an equality test.

What makes this copy worth the trouble: a partial index whose predicate stops
matching the query is NOT an error. Postgres simply stops using it. Nothing
fails, nothing logs, and the only symptom is a query that got slower. So the
test has to be the thing that notices.

Both directions, because they fail differently:
  - the migration CONSTANT against the Python tuple catches editing one and not
    the other in source;
  - the LIVE index predicate against the Python tuple catches a database whose
    index was built from an older revision, which the constant alone cannot see.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping due index contract")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture
async def db_session():  # noqa: ANN201
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


def _migration_constant() -> frozenset[str]:
    import importlib.util

    path = (
        BACKEND_ROOT / "alembic" / "versions" / "0081_finding_assignment.py"
    )
    spec = importlib.util.spec_from_file_location("_m0081", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return frozenset(module.CLOSED_STATUSES_IN_DUE_INDEX)


def test_the_migration_constant_matches_the_gate_vocabulary() -> None:
    from services.policy_gate import _CLOSED_FINDING_STATUSES

    assert _migration_constant() == frozenset(_CLOSED_FINDING_STATUSES), (
        "the due index's closed-status list has drifted from the gate's. A "
        "partial index whose predicate no longer matches the query is not an "
        "error: Postgres stops using it and the only symptom is a slower query."
    )


async def test_the_live_index_predicate_matches_the_gate_vocabulary(
    db_session,  # noqa: ANN001 - session fixture
) -> None:
    """Parsed as a SET, not matched as a string.

    A substring check would pass or fail on the order Postgres chose to print
    the predicate in, or on its spacing, neither of which is the thing being
    protected.
    """
    from services.policy_gate import _CLOSED_FINDING_STATUSES

    definition = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_vuln_findings_due'"
            )
        )
    ).scalar_one_or_none()
    assert definition is not None, "the due index is missing entirely"

    # Postgres renders each literal with its enum cast, e.g.
    #   (status <> 'fixed'::vuln_finding_status)
    in_index = frozenset(re.findall(r"status <> '([a-z_]+)'", definition))
    assert in_index == frozenset(_CLOSED_FINDING_STATUSES), (
        f"the live index excludes {sorted(in_index)} but the gate closes "
        f"{sorted(_CLOSED_FINDING_STATUSES)}"
    )


async def test_the_index_still_covers_only_dated_rows(db_session) -> None:  # noqa: ANN001
    """The other half of the predicate. Indexing undated rows would defeat the
    reason it is partial: most findings have no deadline."""
    definition = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_vuln_findings_due'"
            )
        )
    ).scalar_one()
    assert "due_on IS NOT NULL" in definition


async def test_suppressed_is_deliberately_not_closed(db_session) -> None:  # noqa: ANN001
    """A suppressed finding keeps its deadline, matching the gate and the SLA
    sweep. Pinned because it is the one value a reader is most likely to assume
    belongs in the closed set, and adding it would silently stop those findings
    being counted as overdue."""
    from services.policy_gate import _CLOSED_FINDING_STATUSES

    assert "suppressed" not in _CLOSED_FINDING_STATUSES
    definition = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_vuln_findings_due'"
            )
        )
    ).scalar_one()
    assert "suppressed" not in definition
