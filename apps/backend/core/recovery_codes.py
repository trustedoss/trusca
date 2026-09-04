# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""One-time codes for somebody who has lost their authenticator.

The second factor is the point of failure this exists for: a phone is lost or
wiped far more often than a password is forgotten, and without a way back the
feature turns "I secured my account" into "I locked myself out". A password
reset cannot be that way back, because unlocking the second factor by email
would reduce the second factor to owning the mailbox, which is what the first
factor already proves.

So the way back is a set of codes issued once, shown once, and stored the way
passwords are: hashed, never recoverable, and struck off as they are used.

The generated form is deliberately awkward to mistype and easy to write on
paper, since that is where they end up.
"""

from __future__ import annotations

import secrets

#: How many are issued at a time. Ten is enough that losing a phone does not
#: immediately mean losing the account, and few enough that the whole set fits
#: on the card somebody prints.
CODE_COUNT = 10

#: Two groups of five from an alphabet with no 0/O or 1/I/L, so a code read off
#: paper is not ambiguous. Roughly 51 bits, which is far past guessing when the
#: sign-in throttle is counting failures against the address anyway.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_GROUP = 5


def generate_codes(count: int = CODE_COUNT) -> list[str]:
    """Fresh codes in their display form, e.g. ``ABCDE-FGHJK``."""
    return [
        f"{_random_group()}-{_random_group()}"
        for _ in range(count)
    ]


def _random_group() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP))


def normalise(candidate: str) -> str:
    """The comparable form of whatever somebody typed.

    People retype these from paper, so case and the separator vary and spaces
    creep in. Normalising here rather than at each call site means the form
    stored and the form compared cannot drift apart, which for a hashed value
    would show up as a correct code being rejected with no way to tell why.
    """
    return "".join(candidate.split()).replace("-", "").upper()


__all__ = ["CODE_COUNT", "generate_codes", "normalise"]
