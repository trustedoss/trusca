"""
Integration test for the license-fetcher cache layer + scan_source wiring.

Drives the dispatcher against a real Postgres ``license_fetch_cache``
table (Alembic migration 0004) with a stubbed in-process fetcher.
We pin:

  - First call → cache miss → fetcher invoked → row written.
  - Second call within TTL → cache hit → fetcher NOT invoked again.
  - ``_persist_components`` short-circuits to the cache for components
    cdxgen left licence-empty, emitting a ``concluded`` LicenseFinding.

The HTTP layer is bypassed entirely — the fetcher class itself is
covered by the unit suite. Here we focus on the cache UPSERT + the
scan-pipeline integration.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import integrations.license_fetcher as dispatcher_mod
from integrations.license_fetcher.base import LicenseFetchResult
from models import LicenseFetchCache

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip license_fetcher integration")
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
            f"alembic upgrade head failed; license-fetcher integration cannot run\n"
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


class _StubFetcher:
    """Fetcher double — counts calls, returns a fixed answer."""

    source = "stub_registry"

    def __init__(self, result: LicenseFetchResult | None) -> None:
        self.result = result
        self.calls = 0

    def fetch(
        self, purl: str, *, timeout: float = 30.0  # noqa: ARG002
    ) -> LicenseFetchResult | None:
        self.calls += 1
        return self.result

    def close(self) -> None:
        pass


def _unique_purl() -> str:
    """Build a per-test PURL so the table never collides with prior runs.

    We include the test-unique suffix in the *component coord* (group/
    artifact) as well — the Component table has a unique index on
    ``purl`` (the version-stripped form), and tests that create a
    Component row directly would otherwise collide across reruns.
    """
    suffix = uuid.uuid4().hex[:12]
    return f"pkg:maven/test.fetcher/foo-{suffix}@1.0.0"


# ---------------------------------------------------------------------------
# Cache miss → write
# ---------------------------------------------------------------------------


def test_dispatcher_writes_positive_cache_row(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    purl = _unique_purl()
    stub = _StubFetcher(
        LicenseFetchResult(
            spdx_id="Apache-2.0",
            reference_url="https://example.invalid/LICENSE",
            source="stub_registry",
        )
    )
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER,
        "pkg:maven/",
        lambda: stub,
    )

    result = dispatcher_mod.fetch_license(purl, session=sync_session)
    sync_session.commit()

    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert stub.calls == 1

    cached = sync_session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
    ).scalar_one()
    assert cached.spdx_id == "Apache-2.0"
    assert cached.is_negative is False
    assert cached.source == "stub_registry"

    # Second call inside TTL — cache hit, fetcher not re-called.
    result2 = dispatcher_mod.fetch_license(purl, session=sync_session)
    assert result2 is not None
    assert result2.spdx_id == "Apache-2.0"
    assert stub.calls == 1


def test_dispatcher_writes_negative_cache_row_for_unmapped_response(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    purl = _unique_purl()
    stub = _StubFetcher(None)
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER,
        "pkg:maven/",
        lambda: stub,
    )

    result = dispatcher_mod.fetch_license(purl, session=sync_session)
    sync_session.commit()
    assert result is None
    assert stub.calls == 1

    cached = sync_session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
    ).scalar_one()
    assert cached.is_negative is True
    assert cached.spdx_id is None

    # Repeat — negative cache short-circuits, fetcher untouched.
    result2 = dispatcher_mod.fetch_license(purl, session=sync_session)
    assert result2 is None
    assert stub.calls == 1


def test_dispatcher_refetches_after_ttl_expiry(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    purl = _unique_purl()
    stub = _StubFetcher(
        LicenseFetchResult(spdx_id="MIT", reference_url=None, source="stub_registry")
    )
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER,
        "pkg:maven/",
        lambda: stub,
    )

    # Seed the cache with an expired row (fetched_at = 2 days ago).
    stale = datetime.now(UTC) - timedelta(days=2)
    seed = LicenseFetchCache(
        purl=purl,
        spdx_id="Apache-2.0",
        reference_url=None,
        source="stub_registry",
        is_negative=False,
        fetched_at=stale,
    )
    sync_session.add(seed)
    sync_session.commit()

    result = dispatcher_mod.fetch_license(purl, session=sync_session)
    sync_session.commit()

    assert result is not None
    assert result.spdx_id == "MIT"  # the new answer overrode the stale one
    assert stub.calls == 1

    # The row is now updated in place.
    sync_session.expire_all()
    refreshed = sync_session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
    ).scalar_one()
    assert refreshed.spdx_id == "MIT"
    assert refreshed.fetched_at > stale


def test_dispatcher_skips_fetcher_when_cdxgen_already_has_license(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan_source wiring only calls the fetcher on cdxgen-empty components.

    We exercise the contract by calling ``_persist_component_licenses``
    directly with a cdxgen component that *does* have a license — the
    fetcher must not be invoked, and no cache row should be written.
    """
    from tasks.scan_source import _persist_component_licenses

    purl = _unique_purl()
    stub = _StubFetcher(
        LicenseFetchResult(spdx_id="GPL-3.0-only", reference_url=None, source="stub_registry")
    )
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER,
        "pkg:maven/",
        lambda: stub,
    )

    # Seed minimal Component / ComponentVersion / Scan / Project rows so the
    # FK constraints on LicenseFinding are satisfied.
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from models import Component, ComponentVersion
    from models import Scan as ScanModel
    from tests._helpers import (
        make_membership,
        make_organization,
        make_project,
        make_team,
        make_user,
    )

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            scan = ScanModel(
                project_id=project.id,
                kind="source",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.flush()
            # Re-using the same Component PURL across test runs breaks
            # `uq_components_purl`; derive a unique component purl from
            # the per-test versioned PURL (everything before the @).
            base_purl = purl.split("@", 1)[0]
            comp = Component(
                purl=base_purl,
                package_type="maven",
                name="foo",
            )
            s.add(comp)
            await s.flush()
            cv = ComponentVersion(
                component_id=comp.id,
                version="1.0.0",
                purl_with_version=purl,
            )
            s.add(cv)
            await s.commit()
            scan_id = scan.id
            cv_id = cv.id
        await engine.dispose()
        return scan_id, cv_id

    scan_id, component_version_id = asyncio.run(_build())

    # cdxgen *already* knows the license → fetcher must NOT run.
    cdxgen_component = {
        "purl": purl,
        "name": "foo",
        "version": "1.0.0",
        "licenses": [{"license": {"id": "MIT"}}],
    }
    _persist_component_licenses(
        sync_session,
        scan_uuid=scan_id,
        component_version_id=component_version_id,
        cdxgen_component=cdxgen_component,
        purl=purl,
    )
    sync_session.commit()
    assert stub.calls == 0
    cached = sync_session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
    ).scalar_one_or_none()
    # No cache row — the fetcher path was never taken.
    assert cached is None


def _seed_component_rows(purl: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed the minimal Project/Scan/Component/ComponentVersion rows a
    ``_persist_component_licenses`` call needs, returning ``(scan_id, cv_id)``.

    Mirrors the inline seed in the skip-test above; factored out so the W8-#48
    gate tests do not duplicate 40 lines of async setup.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url
    from models import Component, ComponentVersion
    from models import Scan as ScanModel
    from tests._helpers import (
        make_membership,
        make_organization,
        make_project,
        make_team,
        make_user,
    )

    async def _build() -> tuple[uuid.UUID, uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            scan = ScanModel(
                project_id=project.id,
                kind="source",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.flush()
            comp = Component(
                purl=purl.split("@", 1)[0],
                package_type="maven",
                name="foo",
            )
            s.add(comp)
            await s.flush()
            cv = ComponentVersion(
                component_id=comp.id,
                version="1.0.0",
                purl_with_version=purl,
            )
            s.add(cv)
            await s.commit()
            return scan.id, cv.id

    scan_id, cv_id = asyncio.run(_build())
    return scan_id, cv_id


@pytest.mark.parametrize(
    "flag_value,should_fetch",
    [
        (None, True),        # unset → default on → fetcher runs
        ("true", True),      # explicit on
        ("false", False),    # air-gap off → fetcher skipped
        ("0", False),
        ("no", False),
    ],
)
def test_license_fetch_gate_controls_registry_egress(
    sync_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    flag_value: str | None,
    should_fetch: bool,
) -> None:
    """W8-#48: LICENSE_FETCH_ENABLED gates the post-cdxgen registry fetch.

    An unlicensed cdxgen component (a bare ``requirements.txt`` component has
    no ``licenses[]``) triggers the fetcher only when the gate is on. When
    off (the air-gap posture), the fetcher is never invoked, no network egress
    happens, and no ``concluded`` LicenseFinding / cache row is written — the
    component simply stays license-unknown.
    """
    from models import LicenseFinding
    from tasks.scan_source import _persist_component_licenses

    if flag_value is None:
        monkeypatch.delenv("LICENSE_FETCH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LICENSE_FETCH_ENABLED", flag_value)

    # _unique_purl yields a pkg:maven/ purl; stub that fetcher so a gated-on
    # fetch hits the stub (counting the call) instead of real registry egress.
    purl = _unique_purl()
    stub = _StubFetcher(
        LicenseFetchResult(spdx_id="Apache-2.0", reference_url=None, source="stub_registry")
    )
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER, "pkg:maven/", lambda: stub
    )

    scan_id, cv_id = _seed_component_rows(purl)

    # cdxgen produced NO license for this component (the bare-manifest case).
    cdxgen_component = {"purl": purl, "name": "foo", "version": "1.0.0"}
    _persist_component_licenses(
        sync_session,
        scan_uuid=scan_id,
        component_version_id=cv_id,
        cdxgen_component=cdxgen_component,
        purl=purl,
    )
    sync_session.commit()

    if should_fetch:
        assert stub.calls == 1, "gate on → fetcher must run"
        findings = sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.component_version_id == cv_id
            )
        ).scalars().all()
        assert any(f.kind == "concluded" for f in findings)
    else:
        assert stub.calls == 0, "gate off → fetcher must not run (air-gap)"
        cached = sync_session.execute(
            select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
        ).scalar_one_or_none()
        assert cached is None
        findings = sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.component_version_id == cv_id
            )
        ).scalars().all()
        assert findings == []
