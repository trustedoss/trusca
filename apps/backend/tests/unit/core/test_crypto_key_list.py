# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Parsing the encryption key list, and deriving a key per purpose (E22b).

``GITHUB_APP_ENCRYPTION_KEY`` went from one key to a comma-separated list. The
cases below are the ones a rotation actually produces: a single value that has
never seen a comma, a real two-key list, and the shapes an operator leaves
behind when editing one by hand.

Order is a contract, not a detail: the first key is the one new ciphertext is
written under, so a parser that sorted or reversed would quietly start
encrypting under the key being retired.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from core.crypto import (
    SecretEncryptionError,
    configured_keys,
    decrypt_secret,
    encrypt_secret,
    purpose_cipher,
    purpose_multi,
)

KEY_ENV = "GITHUB_APP_ENCRYPTION_KEY"


def test_one_key_stays_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every deployment today has this value, so it is the case to not break."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, key)
    assert configured_keys() == [key.encode()]


def test_the_first_key_is_the_one_that_encrypts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which end of the list encrypts decides whether a rotation converges.

    Reading from the wrong end would write new rows under the key being
    retired, so the count of stale rows would never reach zero and the reason
    would not be visible from the count.
    """
    new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")

    assert configured_keys() == [new.encode(), old.encode()]

    token = encrypt_secret("value")
    assert Fernet(new.encode()).decrypt(token.encode()) == b"value"


def test_every_key_in_the_list_can_read(monkeypatch: pytest.MonkeyPatch) -> None:
    new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, old)
    written_under_old = encrypt_secret("value")

    monkeypatch.setenv(KEY_ENV, f"{new},{old}")
    assert decrypt_secret(written_under_old) == "value"


@pytest.mark.parametrize(
    "raw",
    [
        "{new}, {old}",  # a space after the comma
        "{new},{old},",  # a trailing comma
        " {new} , {old} ",  # spaces around both
    ],
)
def test_spacing_an_operator_leaves_behind_is_tolerated(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """These are edits made by hand in a .env file, not generated values.

    A blank entry from a trailing comma must be dropped rather than parsed:
    it is not a key, and refusing the whole variable over it would fail a
    rotation on a typo that changes nothing.
    """
    new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, raw.format(new=new, old=old))
    assert configured_keys() == [new.encode(), old.encode()]


def test_a_malformed_entry_names_its_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """The position, and never the value.

    An operator with four keys has to know which one to look at, and key
    material must not reach an exception message that ends up in a log.
    """
    good = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, f"{good},not-a-fernet-key")

    with pytest.raises(SecretEncryptionError) as caught:
        configured_keys()
    assert "entry 2" in str(caught.value)
    assert "not-a-fernet-key" not in str(caught.value)
    assert good not in str(caught.value)


def test_a_value_holding_no_keys_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``,`` or ``"  "`` is a misconfiguration, not an unset variable.

    Treating it as unset would fall back to the key derived from SECRET_KEY and
    write ciphertext nothing later expects to find there.
    """
    monkeypatch.setenv(KEY_ENV, " , ")
    with pytest.raises(SecretEncryptionError, match="no keys"):
        configured_keys()


def test_a_purpose_gets_a_different_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret kept for one reason does not open a secret kept for another."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, key)

    shared = encrypt_secret("value")
    scoped = encrypt_secret("value", purpose="totp")

    assert decrypt_secret(scoped, purpose="totp") == "value"
    with pytest.raises(Exception):  # noqa: B017 - any failure is the point
        decrypt_secret(scoped)
    with pytest.raises(Exception):  # noqa: B017
        decrypt_secret(shared, purpose="totp")


def test_a_purpose_subkey_is_stable_for_one_master() -> None:
    """Derivation has to be deterministic or a rotation could never finish."""
    key = Fernet.generate_key()
    first = purpose_cipher("totp", key).encrypt(b"x")
    assert purpose_cipher("totp", key).decrypt(first) == b"x"


def test_a_purpose_reads_under_every_master(monkeypatch: pytest.MonkeyPatch) -> None:
    """The composition rotation depends on: derive per master, then try each.

    Without this, a rotation would move the shared-key columns and leave every
    purpose-scoped one unreadable the moment the old key came out.
    """
    new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, old)
    written_under_old = encrypt_secret("value", purpose="totp")

    monkeypatch.setenv(KEY_ENV, f"{new},{old}")
    assert purpose_multi("totp").decrypt(written_under_old.encode()) == b"value"
