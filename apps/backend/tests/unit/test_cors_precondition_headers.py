# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The CORS preflight admits the precondition headers the SPA sends.

Production serves the SPA and the API from one origin, so no preflight runs
there and a missing header in the allow-list goes unnoticed. A split-origin
deployment (or local dev on two ports) preflights every PUT that carries
``If-Match`` or ``If-None-Match``, and a header outside the list answers 400:
the browser never sends the write. The gate policy editor sends
``If-None-Match: *`` on a team's first save and ``If-Match`` on every later
one, so both have to pass.

Driven through the real app, not by reading the list, so a header dropped from
the middleware configuration fails here whatever the comment above it says.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from main import _cors_origins, app


@pytest.mark.parametrize(
    "requested",
    ["content-type,if-none-match", "content-type,if-match", "authorization,content-type,if-match"],
)
async def test_a_gate_policy_save_preflight_is_allowed(requested: str) -> None:
    origin = _cors_origins[0]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            f"/v1/gate-policies/teams/{uuid.uuid4()}",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": requested,
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    raw = response.headers["access-control-allow-headers"]
    allowed = {h.strip().lower() for h in raw.split(",")}
    assert set(requested.split(",")) <= allowed


async def test_a_header_outside_the_list_is_still_refused() -> None:
    """The control: the preflight check is live, so the passes above mean something."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            f"/v1/gate-policies/teams/{uuid.uuid4()}",
            headers={
                "Origin": _cors_origins[0],
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-not-allowed",
            },
        )

    assert response.status_code == 400
