"""
Public-demo admin read guard (core.middleware, DEMO_READ_ONLY).

The demo's published accounts include super admins so visitors can see the
admin screens. Under DEMO_READ_ONLY the middleware therefore refuses every
request under the super-admin-only prefixes unless it is a GET/HEAD on an
explicit allow-list of screens.

The route matrix is built from the real application's router, so:

  * a new admin route is covered the moment it is added, and is expected to be
    refused (the guard is deny-by-default);
  * adding a route to the middleware allow-list without adding it to
    ``EXPECTED_ALLOWED`` below fails the matrix;
  * an ``EXPECTED_ALLOWED`` entry for a route that no longer exists fails too.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute

from core.middleware import (
    _DEMO_ADMIN_PREFIXES,
    DemoReadOnlyMiddleware,
    _clean_raw_path,
    _is_demo_admin_read_allowed,
)

DEMO_TYPE = "urn:trustedoss:problem:demo-read-only"

ALL_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

# (method, route template) pairs a demo visitor may read. HEAD is implied for
# every GET entry. Keep this in step with _DEMO_ADMIN_READ_ALLOWLIST: the
# matrix below fails if the two disagree in either direction.
EXPECTED_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/v1/admin/health"),
        ("GET", "/v1/admin/disk"),
        ("GET", "/v1/admin/trivy/health"),
        ("GET", "/v1/admin/kev/health"),
        ("GET", "/v1/admin/eol/health"),
        ("GET", "/v1/admin/malicious/health"),
        ("GET", "/v1/admin/scans"),
        ("GET", "/v1/admin/teams"),
        ("GET", "/v1/admin/teams/{team_id}"),
        ("GET", "/v1/admin/organizations"),
        # Scoped to the demo organisation by services.demo_scope.
        ("GET", "/v1/admin/users"),
        ("GET", "/v1/admin/users/{user_id}"),
        ("GET", "/v1/admin/audit"),
    }
)

# Reads that must stay refused whatever else changes. Each is checked by name
# so a regex widened by accident cannot quietly admit one of them.
NEVER_ALLOWED: tuple[str, ...] = (
    "/v1/admin/backup",
    f"/v1/admin/backup/{'x' * 8}/download",
    "/v1/admin/users/export",
    "/v1/admin/audit/export.csv",
    f"/v1/admin/organizations/{uuid.uuid4()}/registry-credentials",
    f"/v1/admin/organizations/{uuid.uuid4()}/ticket-credentials",
    "/v1/admin/api-keys/hash-migration",
    "/v1/user-anonymisation/awaiting-execution",
)

# Super-admin-only reads outside the guarded prefixes, reviewed and left
# visible on the demo. Writes outside the prefixes are refused by the write
# guard and need no entry.
REVIEWED_SUPER_ADMIN_READS_OUTSIDE_PREFIXES: frozenset[tuple[str, str]] = frozenset(
    {
        # The organisation's default licence policy: configuration, no secrets.
        ("GET", "/v1/license-policies/org/{organization_id}"),
    }
)


def _app_routes() -> list[APIRoute]:
    from main import app

    return [r for r in app.routes if isinstance(r, APIRoute)]


def _guarded(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _DEMO_ADMIN_PREFIXES)


def _concrete(template: str) -> str:
    """Fill every ``{param}`` with a UUID, the shape real ids have."""
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), template)


def _guarded_templates() -> list[str]:
    return sorted({r.path for r in _app_routes() if _guarded(r.path)})


def _route_matrix() -> list[tuple[str, str]]:
    return [(m, t) for t in _guarded_templates() for m in ALL_METHODS]


def _expected(method: str, template: str) -> bool:
    read_method = "GET" if method in ("GET", "HEAD") else method
    return method in ("GET", "HEAD") and (read_method, template) in EXPECTED_ALLOWED


class _Inner:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def _drive(method: str, path: str) -> tuple[bool, int, dict[str, Any] | None, str]:
    """Run one request through the middleware; return (reached app, status, body, ctype)."""
    inner = _Inner()
    mw = DemoReadOnlyMiddleware(inner)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    asyncio.run(mw(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start.get("headers", [])}
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    body = json.loads(raw) if raw else None
    return inner.called, int(start["status"]), body, headers.get("content-type", "")


@pytest.fixture(autouse=True)
def _demo_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    yield


def test_matrix_is_not_empty() -> None:
    # Guards against a prefix typo that would make every case below vanish.
    templates = _guarded_templates()
    assert "/v1/admin/backup" in templates
    assert "/v1/user-anonymisation/awaiting-execution" in templates
    assert len(templates) >= 30


@pytest.mark.parametrize(("method", "template"), _route_matrix())
def test_every_admin_route_and_method(method: str, template: str) -> None:
    reached, status, body, ctype = _drive(method, _concrete(template))
    if _expected(method, template):
        assert reached, f"{method} {template} should reach the app"
        assert status == 200
        return
    assert not reached, f"{method} {template} must be refused on the demo"
    assert status == 403
    assert ctype.startswith("application/problem+json")
    assert body is not None
    assert body["type"] == DEMO_TYPE
    for field in ("type", "title", "status", "detail", "instance"):
        assert field in body
    # Reads get the admin-hidden flag; writes keep the plain write refusal.
    if method in ("GET", "HEAD", "OPTIONS"):
        assert body.get("demo_admin_hidden") is True
    else:
        assert "demo_admin_hidden" not in body


def test_expected_allowed_entries_exist() -> None:
    present = {(m, r.path) for r in _app_routes() for m in r.methods}
    stale = EXPECTED_ALLOWED - present
    assert not stale, f"allow-list cases for routes that no longer exist: {stale}"


@pytest.mark.parametrize("path", NEVER_ALLOWED)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_never_allowed_reads(method: str, path: str) -> None:
    reached, status, _body, _ = _drive(method, path)
    assert not reached
    assert status == 403


@pytest.mark.parametrize(
    "path",
    [
        "/v1/admin",
        "/v1/admin/",
        "/v1/admin/health/../backup",
        "/v1/admin/users/../backup",
        "//v1/admin/backup",
        "/v1//admin/backup",
        "/v1/admin/backup/",
        "/v1/admin/./backup",
        "/v1/admin/users/not-a-uuid",
        f"/v1/admin/users/{uuid.uuid4()}/extra",
        f"/v1/admin/teams/{uuid.uuid4()}/members",
        "/v1/admin/health/extra",
        "/v1/user-anonymisation",
    ],
)
def test_bypass_shapes_are_refused(path: str) -> None:
    reached, status, _body, _ = _drive("GET", path)
    assert not reached, path
    assert status == 403


def test_trailing_slash_on_an_allowed_path_passes() -> None:
    reached, status, _body, _ = _drive("GET", "/v1/admin/health/")
    assert reached
    assert status == 200


# Raw shapes the router dispatches on the RAW path while they normalize
# somewhere else. Each is a path the app really routes (a back-slash or dot
# inside a path parameter), so a guard that only looked at the normalized form
# would hand it to the handler. '%5C' is decoded to '\' before ASGI sees it.
@pytest.mark.parametrize(
    "path",
    [
        # routes to GET /v1/admin/backup/{name}/download, normalizes outside
        # /v1/admin entirely
        "/v1/admin/backup/..\\..\\users/download",
        "/v1/admin/backup/..\\..\\..\\health/download",
        # normalizes onto an allowed screen
        "/v1/admin/backup/..\\health/download",
        "/v1/admin/backup/x\\..\\..\\..\\admin\\health/download",
        # dot segments and empty segments in the raw path
        "/v1/admin/backup/./download",
        "/v1/admin/backup/../health",
        "/v1/admin/./health",
        "/v1/admin//health",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "DELETE"])
def test_raw_path_shapes_are_refused(method: str, path: str) -> None:
    reached, status, _body, _ = _drive(method, path)
    assert not reached, path
    assert status == 403


_TRAVERSAL_VALUES = ("..", ".", "..\\..\\health", "a\\b", "..\\..\\..\\auth\\login")


def _param_matrix() -> list[tuple[str, str, str]]:
    cases = []
    for template in _guarded_templates():
        if "{" not in template:
            continue
        for value in _TRAVERSAL_VALUES:
            for method in ("GET", "POST"):
                cases.append((method, template, value))
    return cases


@pytest.mark.parametrize(("method", "template", "value"), _param_matrix())
def test_traversal_valued_params_are_refused(method: str, template: str, value: str) -> None:
    """Every guarded route with a path parameter, the parameter filled with a
    traversal-shaped value. Whatever the value normalizes to, it is refused."""
    path = re.sub(r"\{[^}]+\}", lambda _m: value, template)
    reached, status, _body, _ = _drive(method, path)
    assert not reached, path
    assert status == 403


def test_lowercase_method_is_normalized() -> None:
    assert _is_demo_admin_read_allowed("get", "/v1/admin/health") is True
    assert _is_demo_admin_read_allowed("get", "/v1/admin/backup") is False


@pytest.mark.parametrize("path", ["/v1/adminx", "/v1/projects", "/v1/audit", "/health"])
def test_paths_outside_the_prefixes_are_not_this_guards_concern(path: str) -> None:
    reached, _status, _body, _ = _drive("GET", path)
    assert reached


@pytest.mark.parametrize(("method", "template"), _route_matrix())
def test_flag_off_passes_everything(
    monkeypatch: pytest.MonkeyPatch, method: str, template: str
) -> None:
    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    reached, _status, _body, _ = _drive(method, _concrete(template))
    assert reached


def _calls(dependant: Any) -> Iterator[Any]:
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def test_super_admin_reads_outside_prefixes_are_reviewed() -> None:
    """A super-admin-only read outside the guarded prefixes must be classified.

    The guard keys on path prefixes. A super-admin-only GET added elsewhere
    would be visible to the demo's public super admins without anyone deciding
    so; this fails until it is either moved under a prefix or reviewed here.
    """
    found: set[tuple[str, str]] = set()
    for route in _app_routes():
        if _guarded(route.path):
            continue
        names = {getattr(c, "__qualname__", "") for c in _calls(route.dependant)}
        if not any("require_super_admin_or_404" in n for n in names):
            continue
        for method in route.methods:
            if method in ("GET", "HEAD"):
                found.add((method, route.path))
    assert found == REVIEWED_SUPER_ADMIN_READS_OUTSIDE_PREFIXES


# The raw-path check is tested on its own. Through the middleware its effect
# is partly masked: the allow-list regexes match UUIDs and fixed words only,
# so a raw path with a back-slash would fail them anyway. Each refusal here
# has to hold without that second layer.
@pytest.mark.parametrize(
    "raw",
    [
        "/v1/admin/backup/..\\..\\users/download",
        "/v1/admin\\health",
        "/v1/admin/./health",
        "/v1/admin/../admin/health",
        "/v1//admin/health",
        "//v1/admin/health",
        "/v1/admin/health//",
        "v1/admin/health",
    ],
)
def test_clean_raw_path_refuses_non_plain_paths(raw: str) -> None:
    assert _clean_raw_path(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/v1/admin/health", "/v1/admin/health"),
        ("/v1/admin/health/", "/v1/admin/health"),
        ("/", "/"),
        ("/v1/admin/users/x.y", "/v1/admin/users/x.y"),
    ],
)
def test_clean_raw_path_keeps_plain_paths(raw: str, expected: str) -> None:
    assert _clean_raw_path(raw) == expected
