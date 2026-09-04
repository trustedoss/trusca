# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Time-based one-time passwords (RFC 6238), written out rather than imported.

The algorithm is HMAC over a counter derived from the clock, truncated to six
digits. It is short, completely specified, and the RFC publishes test vectors,
so a correct transcription can be proved rather than trusted. What a library
would not have answered is the part that actually decides whether this is worth
anything: where the secret is kept, what stops a code being replayed, and how
much clock drift is tolerated. Those live in the service above this module.

The parameters are fixed at SHA-1, six digits, thirty seconds. That is a
compatibility decision, not a security one, and it is the one thing here most
likely to be "improved" by somebody later: authenticator apps overwhelmingly
implement SHA-1 only, so raising the hash means the user's app produces codes
this server will never accept, with nothing to say why.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

#: RFC 4226 §5.3 truncates to a 31-bit integer and takes it modulo 10^digits.
DIGITS = 6

#: One code per half minute, the near-universal default.
PERIOD_SECONDS = 30

#: A 160-bit secret, which is the SHA-1 block the HMAC keys on and what every
#: authenticator app expects to receive as 32 base32 characters.
SECRET_BYTES = 20


def generate_secret() -> str:
    """A fresh base32 secret, unpadded, as authenticator apps expect it."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _counter_for(moment: float) -> int:
    return int(moment) // PERIOD_SECONDS


def code_at(secret: str, *, counter: int) -> str:
    """The six digits for one counter step.

    Kept separate from any notion of "now" so a caller can ask about the step
    before and after, and so the RFC's test vectors can be applied directly.
    """
    # Authenticator apps hand out unpadded base32; b32decode insists on it.
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)

    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # Dynamic truncation (RFC 4226 §5.3): the low nibble of the last byte picks
    # the offset, and the high bit of the selected word is masked off so the
    # result does not depend on the platform's signedness.
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFF_FFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def verify(
    secret: str, candidate: str, *, at: float | None = None, drift_steps: int = 1
) -> int | None:
    """Return the counter the code matched, or None.

    The counter is returned rather than a boolean because the caller has to
    remember it: a code stays valid for its whole step, so without recording
    which step was used, an attacker who observes one code can replay it until
    the step ends. Replay prevention is the caller's job and it needs this
    value to do it.

    ``drift_steps`` accepts the neighbouring steps, which is what makes this
    usable by a phone whose clock is a little off. One step either side is the
    common choice: it widens the guessing target from one code in a million to
    three, which is still far below what the sign-in throttle allows, and going
    wider trades that away for clocks that should be fixed instead.
    """
    if not candidate or not candidate.isdigit() or len(candidate) != DIGITS:
        return None

    now = time.time() if at is None else at
    centre = _counter_for(now)
    for step in range(-drift_steps, drift_steps + 1):
        counter = centre + step
        if counter < 0:
            continue
        # Constant time: a byte-by-byte comparison leaks how much of the code
        # was right through timing, and six digits is few enough that leaking
        # position by position is worth an attacker's while.
        if hmac.compare_digest(code_at(secret, counter=counter), candidate):
            return counter
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """The ``otpauth://`` URI an authenticator app reads from a QR code.

    Returned as a string rather than rendered: drawing the QR belongs to the
    page that shows it, and generating images here would add a dependency for
    something the browser already does.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD_SECONDS}"
    )


__all__ = [
    "DIGITS",
    "PERIOD_SECONDS",
    "code_at",
    "generate_secret",
    "provisioning_uri",
    "verify",
]
