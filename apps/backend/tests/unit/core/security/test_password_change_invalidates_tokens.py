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
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.security import password_change_invalidates, set_password


class _User:
    """Stands in for the ORM row.

    Carries an ``id`` because `set_password` also drops the cached principal
    for that user: the permission cache is keyed by user, so an entry warmed
    before the change would otherwise keep serving the tokens the change is
    meant to refuse.
    """

    def __init__(self) -> None:
        self.id = uuid.uuid4()
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


def test_a_token_issued_in_the_same_second_is_refused() -> None:
    """The rounding direction, and it is not a coin flip.

    `iat` is whole seconds and the column keeps microseconds, so one of them
    has to give. Keeping the same-second token was the first choice, to avoid
    logging out whoever had just changed their password. Nobody is in that
    position: `/auth/reset-password` answers 204 with no tokens and sends the
    user back to sign in.

    The window was reachable on purpose, though. `/auth/refresh` mints a token
    with `iat = now` on every call, so an attacker holding a stolen refresh
    cookie and polling it lands one inside the change's own second most of the
    time, and it then outlives the reset for its full lifetime.
    """
    changed = datetime(2026, 9, 3, 12, 0, 0, 500_000, tzinfo=UTC)
    same_second = int(datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC).timestamp())

    assert password_change_invalidates(same_second, changed) is True


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


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """A naive value would otherwise be read in the process timezone.

    East of UTC that yields a smaller epoch, which skips refusals: the
    fail-open direction. asyncpg returns aware values for timestamptz, so this
    is defence in depth rather than an observed failure, and it matches how
    `auth_service` handles refresh-token expiry.
    """
    aware = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)
    earlier = int((aware - timedelta(minutes=1)).timestamp())

    assert password_change_invalidates(earlier, naive) is True
    assert password_change_invalidates(earlier, aware) is True


def test_a_token_without_iat_is_rejected_before_this_check() -> None:
    """`decode_token` requires `iat`, so the None branch is unreachable in prod.

    Asserted because the None branch fails open: if the requirement were ever
    dropped, a token with the claim stripped would sail past the password
    check rather than being refused.
    """
    import pytest as _pytest
    from jose import JWTError, jwt

    from core.config import secret_key
    from core.security import JWT_ALGORITHM, TOKEN_TYPE_ACCESS, decode_token

    no_iat = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "type": TOKEN_TYPE_ACCESS},
        secret_key(),
        algorithm=JWT_ALGORITHM,
    )

    with _pytest.raises(JWTError):
        decode_token(no_iat, expected_type=TOKEN_TYPE_ACCESS)


# ---------------------------------------------------------------------------
# The WebSocket path resolves users itself, so it needs its own check
# ---------------------------------------------------------------------------


def test_the_websocket_resolver_consults_the_password_change() -> None:
    """`api/v1/ws.py` re-implements `_load_current_user` and does not inherit.

    The duplication is deliberate (a WebSocket scope has no Request for the
    dependency to take), and the cost is that a check added to the original is
    not added here. It was not: a stolen token kept streaming scan progress and
    log lines after the victim reset their password, while the same token was
    correctly refused on every HTTP route.

    Asserted structurally rather than by driving a socket, because what breaks
    is somebody adding a third resolver, and the shape is what says whether
    this one still consults the column.
    """
    for candidate in Path(__file__).resolve().parents:
        ws = candidate / "api" / "v1" / "ws.py"
        if ws.is_file():
            break
    else:
        pytest.skip("api/v1/ws.py not found above this file")

    source = ws.read_text(encoding="utf-8")

    assert "password_change_invalidates" in source, (
        "the WebSocket resolver does not consult the password-change check, so "
        "a token refused on HTTP routes still opens a socket"
    )
    assert 'issued_at=claims.get("iat")' in source, (
        "the WebSocket call site does not pass the token's iat, so the check "
        "it now imports has nothing to judge and silently permits everything"
    )
