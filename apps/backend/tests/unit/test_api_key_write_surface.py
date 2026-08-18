"""
Every route an API key can reach, and whether a read-only key may use it.

The enforcement is on the HTTP method inside the shared dependency, not
declared per route, precisely so a route added later cannot forget it. This
file holds the other half of that bargain: it enumerates the routes that
accept a key and asserts the set of write surfaces is the one somebody
decided on, so a new one arrives as a failing test rather than as a quiet
extension of what a read-only key can do.

The plan calls for reusing the route-enumeration approach the permission
baseline uses (G1). This is that approach, pointed at a different question.
"""

from __future__ import annotations

from typing import Any

#: The routes that accept a `tos_` key and change something. A read-only key
#: is refused all of them.
#:
#: Written out rather than derived, because the derivation is the thing under
#: test. If a new write surface appears, this list is where somebody has to
#: acknowledge it, and the acknowledgement is cheap next to the alternative:
#: a key issued specifically so it could not start a scan quietly gaining a
#: way to do something else.
EXPECTED_KEY_WRITE_ROUTES: set[tuple[str, str]] = {
    ("POST", "/v1/projects/{project_id}/scans"),
    ("POST", "/v1/projects/{project_id}/sbom-ingest"),
    ("POST", "/v1/scans/{scan_id}/post-pr-comment"),
}




#: The dependencies that resolve a `tos_` key into a principal.
#:
#: Matched by name because the principal builder is *called* by each of these
#: rather than injected, so it never appears in the dependency graph itself.
#: The list going stale is the failure mode this file already had once: it
#: matched the shared role gate alone and reported a clean write surface while
#: a POST behind a second dispatcher sat outside it. The test below pins the
#: list against the code, so a fourth dispatcher cannot be added quietly.
KEY_DISPATCHERS: tuple[str, ...] = (
    "require_role_or_api_key.",
    "_principal_from_jwt_or_api_key",
    "_principal_for_anchor",
    "snapshot_anchor",
)

#: The functions that call the principal builder, by their own names. Pinned
#: against the source so a fourth one cannot appear without somebody noticing
#: that KEY_DISPATCHERS needs to grow with it.
KEY_PRINCIPAL_CALLERS: set[str] = {
    # require_role_or_api_key's inner dependency, and the factory around it:
    # the AST walk reports both because the call sits inside the nested
    # function, which is itself inside the factory.
    "require_role_or_api_key",
    "_check",
    # the build-gate surfaces' own dispatcher
    "_principal_from_jwt_or_api_key",
    # the scan-snapshot anchor, used by many read endpoints
    "_principal_for_anchor",
}


def test_the_list_of_key_dispatchers_matches_the_code() -> None:
    """Every function that turns a key into a principal is named above.

    Without this, the enumeration below is only as complete as somebody's
    memory, and the thing it is guarding is whether an API key can reach the
    admin surface.
    """
    import ast
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    callers: set[str] = set()
    for source in backend.rglob("*.py"):
        if ".venv" in source.parts or "tests" in source.parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                called = getattr(inner, "func", None)
                if isinstance(called, ast.Name) and called.id == "get_api_key_principal":
                    callers.add(node.name)

    assert callers == KEY_PRINCIPAL_CALLERS, (
        "the set of functions that resolve an API key changed. Add the new "
        "one to KEY_PRINCIPAL_CALLERS and make sure KEY_DISPATCHERS matches "
        "whatever appears in the route dependency graph, or the enumeration "
        "below silently stops seeing its routes."
    )


def _key_reachable_routes() -> set[tuple[str, str]]:
    """(method, path) for every route whose gate accepts an API key."""
    from fastapi.routing import APIRoute

    from main import app

    def accepts_key(dependant: Any) -> bool:
        for sub in dependant.dependencies:
            qualname = getattr(sub.call, "__qualname__", "") or getattr(
                sub.call, "__name__", ""
            )
            if qualname.startswith(KEY_DISPATCHERS):
                return True
            if accepts_key(sub):
                return True
        return False

    rows: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not accepts_key(route.dependant):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            rows.add((method, route.path))
    return rows


def test_the_key_write_surface_is_the_one_we_decided_on() -> None:
    """A new route that a key can both reach and change things through.

    Only the write surface is enumerated. The reads are many and grow with
    every list endpoint that resolves a scan anchor, so pinning them would be
    churn that teaches people to update the list without reading it. The
    writes are few, and each one is a thing a CI key could do to somebody's
    project, which is worth a line of acknowledgement.
    """
    from core.api_key_auth import SAFE_METHODS

    unsafe = {
        (method, path)
        for method, path in _key_reachable_routes()
        if method not in SAFE_METHODS
    }

    assert unsafe == EXPECTED_KEY_WRITE_ROUTES, (
        "the surfaces an API key can change things through have moved. Add "
        "the new one to EXPECTED_KEY_WRITE_ROUTES, and check the breadth "
        "matrix in tests/integration/test_api_key_breadth.py covers it."
    )


def test_every_declared_write_really_uses_an_unsafe_method() -> None:
    """A write listed with a safe method would be a read-only key's way in."""
    from core.api_key_auth import SAFE_METHODS

    mislabelled = {
        (method, path)
        for method, path in EXPECTED_KEY_WRITE_ROUTES
        if method in SAFE_METHODS
    }

    assert mislabelled == set(), (
        "these are listed as writes but use a safe method, so a read-only key "
        f"would reach them: {sorted(mislabelled)}"
    )


def test_admin_routes_stay_out_of_reach_of_any_key() -> None:
    """Breadth narrows a key. It must not be the only thing standing between
    a key and the admin surface.

    The admin routes are JWT-only by a separate decision, and this asserts
    that decision still holds rather than relying on read-only keys to cover
    it: a read-write key is a normal thing to issue.
    """
    admin_reachable = {
        (method, path)
        for method, path in _key_reachable_routes()
        if path.startswith("/v1/admin")
    }

    assert admin_reachable == set(), (
        f"an API key can now reach admin routes: {sorted(admin_reachable)}"
    )
