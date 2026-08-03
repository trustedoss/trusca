"""
Unit tests for ``core.crypto`` — v2.2-b1 reversible secret encryption.

Pure (no DB). Covers:
  - encrypt → decrypt round-trip (env-key path AND derived-from-secret path).
  - ciphertext ≠ plaintext.
  - wrong / rotated key fails cleanly with SecretDecryptionError (no leak).
  - malformed GITHUB_APP_ENCRYPTION_KEY surfaces SecretEncryptionError.
  - the derived-key path emits a structured WARNING (operator hint).
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

PEM_SAMPLE = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "-----END RSA PRIVATE KEY-----\n"
)


def _set_env_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", key)
    return key


# ---------------------------------------------------------------------------
# Round-trip — env key path
# ---------------------------------------------------------------------------


def test_round_trip_with_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import decrypt_secret, encrypt_secret

    _set_env_key(monkeypatch)
    token = encrypt_secret(PEM_SAMPLE)
    assert decrypt_secret(token) == PEM_SAMPLE


def test_ciphertext_differs_from_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import encrypt_secret

    _set_env_key(monkeypatch)
    token = encrypt_secret(PEM_SAMPLE)
    assert token != PEM_SAMPLE
    assert "BEGIN RSA PRIVATE KEY" not in token


def test_two_encrypts_yield_distinct_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fernet embeds a random IV + timestamp, so two encrypts differ."""
    from core.crypto import decrypt_secret, encrypt_secret

    _set_env_key(monkeypatch)
    a = encrypt_secret(PEM_SAMPLE)
    b = encrypt_secret(PEM_SAMPLE)
    assert a != b
    assert decrypt_secret(a) == decrypt_secret(b) == PEM_SAMPLE


# ---------------------------------------------------------------------------
# Round-trip — derived-from-secret path (no env key)
# ---------------------------------------------------------------------------


def test_round_trip_with_derived_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import decrypt_secret, encrypt_secret

    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    token = encrypt_secret(PEM_SAMPLE)
    assert decrypt_secret(token) == PEM_SAMPLE


def test_derived_key_emits_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The derived path must warn so a prod deployment notices the missing key."""
    import core.crypto as crypto_mod

    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "y" * 40)

    events: list[str] = []

    def _capture(event: str, **_kw: object) -> None:
        events.append(event)

    monkeypatch.setattr(crypto_mod.log, "warning", _capture)
    crypto_mod.encrypt_secret(PEM_SAMPLE)
    assert "crypto.encryption_key_derived_from_secret_key" in events


def test_empty_env_key_falls_back_to_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty GITHUB_APP_ENCRYPTION_KEY is treated as unset (derived path)."""
    from core.crypto import decrypt_secret, encrypt_secret

    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", "   ")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "z" * 40)
    token = encrypt_secret(PEM_SAMPLE)
    assert decrypt_secret(token) == PEM_SAMPLE


# ---------------------------------------------------------------------------
# Wrong / rotated key
# ---------------------------------------------------------------------------


def test_wrong_key_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret

    _set_env_key(monkeypatch)
    token = encrypt_secret(PEM_SAMPLE)

    # Rotate the key under the same process — decrypt must fail cleanly.
    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(SecretDecryptionError) as exc:
        decrypt_secret(token)
    # The error must NOT leak key/plaintext bytes.
    msg = str(exc.value)
    assert PEM_SAMPLE not in msg
    assert "BEGIN" not in msg


def test_derived_key_changes_when_secret_rotates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotating SECRET_KEY orphans rows encrypted under the derived key."""
    from core.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret

    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "a" * 40)
    token = encrypt_secret(PEM_SAMPLE)

    monkeypatch.setenv("SECRET_KEY", "b" * 40)
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(token)


# ---------------------------------------------------------------------------
# Malformed inputs
# ---------------------------------------------------------------------------


def test_malformed_env_key_raises_encryption_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.crypto import SecretEncryptionError, encrypt_secret

    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(SecretEncryptionError) as exc:
        encrypt_secret(PEM_SAMPLE)
    # The bad key must not appear in the surfaced message.
    assert "not-a-valid-fernet-key" not in str(exc.value)


def test_decrypt_empty_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import SecretDecryptionError, decrypt_secret

    _set_env_key(monkeypatch)
    with pytest.raises(SecretDecryptionError):
        decrypt_secret("")


def test_decrypt_garbage_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import SecretDecryptionError, decrypt_secret

    _set_env_key(monkeypatch)
    with pytest.raises(SecretDecryptionError):
        decrypt_secret("this-is-not-a-fernet-token")


def test_encrypt_non_string_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto import SecretEncryptionError, encrypt_secret

    _set_env_key(monkeypatch)
    with pytest.raises(SecretEncryptionError):
        encrypt_secret(b"bytes-not-str")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Prod fail-closed on missing encryption key (Low #1 follow-up)
# ---------------------------------------------------------------------------


def test_prod_missing_key_fails_closed_on_encrypt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prod + no GITHUB_APP_ENCRYPTION_KEY must REFUSE to derive from SECRET_KEY."""
    from core.crypto import SecretEncryptionError, encrypt_secret

    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "p" * 40)
    with pytest.raises(SecretEncryptionError):
        encrypt_secret(PEM_SAMPLE)


def test_prod_blank_key_fails_closed_on_decrypt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank key in prod is treated as unset → fail closed (no derive)."""
    from core.crypto import SecretEncryptionError, decrypt_secret

    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", "   ")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "q" * 40)
    with pytest.raises(SecretEncryptionError):
        # Any non-empty token reaches the key-resolution step, which fails closed.
        decrypt_secret("gAAAAA-not-a-real-token-but-non-empty")


def test_prod_with_explicit_key_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prod fail-closed only applies to the DERIVE path; an explicit key works."""
    from core.crypto import decrypt_secret, encrypt_secret

    monkeypatch.setenv("APP_ENV", "prod")
    _set_env_key(monkeypatch)
    token = encrypt_secret(PEM_SAMPLE)
    assert decrypt_secret(token) == PEM_SAMPLE


def test_non_prod_missing_key_still_derives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-prod keeps the derive-from-secret fallback (existing behavior)."""
    from core.crypto import decrypt_secret, encrypt_secret

    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SECRET_KEY", "r" * 40)
    token = encrypt_secret(PEM_SAMPLE)
    assert decrypt_secret(token) == PEM_SAMPLE
