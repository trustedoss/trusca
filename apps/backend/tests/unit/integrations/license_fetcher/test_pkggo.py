"""
Unit tests for ``integrations.license_fetcher.pkggo``.

pkg.go.dev returns HTML; we feed the fetcher representative snippets
(taken from the production templates as of 2026-05) and assert the
SPDX id ends up extracted. Failure modes (missing license block,
unmappable name, 404) return ``None``.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.pkggo import (
    PkgGoLicenseFetcher,
    _extract_license_name,
    _parse_purl,
)

# ---------------------------------------------------------------------------
# PURL parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "purl,expected",
    [
        (
            "pkg:golang/github.com/spf13/cobra@v1.8.1",
            ("github.com/spf13/cobra", "v1.8.1"),
        ),
        (
            "pkg:golang/golang.org/x/net@v0.34.0",
            ("golang.org/x/net", "v0.34.0"),
        ),
        ("pkg:golang/foo.example/bar@v0.1.0?ext=mod", ("foo.example/bar", "v0.1.0")),
    ],
)
def test_parse_purl_happy(purl: str, expected: tuple[str, str]) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl",
    ["pkg:pypi/foo@1", "pkg:golang/foo", "pkg:golang/foo@", "pkg:golang/@v0"],
)
def test_parse_purl_rejects_malformed(purl: str) -> None:
    assert _parse_purl(purl) is None


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

# Representative pkg.go.dev meta block — the actual production HTML
# wraps the license name in an ``<a href="#lic-0">`` inside a
# ``data-test-id="UnitMeta-license"`` div.
LICENSE_BLOCK_PRIMARY = """\
<html>
<body>
  <div class="UnitMeta">
    <div class="UnitMeta-detailsItem" data-test-id="UnitMeta-license">
      <span class="UnitMeta-detailsLabel">License</span>
      <a href="/license-policy" class="UnitMeta-detailsTooltip">?</a>
      <a class="UnitMeta-licenseLink" href="?tab=licenses#lic-0">Apache-2.0</a>
    </div>
  </div>
</body>
</html>
"""

LICENSE_BLOCK_FALLBACK = """\
<html>
<body>
  <div class="UnitMeta-license">
    <a href="/foo">MIT License</a>
  </div>
</body>
</html>
"""

LICENSE_BLOCK_MISSING = """\
<html>
<body>
  <div class="UnitMeta">
    <p>No license information available.</p>
  </div>
</body>
</html>
"""


def test_extract_primary_anchor() -> None:
    assert _extract_license_name(LICENSE_BLOCK_PRIMARY) == "Apache-2.0"


def test_extract_fallback_anchor() -> None:
    assert _extract_license_name(LICENSE_BLOCK_FALLBACK) == "MIT License"


def test_extract_missing_block() -> None:
    assert _extract_license_name(LICENSE_BLOCK_MISSING) is None


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def test_fetch_returns_apache_from_pkggo(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=LICENSE_BLOCK_PRIMARY)

    fetcher = PkgGoLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:golang/github.com/spf13/cobra@v1.8.1")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert result.source == "pkg_go_dev"
    assert result.reference_url is not None
    assert "tab=licenses" in result.reference_url
    assert requested[0].startswith("https://pkg.go.dev/")


def test_fetch_falls_back_to_normalized_freetext(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=LICENSE_BLOCK_FALLBACK)

    fetcher = PkgGoLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:golang/foo.example/bar@v0.1.0")
    assert result is not None
    assert result.spdx_id == "MIT"


def test_fetch_returns_none_when_block_missing(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=LICENSE_BLOCK_MISSING)

    fetcher = PkgGoLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:golang/foo.example/bar@v0.1.0") is None


def test_fetch_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = PkgGoLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:golang/foo.example/bar@v0.1.0") is None


# ---------------------------------------------------------------------------
# follow_redirects=False — security-reviewer L4 (chore PR #6)
# ---------------------------------------------------------------------------


def _client_no_redirects(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=1.0,
        follow_redirects=False,
    )


def test_fetch_does_not_follow_redirect_to_attacker_host(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            308,
            headers={"Location": "https://attacker.example/spoof?tab=licenses"},
        )

    fetcher = PkgGoLicenseFetcher(http=_client_no_redirects(handler))
    result = fetcher.fetch("pkg:golang/foo.example/bar@v0.1.0")

    assert result is None
    assert len(requested) == 1
    assert "attacker.example" not in requested[0]


def test_default_client_disables_redirect_following() -> None:
    fetcher = PkgGoLicenseFetcher()
    client = fetcher._client(timeout=1.0)
    try:
        assert client.follow_redirects is False
    finally:
        fetcher.close()
