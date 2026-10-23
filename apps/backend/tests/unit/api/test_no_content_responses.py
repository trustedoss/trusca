"""A 204 No Content response must carry no body, at the source and on the wire.

``JSONResponse(content=None, status_code=204)`` renders the body ``null`` and
declares ``content-length: 4``. The ASGI transport the API tests use accepts
that without complaint, so every in-process test passes. A real server does
not: uvicorn's h11 protocol raises ``LocalProtocolError`` and httptools raises
"Response content longer than Content-Length". Either way the server logs an
ERROR and drops the keep-alive connection, so the next request a client sends
on it fails.

Two layers guard the class, and each is checked on its own:

* A static audit over the backend source (AST, not text) that refuses any
  body-carrying response class built with status 204 and any ``Response(...)``
  given content with status 204. A route declared ``status_code=204`` that
  returns a plain value is out of scope: FastAPI empties the body itself for
  statuses that forbid one. Only an explicit response object bypasses that,
  and that is what the audit looks at. The audit also runs against known-bad
  snippets, so a walker that stops matching cannot pass. It resolves
  ``import ... as`` aliases and ``HTTPStatus.NO_CONTENT``. Known blind spots:
  a status held in a variable, a response class reached through a module
  alias of an unknown name, a subclass of a body-carrying class, and setting
  ``.status_code = 204`` on an existing response object.
* A real HTTP round trip: the remediation route that had the defect is served
  by uvicorn (h11) on an ephemeral port, and two requests go over one
  connection. The second request only succeeds if the first 204 left the
  connection reusable.
"""

from __future__ import annotations

import ast
import http.client
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
EXCLUDED_DIRS = {"tests", ".venv", "venv", "__pycache__", "node_modules"}

# Building one of these with status 204 always sends a body (or a
# content-length) the status forbids. ``problem_response`` is the project's
# RFC 7807 helper, which always renders a JSON document.
_BODY_CARRYING = {
    "JSONResponse",
    "ORJSONResponse",
    "UJSONResponse",
    "HTMLResponse",
    "PlainTextResponse",
    "StreamingResponse",
    "FileResponse",
    "RedirectResponse",
    "problem_response",
}


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_204(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return bool(node.value == 204)
    # ``status.HTTP_204_NO_CONTENT`` (starlette/fastapi) and
    # ``HTTPStatus.NO_CONTENT`` (stdlib).
    return _callee_name(node) in ("HTTP_204_NO_CONTENT", "NO_CONTENT")


def _status_arg(call: ast.Call) -> ast.expr | None:
    # ``XResponse(content, status_code)`` and ``problem_response(request,
    # status_code, ...)`` both take the status second when positional.
    for kw in call.keywords:
        if kw.arg == "status_code":
            return kw.value
    return call.args[1] if len(call.args) > 1 else None


def _content_arg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "content":
            return kw.value
    return call.args[0] if call.args else None


def _is_empty(node: ast.expr | None) -> bool:
    return node is None or (isinstance(node, ast.Constant) and node.value in (None, b"", ""))


class _Audit:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.response_204_calls = 0
        # Every file with a 204 construction of any shape, good or bad.
        self.files_with_204: set[str] = set()

    def check(self, source: str, label: str) -> None:
        tree = ast.parse(source)
        # ``from fastapi.responses import JSONResponse as J`` -> {"J": "JSONResponse"}
        aliases = {
            alias.asname: alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self._check_call(node, label, aliases)

    def _check_call(self, call: ast.Call, label: str, aliases: dict[str, str]) -> None:
        name = _callee_name(call.func)
        name = aliases.get(name, name) if name is not None else None
        if not _is_204(_status_arg(call)):
            return
        if name in _BODY_CARRYING:
            self.files_with_204.add(label)
            self.violations.append(
                f"{label}:{call.lineno}: {name}(...) with status 204 sends a body"
            )
        elif name == "Response":
            self.files_with_204.add(label)
            self.response_204_calls += 1
            if not _is_empty(_content_arg(call)):
                self.violations.append(
                    f"{label}:{call.lineno}: Response(content=...) with status 204 sends a body"
                )


def _backend_sources() -> Iterator[Path]:
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if EXCLUDED_DIRS.intersection(rel.parts[:-1]):
            continue
        yield path


def test_backend_source_has_no_204_with_a_body() -> None:
    audit = _Audit()
    for path in _backend_sources():
        audit.check(path.read_text(encoding="utf-8"), str(path.relative_to(BACKEND)))

    assert audit.violations == [], "\n".join(audit.violations)
    # The audit saw the tree it claims to cover: on 2026-10-23 the backend has
    # 29 ``Response(status_code=204)`` constructions. A walker that silently
    # stops matching would report zero and pass the line above.
    assert audit.response_204_calls >= 25, audit.response_204_calls
    # The site that had the defect is among the audited 204 constructions, so
    # the audit is shown to read the shape it was written for there.
    assert "api/v1/remediation.py" in audit.files_with_204, sorted(audit.files_with_204)


@pytest.mark.parametrize(
    "snippet",
    [
        "JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT)",
        "JSONResponse(None, 204)",
        "ORJSONResponse(content={}, status_code=HTTP_204_NO_CONTENT)",
        "responses.PlainTextResponse('', status_code=204)",
        "problem_response(request, 204, 'x')",
        "Response(content=b'null', status_code=204)",
        "Response('x', 204)",
        "JSONResponse(None, status_code=HTTPStatus.NO_CONTENT)",
        "from fastapi.responses import JSONResponse as J\nJ(None, 204)",
    ],
)
def test_audit_flags_known_bad_shapes(snippet: str) -> None:
    audit = _Audit()
    audit.check(snippet, "<snippet>")
    assert len(audit.violations) == 1, audit.violations


@pytest.mark.parametrize(
    "snippet",
    [
        "Response(status_code=status.HTTP_204_NO_CONTENT)",
        "Response(content=None, status_code=204)",
        "JSONResponse(content=None, status_code=status.HTTP_200_OK)",
        "problem_response(request, 404, 'x')",
    ],
)
def test_audit_accepts_empty_204_and_non_204_shapes(snippet: str) -> None:
    audit = _Audit()
    audit.check(snippet, "<snippet>")
    assert audit.violations == []


class _ErrorCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_remediation_no_op_204_is_clean_over_a_real_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn
    from fastapi import FastAPI

    import api.v1.remediation as remediation_api
    from core.db import get_db
    from core.security import CurrentUser, get_optional_current_user
    from services.remediation_pr_service import RemediationPRResult

    calls: list[uuid.UUID] = []

    async def _fake_create(_session, _actor, project_id, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(project_id)
        return RemediationPRResult(record=None, created=False, no_op_reason="no_manifest_change")

    monkeypatch.setattr(remediation_api, "create_npm_remediation_pr", _fake_create)

    async def _no_db():  # type: ignore[no-untyped-def]
        yield None

    def _admin() -> CurrentUser:
        return CurrentUser(id=uuid.uuid4(), email="admin@example.test", role="super_admin")

    app = FastAPI()
    app.include_router(remediation_api.router)
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_optional_current_user] = _admin


    # log_config=None: uvicorn must not run dictConfig, which would both
    # reconfigure logging for the whole test process and detach the collector
    # below (measured: a handler added before Config() is gone after it).
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        http="h11",
        lifespan="off",
        log_config=None,
        log_level="error",
        access_log=False,
    )
    errors = _ErrorCollector()
    uvicorn_logger = logging.getLogger("uvicorn.error")
    uvicorn_logger.addHandler(errors)

    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive(), "uvicorn exited before it started"
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]

        project_id = uuid.uuid4()
        path = f"/v1/projects/{project_id}/remediation/npm/pull-request"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            for attempt in (1, 2):
                conn.request(
                    "POST",
                    path,
                    body=b'{"manifest": "{}"}',
                    headers={"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                body = resp.read()
                assert resp.status == 204, (attempt, resp.status, body)
                assert body == b"", (attempt, body)
                assert resp.getheader("content-length") in (None, "0"), (
                    attempt,
                    resp.getheader("content-length"),
                )
                if attempt == 1:
                    # Checked before the connection is reused, so this
                    # assertion fails on its own when the server rejects the
                    # body, rather than being pre-empted by the reuse failing.
                    # The server raises after the client already read the 204,
                    # so give it a moment to log.
                    time.sleep(0.3)
                    assert [r.getMessage() for r in errors.records] == []
        finally:
            conn.close()

        # Both requests reached the route over the one connection.
        assert calls == [project_id, project_id]
        assert [r.getMessage() for r in errors.records] == []
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        uvicorn_logger.removeHandler(errors)
    assert not thread.is_alive(), "uvicorn did not shut down"
