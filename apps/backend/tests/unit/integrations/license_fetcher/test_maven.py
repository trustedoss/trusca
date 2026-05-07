"""
Unit tests for ``integrations.license_fetcher.maven``.

We feed the fetcher pre-canned ``<licenses>`` POM fragments via
``httpx.MockTransport`` and assert it pulls out the right SPDX id +
reference URL. Failure modes (404 / 5xx / no-licenses / unmappable
free-text) all map to ``None``.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.maven import (
    MavenLicenseFetcher,
    _parse_purl,
)

# ---------------------------------------------------------------------------
# PURL parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "purl,expected",
    [
        (
            "pkg:maven/com.fasterxml.jackson.core/jackson-core@2.18.2",
            ("com.fasterxml.jackson.core", "jackson-core", "2.18.2"),
        ),
        (
            "pkg:maven/io.netty/netty-codec@4.1.133.Final",
            ("io.netty", "netty-codec", "4.1.133.Final"),
        ),
        ("pkg:maven/junit/junit@4.13.2?type=jar", ("junit", "junit", "4.13.2")),
    ],
)
def test_parse_purl_happy(
    purl: str, expected: tuple[str, str, str]
) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl",
    [
        "pkg:pypi/foo@1",
        "pkg:maven/com.example",
        "pkg:maven/com.example/foo",
        "pkg:maven/foo@1",  # missing slash between group/artifact
        "pkg:maven/com.example/foo@",  # empty version
    ],
)
def test_parse_purl_rejects_malformed(purl: str) -> None:
    assert _parse_purl(purl) is None


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------

POM_APACHE = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>foo</artifactId>
  <version>1.0.0</version>
  <licenses>
    <license>
      <name>The Apache Software License, Version 2.0</name>
      <url>https://www.apache.org/licenses/LICENSE-2.0.txt</url>
      <distribution>repo</distribution>
    </license>
  </licenses>
</project>
"""

POM_NO_LICENSES = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>foo</artifactId>
  <version>1.0.0</version>
</project>
"""

POM_UNMAPPABLE_LICENSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <licenses>
    <license>
      <name>Custom Internal License</name>
      <url>https://example.invalid/license</url>
    </license>
  </licenses>
</project>
"""


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def test_fetch_returns_apache_license_from_pom(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=POM_APACHE)

    fetcher = MavenLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:maven/com.example/foo@1.0.0")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert result.reference_url == "https://www.apache.org/licenses/LICENSE-2.0.txt"
    assert result.source == "maven_central"
    # URL shape pin: group dots → slashes, artifact + version repeated in filename.
    assert requested == [
        "https://repo1.maven.org/maven2/com/example/foo/1.0.0/foo-1.0.0.pom"
    ]


def test_fetch_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = MavenLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:maven/com.example/foo@1.0.0") is None


def test_fetch_returns_none_on_pom_without_licenses(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=POM_NO_LICENSES)

    fetcher = MavenLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:maven/com.example/foo@1.0.0") is None


def test_fetch_returns_none_on_unmappable_license(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=POM_UNMAPPABLE_LICENSE)

    fetcher = MavenLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:maven/com.example/foo@1.0.0") is None


def test_fetch_retries_on_5xx_then_succeeds(no_throttle: None) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(503)
        return httpx.Response(200, text=POM_APACHE)

    fetcher = MavenLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:maven/com.example/foo@1.0.0")
    assert result is not None
    assert len(attempts) == 2


def test_fetch_returns_none_on_unrecognised_purl(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP should not be called for non-maven PURL")

    fetcher = MavenLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:npm/foo@1.0.0") is None


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
    """A 3xx with a Location header to an attacker host must be ignored.

    The Maven fetcher is configured with ``follow_redirects=False`` so a
    phishing redirect inserted by a man-in-the-middle (or a registry
    misconfig) returns ``None`` and the attacker host is never contacted.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://attacker.example/spoof.pom"},
        )

    fetcher = MavenLicenseFetcher(http=_client_no_redirects(handler))
    result = fetcher.fetch("pkg:maven/com.example/foo@1.0.0")

    assert result is None
    assert len(requested) == 1
    assert "attacker.example" not in requested[0]


def test_default_client_disables_redirect_following() -> None:
    """Production-default httpx.Client must not follow redirects.

    Pins the L4 contract at the source: a future refactor that flips
    follow_redirects back to True will fail this test.
    """
    fetcher = MavenLicenseFetcher()
    client = fetcher._client(timeout=1.0)
    try:
        assert client.follow_redirects is False
    finally:
        fetcher.close()
