"""
Pure-unit tests for the VEX import router's bounded-read guard.

These exercise ``api.v1.vex._read_bounded`` directly (no DB, no HTTP) so the
streaming size-limit logic is covered deterministically — the DB-backed
end-to-end 413 path lives in ``tests/integration/test_vex_import_api.py``.

The guard exists because the router used to do ``raw = await upload.read()``,
buffering the *entire* body before the service's decoded-size check. A client
that omits or lies about Content-Length could thereby push an arbitrarily large
payload into memory before any 413. ``_read_bounded`` reads in chunks and aborts
the moment the accumulated size crosses the cap.
"""

from __future__ import annotations

import pytest

from api.v1.vex import _read_bounded
from services.vex_import import VEXImportTooLarge


class _FakeUpload:
    """Minimal async stand-in for Starlette's ``UploadFile``.

    ``read(size)`` returns up to ``size`` bytes from ``payload`` and ``b""`` at
    EOF — exactly the contract ``_read_bounded`` relies on. ``read_calls`` lets
    a test assert we stopped early (did not drain the whole body) on overflow.
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._pos = 0
        self.read_calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if size < 0:
            chunk = self._payload[self._pos :]
            self._pos = len(self._payload)
            return chunk
        chunk = self._payload[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


async def test_read_bounded_returns_full_body_under_cap() -> None:
    body = b"x" * 500
    upload = _FakeUpload(body)
    out = await _read_bounded(upload, max_bytes=1000)  # type: ignore[arg-type]
    assert out == body


async def test_read_bounded_returns_body_exactly_at_cap() -> None:
    body = b"y" * 1000
    upload = _FakeUpload(body)
    out = await _read_bounded(upload, max_bytes=1000)  # type: ignore[arg-type]
    assert out == body


async def test_read_bounded_raises_when_over_cap() -> None:
    body = b"z" * 5000
    upload = _FakeUpload(body)
    with pytest.raises(VEXImportTooLarge):
        await _read_bounded(upload, max_bytes=100)  # type: ignore[arg-type]


async def test_read_bounded_aborts_early_does_not_drain_body() -> None:
    """An over-cap body must be rejected without reading every chunk — the
    whole point is not buffering a hostile multi-GB upload."""
    # 64 KiB chunk size in the helper; a 1 MiB body over a 100-byte cap should
    # trip on the first chunk.
    body = b"a" * (1024 * 1024)
    upload = _FakeUpload(body)
    with pytest.raises(VEXImportTooLarge):
        await _read_bounded(upload, max_bytes=100)  # type: ignore[arg-type]
    assert upload.read_calls == 1  # tripped on the first chunk, no full drain


async def test_read_bounded_empty_body() -> None:
    upload = _FakeUpload(b"")
    out = await _read_bounded(upload, max_bytes=100)  # type: ignore[arg-type]
    assert out == b""
