"""H-8 guard — common-password blocklist (NIST 800-63B §5.1.1.2).

The length floor (8) is enforced by the schema ``min_length``; this module
covers the other half: rejecting commonly-used / predictable values. The
report's accepted-but-weak passwords (``password1234`` / ``Password1234`` /
``123456789012``) must now be rejected, while genuinely strong passwords —
including ones that merely *embed* a weak substring — pass.
"""

from __future__ import annotations

import pytest

from core.password_policy import is_weak_password

# Passwords the report saw accepted that must now be flagged.
_WEAK = [
    "password1234",
    "Password1234",
    "123456789012",
    "password",
    "qwerty",
    "12345678",
    "aaaaaaaa",
    "Welcome1",
    "admin123",
    "letmein",
]

# Strong enough to pass: long, mixed, not a common base — including one that
# embeds a weak word ("admin") as a non-dominant substring.
_STRONG = [
    "Abcdef12_x9",
    "Xq7!adminTr9$pLm2",
    "correct-horse-battery-staple-42",
    "S3cur3!Phrase_2026",
    "g8#Lm2$Qz!rT",
]


@pytest.mark.parametrize("pw", _WEAK)
def test_weak_passwords_are_rejected(pw: str) -> None:
    reason = is_weak_password(pw)
    assert reason is not None, f"expected {pw!r} to be flagged weak"
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("pw", _STRONG)
def test_strong_passwords_pass(pw: str) -> None:
    assert is_weak_password(pw) is None, f"expected {pw!r} to be accepted"


@pytest.mark.parametrize("pw", [None, "", "   "])
def test_blank_input_is_not_flagged_here(pw) -> None:
    # The min_length validator owns "too short / required"; the blocklist must
    # not double-report on blank input.
    assert is_weak_password(pw) is None


def test_register_schema_rejects_common_password() -> None:
    from pydantic import ValidationError

    from schemas.auth import RegisterRequest

    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password="password1234")
    # A strong password validates cleanly.
    ok = RegisterRequest(email="a@example.com", password="Xq7!adminTr9$pLm2")
    assert ok.password == "Xq7!adminTr9$pLm2"


def test_reset_schema_rejects_common_password() -> None:
    from pydantic import ValidationError

    from schemas.auth import ResetPasswordRequest

    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="x" * 16, new_password="Password1234")
    ok = ResetPasswordRequest(token="x" * 16, new_password="S3cur3!Phrase_2026")
    assert ok.new_password == "S3cur3!Phrase_2026"
