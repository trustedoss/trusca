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

import re
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from core.crypto import (
    SecretDecryptionError,
    SecretEncryptionError,
    decrypt_secret,
    encrypt_secret,
)
from models import Organization, RegistryCredential
from services.registry_allowlist import (
    allowed_registries,
    is_registry_host_allowed,
    split_registry_host,
)

log = structlog.get_logger("services.registry_credential")


class RegistryCredentialError(Exception):
    """Base class for credential management failures."""


class RegistryNotAllowed(RegistryCredentialError):
    """The registry is excluded by CONTAINER_SCAN_ALLOWED_REGISTRIES."""


class OrganizationNotFound(RegistryCredentialError):
    """No organization with this id.

    Checked rather than left to the foreign key. The database would raise an
    IntegrityError whose text carries the bound parameters, and those include
    the freshly encrypted password, so the row we refused to write ends up in
    an unhandled-exception traceback in the logs instead.
    """


@dataclass(frozen=True)
class RegistryCredentialView:
    """One credential as an operator sees it. Never carries the password."""

    id: uuid.UUID
    registry_host: str
    username: str
    allowed: bool


#: A bare registry host, optionally with a port. Deliberately strict: anything
#: this does not match is operator input we would be guessing about.
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(:[0-9]{1,5})?$")


def normalize_registry_host(raw: str) -> str:
    """Normalise operator input to the host an image reference parses to.

    An operator may paste ``https://ghcr.io`` or ``ghcr.io/`` out of a browser.
    Both must end up as ``ghcr.io``, or the scan-time lookup (which uses the
    parsed host of the image reference) silently never matches.

    Scheme and path only. A pasted URL can also carry the credential itself
    (``https://bot:ghp_x@ghcr.io/``), and that is NOT normalised away here: any
    userinfo is left in place so :func:`_validate_host` sees it and refuses the
    request. Quietly salvaging ``ghcr.io`` out of it would store a row the
    operator never checked while their token sat in the request they think
    succeeded.
    """
    host = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host.strip("/").split("/", 1)[0]


def _validate_host(host: str) -> None:
    """Reject anything that is not a bare host, without echoing the input.

    The case this exists for is a pasted ``https://bot:ghp_x@ghcr.io/``. This
    column is not a secret anywhere: it is returned by the API, written to the
    audit diff and logged, so a password reaching it would sit in plaintext
    beside the encrypted one and make the encryption misleading rather than
    protective. ``mask_git_url`` exists for the same reason.

    The input is not echoed on purpose. The value being rejected is the one
    that may contain the password, and putting it in an error message moves it
    into the response body and the caller's logs, which is what this prevents.
    """
    if not _HOST_RE.match(host):
        raise RegistryCredentialError(
            "registry_host must be a bare hostname such as 'ghcr.io' or "
            "'registry.example.com:5000', with no scheme, credentials or path"
        )


def _registry_is_allowed(host: str) -> bool:
    """Whether the allow-list admits any pull from this host.

    A host is all a credential has, so this asks the host-level question. It
    used to append a synthetic repository path and ask the reference-level
    predicate, which was wrong whenever an allow-list entry carried a path
    prefix: no synthetic path matches ``ghcr.io/trustedoss``, so saving a
    credential for ``ghcr.io`` was refused on exactly the narrow configuration
    the allow-list documentation recommends, and stored credentials in active
    use were reported to operators as ``allowed: false``.
    """
    return is_registry_host_allowed(host, allowed_registries())


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
    way the operator finds out, and :class:`OrganizationNotFound` when the
    organization does not exist.
    """
    host = normalize_registry_host(registry_host)
    if not host:
        raise RegistryCredentialError("registry_host is required")
    _validate_host(host)
    if not username.strip():
        raise RegistryCredentialError("username is required")
    if not password:
        raise RegistryCredentialError("password is required")

    organization_exists = (
        await session.execute(
            select(Organization.id).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()
    if organization_exists is None:
        raise OrganizationNotFound(f"no organization {organization_id}")

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

    try:
        ciphertext = encrypt_secret(password)
    except SecretEncryptionError as exc:
        # Fails closed in production when the encryption key is unset. Left
        # unhandled this is a 500 with no way to act on it, and the same
        # exception reaching the worker would put the key's NAME into
        # `scans.error_message`, which team members can read.
        raise RegistryCredentialError(
            "registry credentials cannot be stored because secret encryption "
            "is not configured on this deployment"
        ) from exc
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

    A row whose ciphertext will not decrypt, or a deployment whose encryption
    key is missing entirely, is skipped with a warning rather than raised: one
    unreadable credential must not stop a scan that may not need it, and the
    pull then fails with a registry error the operator can act on.
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
    except (SecretDecryptionError, SecretEncryptionError):
        # SecretEncryptionError too: `core.crypto` raises it from key
        # derivation, so an unset key in production reaches this call and would
        # otherwise escape into the scan's error_message, telling any team
        # member which environment variable this deployment is missing.
        log.warning(
            "registry_credential_undecryptable",
            organization_id=str(organization_id),
            registry_host=host,
        )
        return {}
    return {host: (row.username, password)}


__all__ = [
    "OrganizationNotFound",
    "RegistryCredentialError",
    "RegistryCredentialView",
    "RegistryNotAllowed",
    "credentials_for_image",
    "delete_credential",
    "list_credentials",
    "normalize_registry_host",
    "upsert_credential",
]
