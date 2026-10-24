"""JWT decode security edges (Tier L — auth lifecycle).

``decode_token`` is the single chokepoint every authenticated request passes
through, yet its rejection paths (expired / tampered / wrong-secret / wrong-type
/ alg-confusion) were untested. These are the classic JWT footguns (CWE-347 /
CWE-345 / alg=none). RBAC role separation is already covered by test_rbac.py /
test_authz_admin.py / test_role_separation.py; this fills the token-validation
gap underneath them.

Each rejection asserts the specific PyJWT error, not just "something raised":
a test that accepts any exception keeps passing when the check it names is
gone and the token fails later for an unrelated reason. Every error must also
be a :data:`core.security.TokenError`, the one type the callers catch.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core.config import secret_key
from core.security import (
    JWT_ALGORITHM,
    TokenError,
    create_access_token,
    create_mfa_pending_token,
    create_refresh_token,
    decode_token,
)


def _encode(claims: dict, *, key: str | None = None, algorithm: str = JWT_ALGORITHM) -> str:
    return jwt.encode(claims, key if key is not None else secret_key(), algorithm=algorithm)


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


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _rejects(token: str, error: type[Exception], *, expected_type: str = "access") -> Exception:
    with pytest.raises(error) as info:
        decode_token(token, expected_type=expected_type)
    assert isinstance(info.value, TokenError)
    return info.value


def test_valid_access_token_decodes() -> None:
    token = create_access_token(subject="user-1", role="developer")
    claims = decode_token(token, expected_type="access")
    assert claims["sub"] == "user-1"
    assert claims["type"] == "access"


def test_hand_built_token_decodes() -> None:
    """The positive control for every hand-built token below: the same
    claims, correctly signed, are accepted. Each rejection then differs from
    this in exactly the one property it names."""
    assert decode_token(_encode(_base()), expected_type="access")["sub"] == "user-1"


def test_expired_token_rejected() -> None:
    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    _rejects(_encode(_base(exp=int(past.timestamp()))), jwt.ExpiredSignatureError)


def test_missing_exp_rejected() -> None:
    claims = _base()
    del claims["exp"]
    err = _rejects(_encode(claims), jwt.MissingRequiredClaimError)
    assert err.claim == "exp"  # type: ignore[attr-defined]


def test_missing_iat_rejected() -> None:
    claims = _base()
    del claims["iat"]
    err = _rejects(_encode(claims), jwt.MissingRequiredClaimError)
    assert err.claim == "iat"  # type: ignore[attr-defined]


def test_iat_slightly_in_the_future_is_accepted() -> None:
    """Kept from the previous library: a fresh token minted by a replica whose
    clock runs a little ahead must not be refused here. PyJWT's own iat check
    would refuse it, which is why it is switched off."""
    ahead = int((datetime.now(tz=UTC) + timedelta(seconds=5)).timestamp())
    assert decode_token(_encode(_base(iat=ahead)), expected_type="access")["iat"] == ahead


@pytest.mark.parametrize(
    "iat", ["not-a-number", [1], "1.5", float("inf"), float("nan")],
    ids=["string", "list", "float-string", "infinity", "nan"],
)
def test_non_integer_iat_rejected(iat: object) -> None:
    err = _rejects(_encode(_base(iat=iat)), jwt.InvalidIssuedAtError)
    assert "integer" in str(err)


def test_future_nbf_rejected() -> None:
    """We never set `nbf`, but one we did not set must still be honoured:
    a token that says it is not valid yet is refused."""
    later = int((datetime.now(tz=UTC) + timedelta(minutes=5)).timestamp())
    _rejects(_encode(_base(nbf=later)), jwt.ImmatureSignatureError)


def test_tampered_signature_rejected() -> None:
    token = create_access_token(subject="user-1")
    # Flip the FIRST char of the signature segment, not the last. HS256
    # signatures are 32 bytes → 256 bits, base64url-encoded as 43 chars which
    # carry 258 bits; the last char's low 2 bits are padding and are silently
    # discarded on decode. Flipping the trailing char can therefore produce a
    # different string that decodes to the *same* 32-byte HMAC, leaving the
    # signature valid — a probabilistic flake that bit us in CI (run 26435784546).
    # The first char has no such ignored bits.
    head, payload, sig = token.split(".")
    bad_sig = ("a" if sig[0] != "a" else "b") + sig[1:]
    _rejects(f"{head}.{payload}.{bad_sig}", jwt.InvalidSignatureError)


def test_wrong_secret_rejected() -> None:
    token = _encode(_base(), key="a-different-secret-key-not-ours-123456")
    _rejects(token, jwt.InvalidSignatureError)


def test_access_token_rejected_as_refresh_and_vice_versa() -> None:
    access = create_access_token(subject="user-1")
    refresh, _jti, _exp = create_refresh_token(subject="user-1")
    err = _rejects(access, jwt.InvalidTokenError, expected_type="refresh")
    assert "unexpected token type" in str(err)
    err = _rejects(refresh, jwt.InvalidTokenError, expected_type="access")
    assert "unexpected token type" in str(err)


def test_mfa_pending_token_rejected_as_access() -> None:
    pending = create_mfa_pending_token(subject="user-1", token_type="mfa-pending")
    err = _rejects(pending, jwt.InvalidTokenError)
    assert "unexpected token type" in str(err)


def test_alg_none_token_rejected() -> None:
    # Hand-craft an unsigned alg=none token; decode pins algorithms=[HS256], so
    # a downgrade to "none" must be refused (the classic alg-confusion bypass).
    forged = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_base())}."
    _rejects(forged, jwt.InvalidAlgorithmError)


def test_alg_none_via_library_rejected() -> None:
    forged = jwt.encode(_base(), None, algorithm="none")
    _rejects(forged, jwt.InvalidAlgorithmError)


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_other_hmac_algorithm_with_our_secret_rejected(algorithm: str) -> None:
    """Signed with the real secret, so only the algorithm pin can refuse it."""
    _rejects(_encode(_base(), algorithm=algorithm), jwt.InvalidAlgorithmError)


def test_rs256_token_rejected() -> None:
    """An asymmetric header must not reach a verifier at all. If the header's
    algorithm were trusted, a verifier would have to treat our HMAC secret as
    an RSA key, which is the key-confusion family."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _rejects(_encode(_base(), key=pem, algorithm="RS256"), jwt.InvalidAlgorithmError)  # type: ignore[arg-type]


def test_hs256_header_signed_with_a_public_key_rejected() -> None:
    """The other half of key confusion: HS256 signed with a public key PEM
    as the HMAC secret. It does not carry our secret, so it fails signature
    verification; the PEM is never accepted as our key."""
    public_pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    signing_input = f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(_base())}"
    sig = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"
    _rejects(token, jwt.InvalidSignatureError)


def test_audience_bearing_token_rejected() -> None:
    """We never set or expect `aud`; a token carrying one is not ours."""
    _rejects(_encode(_base(aud="someone-else")), jwt.InvalidAudienceError)


def test_unusable_key_is_still_a_token_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyJWT refuses a PEM as an HMAC secret with ``InvalidKeyError``, which
    is not an ``InvalidTokenError``. Callers catch :data:`TokenError`, so it
    must be wide enough to include it, or a misconfigured key would surface
    as a 500 on every authenticated route instead of a 401."""
    token = _encode(_base())
    pem = (
        "-----BEGIN PUBLIC KEY-----\n"
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n"
        "-----END PUBLIC KEY-----\n"
    )
    monkeypatch.setattr("core.security.secret_key", lambda: pem)
    _rejects(token, jwt.InvalidKeyError)


def test_garbage_token_rejected() -> None:
    _rejects("not.a.jwt", jwt.DecodeError)
