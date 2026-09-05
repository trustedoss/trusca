# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Issuing the shared secret that turns a project's webhook on.

Before 0084-0086 this was not code. The guide handed the operator an UPDATE
and the operator ran it against the database. That worked only because the
column held plaintext, and it went wrong in a way worth keeping in mind here:
the documented statement set the secret and not the provider, the gateway
matches on both, and every delivery was refused with no symptom except that
scans never started (ER70).

The lesson that carries over is that activation has two fields and setting one
of them is not a partial success, it is a failure that looks like a success.
Both are set here in one call, and the provider is required rather than
defaulted, so there is no shape of this request that leaves a project half
configured.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import bind_audit_team
from core.crypto import SecretEncryptionError, encrypt_secret
from models import Project

log = structlog.get_logger("webhook_activation")

#: 64 urlsafe characters, matching the width the plaintext column used to hold
#: and what the documented ``gen_random_bytes(32)`` produced. GitHub accepts
#: any string; GitLab accepts any string; the constraint that mattered was the
#: column, and keeping the size means an operator who has one of the old
#: secrets in front of them sees the same kind of value.
_SECRET_BYTES = 48


class WebhookActivationError(Exception):
    """Base for failures that the route turns into Problem Details."""


class WebhookSecretEncryptionUnavailable(WebhookActivationError):
    """The deployment's credential encryption key is unset or malformed."""


def generate_webhook_secret() -> str:
    """A fresh shared secret.

    ``token_urlsafe`` rather than ``token_hex``: the value gets pasted into a
    web form by a person, and a shorter string with the same entropy is one
    they are less likely to truncate.
    """
    return secrets.token_urlsafe(_SECRET_BYTES)[:64]


async def issue_webhook_secret(
    session: AsyncSession,
    *,
    project: Project,
    provider: str,
) -> tuple[str, bool]:
    """Give ``project`` a new shared secret and return it with the plaintext.

    Returns ``(secret, replaced_existing)``. The plaintext is returned and not
    stored; the caller surfaces it once and drops it.

    Re-issuing is the same operation as issuing. There is no separate rotate
    path because there is nothing to preserve: the old value stops being
    accepted the moment this commits, whether it is the first secret or the
    fifth, and a caller that had to choose between two endpoints would be
    choosing on a distinction the product does not make.
    """
    replaced_existing = project.webhook_secret_encrypted is not None
    secret = generate_webhook_secret()

    try:
        ciphertext = encrypt_secret(secret)
    except SecretEncryptionError as exc:
        # The message from core.crypto talks about the key and never the
        # plaintext. This does not interpolate the secret regardless, and does
        # not log it: a failure here is a configuration problem and the value
        # that was about to be stored has no business in the record of it.
        log.error(
            "webhook_secret_encrypt_failed",
            project_id=str(project.id),
            error=str(exc),
        )
        raise WebhookSecretEncryptionUnavailable(
            "the webhook secret could not be encrypted; the deployment's "
            "credential encryption key is unset or misconfigured",
        ) from exc

    bind_audit_team(project.team_id)
    project.webhook_secret_encrypted = ciphertext
    project.webhook_provider = provider
    await session.flush()

    # The fact, never the value. The audit row the ORM listener writes has the
    # ciphertext masked out of its diff; this log is the human-readable half
    # and carries no credential bytes either.
    log.info(
        "webhook_secret_issued",
        project_id=str(project.id),
        provider=provider,
        replaced_existing=replaced_existing,
    )
    return secret, replaced_existing


def webhook_issued_at() -> datetime:
    """Now, in UTC, for the response body."""
    return datetime.now(UTC)


__all__ = [
    "WebhookActivationError",
    "WebhookSecretEncryptionUnavailable",
    "generate_webhook_secret",
    "issue_webhook_secret",
    "webhook_issued_at",
]
