"""
Parent-POM licence inheritance in ``integrations.license_fetcher.maven``.

The POMs under ``tests/fixtures/maven_poms`` are real Maven Central files
(see the PROVENANCE there). A fake registry serves them by URL and counts every
request, because the number of requests is what this feature can get wrong.
Chains that do not exist on Central (cycles, missing parents, unresolvable
placeholders) are built inline: there is no real POM to capture for them.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import integrations.license_fetcher.maven as maven_mod
from core.config import license_fetch_maven_parent_max_depth
from integrations.license_fetcher.base import LicenseFetchResult
from integrations.license_fetcher.budget import EnrichmentBudget, enrichment_budget
from integrations.license_fetcher.maven import (
    SOURCE_PARENT,
    MavenLicenseFetcher,
    _parse_license_xml,
    _parse_parent,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "maven_poms"

SLF4J_API = "pkg:maven/org.slf4j/slf4j-api@2.0.2"
COMMONS_LANG3 = "pkg:maven/org.apache.commons/commons-lang3@3.14.0"
SPRING_BOOT = "pkg:maven/org.springframework.boot/spring-boot@3.2.0"
JACKSON_SIBLINGS = [
    "pkg:maven/com.fasterxml.jackson.datatype/jackson-datatype-jdk8@2.15.3",
    "pkg:maven/com.fasterxml.jackson.datatype/jackson-datatype-jsr310@2.15.3",
    "pkg:maven/com.fasterxml.jackson.module/jackson-module-parameter-names@2.15.3",
]


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.pom").read_text(encoding="utf-8")


class _Registry:
    """A Maven Central stand-in: serves POMs by coordinate, records requests."""

    def __init__(self, extra: dict[str, str] | None = None) -> None:
        self.requests: list[str] = []
        self._extra = extra or {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        artifact, version = parts[-3], parts[-2]
        group = ".".join(parts[2:-3])
        key = f"{group}_{artifact}_{version}"
        self.requests.append(f"{group}:{artifact}:{version}")
        if key in self._extra:
            return httpx.Response(200, text=self._extra[key])
        path = FIXTURES / f"{key}.pom"
        if path.exists():
            return httpx.Response(200, text=path.read_text(encoding="utf-8"))
        return httpx.Response(404)

    def fetcher(self) -> MavenLicenseFetcher:
        client = httpx.Client(
            transport=httpx.MockTransport(self.handler), timeout=1.0, follow_redirects=False
        )
        return MavenLicenseFetcher(http=client)


class _MemoryAncestorCache:
    def __init__(self) -> None:
        self.rows: dict[str, LicenseFetchResult] = {}

    def lookup(self, purl: str) -> tuple[bool, LicenseFetchResult | None]:
        row = self.rows.get(purl)
        return (True, row) if row is not None else (False, None)

    def store(self, purl: str, result: LicenseFetchResult) -> None:
        self.rows[purl] = result


def _pom(*, parent: str | None = None, licenses: str | None = None, extra: str = "") -> str:
    return (
        '<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        f"{parent or ''}{licenses or ''}{extra}</project>\n"
    )


def _parent_block(group: str, artifact: str, version: str) -> str:
    return (
        f"<parent><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
        f"<version>{version}</version></parent>\n"
    )


MIT_LICENSES = "<licenses><license><name>MIT License</name></license></licenses>\n"


# ---------------------------------------------------------------------------
# Which <licenses> and <parent> count
# ---------------------------------------------------------------------------


def test_real_child_pom_declares_no_licenses_and_its_parent_does() -> None:
    assert _parse_license_xml(_fixture("org.slf4j_slf4j-api_2.0.2")) is None
    parent = _parse_license_xml(_fixture("org.slf4j_slf4j-parent_2.0.2"))
    assert parent is not None
    assert parent[0] == "MIT License"


def test_real_child_pom_parent_coordinates() -> None:
    assert _parse_parent(_fixture("org.slf4j_slf4j-api_2.0.2")) == (
        "org.slf4j",
        "slf4j-parent",
        "2.0.2",
    )


@pytest.mark.parametrize(
    "xml",
    [
        # Inside a profile: not the project's own declaration.
        _pom(extra=f"<profiles><profile><id>x</id>{MIT_LICENSES}</profile></profiles>"),
        # Inside plugin configuration.
        _pom(extra=f"<build><plugins><plugin><configuration>{MIT_LICENSES}</configuration></plugin></plugins></build>"),
        # Commented out.
        _pom(extra=f"<!-- {MIT_LICENSES} -->"),
        # Inside CDATA.
        _pom(extra=f"<description><![CDATA[{MIT_LICENSES}]]></description>"),
        # Declared but empty.
        _pom(licenses="<licenses/>"),
        _pom(licenses="<licenses></licenses>"),
    ],
    ids=["profile", "plugin-config", "comment", "cdata", "self-closed", "empty"],
)
def test_licenses_that_are_not_the_projects_own_are_ignored(xml: str) -> None:
    assert _parse_license_xml(xml) is None


def test_a_document_without_a_project_root_declares_nothing() -> None:
    assert _parse_license_xml(MIT_LICENSES) is None
    # An error page or another XML document that happens to have the element.
    assert _parse_license_xml(f"<html><body>{MIT_LICENSES}</body></html>") is None
    assert _parse_license_xml(f"<settings>{MIT_LICENSES}</settings>") is None


def test_parent_block_content_is_never_read_as_the_projects_licenses() -> None:
    # A parent element that itself carries a licenses-shaped child (malformed,
    # but it is the shape a naive whole-document search would trip on).
    xml = _pom(
        parent=(
            "<parent><groupId>g</groupId><artifactId>a</artifactId><version>1</version>"
            f"{MIT_LICENSES}</parent>\n"
        )
    )
    assert _parse_license_xml(xml) is None
    assert _parse_parent(xml) == ("g", "a", "1")


@pytest.mark.parametrize(
    "version",
    ["${revision}", "${project.version}", "[1.0,2.0)", "1.0/../../x"],
)
def test_parent_coordinates_that_are_not_literal_are_not_resolved(version: str) -> None:
    assert _parse_parent(_pom(parent=_parent_block("g", "a", version))) is None


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


def test_child_without_licenses_takes_them_from_its_parent(no_throttle: None) -> None:
    registry = _Registry()
    result = registry.fetcher().fetch(SLF4J_API)
    assert result == LicenseFetchResult(
        spdx_id="MIT",
        reference_url=None,
        source=SOURCE_PARENT,
        inherited_from="org.slf4j:slf4j-parent:2.0.2",
    )
    assert registry.requests == ["org.slf4j:slf4j-api:2.0.2", "org.slf4j:slf4j-parent:2.0.2"]


def test_chain_of_two_parents_reaches_the_ancestor_that_declares(no_throttle: None) -> None:
    registry = _Registry()
    result = registry.fetcher().fetch(COMMONS_LANG3)
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert result.inherited_from == "org.apache:apache:30"
    assert registry.requests == [
        "org.apache.commons:commons-lang3:3.14.0",
        "org.apache.commons:commons-parent:64",
        "org.apache:apache:30",
    ]


def test_child_that_declares_a_license_never_reads_its_parent(no_throttle: None) -> None:
    registry = _Registry()
    result = registry.fetcher().fetch(SPRING_BOOT)
    assert result is not None
    assert result.source == "maven_central"
    assert result.inherited_from is None
    assert registry.requests == ["org.springframework.boot:spring-boot:3.2.0"]


def test_child_that_declares_an_unmappable_license_does_not_fall_back_to_its_parent(
    no_throttle: None,
) -> None:
    child = _pom(
        parent=_parent_block("org.slf4j", "slf4j-parent", "2.0.2"),
        licenses="<licenses><license><name>Custom Internal License</name></license></licenses>",
    )
    registry = _Registry({"g_child_1": child})
    assert registry.fetcher().fetch("pkg:maven/g/child@1") is None
    assert registry.requests == ["g:child:1"]


# ---------------------------------------------------------------------------
# Limits: depth, cycles, gaps
# ---------------------------------------------------------------------------


def test_depth_limit_ends_the_chain_as_unknown(
    monkeypatch: pytest.MonkeyPatch, no_throttle: None
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", "1")
    registry = _Registry()
    assert registry.fetcher().fetch(COMMONS_LANG3) is None
    # The child and one parent were read; the second parent was not.
    assert registry.requests == [
        "org.apache.commons:commons-lang3:3.14.0",
        "org.apache.commons:commons-parent:64",
    ]


def test_depth_zero_reads_only_the_components_own_pom(
    monkeypatch: pytest.MonkeyPatch, no_throttle: None
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", "0")
    registry = _Registry()
    assert registry.fetcher().fetch(SLF4J_API) is None
    assert registry.requests == ["org.slf4j:slf4j-api:2.0.2"]


def test_depth_limit_that_is_just_enough_still_resolves(
    monkeypatch: pytest.MonkeyPatch, no_throttle: None
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", "2")
    result = _Registry().fetcher().fetch(COMMONS_LANG3)
    assert result is not None
    assert result.inherited_from == "org.apache:apache:30"


@pytest.mark.parametrize("raw", ["", "abc", "-1", " "])
def test_depth_setting_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", raw)
    assert license_fetch_maven_parent_max_depth() == 3


def test_parent_cycle_is_cut_by_the_visited_set_not_the_depth_limit(
    monkeypatch: pytest.MonkeyPatch, no_throttle: None
) -> None:
    # A depth limit far above the cycle length: only the visited set can stop it.
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", "50")
    registry = _Registry(
        {
            "g_a_1": _pom(parent=_parent_block("g", "b", "1")),
            "g_b_1": _pom(parent=_parent_block("g", "a", "1")),
        }
    )
    assert registry.fetcher().fetch("pkg:maven/g/a@1") is None
    assert registry.requests == ["g:a:1", "g:b:1"]


def test_a_parent_that_names_itself_is_a_cycle(
    monkeypatch: pytest.MonkeyPatch, no_throttle: None
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH", "50")
    registry = _Registry({"g_a_1": _pom(parent=_parent_block("g", "a", "1"))})
    assert registry.fetcher().fetch("pkg:maven/g/a@1") is None
    assert registry.requests == ["g:a:1"]


def test_missing_middle_parent_is_unknown_not_an_error(no_throttle: None) -> None:
    child = _pom(parent=_parent_block("g", "mid", "1"))
    registry = _Registry({"g_child_1": child})  # g:mid:1 answers 404
    fetcher = registry.fetcher()
    assert fetcher.fetch("pkg:maven/g/child@1") is None
    assert registry.requests == ["g:child:1", "g:mid:1"]
    # A 404 is an answer: the miss is confirmed, so it may be cached.
    assert fetcher.lookup_incomplete is False


def test_unresolvable_parent_placeholder_is_unknown_without_a_parent_request(
    no_throttle: None,
) -> None:
    child = _pom(parent=_parent_block("g", "mid", "${revision}"))
    registry = _Registry({"g_child_1": child})
    assert registry.fetcher().fetch("pkg:maven/g/child@1") is None
    assert registry.requests == ["g:child:1"]


def test_parent_with_a_license_name_that_does_not_map_is_unknown(no_throttle: None) -> None:
    child = _pom(parent=_parent_block("g", "mid", "1"))
    mid = _pom(
        licenses="<licenses><license><name>Custom Internal License</name></license></licenses>"
    )
    registry = _Registry({"g_child_1": child, "g_mid_1": mid})
    assert registry.fetcher().fetch("pkg:maven/g/child@1") is None


# ---------------------------------------------------------------------------
# Budget and circuit breaker apply to parent reads
# ---------------------------------------------------------------------------


def test_parent_read_is_refused_once_the_budget_is_spent(no_throttle: None) -> None:
    registry = _Registry()
    fetcher = registry.fetcher()
    budget = EnrichmentBudget(budget_seconds=1.0, breaker_threshold=30, spent_seconds=1.0)
    with enrichment_budget(budget):
        assert fetcher.fetch(SLF4J_API) is None
    # The component's own POM was read; the parent was not.
    assert registry.requests == ["org.slf4j:slf4j-api:2.0.2"]
    assert fetcher.lookup_incomplete is True
    assert budget.skipped_budget == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def test_budget_ends_a_chain_part_way_through_one_lookup(
    monkeypatch: pytest.MonkeyPatch, no_throttle: None
) -> None:
    # The budget has 15 s and nothing is spent when the lookup starts, so the
    # dispatcher lets it in. Each POM read "takes" 10 s: after the second the
    # lookup itself has used the budget, and the third read must not happen.
    clock = _Clock()
    monkeypatch.setattr(maven_mod, "time", clock)
    registry = _Registry()
    inner = registry.handler

    def slow(request: httpx.Request) -> httpx.Response:
        clock.now += 10.0
        return inner(request)

    fetcher = MavenLicenseFetcher(
        http=httpx.Client(transport=httpx.MockTransport(slow), follow_redirects=False)
    )
    budget = EnrichmentBudget(budget_seconds=15.0, breaker_threshold=30)
    with enrichment_budget(budget):
        assert fetcher.fetch(COMMONS_LANG3) is None
    assert registry.requests == [
        "org.apache.commons:commons-lang3:3.14.0",
        "org.apache.commons:commons-parent:64",
    ]
    assert fetcher.lookup_incomplete is True
    assert budget.skipped_budget == 1


def test_open_breaker_stops_the_chain_before_the_parent_read(no_throttle: None) -> None:
    registry = _Registry()
    fetcher = registry.fetcher()
    budget = EnrichmentBudget(budget_seconds=300.0, breaker_threshold=1)
    budget.record_transport_failure()
    with enrichment_budget(budget):
        assert fetcher.fetch(SLF4J_API) is None
    assert registry.requests == ["org.slf4j:slf4j-api:2.0.2"]
    assert fetcher.lookup_incomplete is True
    assert budget.skipped_breaker == 1


def test_unresponsive_parent_registry_counts_toward_the_breaker(no_throttle: None) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "slf4j-parent" in request.url.path:
            raise httpx.ConnectError("down")
        return httpx.Response(200, text=_fixture("org.slf4j_slf4j-api_2.0.2"))

    fetcher = MavenLicenseFetcher(
        http=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    )
    budget = EnrichmentBudget(budget_seconds=300.0, breaker_threshold=2)
    with enrichment_budget(budget):
        assert fetcher.fetch(SLF4J_API) is None
    # The parent read went through the retry wrapper: it was retried, and the
    # failure reached the budget that decides when to stop asking.
    assert sum("slf4j-parent" in path for path in calls) > 1
    assert budget.transport_failures == 1
    assert budget.consecutive_failures == 1


# ---------------------------------------------------------------------------
# Sharing a parent between children
# ---------------------------------------------------------------------------


def test_siblings_share_one_parent_chain_through_the_ancestor_cache(no_throttle: None) -> None:
    registry = _Registry()
    fetcher = registry.fetcher()
    fetcher.ancestor_cache = _MemoryAncestorCache()
    results = [fetcher.fetch(purl) for purl in JACKSON_SIBLINGS]
    assert [r.inherited_from if r else None for r in results] == [
        "com.fasterxml.jackson:jackson-base:2.15.3"
    ] * 3
    assert all(r is not None and r.spdx_id == "Apache-2.0" for r in results)
    # First child walks the whole chain (3 reads). The other two read only their
    # own POM: their shared parent is already answered.
    assert len(registry.requests) == 3 + 1 + 1


def test_without_the_ancestor_cache_every_sibling_walks_the_chain(no_throttle: None) -> None:
    registry = _Registry()
    fetcher = registry.fetcher()
    for purl in JACKSON_SIBLINGS:
        assert fetcher.fetch(purl) is not None
    assert len(registry.requests) == 3 * 3


def test_ancestor_cache_records_each_ancestor_with_its_own_origin(no_throttle: None) -> None:
    cache = _MemoryAncestorCache()
    fetcher = _Registry().fetcher()
    fetcher.ancestor_cache = cache
    assert fetcher.fetch(COMMONS_LANG3) is not None
    assert cache.rows["pkg:maven/org.apache.commons/commons-parent@64"] == LicenseFetchResult(
        spdx_id="Apache-2.0",
        reference_url=None,
        source=SOURCE_PARENT,
        inherited_from="org.apache:apache:30",
    )
    # The ancestor that declares the license itself is not "inherited".
    assert cache.rows["pkg:maven/org.apache/apache@30"] == LicenseFetchResult(
        spdx_id="Apache-2.0", reference_url=None, source="maven_central"
    )


def test_cached_ancestor_that_declared_the_license_is_reported_as_the_origin(
    no_throttle: None,
) -> None:
    cache = _MemoryAncestorCache()
    cache.rows["pkg:maven/org.slf4j/slf4j-parent@2.0.2"] = LicenseFetchResult(
        spdx_id="MIT", reference_url=None, source="maven_central"
    )
    registry = _Registry()
    fetcher = registry.fetcher()
    fetcher.ancestor_cache = cache
    result = fetcher.fetch(SLF4J_API)
    assert result is not None
    assert result.inherited_from == "org.slf4j:slf4j-parent:2.0.2"
    assert registry.requests == ["org.slf4j:slf4j-api:2.0.2"]
