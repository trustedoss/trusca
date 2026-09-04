# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Store and read private registry logins (ER3).

Encryption is ``core.crypto``'s Fernet helpers, the same ones
``github_app_service`` and the per-project git credential use. Nothing here
introduces a second mechanism, and nothing here logs a password.

Relationship to the registry allow-list
---------------------------------------
Both exist and they can disagree. A credential for a registry the allow-list
excludes can never be used, so the operator has configured something that does
nothing, and silence is the worst outcome. Both directions are covered:

* Saving a credential for an excluded registry is REJECTED, with an error
  naming the registry and the setting. That is the common case and immediate
  feedback beats discovery later.
* A credential saved before the list was tightened stays, but reads carry a
  computed ``allowed`` flag so an operator can see which of their credentials
  are now dead. Deleting rows behind the operator's back would be worse: the
  allow-list may be tightened by mistake and the credential is not the error.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from core.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from models import RegistryCredential
from services.registry_allowlist import (
    allowed_registries,
    is_registry_allowed,
    split_registry_host,
)

log = structlog.get_logger("services.registry_credential")


class RegistryCredentialError(Exception):
    """Base class for credential management failures."""


class RegistryNotAllowed(RegistryCredentialError):
    """The registry is excluded by CONTAINER_SCAN_ALLOWED_REGISTRIES."""


@dataclass(frozen=True)
class RegistryCredentialView:
    """One credential as an operator sees it. Never carries the password."""

    id: uuid.UUID
    registry_host: str
    username: str
    allowed: bool


def normalize_registry_host(raw: str) -> str:
    """Normalise operator input to the host an image reference parses to.

    An operator may paste ``https://ghcr.io`` or ``ghcr.io/`` out of a browser.
    Both must end up as ``ghcr.io``, or the scan-time lookup (which uses the
    parsed host of the image reference) silently never matches.
    """
    host = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host.strip("/").split("/", 1)[0]


def _registry_is_allowed(host: str) -> bool:
    """Whether the allow-list admits this host.

    Asked through the same predicate the scan path uses, with a synthetic
    repository appended, so the two can never disagree about a host.
    """
    return is_registry_allowed(f"{host}/placeholder", allowed_registries())


async def upsert_credential(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    registry_host: str,
    username: str,
    password: str,
    created_by_user_id: uuid.UUID | None = None,
) -> RegistryCredentialView:
    """Create or replace the organization's credential for one registry.

    Raises :class:`RegistryNotAllowed` when the allow-list excludes the host,
    because the stored row could never be used and saying so now is the only
    way the operator finds out.
    """
    host = normalize_registry_host(registry_host)
    if not host:
        raise RegistryCredentialError("registry_host is required")
    if not username.strip():
        raise RegistryCredentialError("username is required")
    if not password:
        raise RegistryCredentialError("password is required")

    if not _registry_is_allowed(host):
        raise RegistryNotAllowed(
            f"registry {host!r} is not in CONTAINER_SCAN_ALLOWED_REGISTRIES, so a "
            "credential for it could never be used; add it to that list first"
        )

    existing = (
        await session.execute(
            select(RegistryCredential)
            .where(RegistryCredential.organization_id == organization_id)
            .where(RegistryCredential.registry_host == host)
        )
    ).scalar_one_or_none()

    ciphertext = encrypt_secret(password)
    if existing is None:
        row = RegistryCredential(
            organization_id=organization_id,
            registry_host=host,
            username=username.strip(),
            password_encrypted=ciphertext,
            created_by_user_id=created_by_user_id,
        )
        session.add(row)
    else:
        row = existing
        row.username = username.strip()
        row.password_encrypted = ciphertext
    await session.commit()
    await session.refresh(row)

    # The host is operational information; the username is not a secret. The
    # password is never logged, here or anywhere.
    log.info(
        "registry_credential_saved",
        organization_id=str(organization_id),
        registry_host=host,
    )
    return RegistryCredentialView(
        id=row.id, registry_host=row.registry_host, username=row.username, allowed=True
    )


async def list_credentials(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> list[RegistryCredentialView]:
    """Every credential for an organization, with live allow-list status."""
    rows = (
        await session.execute(
            select(RegistryCredential)
            .where(RegistryCredential.organization_id == organization_id)
            .order_by(RegistryCredential.registry_host)
        )
    ).scalars().all()
    return [
        RegistryCredentialView(
            id=row.id,
            registry_host=row.registry_host,
            username=row.username,
            # Computed live rather than stored: the allow-list can change after
            # the row was written, and a stale flag would be worse than none.
            allowed=_registry_is_allowed(row.registry_host),
        )
        for row in rows
    ]


async def delete_credential(
    session: AsyncSession, *, organization_id: uuid.UUID, credential_id: uuid.UUID
) -> bool:
    """Remove one credential. Returns False when it was not this org's."""
    row = (
        await session.execute(
            select(RegistryCredential)
            .where(RegistryCredential.id == credential_id)
            .where(RegistryCredential.organization_id == organization_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    host = row.registry_host
    await session.delete(row)
    await session.commit()
    log.info(
        "registry_credential_deleted",
        organization_id=str(organization_id),
        registry_host=host,
    )
    return True


def credentials_for_image(
    session: Session,
    *,
    organization_id: uuid.UUID,
    image_ref: str,
) -> dict[str, tuple[str, str]]:
    """``{host: (username, password)}`` for the ONE registry this image uses.

    Synchronous because its only caller is the Celery scan task. Written once
    rather than as a sync/async pair: two implementations of the same rule is
    how one of them stops being exercised.

    Deliberately not every credential the organization holds. The scan needs
    one registry's login, so writing the rest into a file Trivy reads while it
    parses an attacker-supplied image would expose secrets the scan never
    needed. Narrowing here means a compromise of that path reaches the
    credential for the registry being pulled and no other.

    A row whose ciphertext will not decrypt is skipped with a warning rather
    than raised: one unreadable credential must not stop a scan that may not
    need it, and the pull then fails with a registry error the operator can
    act on.
    """
    host = split_registry_host(image_ref)
    row = session.execute(
        select(RegistryCredential)
        .where(RegistryCredential.organization_id == organization_id)
        .where(RegistryCredential.registry_host == host)
    ).scalar_one_or_none()
    if row is None:
        return {}
    try:
        password = decrypt_secret(row.password_encrypted)
    except SecretDecryptionError:
        log.warning(
            "registry_credential_undecryptable",
            organization_id=str(organization_id),
            registry_host=host,
        )
        return {}
    return {host: (row.username, password)}


__all__ = [
    "RegistryCredentialError",
    "RegistryCredentialView",
    "RegistryNotAllowed",
    "credentials_for_image",
    "delete_credential",
    "list_credentials",
    "normalize_registry_host",
    "upsert_credential",
]
