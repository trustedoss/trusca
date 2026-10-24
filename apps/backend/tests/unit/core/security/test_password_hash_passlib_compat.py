"""Password hashing after passlib (#574): old hashes, old answers.

``tests/fixtures/auth/passlib_bcrypt_hashes.json`` was written by the
passlib-based ``core.security`` on main before the switch: the hashes, and the
answer the old ``verify_password`` gave for each candidate password. Checking
the new code against that file compares it with the code it replaced, not with
itself, which is the only comparison that says stored hashes keep working.

The cases that matter most are the edges where passlib and the bcrypt package
differ on their own: inputs over 72 bytes (bcrypt 5 raises, passlib cut them),
NUL bytes (passlib refused them, bcrypt accepts them), and the ``$2x$`` prefix
(passlib refused it, bcrypt accepts it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bcrypt
import pytest

from core.security import hash_password, verify_password

_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "auth" / "passlib_bcrypt_hashes.json"


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data


_DATA = _load()
_HASHES = {h["name"]: h for h in _DATA["hashes"]}

_VERIFY_CASES = [
    pytest.param(h["hash"], candidate, expected, id=f"{h['name']}-{i}")
    for h in _DATA["hashes"]
    for i, (candidate, expected) in enumerate(h["verify"])
]


def test_fixture_was_recorded_by_passlib() -> None:
    """Guard the premise: the file is the old code's output, at cost 12."""
    assert _DATA["_generated_with"]["passlib"] == "1.7.4"
    assert _HASHES["ascii_2b"]["hash"].startswith("$2b$12$")
    # Every edge the module docstring names is present, so a trimmed fixture
    # cannot quietly drop the case that would have failed.
    assert {"ident_2a", "ident_2y", "over_72_bytes_ascii", "over_72_bytes_multibyte"} <= set(
        _HASHES
    )
    assert any(expected for _c, expected in _HASHES["over_72_bytes_ascii"]["verify"])
    assert any(not expected for _c, expected in _HASHES["ascii_2b"]["verify"])


@pytest.mark.parametrize(("stored", "candidate", "expected"), _VERIFY_CASES)
def test_verify_matches_the_passlib_answer(stored: str, candidate: str, expected: bool) -> None:
    assert verify_password(candidate, stored) is expected


@pytest.mark.parametrize(
    ("stored", "expected"), [tuple(x) for x in _DATA["unusable_stored_values"]]
)
def test_unusable_stored_value_verifies_false_without_raising(stored: str, expected: bool) -> None:
    assert expected is False  # premise: the old code refused every one
    assert verify_password(_HASHES["ascii_2b"]["password"], stored) is False


def test_2x_prefix_is_refused_although_bcrypt_accepts_it() -> None:
    """The allow-list is what refuses ``$2x$``, not bcrypt.

    Proves the premise first: bcrypt itself says the ``$2x$`` spelling of a
    valid hash matches. Without that, the assertion below would pass for the
    wrong reason.
    """
    good = _HASHES["ascii_2b"]
    relabelled = "$2x$" + good["hash"][4:]
    assert bcrypt.checkpw(good["password"].encode(), relabelled.encode()) is True
    assert verify_password(good["password"], relabelled) is False


@pytest.mark.parametrize(
    ("password", "refused"), [tuple(x) for x in _DATA["hash_password_refusals"]]
)
def test_hash_password_refuses_what_passlib_refused(password: str, refused: str | None) -> None:
    if refused is None:
        assert verify_password(password, hash_password(password)) is True
    else:
        with pytest.raises(ValueError):
            hash_password(password)


def test_nul_byte_is_refused_although_bcrypt_accepts_it() -> None:
    """Premise first: bcrypt hashes a NUL without complaint, so the refusal
    (and the False from verify) come from our own check."""
    assert bcrypt.hashpw(b"ab\x00cd", bcrypt.gensalt(rounds=4)).startswith(b"$2b$04$")
    with pytest.raises(ValueError, match="NUL"):
        hash_password("ab\x00cd")


def test_long_password_is_cut_not_refused() -> None:
    """bcrypt 5 raises for more than 72 bytes; hashing and verifying a longer
    password must still work and agree on the first 72 bytes only."""
    with pytest.raises(ValueError):
        bcrypt.hashpw(b"a" * 73, bcrypt.gensalt(rounds=4))
    long_pw = "가" * 40  # 120 bytes
    hashed = hash_password(long_pw)
    assert verify_password(long_pw, hashed) is True
    assert verify_password("가" * 24, hashed) is True  # first 72 bytes
    assert verify_password("가" * 23, hashed) is False


def test_new_hashes_are_2b_cost_12() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$2b$12$")
    assert len(hashed) == 60


def test_non_str_inputs_verify_false() -> None:
    stored = _HASHES["ascii_2b"]["hash"]
    assert verify_password(stored, None) is False  # type: ignore[arg-type]
    assert verify_password(b"correct horse battery staple", stored) is False  # type: ignore[arg-type]
