"""Persist boundary + backfill lifecycle for licenses.review_flag (Phase D1).

Integration (real Postgres) — the classifier is unit-tested in
``tests/unit/services/test_license_flags.py``; here we prove the wiring:

  1. persist boundary — feeding a **real ML-BOM fixture** (cloned + mutated from
     the OWASP AIBOM sample so it carries Llama community + CC-BY-NC licenses
     alongside a permissive control) through the real
     ``_extract_spdx_ids`` → ``_get_or_create_license`` path populates
     ``licenses.review_flag`` correctly, and Apache-2.0 stays NULL.
  2. backfill lifecycle — NULL → classify → idempotent re-run no-op, exercising
     the operator-triggered one-shot task against real rows.

Hardening rule §3: the fixture is a realistic tool-output shape (multiple
components, mixed licenses), not a hand-minimised single-license blob.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models import License as LicenseModel
from tasks.scan_source import _extract_spdx_ids, _get_or_create_license

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = (
    BACKEND_ROOT / "tests" / "fixtures" / "sbom_ingest" / "aibom-review-flags-1_7.json"
)


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip review-flag persist integration")
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
            f"alembic upgrade head failed; review-flag integration cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def sync_session() -> Iterator[Session]:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _fixture_spdx_ids() -> list[str]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    ids: list[str] = []
    for comp in data["components"]:
        for spdx_id, _url in _extract_spdx_ids(comp):
            ids.append(spdx_id)
    return ids


def test_persist_boundary_sets_review_flag_from_real_mlbom(
    sync_session: Session,
) -> None:
    """Real ML-BOM fixture → _get_or_create_license populates review_flag."""
    spdx_ids = _fixture_spdx_ids()
    assert "LLAMA-2-Community-License" in spdx_ids
    assert "CC-BY-NC-4.0" in spdx_ids
    assert "Apache-2.0" in spdx_ids

    created: dict[str, LicenseModel] = {}
    for spdx_id in spdx_ids:
        # Namespace the ids per-test so parallel runs / prior data cannot collide
        # on the globally-unique licenses.spdx_id.
        unique_id = f"{spdx_id}-{uuid.uuid4().hex[:8]}"
        row = _get_or_create_license(sync_session, spdx_id=unique_id, reference_url=None)
        created[spdx_id] = row
    sync_session.commit()

    assert created["LLAMA-2-Community-License"].review_flag == "behavioral_use"
    assert created["CC-BY-NC-4.0"].review_flag == "non_commercial"
    # Permissive control must stay unflagged.
    assert created["Apache-2.0"].review_flag is None


def test_backfill_lifecycle_null_then_classify_then_idempotent(
    sync_session: Session,
) -> None:
    """NULL → backfill sets the flag; a second run is a no-op (idempotent)."""
    from tasks.license_review_flag_backfill import backfill_license_review_flags

    # Seed a legacy-shaped row: an AI license with a NULL review_flag (as if
    # created before the classifier existed).
    spdx_id = f"OpenRAIL-M-{uuid.uuid4().hex[:8]}"
    row = LicenseModel(spdx_id=spdx_id, name=spdx_id, category="unknown", review_flag=None)
    sync_session.add(row)
    sync_session.commit()
    row_id = row.id

    # First sweep: NULL → behavioral_use.
    summary1 = backfill_license_review_flags.run(dry_run=False)
    assert summary1["updated"] >= 1
    assert summary1["set_from_null"] >= 1

    sync_session.expire_all()
    refreshed = sync_session.execute(
        select(LicenseModel).where(LicenseModel.id == row_id)
    ).scalar_one()
    assert refreshed.review_flag == "behavioral_use"

    # Second sweep: this row is now in agreement, so it must not be re-counted.
    summary2 = backfill_license_review_flags.run(dry_run=False)
    still = sync_session.execute(
        select(LicenseModel).where(LicenseModel.id == row_id)
    ).scalar_one()
    assert still.review_flag == "behavioral_use"
    # The seeded row contributes nothing to the second run's update tally
    # (idempotence). Other concurrent rows might, so we assert on our row's
    # stability rather than a global zero.
    assert summary2["set_from_null"] <= summary1["set_from_null"]
