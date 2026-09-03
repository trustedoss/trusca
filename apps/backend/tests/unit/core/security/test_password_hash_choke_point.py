# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""One place rotates a password hash, and it stamps the clock while it does.

`set_password` writes `hashed_password` and `password_changed_at` together,
and the second is what refuses the access tokens already in circulation. A path
that writes only the first leaves somebody who was told their password changed
with the old sessions still open, which is worse than not offering the feature:
they act on the belief that the change took effect.

This is the structural half of that guarantee. It replaces a line-by-line
regex, which a security review showed could be stepped around three ways --
`update().values(hashed_password=...)`, `setattr(user, "hashed_password", ...)`,
and raw SQL -- none of which look like an assignment to a text search. A guard
that can be walked around is worse than no guard, because it reports a check
that never happened. String matching has now come up short twice in this area,
so the tree is read instead of the text.

Creating a user is not rotation and is not flagged: there is no earlier session
to end, and `password_changed_at` stays NULL until the first real change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COLUMN = "hashed_password"

#: Where the rotation belongs. `set_password` lives here.
CHOKE_POINT = "core/security.py"

#: Directories that make up the running product, plus the operator scripts:
#: a script that reset a password without the stamp would mislead in exactly
#: the same way, and `scripts/` is where bootstrap and seeding live.
SEARCHED = (
    "api",
    "core",
    "integrations",
    "notifications",
    "schemas",
    "scripts",
    "services",
    "tasks",
)


def _backend_root() -> Path:
    """The backend package root, not the tests tree that mirrors its layout.

    `tests/unit/core/security/` has both a `core` and a `services` sibling, so
    a walk that only looks for those directory names stops inside `tests` and
    reports this file's own fixtures as production code. Requiring `main.py`
    and `alembic.ini` names the real root.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "main.py").is_file() and (candidate / "alembic.ini").is_file():
            return candidate
    pytest.skip("backend root not found above this file")
    raise AssertionError("unreachable")


def _is_column_attribute(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == COLUMN


def _rotations_in(tree: ast.AST) -> list[tuple[int, str]]:
    """Every way this tree writes the column, other than creating a row.

    Returns (line, what it looked like) so a failure names the shape as well as
    the place -- the four forms read nothing alike, and "line 88" without the
    shape sends the next person looking for an assignment that is not there.
    """
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # user.hashed_password = ...   (and the augmented / annotated forms)
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if _is_column_attribute(target) and isinstance(node, ast.stmt):
                found.append((node.lineno, "attribute assignment"))

        if not isinstance(node, ast.Call):
            continue

        # setattr(user, "hashed_password", ...)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == COLUMN
        ):
            found.append((node.lineno, "setattr"))

        # update(User).values(hashed_password=...) -- a keyword on a method
        # call. The same keyword on a plain name is `User(hashed_password=...)`,
        # which constructs a row rather than rotating one, and is left alone.
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"values", "update"}:
            for keyword in node.keywords:
                if keyword.arg == COLUMN:
                    found.append((node.lineno, f"{node.func.attr}() keyword"))

        # session.execute(text("UPDATE users SET hashed_password = ..."))
        for argument in node.args:
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                continue
            sql = " ".join(argument.value.split()).lower()
            if "update" in sql and COLUMN in sql and "set" in sql:
                found.append((node.lineno, "raw SQL"))

    return found


def _production_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory in SEARCHED:
        base = root / directory
        if not base.is_dir():
            continue
        files.extend(p for p in base.rglob("*.py") if "tests" not in p.parts)
    return sorted(files)


def test_the_guard_is_reading_the_production_tree() -> None:
    """A walk that finds no files passes while checking nothing.

    This has happened here before: a root computed one directory too shallow
    left five tests skipping inside a green run.
    """
    root = _backend_root()
    files = _production_files(root)

    assert len(files) > 100, f"only {len(files)} files found under {root}; the walk is wrong"
    assert any(p.name == "auth_service.py" for p in files), "services/ is not being read"
    assert any("scripts" in p.parts for p in files), "scripts/ is not being read"


def test_the_guard_recognises_each_way_around_it() -> None:
    """The four shapes, on a tree built here rather than hoped for in the wild.

    Written because the previous guard silently missed three of them. If a
    future edit narrows the walk, this fails here rather than by quietly
    passing over a real occurrence somewhere else.
    """
    sources = {
        "attribute assignment": "def f(user):\n    user.hashed_password = h\n",
        "setattr": 'def f(user):\n    setattr(user, "hashed_password", h)\n',
        "values() keyword": "def f(s):\n    s.execute(update(User).values(hashed_password=h))\n",
        "raw SQL": 'def f(s):\n    s.execute(text("UPDATE users SET hashed_password = :h"))\n',
    }
    for shape, source in sources.items():
        found = _rotations_in(ast.parse(source))
        assert found, f"the guard does not recognise {shape}"
        assert found[0][1] == shape, found

    # And construction is left alone, or every signup path becomes an offender.
    assert not _rotations_in(ast.parse("def f():\n    return User(hashed_password=h)\n"))


def test_nothing_outside_the_choke_point_rotates_a_password_hash() -> None:
    root = _backend_root()
    offenders: list[str] = []

    for path in _production_files(root):
        relative = path.relative_to(root)
        if str(relative) == CHOKE_POINT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is its own bug
            continue
        for line, shape in _rotations_in(tree):
            offenders.append(f"{relative}:{line} ({shape})")

    assert not offenders, (
        "these write " + COLUMN + " without going through set_password, so the "
        "sessions they were meant to end stay open and the person is told "
        "otherwise: " + ", ".join(offenders)
    )


def test_the_choke_point_still_holds_the_rotation() -> None:
    """If `set_password` moves, the exemption above starts excusing the wrong file."""
    root = _backend_root()
    tree = ast.parse((root / CHOKE_POINT).read_text(encoding="utf-8"))

    assert _rotations_in(tree), (
        f"{CHOKE_POINT} no longer writes {COLUMN}; point CHOKE_POINT at wherever "
        "the rotation lives now, or this guard is exempting a file that does not "
        "need exempting while the real one goes unchecked"
    )


# ---------------------------------------------------------------------------
# The WebSocket resolver, which is a copy and does not inherit
# ---------------------------------------------------------------------------


def _calls_within(tree: ast.AST, function_name: str) -> set[str]:
    """Names called inside one named function, ignoring the rest of the file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name:
            return {
                child.func.id
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
    return set()


def test_the_websocket_resolver_calls_the_password_change_check() -> None:
    """`api/v1/ws.py` re-implements `_load_current_user` and does not inherit.

    The duplication is deliberate: a WebSocket scope carries no Request for the
    dependency to take. The cost is that a check added to the original is not
    added here, and one was not -- a stolen token kept streaming scan progress
    and log lines after the victim reset their password, while the same token
    was refused on every HTTP route.

    The first version of this assertion looked for the function's name anywhere
    in the file, which a comment mentioning it would have satisfied just as
    well. Reading the tree asks the question that was meant: is the check called
    from inside the resolver.
    """
    root = _backend_root()
    ws = root / "api" / "v1" / "ws.py"
    assert ws.is_file(), f"{ws} not found; this guard is pointing at nothing"

    tree = ast.parse(ws.read_text(encoding="utf-8"))
    calls = _calls_within(tree, "_resolve_user")

    assert calls, "_resolve_user not found in api/v1/ws.py; the resolver was renamed"
    assert "password_change_invalidates" in calls, (
        "the WebSocket resolver does not call the password-change check, so a "
        "token refused on every HTTP route still opens a socket"
    )
