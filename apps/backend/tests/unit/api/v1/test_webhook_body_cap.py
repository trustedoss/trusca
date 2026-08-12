"""
Pure-unit tests for the webhook receivers' bounded body read (#38).

These exercise ``api.v1.webhooks._body.read_capped_body`` directly (no DB, no
HTTP) so the cap is covered deterministically; the end-to-end 413 lives in
``tests/integration/test_webhooks_github.py``.

The cap exists because the receivers are public. The signature covers the body,
so the body has to be read and the repository resolved before any credential
can be checked, and resolving the repository walks the URL character by
character. Without a cap an unauthenticated caller chooses how much work the
process does before it learns the caller is nobody.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from api.v1.webhooks._body import read_capped_body


def _request(chunks: list[bytes], *, headers: dict[str, str] | None = None) -> Request:
    """Build a Starlette Request that yields *chunks* from its ASGI receive."""
    header_pairs = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/webhooks/github",
        "headers": header_pairs,
    }
    pending = list(chunks)

    async def receive() -> dict[str, Any]:
        if pending:
            chunk = pending.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _small_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 64 KiB cap: the configured floor, so the accessor returns it as-is."""
    monkeypatch.setenv("WEBHOOK_MAX_BODY_BYTES", str(64 * 1024))


async def test_body_under_the_cap_is_returned_whole() -> None:
    body = b'{"pad":"' + b"x" * 1000 + b'"}'
    request = _request(
        [body], headers={"content-length": str(len(body))}
    )
    assert await read_capped_body(request) == body


async def test_body_split_across_chunks_is_rejoined() -> None:
    request = _request([b"abc", b"def", b"ghi"])
    assert await read_capped_body(request) == b"abcdefghi"


async def test_declared_length_over_the_cap_is_refused_unread() -> None:
    """Content-Length settles it before a single chunk is buffered."""
    consumed: list[bytes] = []

    async def receive() -> dict[str, Any]:
        consumed.append(b"x")
        return {"type": "http.request", "body": b"x" * 1024, "more_body": False}

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/webhooks/github",
        "headers": [(b"content-length", str(10 * 1024 * 1024).encode())],
    }
    assert await read_capped_body(Request(scope, receive)) is None
    assert consumed == []


async def test_understated_length_is_still_caught_by_the_stream_cap() -> None:
    """A caller who lies about the length gains nothing.

    The declared value is a shortcut, never the authority; the running total
    across the stream is what actually bounds the read.
    """
    oversized = b"x" * (65 * 1024)
    request = _request(
        [oversized], headers={"content-length": "10"}
    )
    assert await read_capped_body(request) is None


async def test_chunked_body_with_no_declared_length_is_capped() -> None:
    """No Content-Length at all (chunked transfer) still hits the cap."""
    chunk = b"x" * (16 * 1024)
    request = _request([chunk] * 8)  # 128 KiB across 8 chunks
    assert await read_capped_body(request) is None


async def test_oversized_stream_stops_reading_at_the_cap() -> None:
    """The read aborts on the chunk that crosses the line, not at EOF.

    Draining the rest would buffer what we already decided not to accept.
    """
    reads = 0

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"x" * (32 * 1024), "more_body": True}

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/webhooks/github",
        "headers": [],
    }
    assert await read_capped_body(Request(scope, receive)) is None
    # 64 KiB cap, 32 KiB chunks: the third read is the one that crosses it.
    assert reads == 3


async def test_unparseable_declared_length_falls_through_to_the_stream() -> None:
    """A junk Content-Length tells us nothing; the stream cap still applies."""
    request = _request([b"hello"], headers={"content-length": "not-a-number"})
    assert await read_capped_body(request) == b"hello"


async def test_exactly_at_the_cap_is_accepted() -> None:
    """The boundary belongs to the accepted side; 413 means *over* the cap."""
    body = b"x" * (64 * 1024)
    request = _request([body], headers={"content-length": str(len(body))})
    assert await read_capped_body(request) == body
