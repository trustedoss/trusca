# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Issuing a project's webhook secret (E22a).

The route's end-to-end behaviour is covered by
``tests/integration/test_webhook_activation_procedure.py``, which asks whether
following the guide produces a webhook the gateway accepts. This file covers
what the service does with values, where a stub can make the awkward cases
happen: a deployment with no usable encryption key, and a re-issue.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.crypto import SecretEncryptionError, decrypt_secret
from services.webhook_activation_service import (
    WebhookSecretEncryptionUnavailable,
    generate_webhook_secret,
    issue_webhook_secret,
)


class _Project:
    """Only the attributes the service touches."""

    def __init__(self, existing: str | None = None) -> None:
        self.id = uuid.uuid4()
        self.team_id = uuid.uuid4()
        self.webhook_secret_encrypted = existing
        self.webhook_provider: str | None = None


class _Session:
    def __init__(self) -> None:
        self.flushed = 0

    async def flush(self) -> None:
        self.flushed += 1


def test_two_secrets_are_not_the_same() -> None:
    """A generator that returned a constant would pass every other test here.

    Every assertion below works on whatever the generator produced, so nothing
    else in this file would notice a fixed value, and a deployment where every
    project shared one secret is the failure that would follow.
    """
    assert generate_webhook_secret() != generate_webhook_secret()


def test_the_secret_is_the_width_the_column_held() -> None:
    secret = generate_webhook_secret()
    assert len(secret) == 64, len(secret)


async def test_issuing_stores_ciphertext_and_returns_the_plaintext() -> None:
    project = _Project()
    session = _Session()

    secret, replaced = await issue_webhook_secret(
        session,  # type: ignore[arg-type]
        project=project,
        provider="github",
    )

    assert replaced is False
    assert project.webhook_provider == "github"
    assert project.webhook_secret_encrypted is not None
    assert project.webhook_secret_encrypted != secret, (
        "the column holds the plaintext, so encrypting it changed nothing"
    )
    assert decrypt_secret(project.webhook_secret_encrypted) == secret
    assert session.flushed == 1, (
        "the row was never flushed, so nothing reaches the database and the "
        "caller commits an unchanged session while reporting success"
    )


async def test_re_issuing_says_it_replaced_something() -> None:
    """The caller has to be able to tell the operator their old secret died."""
    project = _Project(existing="an-existing-ciphertext")
    session = _Session()

    secret, replaced = await issue_webhook_secret(
        session,  # type: ignore[arg-type]
        project=project,
        provider="gitlab",
    )

    assert replaced is True
    assert project.webhook_secret_encrypted != "an-existing-ciphertext"
    assert decrypt_secret(project.webhook_secret_encrypted) == secret


async def test_provider_is_written_together_with_the_secret() -> None:
    """ER70 in one assertion.

    A secret with no provider matches nothing in the gateway's lookup. The
    operator has done what they were told and every delivery is refused, with
    no symptom except that scans never start. There is no path through this
    function that sets one without the other, and this is what says so.
    """
    project = _Project()
    session = _Session()

    await issue_webhook_secret(
        session,  # type: ignore[arg-type]
        project=project,
        provider="gitlab",
    )

    assert project.webhook_secret_encrypted is not None
    assert project.webhook_provider == "gitlab"


async def test_a_broken_encryption_key_stops_the_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key, no secret, and nothing half-written.

    The alternative is worse than failing: a project left with a provider and
    no secret is a project the gateway refuses every delivery for, which is
    the ER70 shape arrived at from the other direction.
    """
    def _boom(_plaintext: str) -> str:
        raise SecretEncryptionError("GITHUB_APP_ENCRYPTION_KEY is not a valid key")

    monkeypatch.setattr(
        "services.webhook_activation_service.encrypt_secret", _boom
    )
    project = _Project()
    session = _Session()

    with pytest.raises(WebhookSecretEncryptionUnavailable):
        await issue_webhook_secret(
            session,  # type: ignore[arg-type]
            project=project,
            provider="github",
        )

    assert project.webhook_secret_encrypted is None
    assert project.webhook_provider is None, (
        "the provider was written before the failure, leaving the project in "
        "the half-configured state the gateway silently refuses"
    )
    assert session.flushed == 0


async def test_the_failure_message_carries_no_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """A configuration error must not be the thing that logs the value.

    The secret is generated before the encryption is attempted, so it exists
    at the moment the failure is handled. That is the window this closes.
    """
    generated: list[str] = []

    def _capture_then_fail(plaintext: str) -> str:
        generated.append(plaintext)
        raise SecretEncryptionError("key is malformed")

    monkeypatch.setattr(
        "services.webhook_activation_service.encrypt_secret", _capture_then_fail
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(WebhookSecretEncryptionUnavailable) as caught:
            await issue_webhook_secret(
                _Session(),  # type: ignore[arg-type]
                project=_Project(),
                provider="github",
            )

    assert generated, "encryption was never attempted, so nothing was at risk"
    secret = generated[0]
    assert secret not in str(caught.value)
    assert secret not in caplog.text
