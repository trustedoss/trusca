# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Every path that creates a session takes the per-user lock first.

A password reset revokes the refresh tokens that exist when its sweep runs. A
path that inserts a new one without taking the same lock can slip a row in
behind that sweep, and the reset stops meaning what it says. That is not a
property of any one function, it is a property of the set of them, so the thing
worth testing is the set: whoever adds the next producer has to join the
protocol or hear about it here.

Read with the AST rather than by matching text. The equivalent string check on
the password-hash choke point turned out to be walkable around with
``update().values()`` or ``setattr``, and a guard that can be stepped over is
worse than none: it reports safety that was never checked.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]

# The helpers in services.auth_service that take the per-user lock. Either is
# acceptable: one also re-checks the credential, which a path holding a password
# needs and a path holding a refresh token does not.
LOCK_HELPERS = frozenset({"lock_user_for_session_write", "_lock_user_row"})

# Directories that serve requests. A file outside these can still be wrong, but
# it is not on the path an attacker drives.
PRODUCTION_DIRS = ("api", "services", "tasks", "core", "integrations", "notifications")

# Exempt, with the reason recorded rather than left to be guessed at.
EXEMPT: dict[str, str] = {
    # A seeding script for the end-to-end environment. It runs offline against a
    # database nobody is resetting a password on, and it is not reachable from
    # any request.
    "scripts/seed_e2e_user.py": "offline seeding script, not a request path",
}


def _production_files() -> list[Path]:
    files: list[Path] = []
    for directory in PRODUCTION_DIRS:
        root = BACKEND_ROOT / directory
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if "/tests/" not in str(p))
    scripts = BACKEND_ROOT / "scripts"
    if scripts.is_dir():
        files.extend(scripts.rglob("*.py"))
    return sorted(files)


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _producers() -> list[tuple[str, str, bool]]:
    """Return (relative path, enclosing function, whether it takes the lock)."""
    found: list[tuple[str, str, bool]] = []
    for path in _production_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is its own bug
            continue

        # Map each function to the calls it makes, then ask which of them
        # construct a RefreshToken.
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            calls = _called_names(node)
            if "RefreshToken" not in calls:
                continue
            relative = str(path.relative_to(BACKEND_ROOT))
            found.append((relative, node.name, bool(calls & LOCK_HELPERS)))
    return found


def test_the_guard_can_see_the_producers_it_is_guarding() -> None:
    """A guard that finds nothing passes for the wrong reason.

    The paths and directory names below are the kind of thing a refactor moves.
    If this list ever comes back empty the file above still reports green while
    checking nothing, so the count is asserted before the property is.
    """
    producers = _producers()
    assert producers, (
        "no refresh-token producer was found at all; the guard is looking in the "
        f"wrong place (searched {PRODUCTION_DIRS} under {BACKEND_ROOT})"
    )
    paths = {path for path, _fn, _ok in producers}
    assert "services/auth_service.py" in paths, f"expected the login path, saw {sorted(paths)}"


def test_every_refresh_row_producer_takes_the_per_user_lock() -> None:
    unguarded = [
        f"{path}::{fn}"
        for path, fn, takes_lock in _producers()
        if not takes_lock and path not in EXEMPT
    ]
    assert not unguarded, (
        "these create a refresh token without taking the per-user session-write "
        "lock, so a password reset committing alongside them can miss the row "
        "they insert: " + ", ".join(unguarded) + ". Call "
        "services.auth_service.lock_user_for_session_write before inserting, or "
        "record an exemption with its reason in EXEMPT."
    )


def test_the_exemptions_still_exist() -> None:
    """An exemption for a file that has moved is a hole nobody can see."""
    missing = [name for name in EXEMPT if not (BACKEND_ROOT / name).exists()]
    assert not missing, f"exempt files no longer present, drop them from EXEMPT: {missing}"


def test_the_stored_credential_column_is_not_nullable() -> None:
    """The credential re-check assumes a value is always there.

    ``lock_user_for_session_write`` decides by comparing the stored hash before
    and after the lock. If the column were nullable, a user with no hash would
    compare ``None`` against ``None`` and read as unchanged, which is the answer
    that lets the session through. The code refuses that case explicitly rather
    than relying on this, but the column is why the case is unreachable, so the
    assumption is written down here instead of living in a comment.

    An OAuth signup does not leave it empty either: it stores the bcrypt of a
    random string, which is what makes "the hash changed" a usable signal for
    accounts that never had a password typed into them.
    """
    from models import User

    column = User.__table__.c.hashed_password
    assert not column.nullable, (
        "hashed_password became nullable; lock_user_for_session_write compares "
        "stored hashes to decide whether a password changed mid-request, and two "
        "NULLs compare equal, which reads as 'nothing changed'"
    )
