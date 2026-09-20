"""
A fingerprint match that a package manager already found is one row (real Postgres).

SCANOSS names a library by its GitHub repository while the package manager names
it by the registry package, so the same library used to appear twice in one
scan: once declared, once vendored. ``_persist_vendored_components`` now folds
the fingerprint match into the declared row when the two agree on identity AND
version, and says so on the row.

The rule has to be narrow, because the failure it must not have is silent: a
merge that joins two DIFFERENT libraries removes a real component from the
inventory and nothing reports it. So the negative cases below carry as much
weight as the positive one.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from integrations.scanoss import VendoredComponent
from models import Component, ComponentVersion, ScanComponent
from tasks.scan_source import _persist_vendored_components
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _seed_scan() -> uuid.UUID:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from tests._helpers import (
        make_organization,
        make_project,
        make_scan,
        make_team,
        make_user,
    )

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            project = await make_project(s, team=team, git_url=None)
            scan = await make_scan(s, project=project, requested_by=user, status="running")
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _declared(session: Session, scan_id: uuid.UUID, *, name: str, version: str) -> ScanComponent:
    """What cdxgen leaves behind: one npm package, declared by the manifest."""
    component = Component(purl=f"pkg:npm/{name}", package_type="npm", name=name)
    session.add(component)
    session.flush()
    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"pkg:npm/{name}@{version}",
    )
    session.add(cv)
    session.flush()
    row = ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True)
    session.add(row)
    session.commit()
    return row


def _rows(session: Session, scan_id: uuid.UUID) -> list[ScanComponent]:
    return list(
        session.execute(select(ScanComponent).where(ScanComponent.scan_id == scan_id)).scalars()
    )


def _vendored(
    tag: str, *, version: str, alternatives: tuple[str, ...], purl: str | None = None
) -> VendoredComponent:
    return VendoredComponent(
        purl=purl or f"pkg:github/acme{tag}/widget{tag}",
        name=f"widget{tag}",
        version=version,
        licenses=["MIT"],
        matched_files=3,
        alternative_purls=alternatives,
    )


def test_same_library_under_two_identities_is_one_row_with_the_evidence(
    sync_session: Session,
) -> None:
    scan_id = _seed_scan()
    tag = uuid.uuid4().hex[:10]
    declared = _declared(sync_session, scan_id, name=f"widget{tag}", version="4.17.21")

    created = _persist_vendored_components(
        sync_session,
        scan_uuid=scan_id,
        # "v4.17.21" against "4.17.21": a leading v is not a different version.
        vendored=[_vendored(tag, version="v4.17.21", alternatives=(f"pkg:npm/widget{tag}",))],
    )
    sync_session.commit()

    assert created == 0
    rows = _rows(sync_session, scan_id)
    assert [r.id for r in rows] == [declared.id]
    sync_session.refresh(declared)
    assert declared.raw_data is not None
    evidence = declared.raw_data["fingerprint_match"]
    assert evidence["purl"] == f"pkg:github/acme{tag}/widget{tag}"
    assert evidence["matched_files"] == 3
    assert evidence["licenses"] == ["MIT"]
    # No catalog rows were made for the fingerprint's own identity either.
    assert (
        sync_session.execute(
            select(func.count())
            .select_from(Component)
            .where(Component.purl == f"pkg:github/acme{tag}/widget{tag}")
        ).scalar_one()
        == 0
    )


def test_a_different_version_is_a_second_copy_and_stays_a_second_row(
    sync_session: Session,
) -> None:
    """Same identity, different version: the tree holds two copies of the library."""
    scan_id = _seed_scan()
    tag = uuid.uuid4().hex[:10]
    _declared(sync_session, scan_id, name=f"widget{tag}", version="4.17.21")

    created = _persist_vendored_components(
        sync_session,
        scan_uuid=scan_id,
        vendored=[_vendored(tag, version="3.10.1", alternatives=(f"pkg:npm/widget{tag}",))],
    )
    sync_session.commit()

    assert created == 1
    assert len(_rows(sync_session, scan_id)) == 2


def test_a_shared_name_without_shared_identity_is_not_merged(
    sync_session: Session,
) -> None:
    """Two libraries that happen to share a name. Nothing in the fingerprint's
    identity list points at the npm package, so they stay two."""
    scan_id = _seed_scan()
    tag = uuid.uuid4().hex[:10]
    _declared(sync_session, scan_id, name=f"widget{tag}", version="1.0.0")

    created = _persist_vendored_components(
        sync_session,
        scan_uuid=scan_id,
        vendored=[_vendored(tag, version="1.0.0", alternatives=("pkg:apk/other",))],
    )
    sync_session.commit()

    assert created == 1
    assert len(_rows(sync_session, scan_id)) == 2


def test_two_fingerprint_components_are_never_merged_into_each_other(
    sync_session: Session,
) -> None:
    """The index skips fingerprint rows, so a vendored twin stays what the
    service said it was, as the recorded-response tests already require."""
    scan_id = _seed_scan()
    tag = uuid.uuid4().hex[:10]
    first = _vendored(tag, version="1.0.0", alternatives=(f"pkg:npm/widget{tag}",))
    second = _vendored(
        tag,
        version="1.0.0",
        purl=f"pkg:golang/github.com/acme{tag}/widget{tag}",
        alternatives=(f"pkg:github/acme{tag}/widget{tag}",),
    )

    # Two separate calls, so the first call's row is already in the scan when
    # the second call builds its index. In one call the index is built before
    # any fingerprint row exists and cannot show whether they are excluded; a
    # version of this test that used one call survived the mutation that
    # includes them.
    assert _persist_vendored_components(sync_session, scan_uuid=scan_id, vendored=[first]) == 1
    sync_session.commit()
    assert _persist_vendored_components(sync_session, scan_uuid=scan_id, vendored=[second]) == 1
    sync_session.commit()

    rows = _rows(sync_session, scan_id)
    assert len(rows) == 2
    assert all("fingerprint_match" not in (r.raw_data or {}) for r in rows)


def test_persisting_the_same_match_again_changes_nothing(sync_session: Session) -> None:
    """A re-scan re-runs the persist: the row count and the evidence stay put."""
    scan_id = _seed_scan()
    tag = uuid.uuid4().hex[:10]
    declared = _declared(sync_session, scan_id, name=f"widget{tag}", version="2.0.0")
    match = _vendored(tag, version="2.0.0", alternatives=(f"pkg:npm/widget{tag}",))

    for _ in range(2):
        assert _persist_vendored_components(sync_session, scan_uuid=scan_id, vendored=[match]) == 0
        sync_session.commit()

    assert len(_rows(sync_session, scan_id)) == 1
    sync_session.refresh(declared)
    assert declared.raw_data is not None
    assert declared.raw_data["fingerprint_match"]["matched_files"] == 3
