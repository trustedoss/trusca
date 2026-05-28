"""
Unit tests for the security-reviewer round-1 blockers (C-1, H-2, H-3).

These exercise the bootstrap-time guards added to harden the auth surface:

  C-1 — secret_key() refuses to return a weak/missing key outside dev.
  H-2 — services.auth_service exposes a pre-computed dummy bcrypt hash so
        /auth/login pays the same bcrypt cost on the "user not found" branch
        as on the "wrong password" branch.
  H-3 — validate_cors_origins() rejects '*' (incompatible with credentials)
        and http:// origins in production.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# C-1 — SECRET_KEY validation
# ---------------------------------------------------------------------------


def test_secret_key_dev_falls_back_to_placeholder(monkeypatch):
    """In dev, an unset SECRET_KEY returns the documented placeholder."""
    from core import config

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")

    key = config.secret_key()
    assert key == config._DEV_PLACEHOLDER_SECRET
    assert len(key) >= config._MIN_SECRET_LEN
    assert "DO-NOT-USE-IN-PROD" in key


def test_secret_key_prod_missing_raises(monkeypatch):
    """In prod, an unset SECRET_KEY must crash boot — never silently default."""
    from core import config

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")

    with pytest.raises(RuntimeError, match="SECRET_KEY is required"):
        config.secret_key()


def test_secret_key_too_short_raises(monkeypatch):
    """Even with a value, length < 32 is rejected."""
    from core import config

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "short")

    with pytest.raises(RuntimeError, match="at least"):
        config.secret_key()


def test_secret_key_strong_value_returned(monkeypatch):
    """A 32+ char value is returned verbatim, regardless of APP_ENV."""
    from core import config

    strong = "x" * 64
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", strong)

    assert config.secret_key() == strong


def test_min_secret_len_constant_is_32():
    from core import config

    assert config._MIN_SECRET_LEN == 32


# ---------------------------------------------------------------------------
# H-2 — login timing oracle
# ---------------------------------------------------------------------------


def test_dummy_bcrypt_hash_is_precomputed_at_import():
    """
    services.auth_service must precompute a dummy bcrypt hash at module load
    so the 'user not found' branch of authenticate() pays the same cost as
    the 'wrong password' branch.
    """
    from services.auth_service import _DUMMY_BCRYPT_HASH

    # bcrypt $2b$ prefix with cost 12, per CLAUDE.md §3.
    assert _DUMMY_BCRYPT_HASH.startswith("$2b$12$")


def test_authenticate_verifies_dummy_when_user_not_found(monkeypatch):
    """
    The verify_password path must run even when select() returns no row, so
    the response time leaks no information about whether the email exists.

    We don't need a real DB here — a stub session with a result that returns
    None is enough to drive the branch, and we observe verify_password being
    called with the dummy hash.
    """
    import asyncio
    from unittest.mock import MagicMock

    from services import auth_service

    calls: list[tuple[str, str]] = []

    def fake_verify(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return False

    monkeypatch.setattr(auth_service, "verify_password", fake_verify)

    # Stub session.execute().scalar_one_or_none() → None
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = None

    class FakeSession:
        async def execute(self, _stmt):  # noqa: ANN001
            return fake_result

    user = asyncio.run(
        auth_service.authenticate(
            FakeSession(),  # type: ignore[arg-type]
            email="ghost@example.com",
            password="anything",
        )
    )

    assert user is None
    assert len(calls) == 1, "verify_password must run on the no-user branch"
    plain, hashed = calls[0]
    assert plain == "anything"
    assert hashed == auth_service._DUMMY_BCRYPT_HASH


# ---------------------------------------------------------------------------
# H-3 — CORS bootstrap guard
# ---------------------------------------------------------------------------


def test_validate_cors_rejects_wildcard():
    """allow_origins=['*'] is incompatible with allow_credentials=True."""
    from core.config import validate_cors_origins

    with pytest.raises(RuntimeError, match="allow_credentials"):
        validate_cors_origins(["*"], env="dev")


def test_validate_cors_rejects_http_in_prod():
    from core.config import validate_cors_origins

    with pytest.raises(RuntimeError, match="https://"):
        validate_cors_origins(["http://app.example.com"], env="prod")


def test_validate_cors_allows_http_in_dev():
    """dev over plain http://localhost:5173 is fine."""
    from core.config import validate_cors_origins

    # Should not raise.
    validate_cors_origins(["http://localhost:5173"], env="dev")


def test_validate_cors_allows_https_in_prod():
    from core.config import validate_cors_origins

    # Should not raise.
    validate_cors_origins(
        ["https://portal.example.com", "https://admin.example.com"],
        env="prod",
    )
