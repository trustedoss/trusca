# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for ``core.security``'s API-key keyed-hash primitives (A5,
concurrency-scaling-plan-2026-08-22.md §3.3):
``hash_api_key_secret`` / ``is_api_key_hmac_hash`` / ``verify_api_key_hmac``.

Pure (no DB). Each test sets ``API_KEY_HMAC_SECRET`` explicitly rather than
relying on the dev-derive fallback, so these tests are independent of
whatever ``SECRET_KEY`` happens to be in the test environment (see
``tests/unit/core/test_api_key_hmac_secret.py`` for the accessor's own
resolution-order tests).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hmac_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "test-fixture-hmac-secret-" + "k" * 20)


def test_hash_has_the_documented_scheme_prefix() -> None:
    from core.security import hash_api_key_secret

    hashed = hash_api_key_secret("tos_deadbeef_some-secret")
    assert hashed.startswith("hmac-sha256$")
    # 64 hex chars for a SHA-256 digest, plus the prefix.
    assert len(hashed) == len("hmac-sha256$") + 64


def test_correct_plaintext_verifies() -> None:
    from core.security import hash_api_key_secret, verify_api_key_hmac

    plaintext = "tos_deadbeef_abc123XYZ_-secret"
    hashed = hash_api_key_secret(plaintext)
    assert verify_api_key_hmac(plaintext, hashed) is True


def test_wrong_plaintext_does_not_verify() -> None:
    from core.security import hash_api_key_secret, verify_api_key_hmac

    hashed = hash_api_key_secret("tos_deadbeef_right-secret")
    assert verify_api_key_hmac("tos_deadbeef_wrong-secret", hashed) is False


def test_empty_plaintext_does_not_verify_a_real_hash() -> None:
    from core.security import hash_api_key_secret, verify_api_key_hmac

    hashed = hash_api_key_secret("tos_deadbeef_real-secret")
    assert verify_api_key_hmac("", hashed) is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-recognized-format",
        "hmac-sha256$",  # empty digest
        "hmac-sha256$not-hex-at-all",
        "$2b$12$abcdefghijklmnopqrstuv",  # bcrypt-shaped, not HMAC
        "HMAC-SHA256$" + "a" * 64,  # wrong case on the scheme marker
    ],
)
def test_verify_returns_false_never_raises_on_malformed_hash(malformed: str) -> None:
    from core.security import verify_api_key_hmac

    assert verify_api_key_hmac("anything", malformed) is False


def test_is_api_key_hmac_hash_true_only_for_the_new_format() -> None:
    from core.security import hash_api_key_secret, hash_password, is_api_key_hmac_hash

    assert is_api_key_hmac_hash(hash_api_key_secret("x")) is True
    assert is_api_key_hmac_hash(hash_password("x")) is False
    assert is_api_key_hmac_hash("") is False
    assert is_api_key_hmac_hash("hmac-sha256") is False  # missing the "$" separator


def test_different_secrets_produce_different_hashes() -> None:
    from core.security import hash_api_key_secret

    assert hash_api_key_secret("tos_deadbeef_one") != hash_api_key_secret("tos_deadbeef_two")


def test_hash_depends_on_the_server_side_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same plaintext hashed under two different API_KEY_HMAC_SECRET
    values must produce two different digests, and a hash produced under
    one key must not verify under another -- otherwise the server-side key
    would not actually be gating anything."""
    from core.security import hash_api_key_secret, verify_api_key_hmac

    plaintext = "tos_deadbeef_same-plaintext-both-times"

    monkeypatch.setenv("API_KEY_HMAC_SECRET", "first-server-key-" + "a" * 20)
    hashed_under_first_key = hash_api_key_secret(plaintext)

    monkeypatch.setenv("API_KEY_HMAC_SECRET", "second-server-key-" + "b" * 20)
    hashed_under_second_key = hash_api_key_secret(plaintext)

    assert hashed_under_first_key != hashed_under_second_key
    # A hash minted under the first key must not verify once the process
    # (or, here, the environment) has rotated to the second key.
    assert verify_api_key_hmac(plaintext, hashed_under_first_key) is False


def test_plaintext_never_appears_in_its_own_hash() -> None:
    from core.security import hash_api_key_secret

    plaintext = "tos_deadbeef_do-not-leak-this-value"
    hashed = hash_api_key_secret(plaintext)
    assert plaintext not in hashed
