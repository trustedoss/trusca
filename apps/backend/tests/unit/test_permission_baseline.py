"""
Permission baseline: the oracle every later authorization change is scored
against.

Why this file exists: the role floor is not declared in one place. It is
``Depends(require_role(...))`` repeated across the API modules, so "did this
change widen a gate?" has no answer that a reviewer can read off a page. Two
failure modes follow from that, and each needs its own direction of assertion:

  - Widening. A route loses its gate, or gains a lower floor. Tests that only
    assert denial ("a developer gets 403 here") stay green when a surface is
    opened up, because the denial they pin is somewhere else.
  - Narrowing, and its quieter cousin, staleness. The gate comparison treats
    an unknown role as privilege 0 (`core.security._has_at_least`), so a role
    that is missing from the matrix is denied everywhere, which passes every
    security assertion while the product is broken.

So the inventory in ``tests/contracts/permission-matrix.json`` is asserted
both ways: a route with no row fails (a surface shipped undeclared), and a row
with no route fails (the matrix went stale). Adding a route means adding its
row; that coupling is the point, and it is what keeps the matrix usable as an
oracle after the surface count grows.

The gate assertions likewise run in both directions: for every declared floor,
the roles at or above it are asserted to pass and the roles below it are
asserted to be rejected. These call the dependency callables directly (the
factories accept ``current_user`` as a keyword argument precisely so they can
be exercised without a request), which keeps the whole matrix cheap enough to
run on every push, with no database.

Two surfaces are declared rather than derived, and both say so in the fixture:
the ``role_or_key`` routes resolve their principal from a request and a
session, so only their declared floor is checked here (behavior lives in the
API-key tests), and the WebSocket route authenticates in-band rather than
through the dependency tree.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[4]
MATRIX_PATH = REPO_ROOT / "tests" / "contracts" / "permission-matrix.json"

# Callables that establish a principal. A route whose dependency tree contains
# none of these reaches its handler with no identity, which is only correct for
# the routes the fixture marks `public` (each with a stated reason).
PRINCIPAL_RESOLVERS = {
    "get_current_user",
    "get_optional_current_user",
    "_principal_from_jwt_or_api_key",
}

# Dependency factories that impose a floor, keyed by the qualname prefix their
# closures carry. The value is the gate vocabulary written in the fixture.
_ROLE_FACTORIES = {
    "require_role.": "role>=",
    "require_role_or_api_key.": "role_or_key>=",
}

# Most specific first: a route carrying both an admin gate and the optional
# user resolver is an admin route, not an "authed" one.
_GATE_PRECEDENCE = ("super_admin_404", "role_or_key>=", "role>=", "authed")

ALL_ROLES = ("developer", "team_admin", "super_admin")


# ---------------------------------------------------------------------------
# Extraction: what gate does the app actually carry?
# ---------------------------------------------------------------------------


def _gate_of(call: Any) -> str | None:
    """Classify one dependency callable, or None if it is not a gate."""
    qualname = getattr(call, "__qualname__", "")
    code = getattr(call, "__code__", None)
    closure = getattr(call, "__closure__", None) or ()
    cells = dict(zip(getattr(code, "co_freevars", ()), [c.cell_contents for c in closure]))

    for prefix, vocabulary in _ROLE_FACTORIES.items():
        if qualname.startswith(prefix):
            return f"{vocabulary}{cells.get('role')}"
    if qualname.startswith("require_super_admin_or_404."):
        return "super_admin_404"
    if getattr(call, "__name__", "") in PRINCIPAL_RESOLVERS:
        return "authed"
    return None


def _gates_in(dependant: Any) -> set[str]:
    found: set[str] = set()
    for sub in dependant.dependencies:
        gate = _gate_of(sub.call)
        if gate is not None:
            found.add(gate)
        found |= _gates_in(sub)
    return found


def _gate_callables_in(dependant: Any) -> list[Any]:
    """The gate callables themselves, so the allow/deny direction can be run."""
    found: list[Any] = []
    invocable = ("require_role.", "require_super_admin_or_404.")
    for sub in dependant.dependencies:
        qualname = getattr(sub.call, "__qualname__", "")
        if qualname.startswith(invocable):
            found.append(sub.call)
        found.extend(_gate_callables_in(sub))
    return found


def _pick(found: set[str]) -> str:
    for prefix in _GATE_PRECEDENCE:
        for gate in sorted(found):
            if gate.startswith(prefix):
                return gate
    return "public"


def _app_routes() -> dict[tuple[str, str], dict[str, Any]]:
    """(method, path) -> {gate, callables} for every HTTP route the app serves."""
    from fastapi.routing import APIRoute

    from main import app

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        entry = {
            "gate": _pick(_gates_in(route.dependant)),
            "callables": _gate_callables_in(route.dependant),
        }
        for method in route.methods - {"HEAD", "OPTIONS"}:
            rows[(method, route.path)] = entry
    return rows


def _matrix_rows() -> dict[tuple[str, str], dict[str, Any]]:
    payload = json.loads(MATRIX_PATH.read_text())
    return {(row["method"], row["path"]): row for row in payload["routes"]}


# Rows the dependency walk cannot see, declared in the fixture with a note.
_NON_APIROUTE = {("WEBSOCKET", "/ws/scans/{scan_id}")}


def _principal(role: str, *, is_active: bool = True) -> Any:
    from core.security import CurrentUser

    return CurrentUser(
        id=uuid.uuid4(),
        email=f"{role}@example.test",
        role=role,
        is_active=is_active,
        is_superuser=(role == "super_admin"),
    )


# ---------------------------------------------------------------------------
# Inventory, both directions
# ---------------------------------------------------------------------------


def test_every_route_is_declared_in_the_matrix() -> None:
    """A surface that ships without a row is a surface nobody classified."""
    undeclared = sorted(set(_app_routes()) - set(_matrix_rows()))
    assert undeclared == [], (
        "these routes carry no permission-matrix row; add one to "
        f"{MATRIX_PATH.name} stating the gate: {undeclared}"
    )


def test_every_matrix_row_still_has_a_route() -> None:
    """The staleness direction: a matrix that outlives its routes stops being an oracle."""
    stale = sorted(set(_matrix_rows()) - set(_app_routes()) - _NON_APIROUTE)
    assert stale == [], (
        f"these {MATRIX_PATH.name} rows match no route; remove them or restore "
        f"the route: {stale}"
    )


def test_declared_gate_matches_the_code() -> None:
    """Every row's gate is the gate the dependency tree actually carries."""
    app_routes = _app_routes()
    mismatches = [
        (key, row["gate"], app_routes[key]["gate"])
        for key, row in sorted(_matrix_rows().items())
        if key in app_routes and row["gate"] != app_routes[key]["gate"]
    ]
    assert mismatches == [], (
        "declared gate != code gate (key, declared, actual). Decide which side "
        f"is wrong before editing the fixture: {mismatches}"
    )


def test_public_routes_are_few_and_each_states_why() -> None:
    """No principal at all is a decision, so it is written down and reviewed."""
    public = {key: row for key, row in _matrix_rows().items() if row["gate"] == "public"}
    missing_reason = sorted(key for key, row in public.items() if not row.get("why"))
    assert (
        missing_reason == []
    ), f"a public route needs a `why` in {MATRIX_PATH.name}: {missing_reason}"


# ---------------------------------------------------------------------------
# Gate behavior, both directions
# ---------------------------------------------------------------------------


def _role_gate_cases() -> list[tuple[str, str, str, bool]]:
    """(method, path, role, should_pass) for every route carrying a role floor."""
    from core.security import _ROLE_PRIORITY

    cases: list[tuple[str, str, str, bool]] = []
    for (method, path), entry in sorted(_app_routes().items()):
        gate = entry["gate"]
        if not gate.startswith("role>="):
            continue
        required = gate.removeprefix("role>=")
        for role in ALL_ROLES:
            should_pass = _ROLE_PRIORITY[role] >= _ROLE_PRIORITY[required]
            cases.append((method, path, role, should_pass))
    return cases


@pytest.mark.parametrize(("method", "path", "role", "should_pass"), _role_gate_cases())
def test_role_gate_allows_and_denies_the_declared_roles(
    method: str, path: str, role: str, should_pass: bool
) -> None:
    """Both directions on one gate: the floor lets the right roles through and stops the rest.

    Denial-only assertions miss a widened gate; allow-only assertions miss a
    narrowed one. Every route runs both.
    """
    gates = _app_routes()[(method, path)]["callables"]
    assert gates, f"{method} {path} is declared as role-gated but carries no gate callable"
    user = _principal(role)

    for gate in gates:
        if should_pass:
            assert gate(current_user=user) is user
        else:
            with pytest.raises(HTTPException) as excinfo:
                gate(current_user=user)
            assert excinfo.value.status_code == 403


@pytest.mark.parametrize("role", ["developer", "team_admin"])
def test_admin_gate_hides_its_existence_from_lower_roles(role: str) -> None:
    """The admin surface answers 404, not 403: a 403 would confirm the URL space."""
    from core.security import require_super_admin_or_404

    gate = require_super_admin_or_404()
    with pytest.raises(HTTPException) as excinfo:
        gate(current_user=_principal(role))
    assert excinfo.value.status_code == 404

    assert gate(current_user=_principal("super_admin")) is not None


@pytest.mark.parametrize("gate_factory_arg", ["developer", "team_admin"])
def test_anonymous_and_deactivated_callers_are_rejected_before_any_role_check(
    gate_factory_arg: str,
) -> None:
    """A deactivated user still holds a valid token until it expires."""
    from core.security import require_role

    gate = require_role(gate_factory_arg)
    for user in (None, _principal("super_admin", is_active=False)):
        with pytest.raises(HTTPException) as excinfo:
            gate(current_user=user)
        assert excinfo.value.status_code == 401


def test_role_priority_is_pinned() -> None:
    """The map every gate comparison reads. Reordering it silently re-grades every route.

    The numbers moved up by one when ``viewer`` was added (migration 0055):
    the lowest grade cannot take 0, because ``_has_at_least`` reads this map
    with a default of 0 and a real grade sitting there would be
    indistinguishable from a role that does not exist.
    """
    from core.security import _ROLE_PRIORITY

    assert _ROLE_PRIORITY == {
        "viewer": 1,
        "developer": 2,
        "team_admin": 3,
        "super_admin": 4,
    }


def test_a_role_outside_the_priority_map_is_denied_everywhere() -> None:
    """Fail-safe direction, and a note for whoever adds the next role.

    An unknown role compares as privilege 0, so adding an enum value without
    adding it to the priority map produces a user who can reach nothing. That
    denial passes every security assertion in the suite, which is why the
    allow direction above is not optional.
    """
    from core.security import require_role

    gate = require_role("developer")
    with pytest.raises(HTTPException) as excinfo:
        gate(current_user=_principal("role_that_does_not_exist_yet"))
    assert excinfo.value.status_code == 403
