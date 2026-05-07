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
        "MIT OR Apache-2.0",
        "GPL-2.0 WITH Classpath-exception-2.0",
        "BSD-3-Clause AND MIT",
        "totally-not-a-license-name",
    ],
)
def test_normalize_spdx_rejects_unmappable(raw: str | None) -> None:
    assert normalize_spdx_id(raw) is None


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
