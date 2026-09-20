"""U3-E, Maven parent-POM licence inheritance through the real persist path.

A Maven Central stand-in serves real POMs (``tests/fixtures/maven_poms``) to the
real fetcher behind ``persist_sbom_components`` and a real Postgres. What this
pins is what the unit suite cannot: the provenance survives the cache, sibling
components share one parent chain across the scan, and a parent lookup that was
cut short is not stored as a confirmed miss.
"""

from __future__ import annotations

import functools
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import integrations.license_fetcher as dispatcher_mod
import integrations.license_fetcher.base as base_mod
import integrations.license_fetcher.maven as maven_mod
from models import LicenseFetchCache, LicenseFinding
from tests._db_required import migrate_to_head
from tests.integration.scan.test_persist_sbom_bulk_catalog import _seed_queued_scan

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "maven_poms"

JACKSON = [
    "pkg:maven/com.fasterxml.jackson.datatype/jackson-datatype-jdk8@2.15.3",
    "pkg:maven/com.fasterxml.jackson.datatype/jackson-datatype-jsr310@2.15.3",
    "pkg:maven/com.fasterxml.jackson.module/jackson-module-parameter-names@2.15.3",
]
SLF4J = "pkg:maven/org.slf4j/slf4j-api@2.0.2"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


class _Registry:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        artifact, version = parts[-3], parts[-2]
        group = ".".join(parts[2:-3])
        self.requests.append(f"{group}:{artifact}:{version}")
        path = FIXTURES / f"{group}_{artifact}_{version}.pom"
        if path.exists():
            return httpx.Response(200, text=path.read_text(encoding="utf-8"))
        return httpx.Response(404)


def _use_registry(monkeypatch: pytest.MonkeyPatch, registry: _Registry) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(registry.handler), timeout=1.0, follow_redirects=False
    )
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER,
        "pkg:maven/",
        lambda: maven_mod.MavenLicenseFetcher(http=client),
    )
    monkeypatch.setattr(
        maven_mod,
        "request_with_retry",
        functools.partial(base_mod.request_with_retry, sleep=lambda _s: None),
    )
    base_mod._HOST_LOCKS.clear()


def _sbom(purls: list[str]) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {
                "type": "library",
                "name": purl.split("/")[-1].split("@")[0],
                "group": purl.split("/")[-2],
                "version": purl.split("@")[1],
                "purl": purl,
                "bom-ref": purl,
            }
            for purl in purls
        ],
    }


def _run(sync_session: Session, purls: list[str]) -> uuid.UUID:
    from tasks.scan_source import persist_sbom_components

    scan_id = _seed_queued_scan()
    persist_sbom_components(sync_session, scan_uuid=scan_id, sbom=_sbom(purls))
    sync_session.commit()
    return scan_id


def _concluded(sync_session: Session, scan_id: uuid.UUID) -> list[dict[str, Any]]:
    sync_session.expire_all()
    rows = (
        sync_session.execute(
            select(LicenseFinding).where(
                LicenseFinding.scan_id == scan_id, LicenseFinding.kind == "concluded"
            )
        )
        .scalars()
        .all()
    )
    return [dict(row.raw_data or {}) for row in rows]


def _cache(sync_session: Session, purl: str) -> LicenseFetchCache | None:
    sync_session.expire_all()
    return sync_session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
    ).scalar_one_or_none()


@pytest.fixture(autouse=True)
def _fresh_cache(sync_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache is keyed by purl and outlives a test; these purls are shared."""
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    from sqlalchemy import delete

    sync_session.execute(delete(LicenseFetchCache).where(LicenseFetchCache.purl.like("pkg:maven/%")))
    sync_session.commit()


def test_inherited_license_is_concluded_with_its_origin_and_survives_the_cache(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _Registry()
    _use_registry(monkeypatch, registry)

    first = _run(sync_session, [SLF4J])
    assert _concluded(sync_session, first) == [
        {"source": "maven_central_parent", "inherited_from": "org.slf4j:slf4j-parent:2.0.2"}
    ]
    assert registry.requests == ["org.slf4j:slf4j-api:2.0.2", "org.slf4j:slf4j-parent:2.0.2"]

    # A later scan is served from the cache: no request, same provenance.
    registry.requests.clear()
    second = _run(sync_session, [SLF4J])
    assert registry.requests == []
    assert _concluded(sync_session, second) == [
        {"source": "maven_central_parent", "inherited_from": "org.slf4j:slf4j-parent:2.0.2"}
    ]


def test_siblings_of_one_project_share_one_parent_chain(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _Registry()
    _use_registry(monkeypatch, registry)

    scan_id = _run(sync_session, JACKSON)

    # Three children read once each, the shared chain (modules-java8, jackson-base)
    # read once in total. Without sharing this is 9.
    assert len(registry.requests) == 5
    assert sorted(registry.requests) == [
        "com.fasterxml.jackson.datatype:jackson-datatype-jdk8:2.15.3",
        "com.fasterxml.jackson.datatype:jackson-datatype-jsr310:2.15.3",
        "com.fasterxml.jackson.module:jackson-module-parameter-names:2.15.3",
        "com.fasterxml.jackson.module:jackson-modules-java8:2.15.3",
        "com.fasterxml.jackson:jackson-base:2.15.3",
    ]
    findings = _concluded(sync_session, scan_id)
    assert findings == [
        {
            "source": "maven_central_parent",
            "inherited_from": "com.fasterxml.jackson:jackson-base:2.15.3",
        }
    ] * 3


def test_ancestor_rows_carry_their_own_origin(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_registry(monkeypatch, _Registry())
    _run(sync_session, ["pkg:maven/org.apache.commons/commons-lang3@3.14.0"])

    middle = _cache(sync_session, "pkg:maven/org.apache.commons/commons-parent@64")
    top = _cache(sync_session, "pkg:maven/org.apache/apache@30")
    assert middle is not None and not middle.is_negative
    assert middle.source == "maven_central_parent|org.apache:apache:30"
    assert top is not None and not top.is_negative
    assert top.source == "maven_central"


def test_chain_cut_by_the_depth_limit_is_cached_as_a_miss_for_the_child_only(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", "1")
    registry = _Registry()
    _use_registry(monkeypatch, registry)
    purl = "pkg:maven/org.apache.commons/commons-lang3@3.14.0"

    scan_id = _run(sync_session, [purl])

    assert _concluded(sync_session, scan_id) == []
    row = _cache(sync_session, purl)
    assert row is not None and row.is_negative
    # Nothing was learned about the ancestors, so nothing is stored for them.
    assert _cache(sync_session, "pkg:maven/org.apache.commons/commons-parent@64") is None


def test_parent_lookup_the_budget_refused_is_not_cached_as_a_miss(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Clock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = _Clock()
    monkeypatch.setattr(maven_mod, "time", clock)
    monkeypatch.setenv("LICENSE_FETCH_SCAN_BUDGET_SECONDS", "5")
    registry = _Registry()
    inner = registry.handler

    def slow(request: httpx.Request) -> httpx.Response:
        clock.now += 10.0
        return inner(request)

    registry.handler = slow  # type: ignore[method-assign]
    _use_registry(monkeypatch, registry)
    purl = "pkg:maven/org.apache.commons/commons-lang3@3.14.0"

    scan_id = _run(sync_session, [purl])

    # The child's own POM was read; the budget was gone before its parent.
    assert registry.requests == ["org.apache.commons:commons-lang3:3.14.0"]
    assert _concluded(sync_session, scan_id) == []
    assert _cache(sync_session, purl) is None
    from models import Scan

    sync_session.expire_all()
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    record = (scan.scan_metadata or {}).get("license_enrichment")
    assert record is not None
    assert record["not_looked_up"] == 1
    assert record["not_looked_up_reason"] == "budget_exhausted"
