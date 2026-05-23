"""JWT decode security edges (Tier L — auth lifecycle).

``decode_token`` is the single chokepoint every authenticated request passes
through, yet its rejection paths (expired / tampered / wrong-secret / wrong-type
/ alg-confusion) were untested. These are the classic JWT footguns (CWE-347 /
CWE-345 / alg=none). RBAC role separation is already covered by test_rbac.py /
test_authz_admin.py / test_role_separation.py; this fills the token-validation
gap underneath them.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from core.config import secret_key
from core.security import (
    JWT_ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_token,
)


def _encode(claims: dict) -> str:
    return str(jwt.encode(claims, secret_key(), algorithm=JWT_ALGORITHM))


def _base(**over) -> dict:
    now = datetime.now(tz=UTC)
    claims = {
        "sub": "user-1",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    claims.update(over)
    return claims


def test_valid_access_token_decodes() -> None:
    token = create_access_token(subject="user-1", role="developer")
    claims = decode_token(token, expected_type="access")
    assert claims["sub"] == "user-1"
    assert claims["type"] == "access"


def test_expired_token_rejected() -> None:
    past = datetime.now(tz=UTC) - timedelta(minutes=1)
    token = _encode(_base(exp=int(past.timestamp())))
    with pytest.raises(Exception):  # jose ExpiredSignatureError ⊂ JWTError
        decode_token(token, expected_type="access")


def test_tampered_signature_rejected() -> None:
    token = create_access_token(subject="user-1")
    # Flip the last char of the signature segment.
    head, payload, sig = token.split(".")
    bad_sig = sig[:-1] + ("a" if sig[-1] != "a" else "b")
    with pytest.raises(Exception):
        decode_token(f"{head}.{payload}.{bad_sig}", expected_type="access")


def test_wrong_secret_rejected() -> None:
    token = jwt.encode(_base(), "a-different-secret-key-not-ours-123456", algorithm=JWT_ALGORITHM)
    with pytest.raises(Exception):
        decode_token(token, expected_type="access")


def test_access_token_rejected_as_refresh_and_vice_versa() -> None:
    access = create_access_token(subject="user-1")
    refresh, _jti, _exp = create_refresh_token(subject="user-1")
    with pytest.raises(Exception):
        decode_token(access, expected_type="refresh")
    with pytest.raises(Exception):
        decode_token(refresh, expected_type="access")


def test_alg_none_token_rejected() -> None:
    # Hand-craft an unsigned alg=none token; decode pins algorithms=[HS*], so a
    # downgrade to "none" must be refused (the classic alg-confusion bypass).
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(_base())}."
    with pytest.raises(Exception):
        decode_token(forged, expected_type="access")


def test_garbage_token_rejected() -> None:
    with pytest.raises(Exception):
        decode_token("not.a.jwt", expected_type="access")
