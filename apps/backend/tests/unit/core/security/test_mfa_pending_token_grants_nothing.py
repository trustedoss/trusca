# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A token that proves one factor must not open anything.

This is the assertion the whole second-factor design rests on. If the thing
handed out after a correct password can be presented to an ordinary route, the
code screen is decoration: a client ignores it and carries on. Everything else
here could be right and the factor would still be optional.

The obvious implementation is the one that fails this. The OAuth callback
already sets a refresh cookie and redirects, so the natural way to add a second
factor is "set the cookie, then redirect to the code screen" -- at which point
the session exists and calling the refresh endpoint finishes the sign-in
without a code ever being entered.
"""

from __future__ import annotations

import pytest
from jose import JWTError

from core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_MFA_ENROLLING,
    TOKEN_TYPE_MFA_PENDING,
    TOKEN_TYPE_REFRESH,
    create_mfa_pending_token,
    decode_token,
)

_SUBJECT = "11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize(
    "pending_type", [TOKEN_TYPE_MFA_PENDING, TOKEN_TYPE_MFA_ENROLLING]
)
@pytest.mark.parametrize("read_as", [TOKEN_TYPE_ACCESS, TOKEN_TYPE_REFRESH])
def test_a_pending_token_is_refused_as_an_access_or_refresh_token(
    pending_type: str, read_as: str
) -> None:
    """Every combination, because one gap is the whole gap.

    Read as an access token it would authorise API calls; read as a refresh
    token it would be exchanged for one. Either is a complete sign-in without
    a second factor, so both are asserted for both pending kinds rather than
    one being taken as evidence for the rest.
    """
    token = create_mfa_pending_token(subject=_SUBJECT, token_type=pending_type)

    with pytest.raises(JWTError):
        decode_token(token, expected_type=read_as)


def test_the_two_pending_kinds_do_not_substitute_for_each_other() -> None:
    """Enrolling and signing in are different sentences.

    A token minted while somebody scans a QR code says "this person is signed
    in and setting up". A token minted after a password says "this person is
    not signed in yet". If the first were accepted where the second belongs,
    starting an enrolment would hand out a way past the factor being enrolled.
    """
    enrolling = create_mfa_pending_token(subject=_SUBJECT, token_type=TOKEN_TYPE_MFA_ENROLLING)
    signing_in = create_mfa_pending_token(subject=_SUBJECT, token_type=TOKEN_TYPE_MFA_PENDING)

    with pytest.raises(JWTError):
        decode_token(enrolling, expected_type=TOKEN_TYPE_MFA_PENDING)
    with pytest.raises(JWTError):
        decode_token(signing_in, expected_type=TOKEN_TYPE_MFA_ENROLLING)


def test_each_kind_still_reads_as_itself() -> None:
    """A refusal that refused everything would pass the tests above."""
    for kind in (TOKEN_TYPE_MFA_PENDING, TOKEN_TYPE_MFA_ENROLLING):
        token = create_mfa_pending_token(subject=_SUBJECT, token_type=kind)
        claims = decode_token(token, expected_type=kind)
        assert claims["sub"] == _SUBJECT


def test_it_expires_in_minutes_not_hours() -> None:
    """Long enough to read a code out of an app, short enough to go stale.

    A pending token names a user who has already proved a password, so one left
    in a browser history or a server log is worth something to whoever finds
    it. Asserted against the access token's own lifetime rather than a literal,
    so raising that does not silently raise this.
    """
    from core.config import access_token_expire_minutes
    from core.security import MFA_PENDING_EXPIRE_MINUTES

    assert MFA_PENDING_EXPIRE_MINUTES < access_token_expire_minutes()

    token = create_mfa_pending_token(subject=_SUBJECT, token_type=TOKEN_TYPE_MFA_PENDING)
    claims = decode_token(token, expected_type=TOKEN_TYPE_MFA_PENDING)
    lifetime_seconds = claims["exp"] - claims["iat"]

    assert lifetime_seconds == MFA_PENDING_EXPIRE_MINUTES * 60


def test_minting_refuses_a_type_that_is_not_a_pending_one() -> None:
    """The minter cannot be talked into producing a real credential.

    Without this, a caller passing TOKEN_TYPE_ACCESS gets a five-minute access
    token from a function whose whole purpose is to hand out no authority.
    """
    for kind in (TOKEN_TYPE_ACCESS, TOKEN_TYPE_REFRESH, "", "anything"):
        with pytest.raises(ValueError):
            create_mfa_pending_token(subject=_SUBJECT, token_type=kind)
