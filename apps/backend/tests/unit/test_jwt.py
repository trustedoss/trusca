"""
Unit tests for JWT helpers (token mint/verify, expiration, claim shape).

These tests run without a database — they exercise core/security.py only.
"""

from __future__ import annotations

import time
import uuid

import pytest


@pytest.fixture(autouse=True)
def _stable_secret(monkeypatch):
    """Pin SECRET_KEY so tokens minted in one test verify in the next."""
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret-key-min-32-chars-XXXXXX")
    yield


def test_create_access_token_round_trips_subject():
    from core.security import create_access_token, decode_token

    user_id = str(uuid.uuid4())
    token = create_access_token(subject=user_id, role="developer")
    claims = decode_token(token, expected_type="access")

    assert claims["sub"] == user_id
    assert claims["role"] == "developer"
    assert claims["type"] == "access"
    assert isinstance(claims["exp"], int)


def test_access_token_expiry_matches_env(monkeypatch):
    from core.security import create_access_token, decode_token

    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1")
    user_id = str(uuid.uuid4())
    before = int(time.time())
    token = create_access_token(subject=user_id, role="developer")
    claims = decode_token(token, expected_type="access")

    # Allow a small skew window (±5s)
    assert before + 60 - 5 <= claims["exp"] <= before + 60 + 5


def test_decode_rejects_token_signed_with_other_secret(monkeypatch):
    from core.security import create_access_token, decode_token

    token = create_access_token(subject="anyone", role="developer")
    monkeypatch.setenv("SECRET_KEY", "different-secret-key-min-32-chars-XXXXX")

    with pytest.raises(Exception):
        decode_token(token, expected_type="access")


def test_decode_rejects_wrong_token_type():
    """An access token must not validate as a refresh token (and vice versa)."""
    from core.security import create_access_token, decode_token

    token = create_access_token(subject="u", role="developer")

    with pytest.raises(Exception):
        decode_token(token, expected_type="refresh")


def test_password_hash_is_bcrypt_cost_12():
    from core.security import hash_password, verify_password

    password = "Sup3rS3cret!abcd"
    hashed = hash_password(password)

    assert hashed != password
    assert hashed.startswith("$2b$12$") or hashed.startswith("$2a$12$")
    assert verify_password(password, hashed) is True
    assert verify_password("wrong", hashed) is False
