"""
DTClient — request classification.

The client maps every response into one of three buckets:

    2xx → returned to caller
    4xx → DTClientError       (NOT counted by the breaker — user error)
    5xx → DTUnavailable       (counted by the breaker — DT outage)
    timeout / network → DTUnavailable

We use `httpx.MockTransport` so the tests are sealed inside the process —
no fixtures, no real network, no DT instance. The transport callback inspects
the request and returns whatever response we want to drive each branch.
"""

from __future__ import annotations

import httpx
import pytest

from integrations.dt import DTClientError, DTUnavailable

# ---------------------------------------------------------------------------
# Happy path — 2xx
# ---------------------------------------------------------------------------


def test_health_returns_parsed_version_on_200(make_dt_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(200, json={"version": "4.12.0"})

    client = make_dt_client(handler)
    try:
        version = client.health()
    finally:
        client.close()
    assert version == {"version": "4.12.0"}


def test_upsert_project_lookup_first_returns_existing_uuid(make_dt_client) -> None:
    """upsert_project must hit /api/v1/project/lookup before creating."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/project/lookup":
            return httpx.Response(200, json={"uuid": "existing-uuid"})
        # Should never reach the create path
        return httpx.Response(500, text="should not have created")

    client = make_dt_client(handler)
    try:
        uuid_str = client.upsert_project(name="x", version="1.0.0")
    finally:
        client.close()
    assert uuid_str == "existing-uuid"
    assert seen == ["GET /api/v1/project/lookup"]


def test_upsert_project_creates_when_lookup_404(make_dt_client) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/project/lookup":
            return httpx.Response(404, text="not found")
        if request.method == "PUT" and request.url.path == "/api/v1/project":
            return httpx.Response(201, json={"uuid": "new-uuid"})
        return httpx.Response(500, text="unexpected")

    client = make_dt_client(handler)
    try:
        uuid_str = client.upsert_project(name="x", version="1.0.0")
    finally:
        client.close()
    assert uuid_str == "new-uuid"
    assert seen == ["GET /api/v1/project/lookup", "PUT /api/v1/project"]


# ---------------------------------------------------------------------------
# 4xx — DTClientError
# ---------------------------------------------------------------------------


def test_health_raises_dt_client_error_on_401(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTClientError):
            client.health()
    finally:
        client.close()


def test_get_findings_raises_dt_client_error_on_403(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTClientError):
            client.get_findings(project_uuid="abc")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# count_vulnerabilities — reads the X-Total-Count header (#35)
# ---------------------------------------------------------------------------


def test_count_vulnerabilities_reads_x_total_count(make_dt_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/vulnerability"
        # pageSize=1 — we never want to materialise the catalog just to count.
        assert request.url.params.get("pageSize") == "1"
        return httpx.Response(200, json=[{}], headers={"X-Total-Count": "274321"})

    client = make_dt_client(handler)
    try:
        assert client.count_vulnerabilities() == 274321
    finally:
        client.close()


@pytest.mark.parametrize(
    "headers",
    [
        {},  # header absent entirely (some proxies strip it)
        {"X-Total-Count": "not-a-number"},  # garbage
        {"X-Total-Count": ""},  # empty
        {"X-Total-Count": "-5"},  # negative is clamped to 0
    ],
)
def test_count_vulnerabilities_degrades_to_zero_on_bad_header(
    make_dt_client, headers
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers=headers)

    client = make_dt_client(handler)
    try:
        assert client.count_vulnerabilities() == 0
    finally:
        client.close()


def test_count_vulnerabilities_raises_dt_unavailable_on_500(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="vuln list unavailable")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTUnavailable):
            client.count_vulnerabilities()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 5xx — DTUnavailable
# ---------------------------------------------------------------------------


def test_health_raises_dt_unavailable_on_500(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTUnavailable):
            client.health()
    finally:
        client.close()


def test_health_raises_dt_unavailable_on_503(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTUnavailable):
            client.health()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Timeout / network failure → DTUnavailable
# ---------------------------------------------------------------------------


def test_timeout_is_classified_as_dt_unavailable(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout simulated")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTUnavailable):
            client.health()
    finally:
        client.close()


def test_network_error_is_classified_as_dt_unavailable(make_dt_client) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect simulated")

    client = make_dt_client(handler)
    try:
        with pytest.raises(DTUnavailable):
            client.health()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# upload_sbom payload shape
# ---------------------------------------------------------------------------


def test_upload_sbom_base64_encodes_bom_and_returns_token(make_dt_client) -> None:
    """The wire body must contain {project, bom (base64)} per the DT API."""
    import base64

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/v1/bom"
        body = request.content
        import json as _json

        parsed = _json.loads(body)
        captured["body"] = parsed
        return httpx.Response(200, json={"token": "tok-123"})

    client = make_dt_client(handler)
    try:
        token = client.upload_sbom(project_uuid="proj-uuid", sbom_json=b'{"x":1}')
    finally:
        client.close()

    assert token == "tok-123"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["project"] == "proj-uuid"
    assert base64.b64decode(body["bom"]) == b'{"x":1}'
