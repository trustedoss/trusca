# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Privilege order is written down once.

There is already a contract test tying ``core.security._ROLE_PRIORITY`` to the
role enum, and it was green the whole time ``api/v1/ws.py`` carried a second
map of its own that listed three grades and omitted ``viewer``. A contract test
cannot check a table it does not know exists, so the thing to hold is not the
contents of the map but the fact that there is one of it.

The copy was not an oversight either: a comment explained that it existed to
avoid importing a private name. That is how a duplicate survives review, and
it is why the guard is on the shape rather than on anyone remembering.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: Where privilege order is allowed to be written.
HOME = "core/security.py"

SEARCH_DIRS = ("api", "core", "services", "tasks", "integrations", "notifications", "schemas")

#: Two grades are enough to encode an order. One name in a dict is a lookup,
#: not a ranking, and pinning it at two rather than three is deliberate: the
#: map this guard was written for listed three, so a threshold of three would
#: have let a two-grade version of the same mistake through.
_MIN_GRADES_TO_COUNT = 2


def _role_names() -> frozenset[str]:
    from models.auth import ROLE_VALUES

    return frozenset(ROLE_VALUES)


def _dict_literals_keyed_by_role(tree: ast.AST, roles: frozenset[str]) -> list[int]:
    """Line numbers of dict literals whose string keys are role names."""
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            k.value
            for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
        if len(keys) < _MIN_GRADES_TO_COUNT:
            continue
        if set(keys) <= roles:
            found.append(node.lineno)
    return found


def test_the_guard_knows_the_role_names() -> None:
    """Without the names, every check below is vacuously true."""
    roles = _role_names()
    assert "viewer" in roles and "super_admin" in roles, roles


def test_privilege_order_is_not_written_down_twice() -> None:
    roles = _role_names()
    offenders: list[str] = []

    for directory in SEARCH_DIRS:
        root = BACKEND_ROOT / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            relative = str(path.relative_to(BACKEND_ROOT))
            if relative == HOME:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a parse failure is its own bug
                continue
            for line in _dict_literals_keyed_by_role(tree, roles):
                offenders.append(f"{relative}:{line}")

    assert not offenders, (
        "a second table keyed by role grades, outside " + HOME + ": "
        + ", ".join(offenders)
        + ". A copy drifts from the original without any contract test noticing, "
        "which is exactly how ws.py came to rank a viewer against a map that had "
        "no viewer in it. Import core.security.highest_role instead."
    )


def test_the_home_still_holds_the_map() -> None:
    """If the map moves, the exemption above starts excusing the wrong file."""
    tree = ast.parse((BACKEND_ROOT / HOME).read_text(encoding="utf-8"))
    assert _dict_literals_keyed_by_role(tree, _role_names()), (
        f"{HOME} no longer contains a dict keyed by role grades; move HOME to "
        "wherever privilege order now lives, or this guard exempts a file that "
        "has nothing to exempt"
    )
