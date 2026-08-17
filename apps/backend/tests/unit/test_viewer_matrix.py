"""
Permission matrix for the viewer grade.

This file is the oracle for the gate rewiring, and it landed one change ahead
of it. Had the target and the gates moved together, the rewiring would have
been marking its own paper: forty-seven gates get edited at once, and whoever
edits them can make any expectation agree by editing it too. The split below
was reviewed on its own first, so the rewiring could only pass by moving the
gates to meet it.

The decisions behind the split (2026-08-17):

  - The approval queue and licence policy are readable. Auditing what was
    approved and which policy applied is the reason the grade exists; without
    it the role cannot do its job and people are handed developer instead,
    which is the situation this whole line of work exists to end.
  - Scan tool logs are not. They carry repository URLs, file paths and build
    commands, so opening them while closing the source tree would leak the
    same material through a different door.
  - Credential surfaces (API keys, app credentials) are not. Metadata alone is
    still a credential surface and has no business at the lowest grade.
  - The audit log stays where it is, at developer. Lowering it was not part of
    this change, and moving an existing grade is a separate decision from
    adding a new one.

Not every gate can be run here, and the ones that cannot are read rather than
skipped: see ``_gates``. A route scored by nothing would sit in the matrix
looking checked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TARGET_MATRIX = REPO_ROOT / "tests" / "contracts" / "viewer-target-matrix.json"

def _target() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(TARGET_MATRIX.read_text())
    return payload


def _principal(role: str) -> Any:
    import uuid

    from core.security import CurrentUser

    return CurrentUser(
        id=uuid.uuid4(),
        email=f"{role}@example.test",
        role=role,
        is_active=True,
        is_superuser=(role == "super_admin"),
    )


def _gates(method: str, path: str) -> tuple[list[Any], list[str]]:
    """Every gate on a route, as (callables we can run, floors we can only read).

    Not all gates can be exercised here. ``require_role_or_api_key`` resolves
    its principal from a request and a session, so its floor is read off the
    closure instead and compared by priority. Skipping those routes rather
    than reading them would leave the five CI-facing endpoints, one of which
    is a write, scored by nothing at all.
    """
    from fastapi.routing import APIRoute

    from main import app

    def walk(dependant: Any) -> tuple[list[Any], list[str]]:
        callables: list[Any] = []
        floors: list[str] = []
        for sub in dependant.dependencies:
            qualname = getattr(sub.call, "__qualname__", "")
            if qualname.startswith(("require_role.", "require_super_admin_or_404.")):
                callables.append(sub.call)
                if qualname.startswith("require_super_admin_or_404."):
                    floors.append("super_admin")
            if qualname.startswith(("require_role.", "require_role_or_api_key.")):
                code = getattr(sub.call, "__code__", None)
                closure = getattr(sub.call, "__closure__", None) or ()
                names = getattr(code, "co_freevars", ())
                cells = dict(zip(names, [c.cell_contents for c in closure]))
                role = cells.get("role")
                if isinstance(role, str):
                    floors.append(role)
            sub_callables, sub_floors = walk(sub)
            callables.extend(sub_callables)
            floors.extend(sub_floors)
        return callables, floors

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return walk(route.dependant)
    raise AssertionError(f"no route for {method} {path}")


def _viewer_clears(floor: str) -> bool:
    from core.security import _ROLE_PRIORITY

    return _ROLE_PRIORITY["viewer"] >= _ROLE_PRIORITY.get(floor, 0)


def test_viewer_reaches_every_route_the_target_allows() -> None:
    """The allow direction. Without it, a gate left too high reads as secure."""
    viewer = _principal("viewer")
    refused: list[str] = []
    for method, path in _target()["allow"]:
        callables, floors = _gates(method, path)
        assert floors, f"{method} {path} declares no floor to check"
        if not all(_viewer_clears(floor) for floor in floors):
            refused.append(f"{method} {path} (floors {floors})")
            continue
        if not all(_passes(gate, viewer) for gate in callables):
            refused.append(f"{method} {path} (gate refused viewer)")
    assert refused == [], f"viewer is refused routes the target opens: {refused}"


def test_viewer_is_refused_every_route_the_target_denies() -> None:
    """The deny direction, which is the one a rewiring mistake widens."""
    viewer = _principal("viewer")
    reached: list[str] = []
    for method, path in _target()["deny"]:
        callables, floors = _gates(method, path)
        assert floors, f"{method} {path} carries no floor to refuse with"
        stopped_by_floor = any(not _viewer_clears(floor) for floor in floors)
        stopped_by_gate = any(not _passes(gate, viewer) for gate in callables)
        if not (stopped_by_floor or stopped_by_gate):
            reached.append(f"{method} {path} (floors {floors})")
    assert reached == [], f"viewer reaches routes the target closes: {reached}"


def _passes(gate: Any, principal: Any) -> bool:
    from fastapi import HTTPException

    try:
        gate(current_user=principal)
    except HTTPException:
        return False
    return True


@pytest.mark.parametrize("role", ["team_admin", "super_admin"])
def test_the_grades_above_keep_everything_they_had(role: str) -> None:
    """Adding a grade below must not narrow the grades above.

    developer is left out on purpose: two routes in this matrix already sit at
    a team_admin floor, so developer not reaching them is the existing
    contract rather than a regression this change could cause.
    """
    target = _target()
    principal = _principal(role)
    lost = [
        f"{method} {path}"
        for method, path in target["allow"] + target["deny"]
        for gate in _gates(method, path)[0]
        if not _passes(gate, principal)
    ]
    assert lost == [], f"{role} lost access to: {lost}"
