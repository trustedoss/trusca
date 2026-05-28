"""
Reversible secret encryption at rest — v2.2-b1 (GitHub App credential storage).

Unlike API-key hashing (bcrypt, one-way) the GitHub App flow MUST recover the
PEM private key to mint short-lived installation tokens — it is a *reversible*
secret, not a verifier. We therefore use authenticated symmetric encryption
(``cryptography.fernet.Fernet`` — AES-128-CBC + HMAC-SHA256, with a versioned
URL-safe-base64 token and a bundled timestamp) so a row read back from Postgres
can be decrypted, while a database-only compromise (no key) yields ciphertext.

Key resolution (CLAUDE.md core rule #11 — NO module-level env caching; every
accessor reads ``os.getenv`` at call time):

  1. ``GITHUB_APP_ENCRYPTION_KEY`` — a urlsafe-base64-encoded 32-byte Fernet
     key (i.e. ``Fernet.generate_key().decode()``). This is the production
     path: a dedicated, rotatable key independent of the JWT signing secret.
  2. If unset, DERIVE one deterministically from ``core.config.secret_key()``::
         base64.urlsafe_b64encode(sha256(secret_key().encode()).digest())
     so local dev / CI works without extra configuration. We emit a structured
     WARNING every time the derived key is used so a production deployment that
     forgot to set a dedicated key is loud about it (the derived key shares the
     blast radius of the JWT secret — rotating the JWT secret would orphan every
     stored credential, which is exactly what the WARNING tells operators).

Security contract:
  - ``encrypt_secret`` / ``decrypt_secret`` are the ONLY functions that touch
    plaintext credential material. They never log the plaintext or the key.
  - A decrypt failure (wrong/rotated key, corrupted ciphertext, tampered token)
    raises :class:`SecretDecryptionError` with NO plaintext / key bytes in the
    message — callers translate it into a clean operational error, never a 500
    that leaks internals.
  - The derived-key path is deterministic so a process restart can still decrypt
    rows it wrote before the restart; it is NOT a substitute for a managed key.
  - **Prod fail-closed.** When ``app_env() == "prod"`` and
    ``GITHUB_APP_ENCRYPTION_KEY`` is unset/blank, key resolution RAISES
    :class:`SecretEncryptionError` instead of deriving from ``SECRET_KEY`` — a
    forgotten dedicated key must not silently bind every stored credential to
    the JWT secret's blast radius. The derive-from-secret fallback is non-prod
    only.

  Follow-up (tracked): key ROTATION currently requires re-registering every
  credential (the new key cannot decrypt rows written under the old one).
  Rolling rotation via ``cryptography.fernet.MultiFernet`` (accept old keys for
  decrypt, encrypt with the newest) is a planned enhancement; until then,
  ``.env.example`` documents the re-registration requirement next to
  ``GITHUB_APP_ENCRYPTION_KEY``.
"""

from __future__ import annotations

import base64
import hashlib
import os

import structlog
from cryptography.fernet import Fernet, InvalidToken

log = structlog.get_logger("crypto")

_ENCRYPTION_KEY_ENV = "GITHUB_APP_ENCRYPTION_KEY"


class SecretEncryptionError(Exception):
    """Raised when a plaintext secret cannot be encrypted (misconfigured key)."""


class SecretDecryptionError(Exception):
    """Raised when a stored ciphertext cannot be decrypted.

    The message intentionally carries NO key or plaintext bytes — only an
    operator-actionable hint. The usual cause is a key rotation mismatch:
    the row was encrypted under a key the current process no longer has.
    """


def _derive_key_from_secret() -> bytes:
    """Deterministically derive a 32-byte urlsafe-base64 Fernet key.

    Derived from the JWT signing secret via SHA-256 so dev/CI need no extra
    config. Emits a WARNING on every call so a non-prod deployment lacking a
    dedicated ``GITHUB_APP_ENCRYPTION_KEY`` is loud about the shared blast
    radius (rotating ``SECRET_KEY`` would orphan all stored credentials).

    Prod fail-closed: if ``app_env() == "prod"`` this RAISES
    :class:`SecretEncryptionError` rather than deriving, so a forgotten
    dedicated key cannot silently bind every credential to the JWT secret.
    """
    # Local import keeps this module importable even in contexts where the
    # full config stack is not yet wired, and honours rule #11 (read at call).
    from core.config import app_env, secret_key

    if app_env() == "prod":
        raise SecretEncryptionError(
            "GITHUB_APP_ENCRYPTION_KEY is unset in production. Refusing to "
            "derive the credential encryption key from SECRET_KEY (that would "
            "bind every stored GitHub App credential to the JWT secret's blast "
            "radius). Set a dedicated, rotatable GITHUB_APP_ENCRYPTION_KEY."
        )

    digest = hashlib.sha256(secret_key().encode("utf-8")).digest()  # 32 bytes
    log.warning(
        "crypto.encryption_key_derived_from_secret_key",
        detail=(
            "GITHUB_APP_ENCRYPTION_KEY is unset; deriving the credential "
            "encryption key from SECRET_KEY. Set a dedicated, rotatable "
            "GITHUB_APP_ENCRYPTION_KEY in production — otherwise rotating "
            "SECRET_KEY will orphan every stored GitHub App credential."
        ),
    )
    return base64.urlsafe_b64encode(digest)


def _resolve_fernet() -> Fernet:
    """Build a Fernet from the resolved key at call time (rule #11).

    Raises :class:`SecretEncryptionError` if an explicitly-provided
    ``GITHUB_APP_ENCRYPTION_KEY`` is malformed (not a valid 32-byte urlsafe
    base64 key) — a misconfiguration we want to surface clearly rather than
    silently falling back to the derived key (which would make ciphertext
    written under the bad config undecryptable later).
    """
    raw = os.getenv(_ENCRYPTION_KEY_ENV)
    if raw is not None and raw.strip() != "":
        key_bytes = raw.strip().encode("utf-8")
        try:
            return Fernet(key_bytes)
        except (ValueError, TypeError) as exc:
            # Do NOT echo the key material into the error.
            raise SecretEncryptionError(
                f"{_ENCRYPTION_KEY_ENV} is set but is not a valid urlsafe-base64 "
                "32-byte Fernet key (generate one with "
                "Fernet.generate_key().decode())"
            ) from exc
    return Fernet(_derive_key_from_secret())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return a URL-safe Fernet token (str).

    The returned token is what gets persisted in the ``*_encrypted`` columns.
    Raises :class:`SecretEncryptionError` on a misconfigured key. Never logs
    the plaintext.
    """
    if not isinstance(plaintext, str):
        raise SecretEncryptionError("plaintext to encrypt must be a str")
    fernet = _resolve_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet ``token`` (as produced by :func:`encrypt_secret`).

    Raises :class:`SecretDecryptionError` on any failure — wrong/rotated key,
    corrupted or tampered ciphertext, or a non-string input. The message
    carries no key or plaintext bytes.
    """
    if not isinstance(token, str) or token == "":
        raise SecretDecryptionError("ciphertext token to decrypt must be a non-empty str")
    fernet = _resolve_fernet()
    try:
        plaintext = fernet.decrypt(token.encode("utf-8"))
    except InvalidToken as exc:
        # InvalidToken covers wrong key, tampered token, and corruption. We do
        # NOT include the token or any key bytes in the surfaced message.
        raise SecretDecryptionError(
            "stored secret could not be decrypted — the encryption key may have "
            "been rotated or the ciphertext is corrupted"
        ) from exc
    return plaintext.decode("utf-8")


__all__ = [
    "SecretDecryptionError",
    "SecretEncryptionError",
    "decrypt_secret",
    "encrypt_secret",
]
