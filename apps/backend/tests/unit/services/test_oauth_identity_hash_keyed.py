"""
Unit tests for ``services.oauth_identity_service._hash_provider_user_id``
keyed-BLAKE2b upgrade — Marathon bundle 4 (T / L4).

Behaviour contract:
  - When ``AUDIT_HASH_KEY`` is unset/empty → bare SHA-256 (legacy).
  - When ``AUDIT_HASH_KEY`` is set + length >= 16 bytes → keyed BLAKE2b-256.
  - When ``AUDIT_HASH_KEY`` is set but < 16 bytes → RuntimeError (M2 fix).
  - In APP_ENV=prod with key unset → log a structured WARNING (M2 fix).
  - The function reads env vars at call time per CLAUDE.md core rule
    #11 — rotation takes effect immediately.
  - The keyed digest must NOT collide with the legacy SHA-256 digest of
    the same input (otherwise the upgrade is meaningless).
  - The keyed digest must differ across distinct keys for the same input.
"""

from __future__ import annotations

import hashlib

import pytest


def test_hash_falls_back_to_sha256_when_key_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUDIT_HASH_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    from services.oauth_identity_service import _hash_provider_user_id

    expected = hashlib.sha256(b"github-12345").hexdigest()
    assert _hash_provider_user_id("github-12345") == expected


def test_hash_falls_back_to_sha256_when_key_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty string is treated as unset — preserves legacy behaviour."""
    monkeypatch.setenv("AUDIT_HASH_KEY", "")
    monkeypatch.delenv("APP_ENV", raising=False)
    from services.oauth_identity_service import _hash_provider_user_id

    expected = hashlib.sha256(b"github-12345").hexdigest()
    assert _hash_provider_user_id("github-12345") == expected


def test_hash_uses_keyed_blake2b_when_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "deployment-secret-32-bytes-padding-aabbccdd"
    monkeypatch.setenv("AUDIT_HASH_KEY", key)
    from services.oauth_identity_service import _hash_provider_user_id

    expected = hashlib.blake2b(
        b"github-12345", key=key.encode("utf-8"), digest_size=32
    ).hexdigest()
    assert _hash_provider_user_id("github-12345") == expected
    assert _hash_provider_user_id("github-12345") != hashlib.sha256(
        b"github-12345"
    ).hexdigest()


def test_hash_rejects_short_key_with_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marathon bundle 4 (T / L4) — security-reviewer M2: a short key
    must fail loud rather than silently degrade entropy."""
    monkeypatch.setenv("AUDIT_HASH_KEY", "test")  # 4 bytes — way under 16
    from services.oauth_identity_service import _hash_provider_user_id

    with pytest.raises(RuntimeError, match="shorter than"):
        _hash_provider_user_id("github-12345")


def test_hash_accepts_exactly_min_length_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boundary: 16-byte key passes; < 16 fails."""
    monkeypatch.setenv("AUDIT_HASH_KEY", "x" * 16)
    from services.oauth_identity_service import _hash_provider_user_id

    out = _hash_provider_user_id("github-1")
    assert isinstance(out, str)
    assert len(out) == 64

    monkeypatch.setenv("AUDIT_HASH_KEY", "x" * 15)
    with pytest.raises(RuntimeError):
        _hash_provider_user_id("github-1")


def test_hash_differs_across_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same input under different keys must produce different digests
    (otherwise the key adds no security)."""
    from services.oauth_identity_service import _hash_provider_user_id

    monkeypatch.setenv("AUDIT_HASH_KEY", "key-one-padding-32-chars-xxxxxxxx")
    digest_one = _hash_provider_user_id("github-12345")

    monkeypatch.setenv("AUDIT_HASH_KEY", "key-two-padding-32-chars-yyyyyyyy")
    digest_two = _hash_provider_user_id("github-12345")

    assert digest_one != digest_two


def test_hash_runtime_env_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key rotation must take effect on the next call without restart
    (CLAUDE.md core rule #11)."""
    from services.oauth_identity_service import _hash_provider_user_id

    monkeypatch.delenv("AUDIT_HASH_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    legacy = _hash_provider_user_id("user-x")
    assert legacy == hashlib.sha256(b"user-x").hexdigest()

    monkeypatch.setenv("AUDIT_HASH_KEY", "rotated-key-32-bytes-padding-abcdefgh")
    keyed = _hash_provider_user_id("user-x")
    assert keyed != legacy


def test_hash_logs_warning_in_prod_when_key_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2 follow-up: APP_ENV=prod with AUDIT_HASH_KEY unset must emit a
    structured WARNING per call so operators see the signal.

    structlog's default config in this repo bypasses stdlib `logging` so
    `caplog` doesn't capture it. We monkeypatch the module-level `log`
    binding's `warning` method to record the call shape directly.
    """
    monkeypatch.delenv("AUDIT_HASH_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    from services import oauth_identity_service as svc

    captured: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        captured.append((event, kwargs))

    monkeypatch.setattr(svc.log, "warning", _capture)
    svc._hash_provider_user_id("user-y")

    assert any(event == "audit_hash.legacy_sha256_active" for event, _ in captured), (
        f"expected legacy_sha256_active warning, got: {captured}"
    )


def test_hash_does_not_log_warning_outside_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warning is gated on APP_ENV=prod so dev / test runs stay quiet."""
    monkeypatch.delenv("AUDIT_HASH_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    from services import oauth_identity_service as svc

    captured: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        captured.append((event, kwargs))

    monkeypatch.setattr(svc.log, "warning", _capture)
    svc._hash_provider_user_id("user-z")

    assert not any(
        event == "audit_hash.legacy_sha256_active" for event, _ in captured
    ), f"unexpected warning in dev: {captured}"


@pytest.mark.parametrize(
    "adversarial",
    [
        "",
        "\x00",
        "\x00user",
        "user\x00",
        "user\r\n",
        "user‮",  # RTL override
        "u" * 100_000,  # oversized
    ],
)
def test_hash_handles_adversarial_provider_user_ids(
    monkeypatch: pytest.MonkeyPatch, adversarial: str
) -> None:
    """The hash MUST never raise on an attacker-supplied provider id."""
    monkeypatch.setenv("AUDIT_HASH_KEY", "test-key-32-bytes-padding-aabbccdd")
    from services.oauth_identity_service import _hash_provider_user_id

    out = _hash_provider_user_id(adversarial)
    assert isinstance(out, str)
    assert len(out) == 64
    assert all(c in "0123456789abcdef" for c in out)
