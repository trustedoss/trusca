"""
Unit tests for `core.middleware.SecurityHeadersMiddleware`.

The middleware appends a fixed set of hardening headers to every HTTP
response. Tests pin:

  - The three baseline headers are always present on a successful response.
  - The headers are also present on a 4xx response (RFC 7807 error envelope
    surface — security-reviewer Info #2 from PR #13 was specifically about
    `/notice` returning 401/422 without nosniff).
  - The middleware does not override an upstream-set header, so future
    per-route policies can opt out.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ok")
    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/forbidden")
    async def _forbidden() -> JSONResponse:
        return JSONResponse({"detail": "no"}, status_code=403)

    @app.get("/custom-frame")
    async def _custom_frame() -> PlainTextResponse:
        return PlainTextResponse(
            "embed",
            headers={"X-Frame-Options": "SAMEORIGIN"},
        )

    return app


def test_baseline_headers_on_success() -> None:
    client = TestClient(_build_app())
    response = client.get("/ok")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_baseline_headers_on_error_response() -> None:
    """A 4xx must still carry the security headers (defence-in-depth)."""
    client = TestClient(_build_app())
    response = client.get("/forbidden")
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_existing_header_is_preserved() -> None:
    """If the route handler already set the header, the middleware
    leaves it alone (no duplication, no override)."""
    client = TestClient(_build_app())
    response = client.get("/custom-frame")
    # Route's value wins.
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    # Other headers still appended.
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    # No duplicate header — `headers.get_list` returns a single entry.
    frame_values = response.headers.get_list("x-frame-options")
    assert frame_values == ["SAMEORIGIN"]
