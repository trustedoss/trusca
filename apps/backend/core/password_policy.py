"""Password strength policy — common-password / blocklist check (H-8).

CLAUDE.md §3 keeps the minimum length at 8 (NIST 800-63B floor) but also
requires the *other* half of 800-63B §5.1.1.2: reject passwords that appear on
a list of commonly-used, expected, or compromised values. The length check
alone let ``password1234`` / ``Password1234`` / ``123456789012`` through.

This is a pragmatic embedded blocklist — the passwords brute-force tools try
first — not the full Have-I-Been-Pwned corpus (which would add a network
dependency and a k-anonymity round-trip on every signup). It is intentionally
conservative: it reduces a candidate to its alphabetic core before matching a
common base word, so a genuinely strong password that merely *contains* a weak
substring (e.g. ``Xq7!adminTr9$pLm2``) is NOT rejected.
"""

from __future__ import annotations

# Exact lowercased values that must always be rejected. The classic top-of-the
# leak-list entries plus a couple of product-specific guesses.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "password1234",
        "passw0rd",
        "qwerty",
        "qwerty123",
        "qwertyuiop",
        "123456",
        "1234567",
        "12345678",
        "123456789",
        "1234567890",
        "111111",
        "000000",
        "iloveyou",
        "admin",
        "administrator",
        "welcome",
        "welcome1",
        "letmein",
        "monkey",
        "dragon",
        "master",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "abc123",
        "trustedoss",
        "changeme",
        "secret",
        "login",
    }
)

# Alphabetic "base words" — a candidate whose letters-only core equals one of
# these is rejected regardless of the digits/symbols around it. This is what
# catches ``password1234`` (core ``password``) and ``Welcome2026!`` (core
# ``welcome``) without flagging a strong password that only embeds the word.
_COMMON_BASES = frozenset(
    {
        "password",
        "passwd",
        "qwerty",
        "qwertyuiop",
        "admin",
        "administrator",
        "welcome",
        "letmein",
        "iloveyou",
        "monkey",
        "dragon",
        "master",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "trustedoss",
        "changeme",
        "secret",
        "login",
        "abc",
    }
)


def is_weak_password(password: str) -> str | None:
    """Return a human-readable rejection reason, or None when acceptable.

    Checks, in order: exact common-password match, all-numeric (NIST flags
    purely numeric secrets), single-repeated-character, and a letters-only core
    that is a well-known base word. Defensive on None/blank input — returns None
    so the surrounding ``min_length`` validator owns the "too short / required"
    message.
    """
    if not password:
        return None
    candidate = password.strip()
    if not candidate:
        return None

    lowered = candidate.lower()
    if lowered in _COMMON_PASSWORDS:
        return "this password is among the most commonly used — choose a less predictable one"

    if candidate.isdigit():
        return "an all-numeric password is too easy to guess — add letters and symbols"

    if len(set(candidate)) == 1:
        return "a password of a single repeated character is too easy to guess"

    alpha_core = "".join(c for c in lowered if c.isalpha())
    if alpha_core and alpha_core in _COMMON_BASES:
        return "this password is built on a common word — choose a less predictable one"

    return None


__all__ = ["is_weak_password"]
