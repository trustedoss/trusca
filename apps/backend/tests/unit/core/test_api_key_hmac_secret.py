# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for ``core.config.api_key_hmac_secret`` (A5,
concurrency-scaling-plan-2026-08-22.md §3.3).

Pure (no DB). Mirrors ``tests/unit/core/test_crypto.py``'s coverage shape
for ``GITHUB_APP_ENCRYPTION_KEY``, since this accessor follows the same
dedicated-key / derive-in-dev-with-warning shape, but with a STRICTER
fail-closed condition than that module's (``app_env() != "dev"``, i.e.
staging AND prod, not just prod -- see the accessor's own docstring for
why):

  - explicit ``API_KEY_HMAC_SECRET`` is returned verbatim (after trimming).
  - too-short an explicit value raises, in ANY environment.
  - unset in dev derives deterministically from ``secret_key()``, changes
    when ``SECRET_KEY`` rotates, and differs from ``core.crypto``'s derived
    Fernet key (domain separation).
  - unset in non-dev (staging AND prod) raises rather than deriving.
  - blank (whitespace-only) is treated the same as unset.
"""

from __future__ import annotations

import pytest


def test_explicit_key_returned_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import api_key_hmac_secret

    key = "238cc02c1d7c23c5c88d23db4684975057dd12c5"
    monkeypatch.setenv("API_KEY_HMAC_SECRET", key)
    assert api_key_hmac_secret() == key


def test_explicit_key_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import api_key_hmac_secret

    key = "238cc02c1d7c23c5c88d23db4684975057dd12c5"
    monkeypatch.setenv("API_KEY_HMAC_SECRET", f"  {key}  ")
    assert api_key_hmac_secret() == key


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_explicit_key_too_short_raises_in_every_environment(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    from core.config import api_key_hmac_secret

    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "short")
    with pytest.raises(RuntimeError, match="at least"):
        api_key_hmac_secret()


def test_dev_unset_derives_from_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import api_key_hmac_secret

    monkeypatch.delenv("API_KEY_HMAC_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "7f3a91c4e08b256d4af1c93e75b028da6c14e9f3")

    value = api_key_hmac_secret()
    assert isinstance(value, str)
    assert len(value) > 0
    # Deterministic: calling again with the same SECRET_KEY reproduces it,
    # which is required since nothing else is stored alongside a hash to
    # reproduce the key material at verify time.
    assert api_key_hmac_secret() == value


def test_dev_derived_key_changes_when_secret_key_rotates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import api_key_hmac_secret

    monkeypatch.delenv("API_KEY_HMAC_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")

    monkeypatch.setenv("SECRET_KEY", "b28e4d70a915c3f6e0d47b1a89c25f30e6a4d19c")
    first = api_key_hmac_secret()

    monkeypatch.setenv("SECRET_KEY", "6c78c141f3b3dc9739be569ecfcbed345ea404b0")
    second = api_key_hmac_secret()

    assert first != second


def test_dev_derived_key_differs_from_crypto_derived_fernet_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Domain separation: deriving from the same SECRET_KEY must not
    produce a value that collides with (or is trivially related to)
    core.crypto's independently-derived encryption key. Different callers
    of the same JWT secret should not end up sharing effective key
    material."""
    from core.config import api_key_hmac_secret
    from core.crypto import _derive_key_from_secret

    monkeypatch.delenv("API_KEY_HMAC_SECRET", raising=False)
    monkeypatch.delenv("GITHUB_APP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "shared-secret-" + "d57168e4824324f7ab29fc72048f67f97fb69f39")

    hmac_secret = api_key_hmac_secret()
    fernet_key = _derive_key_from_secret().decode("ascii")

    assert hmac_secret != fernet_key


def test_dev_derived_key_emits_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors test_concurrency_config.py's L2 pattern for a function that
    locally imports structlog per CLAUDE.md core rule #11 rather than
    binding a module-level logger."""
    from unittest.mock import MagicMock, patch

    from core.config import api_key_hmac_secret

    monkeypatch.delenv("API_KEY_HMAC_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "241bdf90a275dc32ccf4f2448f94a74559f196e6")

    fake_logger = MagicMock()
    with patch("structlog.get_logger", return_value=fake_logger):
        api_key_hmac_secret()

    fake_logger.warning.assert_called_once()
    args, _kwargs = fake_logger.warning.call_args
    assert args[0] == "config.api_key_hmac_secret_derived_from_secret_key"


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_non_dev_unset_key_fails_closed(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    from core.config import api_key_hmac_secret

    monkeypatch.delenv("API_KEY_HMAC_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("SECRET_KEY", "b4ea8c86a66f27320a4a8a66e5ee00680e6ce126")

    with pytest.raises(RuntimeError, match="API_KEY_HMAC_SECRET is required"):
        api_key_hmac_secret()


def test_non_dev_blank_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import api_key_hmac_secret

    monkeypatch.setenv("API_KEY_HMAC_SECRET", "   ")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "b441c01cb0bb5c88f7ad458be5a8e3ddb1740572")

    with pytest.raises(RuntimeError, match="API_KEY_HMAC_SECRET is required"):
        api_key_hmac_secret()


def test_prod_with_explicit_key_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import api_key_hmac_secret

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "e57c20a94f1d836b0ac7e49215f3d68b1c0a7e94")
    assert api_key_hmac_secret() == "e57c20a94f1d836b0ac7e49215f3d68b1c0a7e94"
