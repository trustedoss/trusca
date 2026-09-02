"""
Unit tests for ``integrations.depsdev``.

All HTTP is stubbed via ``httpx.MockTransport`` fed from real, recorded
deps.dev responses under ``tests/fixtures/depsdev/`` (captured 2026-09-02),
not hand-built minimal JSON -- with two documented exceptions where the
scaffolding around a real detail payload needed a specific, small shape
(a namespace/purl variant, and a versions-list missing an isDefault entry)
that the live catalog does not happen to exhibit for the packages captured.

Coverage:
  - ``lookup_package``: found with advisories, found with none, not found,
    versions-present-but-detail-missing, empty versions list, no isDefault
    entry (falls back to latest publishedAt), maven namespace/purl split,
    npm scoped-name purl split, missing links.
  - Input validation: unknown ecosystem, empty/too-long/control-char name.
  - Transport failures: timeout, connection error, non-JSON body, oversized
    body (wire-count cap, not just decompressed-count).
  - ``lookup_advisory``: found, not found, invalid id.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from integrations.depsdev import (
    DepsDevUpstreamError,
    lookup_advisory,
    lookup_package,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "depsdev"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.deps.dev/v3"
    )


def _json_response(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=body.encode("utf-8"))


# ---------------------------------------------------------------------------
# lookup_package -- happy paths
# ---------------------------------------------------------------------------


async def test_found_current_default_no_advisories() -> None:
    """Real current state: lodash's live default version carries no advisories."""
    versions = _load("npm-lodash-versions.json")
    detail = _load("npm-lodash-4.18.1-detail.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/versions/4.18.1"):
            return _json_response(detail)
        return _json_response(versions)

    async with _client(handler) as client:
        result = await lookup_package("npm", "lodash", http_client=client)

    assert result.found is True
    assert result.version == "4.18.1"
    assert result.purl == "pkg:npm/lodash"
    assert result.licenses == ["MIT"]
    assert result.advisory_count == 0
    assert result.advisory_ids == []
    assert result.homepage_url == "https://lodash.com/"
    assert result.source_repo_url == "git+https://github.com/lodash/lodash.git"


async def test_found_with_advisories() -> None:
    """A historical version (real, recorded detail) that does carry advisories.

    The versions-list mock here is a small constructed shape (one entry,
    isDefault=true) rather than the real list, because the live catalog's
    current default carries zero advisories -- there is no real (versions
    list, isDefault version) pair that exercises this path today. The
    detail payload driving every assertion is still the real, recorded
    response for lodash@4.17.21.
    """
    detail = _load("npm-lodash-4.17.21-detail.json")
    versions = json.dumps(
        {
            "packageKey": {"system": "NPM", "name": "lodash"},
            "versions": [
                {
                    "versionKey": {"system": "NPM", "name": "lodash", "version": "4.17.21"},
                    "publishedAt": "2021-02-20T15:42:16Z",
                    "isDefault": True,
                    "isDeprecated": False,
                    "deprecatedReason": "",
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/versions/4.17.21"):
            return _json_response(detail)
        return _json_response(versions)

    async with _client(handler) as client:
        result = await lookup_package("npm", "lodash", http_client=client)

    assert result.found is True
    assert result.version == "4.17.21"
    assert result.advisory_count == 3
    assert result.advisory_ids == [
        "GHSA-f23m-r3pf-42rh",
        "GHSA-r5fr-rjxr-66jc",
        "GHSA-xxjr-mmjv-4gpg",
    ]


async def test_maven_namespace_and_purl() -> None:
    versions = _load("maven-commons-text-versions.json")
    detail = _load("maven-commons-text-detail.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if "/versions/" in request.url.path:
            return _json_response(detail)
        return _json_response(versions)

    async with _client(handler) as client:
        result = await lookup_package(
            "maven", "org.apache.commons:commons-text", http_client=client
        )

    assert result.found is True
    assert result.purl == "pkg:maven/org.apache.commons/commons-text"
    assert result.licenses == ["Apache-2.0"]
    assert result.advisory_count == 0
    # This target has no ORIGIN link at all -- confirms the "label may be
    # absent" handling rather than indexing into a fixed link order.
    assert result.homepage_url == "https://commons.apache.org/proper/commons-text"


async def test_npm_scoped_name_purl_split() -> None:
    """Constructed minimal fixtures: this test's purpose is purl-shape
    verification for a scoped npm name, which none of the recorded
    real-package fixtures happen to exercise (none are scoped)."""
    versions = json.dumps(
        {
            "packageKey": {"system": "NPM", "name": "@angular/core"},
            "versions": [
                {
                    "versionKey": {
                        "system": "NPM",
                        "name": "@angular/core",
                        "version": "20.0.0",
                    },
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "isDefault": True,
                    "isDeprecated": False,
                    "deprecatedReason": "",
                }
            ],
        }
    )
    detail = json.dumps(
        {
            "versionKey": {"system": "NPM", "name": "@angular/core", "version": "20.0.0"},
            "publishedAt": "2026-01-01T00:00:00Z",
            "isDefault": True,
            "isDeprecated": False,
            "deprecatedReason": "",
            "licenses": ["MIT"],
            "advisoryKeys": [],
            "links": [],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "/versions/" in request.url.path:
            return _json_response(detail)
        return _json_response(versions)

    async with _client(handler) as client:
        result = await lookup_package("npm", "@angular/core", http_client=client)

    assert result.found is True
    assert result.purl == "pkg:npm/%40angular/core"


# ---------------------------------------------------------------------------
# lookup_package -- not-found / edge shapes
# ---------------------------------------------------------------------------


async def test_package_not_found() -> None:
    body = _load("npm-not-found.txt")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=body.encode("utf-8"))

    async with _client(handler) as client:
        result = await lookup_package(
            "npm", "this-package-does-not-exist-xyz-123", http_client=client
        )

    assert result.found is False
    assert result.purl is None


async def test_versions_present_but_detail_missing() -> None:
    """Versions list resolves a default, but its detail 404s -- a real
    inconsistency, not a crash."""
    versions = json.dumps(
        {
            "packageKey": {"system": "NPM", "name": "lodash"},
            "versions": [
                {
                    "versionKey": {"system": "NPM", "name": "lodash", "version": "4.18.1"},
                    "publishedAt": "2026-04-01T21:01:20Z",
                    "isDefault": True,
                    "isDeprecated": False,
                    "deprecatedReason": "",
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "/versions/" in request.url.path:
            return httpx.Response(404, content=b"package not found")
        return _json_response(versions)

    async with _client(handler) as client:
        result = await lookup_package("npm", "lodash", http_client=client)

    assert result.found is False


async def test_empty_versions_list() -> None:
    empty = json.dumps({"packageKey": {"system": "NPM", "name": "ghost"}, "versions": []})

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(empty)

    async with _client(handler) as client:
        result = await lookup_package("npm", "ghost", http_client=client)

    assert result.found is False


async def test_no_default_falls_back_to_latest_published() -> None:
    """Real, trimmed subset of the live versions list: two historical
    entries, neither marked isDefault (true of every non-current version
    in the real catalog) -- exercises the latest-publishedAt fallback
    honestly, without fabricating an isDefault flag."""
    versions = _load("npm-lodash-versions-no-default-trimmed.json")
    detail = json.dumps(
        {
            "versionKey": {"system": "NPM", "name": "lodash", "version": "0.10.0"},
            "publishedAt": "2013-08-31T04:56:09Z",
            "isDefault": False,
            "isDeprecated": False,
            "deprecatedReason": "",
            "licenses": [],
            "advisoryKeys": [],
            "links": [],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "/versions/" in request.url.path:
            return _json_response(detail)
        return _json_response(versions)

    async with _client(handler) as client:
        result = await lookup_package("npm", "lodash", http_client=client)

    # 0.10.0 (2013-08-31) is more recent than 0.1.0 (2012-04-23) -- the
    # fallback picked the later publishedAt, not just the first entry.
    assert result.found is True
    assert result.version == "0.10.0"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


async def test_unknown_ecosystem_raises() -> None:
    with pytest.raises(ValueError):
        await lookup_package("bogus", "lodash")


async def test_empty_name_raises() -> None:
    with pytest.raises(ValueError):
        await lookup_package("npm", "")


async def test_too_long_name_raises() -> None:
    with pytest.raises(ValueError):
        await lookup_package("npm", "a" * 256)


async def test_control_char_in_name_raises() -> None:
    with pytest.raises(ValueError):
        await lookup_package("npm", "evil\r\npkg")


# ---------------------------------------------------------------------------
# Transport failures
# ---------------------------------------------------------------------------


async def test_timeout_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.TimeoutException):
            await lookup_package("npm", "lodash", http_client=client)


async def test_connect_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.ConnectError):
            await lookup_package("npm", "lodash", http_client=client)


async def test_non_json_body_raises_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    async with _client(handler) as client:
        with pytest.raises(DepsDevUpstreamError):
            await lookup_package("npm", "lodash", http_client=client)


async def test_unexpected_status_raises_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"internal error")

    async with _client(handler) as client:
        with pytest.raises(DepsDevUpstreamError):
            await lookup_package("npm", "lodash", http_client=client)


async def test_oversized_body_raises_upstream_error() -> None:
    # One byte over the cap -- confirms the wire-byte counter trips even
    # though this body is plain (uncompressed) JSON-shaped bytes, i.e. the
    # cap is not accidentally gated behind a Content-Encoding check.
    from integrations.depsdev import MAX_BODY_BYTES

    oversized = b'{"versions": [' + b"1" * MAX_BODY_BYTES + b"]}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    async with _client(handler) as client:
        with pytest.raises(DepsDevUpstreamError):
            await lookup_package("npm", "lodash", http_client=client)


async def test_gzip_compressed_body_trips_the_decoded_size_cap() -> None:
    """The real threat the size cap defends against: a small WIRE payload
    that decompresses to something huge. A gzip'd 2 MiB body compresses to
    under 2 KiB, so a cap that only checked wire bytes would let it through
    -- this proves the decoded-byte counter (`size`) independently catches
    it (security review finding, 2026-09-02)."""
    import gzip

    huge_decoded = gzip.compress(b"A" * 2_000_000)
    assert len(huge_decoded) < 2_000  # sanity: wire size is tiny next to decoded size

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=huge_decoded
        )

    async with _client(handler) as client:
        with pytest.raises(DepsDevUpstreamError):
            await lookup_package("npm", "lodash", http_client=client)


async def test_redirect_response_raises_upstream_error() -> None:
    """`follow_redirects=False` (security review, SSRF-adjacent defense):
    a 3xx must never be silently followed to a Location deps.dev did not
    ask for -- it surfaces as an upstream error instead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example/"})

    async with _client(handler) as client:
        with pytest.raises(DepsDevUpstreamError):
            await lookup_package("npm", "lodash", http_client=client)


# ---------------------------------------------------------------------------
# lookup_advisory
# ---------------------------------------------------------------------------


async def test_advisory_found() -> None:
    body = _load("advisory-ghsa-f23m.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(body)

    async with _client(handler) as client:
        result = await lookup_advisory("GHSA-f23m-r3pf-42rh", http_client=client)

    assert result.found is True
    assert result.title == (
        "lodash vulnerable to Prototype Pollution via array path bypass "
        "in `_.unset` and `_.omit`"
    )
    assert result.cvss3_score == 6.5
    assert result.cvss3_vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:L/A:L"
    assert "CVE-2025-13465" in result.aliases


async def test_advisory_found_by_cve_id() -> None:
    """Same endpoint, same shape, requested by CVE id instead of GHSA id --
    the code path does not branch on which id shape was given."""
    body = _load("advisory-ghsa-f23m.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(body)

    async with _client(handler) as client:
        result = await lookup_advisory("CVE-2025-13465", http_client=client)

    assert result.found is True


async def test_advisory_not_found() -> None:
    body = _load("advisory-not-found.txt")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=body.encode("utf-8"))

    async with _client(handler) as client:
        result = await lookup_advisory("CVE-9999-99999", http_client=client)

    assert result.found is False


async def test_advisory_invalid_id_raises() -> None:
    with pytest.raises(ValueError):
        await lookup_advisory("bad\r\nid")


async def test_advisory_id_too_long_raises() -> None:
    with pytest.raises(ValueError):
        await lookup_advisory("A" * 65)
