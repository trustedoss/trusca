# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Changing a password ends the sessions that were already open.

It did not. Access tokens are verified by signature, expiry and a lookup of the
subject; nothing in that path consulted the password, so a token minted before
a change kept working until it expired on its own. Refresh tokens were already
revoked on a reset, which stopped renewal but not the token the holder had.

Changing a password is what somebody does when they believe a credential has
leaked. Thirty minutes of continued access is the wrong answer to that.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.security import password_change_invalidates, set_password


class _User:
    """Stands in for the ORM row: `set_password` writes two attributes."""

    def __init__(self) -> None:
        self.hashed_password = "old-hash"
        self.password_changed_at: datetime | None = None


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def test_a_token_issued_before_the_change_is_refused() -> None:
    changed = datetime.now(UTC)
    issued = int((changed - timedelta(minutes=5)).timestamp())

    assert password_change_invalidates(issued, changed) is True


def test_a_token_issued_after_the_change_is_kept() -> None:
    changed = datetime.now(UTC)
    issued = int((changed + timedelta(seconds=1)).timestamp())

    assert password_change_invalidates(issued, changed) is False


def test_a_token_issued_in_the_same_second_survives() -> None:
    """The rounding direction, asserted because either choice is defensible.

    `iat` is whole seconds and the column keeps microseconds. Rounding the
    change time UP would refuse the token issued to the person who just
    changed their password, logging them out at the moment they were told it
    worked. Rounding down leaves a one-second window instead, which an attacker
    cannot use without the password they no longer have.
    """
    changed = datetime(2026, 9, 3, 12, 0, 0, 500_000, tzinfo=UTC)
    same_second = int(datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC).timestamp())

    assert password_change_invalidates(same_second, changed) is False


def test_the_second_before_the_change_is_refused() -> None:
    """The other half of the boundary: one second earlier must die."""
    changed = datetime(2026, 9, 3, 12, 0, 0, 500_000, tzinfo=UTC)
    one_second_earlier = int(datetime(2026, 9, 3, 11, 59, 59, tzinfo=UTC).timestamp())

    assert password_change_invalidates(one_second_earlier, changed) is True


@pytest.mark.parametrize(
    ("issued", "changed", "why"),
    [
        (None, datetime.now(UTC), "a token with no iat"),
        (int(datetime.now(UTC).timestamp()), None, "a user who never changed it"),
        (None, None, "neither"),
    ],
)
def test_missing_information_never_invalidates(
    issued: int | None, changed: datetime | None, why: str
) -> None:
    """NULL is not a licence to log people out.

    Existing rows carry NULL because migration 0077 deliberately did not
    backfill: stamping the deployment time would have ended every session on a
    running deployment at upgrade.
    """
    assert password_change_invalidates(issued, changed) is False, why


# ---------------------------------------------------------------------------
# The choke point
# ---------------------------------------------------------------------------


def test_setting_a_password_stamps_the_time() -> None:
    user = _User()
    before = datetime.now(UTC)

    set_password(user, "a new passphrase for the account")

    assert user.hashed_password != "old-hash"
    assert user.password_changed_at is not None
    assert user.password_changed_at >= before


def test_a_token_minted_before_that_call_is_then_refused() -> None:
    """End to end through the two pieces, which is the behaviour that matters."""
    issued = int((datetime.now(UTC) - timedelta(seconds=30)).timestamp())
    user = _User()

    assert password_change_invalidates(issued, user.password_changed_at) is False
    set_password(user, "a new passphrase for the account")
    assert password_change_invalidates(issued, user.password_changed_at) is True


# ---------------------------------------------------------------------------
# Nothing rotates a hash behind the choke point's back
# ---------------------------------------------------------------------------


def _backend_root() -> Path:
    """The backend package root, not the tests tree that mirrors its layout.

    `tests/unit/core/security/` has both a `core` and a `services` sibling, so
    a walk that only checks for those directories stops inside `tests` and
    reports the fixtures in this very file as production code. Requiring
    `main.py` and `alembic.ini` names the real root.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "main.py").is_file() and (candidate / "alembic.ini").is_file():
            return candidate
    pytest.skip("backend root not found above this file")
    raise AssertionError("unreachable")


def test_no_production_code_assigns_hashed_password_outside_the_choke_point() -> None:
    """A path that rotates the hash without the timestamp is worse than none.

    Somebody who changes their password there is told the action worked and
    believes their other sessions ended. They have not. This walks the
    production tree rather than trusting that the one path found today is the
    only one: `sync_session_scope` looked like one missing commit and was
    eight, and `.env.example` looked like one published credential and was
    two.

    Creating a user is exempt: there is no prior session to end, and the
    column stays NULL until the first change.
    """
    root = _backend_root()
    assignment = re.compile(r"\.hashed_password\s*=|hashed_password\s*=\s*hash_password")
    creation = re.compile(
        r"hashed_password\s*=\s*\(?\s*(?:await\s+)?(?:run_in_threadpool|hash_password|_unusable_password|_NO_PASSWORD|hashed_pw|hashed)\b"
    )

    offenders: list[str] = []
    for directory in ("services", "api", "core", "tasks"):
        for path in (root / directory).rglob("*.py"):
            if any(part == "tests" for part in path.parts):
                continue
            # The choke point itself is where the assignment belongs.
            if path == root / "core" / "security.py":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not assignment.search(line):
                    continue
                # `User(hashed_password=...)` is construction, not rotation.
                if creation.search(line) and ".hashed_password" not in line:
                    continue
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")

    assert not offenders, (
        "these rotate a password hash without going through `set_password`, so "
        "the sessions they were meant to end stay alive:\n" + "\n".join(offenders)
    )
