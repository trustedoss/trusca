"""
Unit tests for the cosign config accessors (v2.3-s1).

Pin the env-resolution contract (CLAUDE.md core rule #11 — read at call time):
  - the keyless toggle parses the standard truthy spellings,
  - blank / unset key path + encrypted password resolve to None (so the adapter
    skips signing best-effort),
  - the timeout has a documented default and honours an override.
"""

from __future__ import annotations

import pytest

from core.config import (
    cosign_key_password_encrypted,
    cosign_key_path,
    cosign_keyless,
    cosign_timeout_seconds,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
        ("garbage", False),
    ],
)
def test_cosign_keyless_truthy_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("COSIGN_KEYLESS", raw)
    assert cosign_keyless() is expected


def test_cosign_keyless_default_is_key_based(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2: default is key-based (keyless False) when the toggle is unset."""
    monkeypatch.delenv("COSIGN_KEYLESS", raising=False)
    assert cosign_keyless() is False


def test_cosign_key_path_blank_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", "   ")
    assert cosign_key_path() is None
    monkeypatch.delenv("COSIGN_KEY_PATH", raising=False)
    assert cosign_key_path() is None


def test_cosign_key_path_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSIGN_KEY_PATH", "  /cosign/cosign.key  ")
    assert cosign_key_path() == "/cosign/cosign.key"


def test_cosign_key_password_encrypted_blank_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", "  ")
    assert cosign_key_password_encrypted() is None
    monkeypatch.delenv("COSIGN_KEY_PASSWORD_ENCRYPTED", raising=False)
    assert cosign_key_password_encrypted() is None


def test_cosign_key_password_encrypted_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", "  gAAAAA-ciphertext  ")
    assert cosign_key_password_encrypted() == "gAAAAA-ciphertext"


def test_cosign_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSIGN_TIMEOUT_SECONDS", raising=False)
    assert cosign_timeout_seconds() == 120
    monkeypatch.setenv("COSIGN_TIMEOUT_SECONDS", "45")
    assert cosign_timeout_seconds() == 45
