"""
Unit tests for ``integrations.license_fetcher.base``.

Coverage:
  - ``normalize_spdx_id`` accepts canonical SPDX ids unchanged.
  - Free-text license names map through the alias table.
  - Compound expressions (``... AND ...`` / ``... OR ...``) yield None.
  - ``request_with_retry`` retries on 429 / 5xx and gives up after
    ``max_retries`` attempts.
  - ``request_with_retry`` returns ``None`` (not raise) on persistent
    transport errors.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.base import (
    normalize_spdx_id,
    request_with_retry,
)

# ---------------------------------------------------------------------------
# normalize_spdx_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Apache-2.0", "Apache-2.0"),
        ("MIT", "MIT"),
        ("BSD-3-Clause", "BSD-3-Clause"),
        ("Apache 2.0", "Apache-2.0"),
        ("apache license, version 2.0", "Apache-2.0"),
        ("MIT License", "MIT"),
        ("BSD-3", "BSD-3-Clause"),
        ("New BSD License", "BSD-3-Clause"),
        ("ISC License", "ISC"),
        ("MPL-2.0", "MPL-2.0"),
    ],
)
def test_normalize_spdx_known_aliases(raw: str, expected: str) -> None:
    assert normalize_spdx_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        # AND is the only compound we still reject — picking a single
        # SPDX id when the licence requires both is unsound.
        "BSD-3-Clause AND MIT",
        "totally-not-a-license-name",
        # security-reviewer High (chore PR #7) — bare separator tokens
        # would otherwise infinite-recurse.
        "WITH",
        "OR",
        "WITH WITH",
        "OR OR OR",
        "OR WITH",
        "WITH OR",
        # security-reviewer Medium #2 (chore PR #7) — adversarial
        # payloads must not pass the verbatim-SPDX-id heuristic.
        "javascript:alert(1)",
        "a:b",
        "<x>1</x>",
        "../etc/passwd",
        "id;DROP TABLE",
    ],
)
def test_normalize_spdx_rejects_unmappable(raw: str | None) -> None:
    assert normalize_spdx_id(raw) is None


# ---------------------------------------------------------------------------
# Compound expression handling — chore PR #7 UAT v2 fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # OR — pick the first valid token.
        ("MIT OR Apache-2.0", "MIT"),
        ("Apache-2.0 OR MIT", "Apache-2.0"),
        # OR with free-text aliases.
        ("Apache 2.0 OR MIT", "Apache-2.0"),
        # OR with a leading non-SPDX token — fall through to next.
        ("Custom-License OR MIT", "MIT"),
        # WITH — pick the base license (left of WITH).
        ("GPL-2.0 WITH Classpath-exception-2.0", "GPL-2.0-only"),
        ("Apache-2.0 WITH LLVM-exception", "Apache-2.0"),
        # Free-text containing literal "or later" stays in the alias map.
        ("GNU Lesser General Public License v2.1 or later", "LGPL-2.1-or-later"),
    ],
)
def test_normalize_spdx_compound_expressions(raw: str, expected: str) -> None:
    """The chore PR #7 UAT v2 spot-check showed the previous
    "reject all compounds" rule produced 90% unknown for the Rust
    ecosystem. The new policy: OR picks the first valid token, WITH
    picks the base license, AND still rejects.
    """
    assert normalize_spdx_id(raw) == expected


# ---------------------------------------------------------------------------
# request_with_retry
# ---------------------------------------------------------------------------


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def test_request_with_retry_returns_response_on_2xx(no_throttle: None) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="hello")

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        sleep=lambda _: None,
    )
    assert response is not None
    assert response.text == "hello"
    assert len(calls) == 1


def test_request_with_retry_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        sleep=lambda _: None,
    )
    assert response is None


def test_request_with_retry_retries_on_5xx_then_succeeds(no_throttle: None) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        max_retries=3,
        sleep=lambda _: None,
    )
    assert response is not None
    assert response.status_code == 200
    assert len(attempts) == 3


def test_request_with_retry_retries_on_429(no_throttle: None) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(429)
        return httpx.Response(200, text="ok")

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        max_retries=3,
        sleep=lambda _: None,
    )
    assert response is not None
    assert len(attempts) == 2


def test_request_with_retry_gives_up_on_persistent_5xx(no_throttle: None) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        max_retries=2,
        sleep=lambda _: None,
    )
    assert response is None
    # max_retries=2 → 3 total attempts (initial + 2 retries).
    assert len(attempts) == 3


def test_request_with_retry_skips_retry_on_4xx_other_than_429_404(
    no_throttle: None,
) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(403)

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        max_retries=3,
        sleep=lambda _: None,
    )
    assert response is None
    assert len(attempts) == 1


def test_request_with_retry_returns_none_on_persistent_transport_error(
    no_throttle: None,
) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("boom", request=request)

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        max_retries=2,
        sleep=lambda _: None,
    )
    assert response is None
    assert len(attempts) == 3


# ---------------------------------------------------------------------------
# Response-body size cap (W8-#49 security-reviewer follow-up).
# ---------------------------------------------------------------------------


def test_request_with_retry_caps_oversized_body(no_throttle: None) -> None:
    """A body over ``max_body_bytes`` (decompression bomb from a compromised
    registry) degrades to ``None`` — the same observable state as a registry
    without metadata — instead of ballooning worker memory."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2048)

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        sleep=lambda _: None,
        max_body_bytes=1024,
    )
    assert response is None


def test_request_with_retry_capped_body_keeps_json_usable(no_throttle: None) -> None:
    """Under the cap the rebuilt response must behave like a normal one for
    the callers (``.json()`` / ``.text``), with the wire-only headers
    (Content-Encoding / Content-Length) dropped after decompression."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"license": "MIT"},
            headers={"X-Registry": "mock"},
        )

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        sleep=lambda _: None,
    )
    assert response is not None
    assert response.json() == {"license": "MIT"}
    assert response.headers["X-Registry"] == "mock"
    # The wire Content-Encoding must not survive (the body is already
    # decompressed); httpx re-derives an accurate Content-Length from the
    # materialised body, which is fine.
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(response.content))


def test_request_with_retry_caps_decompression_bomb(no_throttle: None) -> None:
    """A small gzip body that inflates past the cap is rejected on the
    DECOMPRESSED count — the wire size alone would sail under the limit."""
    import gzip as _gzip

    bomb = _gzip.compress(b"\0" * (4 * 1024 * 1024))  # ~4 KiB wire, 4 MiB inflated

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=bomb, headers={"Content-Encoding": "gzip"}
        )

    client = _make_client(handler)
    response = request_with_retry(
        client=client,
        method="GET",
        url="https://example.invalid/x",
        host="example.invalid",
        sleep=lambda _: None,
        max_body_bytes=1024 * 1024,
    )
    assert response is None
