# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Store and read per-organization ticket-tracker logins (#385).

Encryption is ``core.crypto``'s Fernet helpers, the same ones
``registry_credential_service`` uses. Nothing here introduces a second
mechanism, and nothing here logs a token.

Only one ``auth_scheme`` has an adapter today (``jira_basic``, see
``integrations.ticket_status.jira``), but the table and this service are
written to the scheme rather than to Jira specifically, so a future GitHub
Issues / GitLab adapter is a new scheme value and a new row shape, not a
new table.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import (
    SecretDecryptionError,
    SecretEncryptionError,
    decrypt_secret,
    encrypt_secret,
)
from models import DEFAULT_AUTH_SCHEME, Organization, TicketCredential

log = structlog.get_logger("services.ticket_credential")

#: Auth schemes an adapter actually exists for. Saving a credential under
#: any other value would sit unused forever with no way to find out, so it
#: is rejected outright rather than accepted and silently never read.
SUPPORTED_AUTH_SCHEMES = frozenset({DEFAULT_AUTH_SCHEME})


class TicketCredentialError(Exception):
    """Base class for credential management failures."""


class AuthSchemeNotSupported(TicketCredentialError):
    """No adapter exists for the requested ``auth_scheme``."""


class OrganizationNotFound(TicketCredentialError):
    """No organization with this id.

    Checked rather than left to the foreign key, matching
    ``registry_credential_service.OrganizationNotFound``: an IntegrityError's
    text carries the bound parameters, which would put the freshly encrypted
    token into an unhandled-exception traceback in the logs.
    """


@dataclass(frozen=True)
class TicketCredentialView:
    """One credential as an operator sees it. Never carries the token."""

    id: uuid.UUID
    host: str
    auth_scheme: str
    username: str | None


#: A bare hostname, optionally with a port. Same shape as
#: ``registry_credential_service._HOST_RE`` and for the same reason: this is
#: operator input we would otherwise be guessing about.
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(:[0-9]{1,5})?$")


def normalize_host(raw: str) -> str:
    """Normalise operator input to the host a ticket URL's hostname parses to.

    An operator may paste ``https://mycompany.atlassian.net`` out of a
    browser's address bar; both that and the bare host must end up the
    same way, or the read-time lookup (by the parsed host of a finding's
    ``ticket_url``) silently never matches.
    """
    host = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host.strip("/").split("/", 1)[0]


def _validate_host(host: str) -> None:
    """Reject anything that is not a bare host, without echoing the input.

    Mirrors ``registry_credential_service._validate_host``: a pasted
    ``https://bot:token@mycompany.atlassian.net/`` could carry the secret
    itself in the userinfo segment, and this column is not a secret
    anywhere (returned by the API, written to the audit diff, logged), so
    the input is not echoed on rejection.
    """
    if not _HOST_RE.match(host):
        raise TicketCredentialError(
            "host must be a bare hostname such as 'mycompany.atlassian.net', "
            "with no scheme, credentials or path"
        )


async def upsert_credential(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    host: str,
    auth_scheme: str,
    username: str | None,
    api_token: str,
    created_by_user_id: uuid.UUID | None = None,
) -> TicketCredentialView:
    """Create or replace the organization's credential for one tracker host.

    Raises :class:`AuthSchemeNotSupported` when no adapter reads that
    scheme, and :class:`OrganizationNotFound` when the organization does
    not exist.
    """
    normalized_host = normalize_host(host)
    if not normalized_host:
        raise TicketCredentialError("host is required")
    _validate_host(normalized_host)

    if auth_scheme not in SUPPORTED_AUTH_SCHEMES:
        raise AuthSchemeNotSupported(
            f"auth_scheme {auth_scheme!r} has no adapter; use one of "
            f"{sorted(SUPPORTED_AUTH_SCHEMES)}"
        )
    if auth_scheme == DEFAULT_AUTH_SCHEME and not (username or "").strip():
        raise TicketCredentialError(f"username is required for auth_scheme {auth_scheme!r}")
    if not api_token:
        raise TicketCredentialError("api_token is required")

    organization_exists = (
        await session.execute(
            select(Organization.id).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()
    if organization_exists is None:
        raise OrganizationNotFound(f"no organization {organization_id}")

    existing = (
        await session.execute(
            select(TicketCredential)
            .where(TicketCredential.organization_id == organization_id)
            .where(TicketCredential.host == normalized_host)
        )
    ).scalar_one_or_none()

    try:
        ciphertext = encrypt_secret(api_token)
    except SecretEncryptionError as exc:
        # Fails closed in production when the encryption key is unset;
        # matches registry_credential_service's own handling of the same
        # failure, for the same reason: unhandled, this is a 500 that puts
        # the key's NAME into the response.
        raise TicketCredentialError(
            "ticket credentials cannot be stored because secret encryption "
            "is not configured on this deployment"
        ) from exc

    normalized_username = (username or "").strip() or None
    if existing is None:
        row = TicketCredential(
            organization_id=organization_id,
            host=normalized_host,
            auth_scheme=auth_scheme,
            username=normalized_username,
            api_token_encrypted=ciphertext,
            created_by_user_id=created_by_user_id,
        )
        session.add(row)
    else:
        row = existing
        row.auth_scheme = auth_scheme
        row.username = normalized_username
        row.api_token_encrypted = ciphertext
    await session.commit()
    await session.refresh(row)

    log.info(
        "ticket_credential_saved",
        organization_id=str(organization_id),
        host=normalized_host,
        auth_scheme=auth_scheme,
    )
    return TicketCredentialView(
        id=row.id, host=row.host, auth_scheme=row.auth_scheme, username=row.username
    )


async def list_credentials(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> list[TicketCredentialView]:
    """Every ticket-tracker credential for an organization."""
    rows = (
        (
            await session.execute(
                select(TicketCredential)
                .where(TicketCredential.organization_id == organization_id)
                .order_by(TicketCredential.host)
            )
        )
        .scalars()
        .all()
    )
    return [
        TicketCredentialView(
            id=row.id, host=row.host, auth_scheme=row.auth_scheme, username=row.username
        )
        for row in rows
    ]


async def delete_credential(
    session: AsyncSession, *, organization_id: uuid.UUID, credential_id: uuid.UUID
) -> bool:
    """Remove one credential. Returns False when it was not this org's."""
    row = (
        await session.execute(
            select(TicketCredential)
            .where(TicketCredential.id == credential_id)
            .where(TicketCredential.organization_id == organization_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    host = row.host
    await session.delete(row)
    await session.commit()
    log.info("ticket_credential_deleted", organization_id=str(organization_id), host=host)
    return True


async def credential_for_host(
    session: AsyncSession, *, organization_id: uuid.UUID, host: str
) -> tuple[str, str | None, str] | None:
    """``(auth_scheme, username, api_token)`` for one host, or ``None``.

    The plaintext token never leaves this function's caller's stack frame
    for longer than the one outbound request it is used for; nothing
    persists it beyond that call.

    A row whose ciphertext will not decrypt, or a deployment whose
    encryption key is missing entirely, is treated as absent (logged, not
    raised): one unreadable credential must not turn a "refresh ticket
    status" click into a 500 with no actionable message, and the caller
    already renders "no credential configured" the same way it would
    render a genuinely missing row.
    """
    row = (
        await session.execute(
            select(TicketCredential)
            .where(TicketCredential.organization_id == organization_id)
            .where(TicketCredential.host == normalize_host(host))
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        token = decrypt_secret(row.api_token_encrypted)
    except (SecretDecryptionError, SecretEncryptionError):
        log.warning(
            "ticket_credential_undecryptable",
            organization_id=str(organization_id),
            host=row.host,
        )
        return None
    return row.auth_scheme, row.username, token


__all__ = [
    "AuthSchemeNotSupported",
    "OrganizationNotFound",
    "SUPPORTED_AUTH_SCHEMES",
    "TicketCredentialError",
    "TicketCredentialView",
    "credential_for_host",
    "delete_credential",
    "list_credentials",
    "normalize_host",
    "upsert_credential",
]
