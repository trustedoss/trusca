# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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

  Rotation: ``GITHUB_APP_ENCRYPTION_KEY`` takes a comma-separated list. The
  first key encrypts, every key can decrypt. Put the new key first, run
  ``scripts/reencrypt_secrets.py`` until nothing is left on an older key, then
  drop the last entry. Removing it first leaves rows nothing can open, so the
  count of rows still on an older key is reported by the command and at boot.

  The old key has to outlive the backups taken before the rotation. Restoring
  one brings back ciphertext written under it, and a key that is no longer in
  the list cannot read it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

import structlog
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

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


def configured_keys() -> list[bytes]:
    """The encryption keys this process holds, newest first.

    ``GITHUB_APP_ENCRYPTION_KEY`` takes a comma-separated list. The first
    entry encrypts; every entry can decrypt. A deployment that has one key
    has a value with no comma in it and reaches exactly the same code path it
    did before the list was allowed, which is why the variable was widened
    rather than replaced.

    Order is the whole contract. Rotation means putting the new key first,
    re-encrypting, then removing the last one. Removing it before the
    re-encryption finishes leaves rows nothing can open, and that is not
    recoverable, which is why ``stale_row_count`` exists.

    Blank entries are dropped so a trailing comma, or the spaces somebody
    leaves after one, does not become a key that fails to parse.
    """
    raw = os.getenv(_ENCRYPTION_KEY_ENV)
    if raw is None or raw.strip() == "":
        return [_derive_key_from_secret()]

    keys: list[bytes] = []
    for index, entry in enumerate(raw.split(",")):
        candidate = entry.strip()
        if candidate == "":
            continue
        key_bytes = candidate.encode("utf-8")
        try:
            Fernet(key_bytes)
        except (ValueError, TypeError) as exc:
            # Position, never the value. An operator with four keys needs to
            # know which one to look at, and the bytes must not reach a log.
            raise SecretEncryptionError(
                f"{_ENCRYPTION_KEY_ENV} entry {index + 1} is not a valid "
                "urlsafe-base64 32-byte Fernet key (generate one with "
                "Fernet.generate_key().decode())"
            ) from exc
        keys.append(key_bytes)

    if not keys:
        # Every entry was blank, which is a value like "," or "  ". Treating
        # that as "unset" would silently fall back to the derived key and
        # write ciphertext nothing later expects.
        raise SecretEncryptionError(
            f"{_ENCRYPTION_KEY_ENV} is set but contains no keys"
        )
    return keys


def _resolve_fernet() -> MultiFernet:
    """The cipher for the shared key, at call time (rule #11).

    A :class:`MultiFernet` even when there is one key, so the single-key and
    rotating cases run the same code. Encryption uses the first key; decrypt
    tries each in order.
    """
    return MultiFernet([Fernet(k) for k in configured_keys()])


def _fernet_key_material() -> bytes:
    """The key new ciphertext is written under.

    Named separately from the list because purpose subkeys derive from this
    one alone: a subkey derived from an old master would encrypt new rows
    under a key the rotation is trying to retire.
    """
    return configured_keys()[0]


def purpose_cipher(purpose: str, key: bytes) -> Fernet:
    """The cipher for one purpose under one master key.

    Secrets kept for different reasons should not open with the same key, so
    a purpose derives a subkey rather than reusing the master. Deterministic,
    so the same master and purpose always give the same subkey, which is what
    lets rotation build a list of "this purpose under each master".

    Kept separate from :func:`purpose_multi` because rotation needs a single
    master's subkey (to ask whether a row is on the newest key) as well as
    the whole list (to open a row written under any of them).
    """
    subkey = hmac.new(
        base64.urlsafe_b64decode(key),
        f"trusca/secret/{purpose}/v1".encode(),
        hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(subkey))


def purpose_multi(purpose: str | None) -> MultiFernet:
    """Every key that can open this purpose, newest first.

    ``None`` is the shared key and must stay that way: the credentials
    already sitting in deployments were written under it. Moving a column
    from ``None`` to a purpose is a data migration, and
    ``core.encrypted_columns`` is where that decision is recorded so a test
    can refuse a change that skips the migration.
    """
    keys = configured_keys()
    if purpose is None:
        return MultiFernet([Fernet(k) for k in keys])
    return MultiFernet([purpose_cipher(purpose, k) for k in keys])


def encrypt_secret(plaintext: str, *, purpose: str | None = None) -> str:
    """Encrypt ``plaintext`` and return a URL-safe Fernet token (str).

    The returned token is what gets persisted in the ``*_encrypted`` columns.
    Raises :class:`SecretEncryptionError` on a misconfigured key. Never logs
    the plaintext.
    """
    if not isinstance(plaintext, str):
        raise SecretEncryptionError("plaintext to encrypt must be a str")
    fernet = purpose_multi(purpose)
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(token: str, *, purpose: str | None = None) -> str:
    """Decrypt a Fernet ``token`` (as produced by :func:`encrypt_secret`).

    Raises :class:`SecretDecryptionError` on any failure — wrong/rotated key,
    corrupted or tampered ciphertext, or a non-string input. The message
    carries no key or plaintext bytes.
    """
    if not isinstance(token, str) or token == "":
        raise SecretDecryptionError("ciphertext token to decrypt must be a non-empty str")
    fernet = purpose_multi(purpose)
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
