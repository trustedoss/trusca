"""W8-#46 regression — Maven classifier purl no longer collides on
``component_versions``.

Reproduced 2x against WebGoat v8.2.2 (com.github.jnr/jffi@1.3.1) via
scan-bench: cdxgen emitted both ``pkg:maven/.../jffi@1.3.1`` and
``pkg:maven/.../jffi@1.3.1?classifier=native&type=jar``, and the redundant
``uq_component_versions_component_version`` UNIQUE on (component_id, version)
collapsed the second insert into a UniqueViolation even though
``purl_with_version`` (the natural key) differed.

After alembic 0027 the only surviving constraint is the per-column UNIQUE on
``purl_with_version`` itself. This test pins the invariant: two
ComponentVersion rows that share (component_id, "1.3.1") but differ in the
qualifier-bearing purl_with_version must both persist without colliding.

See ``docs/scans/realworld-benchmark-2026-05-27.md`` for the original WebGoat
trace and ``apps/backend/alembic/versions/0027_drop_redundant_component_version_unique.py``.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from models.scan import Component, ComponentVersion

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip W8-#46 classifier integration")
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
            f"alembic upgrade head failed; W8-#46 integration cannot run\n"
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


def _suite_prefix() -> str:
    """Unique per-run prefix so cross-suite runs don't trip
    ``uq_components_purl`` on rerun. Mirrors the seed-component-prefix
    isolation pattern from PR #53 ([[feedback_seed_component_prefix_isolation]]).
    """
    return f"w846-{uuid.uuid4().hex[:8]}"


def test_maven_classifier_variants_coexist_under_same_version(
    sync_session: Session,
) -> None:
    """Two purls sharing (group, artefact, version) but differing in classifier
    qualifier must persist as two ComponentVersion rows hanging off two
    Component rows — the WebGoat com.github.jnr/jffi shape."""
    prefix = _suite_prefix()
    base = f"pkg:maven/com.github.jnr/{prefix}-jffi"

    main_component = Component(
        purl=base,
        package_type="maven",
        name=f"{prefix}-jffi",
        namespace="com.github.jnr",
    )
    native_component = Component(
        purl=f"{base}?classifier=native&type=jar",
        package_type="maven",
        name=f"{prefix}-jffi",
        namespace="com.github.jnr",
    )
    sync_session.add_all([main_component, native_component])
    sync_session.flush()

    main_cv = ComponentVersion(
        component_id=main_component.id,
        version="1.3.1",
        purl_with_version=f"{base}@1.3.1",
    )
    native_cv = ComponentVersion(
        component_id=native_component.id,
        version="1.3.1",
        purl_with_version=f"{base}@1.3.1?classifier=native&type=jar",
    )
    sync_session.add_all([main_cv, native_cv])

    # Pre-0027 this flush raised IntegrityError on
    # ``uq_component_versions_component_version`` for the WebGoat shape. With
    # 0027 it must succeed cleanly.
    sync_session.flush()

    persisted = sync_session.execute(
        select(ComponentVersion.purl_with_version)
        .where(ComponentVersion.version == "1.3.1")
        .where(ComponentVersion.purl_with_version.like(f"{base}@1.3.1%"))
        .order_by(ComponentVersion.purl_with_version)
    ).scalars().all()

    assert persisted == [
        f"{base}@1.3.1",
        f"{base}@1.3.1?classifier=native&type=jar",
    ]

    sync_session.rollback()


def test_duplicate_purl_with_version_still_blocked(sync_session: Session) -> None:
    """Sanity: dropping the redundant (component_id, version) constraint did
    not weaken the natural key. Two ComponentVersion rows with identical
    ``purl_with_version`` must still collide on
    ``uq_component_versions_purl_with_version``."""
    prefix = _suite_prefix()
    component = Component(
        purl=f"pkg:npm/{prefix}-fixture",
        package_type="npm",
        name=f"{prefix}-fixture",
    )
    sync_session.add(component)
    sync_session.flush()

    first = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"pkg:npm/{prefix}-fixture@1.0.0",
    )
    second = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"pkg:npm/{prefix}-fixture@1.0.0",
    )
    sync_session.add_all([first, second])

    with pytest.raises(sa.exc.IntegrityError):
        sync_session.flush()
    sync_session.rollback()
