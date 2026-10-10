"""
A vendored library matched at a new version is recorded at that version (real Postgres).

Issue #500. The fingerprint service names a library without a version
(``pkg:github/davegamble/cjson``) and reports the version beside it. The catalog
row for a version is keyed on ``purl_with_version``, so persisting the bare purl
there meant the first scan to match a library created the row and every later
scan resolved to it, whatever version that scan matched. A project that
upgraded its vendored copy kept reporting the old version.

Both scans below are built from the recorded response in
``tests/fixtures/scanoss/vendored-tree-osskb.json`` (see its PROVENANCE.md); the
second only moves cJSON to a later release, the way an upgrade would.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from integrations.scanoss import VendoredComponent, _parse_vendored
from models import Component, ComponentVersion, ScanComponent
from tasks.scan_source import _persist_vendored_components, _purl_pinned_to_version
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration

_RECORDED = (
    Path(__file__).resolve().parents[2] / "fixtures" / "scanoss" / "vendored-tree-osskb.json"
)
_CJSON = "pkg:github/davegamble/cjson"
_CJSON_FILES = ("third_party/cjson/cJSON.c", "third_party/cjson/cJSON.h")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _seed_scans(count: int) -> list[uuid.UUID]:
    """``count`` scans of ONE project, as successive scans of a repository are."""
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

    async def _build() -> list[uuid.UUID]:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            project = await make_project(s, team=team, git_url=None)
            ids = []
            for index in range(count):
                # One active scan per project: the earlier ones have finished.
                status = "running" if index == count - 1 else "succeeded"
                scan = await make_scan(s, project=project, requested_by=user, status=status)
                ids.append(scan.id)
        await engine.dispose()
        return ids

    return asyncio.run(_build())


def _recorded_with_cjson_at(version: str, tmp_path: Path) -> list[VendoredComponent]:
    """The recorded response with cJSON's two agreeing files moved to ``version``."""
    payload = json.loads(_RECORDED.read_text())
    for path in _CJSON_FILES:
        for match in payload[path]:
            match["version"] = version
    target = tmp_path / f"osskb-{uuid.uuid4().hex}.json"
    target.write_text(json.dumps(payload))
    return _parse_vendored(target)


def _vendored_version(session: Session, scan_id: uuid.UUID, purl: str) -> ComponentVersion:
    rows = session.execute(
        select(ComponentVersion)
        .join(ScanComponent, ScanComponent.component_version_id == ComponentVersion.id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(ScanComponent.scan_id == scan_id, Component.purl == purl)
    ).scalars()
    found = list(rows)
    assert len(found) == 1, found
    return found[0]


def test_a_later_scan_matching_a_newer_version_records_the_newer_version(
    sync_session: Session, tmp_path: Path
) -> None:
    first_scan, second_scan = _seed_scans(2)
    # Unique per run so the second version is new to the shared catalog even on
    # a database earlier runs have written to. The first is the recorded one.
    upgraded = f"v1.7.19-{uuid.uuid4().hex[:8]}"

    first = _parse_vendored(_RECORDED)
    assert {vc.purl: vc.version for vc in first}[_CJSON] == "v1.7.18"
    _persist_vendored_components(sync_session, scan_uuid=first_scan, vendored=first)
    sync_session.commit()

    second = _recorded_with_cjson_at(upgraded, tmp_path)
    assert {vc.purl: vc.version for vc in second}[_CJSON] == upgraded
    _persist_vendored_components(sync_session, scan_uuid=second_scan, vendored=second)
    sync_session.commit()

    before = _vendored_version(sync_session, first_scan, _CJSON)
    after = _vendored_version(sync_session, second_scan, _CJSON)
    assert after.version == upgraded
    assert after.purl_with_version == f"{_CJSON}@{upgraded}"
    assert before.version == "v1.7.18"
    assert before.purl_with_version == f"{_CJSON}@v1.7.18"
    # One library, two versions: the identity row is shared, the version row is not.
    assert before.component_id == after.component_id
    assert before.id != after.id


def test_every_recorded_match_is_pinned_to_the_version_it_matched(
    sync_session: Session,
) -> None:
    """The Go pseudo-version and the commit-hash versions get pinned the same way."""
    (scan_id,) = _seed_scans(1)
    vendored = _parse_vendored(_RECORDED)
    _persist_vendored_components(sync_session, scan_uuid=scan_id, vendored=vendored)
    sync_session.commit()

    for vc in vendored:
        row = _vendored_version(sync_session, scan_id, vc.purl)
        assert row.version == vc.version
        assert row.purl_with_version == f"{vc.purl}@{vc.version}"


@pytest.mark.parametrize(
    ("purl", "version", "expected"),
    [
        # The shape the service returns: bare identity, version beside it.
        (_CJSON, "v1.7.18", f"{_CJSON}@v1.7.18"),
        # No version to pin: the identity is all there is, and inventing
        # "@unknown" would put a version in the purl that nobody reported.
        (_CJSON, "unknown", _CJSON),
        (_CJSON, "", _CJSON),
        # Already pinned: the purl's own version is the identity, left alone.
        (f"{_CJSON}@v1.7.17", "v1.7.18", f"{_CJSON}@v1.7.17"),
        # The version goes before qualifiers and subpath, not after them.
        (
            "pkg:rpm/opensuse/cJSON?arch=x86_64#src/cJSON.c",
            "1.7.18",
            "pkg:rpm/opensuse/cJSON@1.7.18?arch=x86_64#src/cJSON.c",
        ),
        # An '@' in a qualifier value is not a version.
        (
            "pkg:generic/lib?download_url=https://u@host/x",
            "2.0",
            "pkg:generic/lib@2.0?download_url=https://u@host/x",
        ),
        # A scoped npm name keeps its scope; only the last segment can carry a version.
        ("pkg:npm/%40scope/name", "1.0.0", "pkg:npm/%40scope/name@1.0.0"),
        # Characters that would end the version segment are percent-encoded.
        ("pkg:generic/lib", "1.0+build#7?x", "pkg:generic/lib@1.0%2Bbuild%237%3Fx"),
    ],
)
def test_purl_pinned_to_version(purl: str, version: str, expected: str) -> None:
    assert _purl_pinned_to_version(purl, version) == expected
