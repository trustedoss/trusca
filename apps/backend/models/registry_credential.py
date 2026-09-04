# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Per-organization login for a private container registry (ER3).

Scoped to the organization rather than the team or the project: a private
registry is deployment infrastructure that every team publishing from it
shares. Per-team rows would mean the same secret pasted once per team, and
rotating it would mean hunting for all of them.

``registry_host`` is half the unique key and is what binds a credential to one
registry. The worker writes it into a Docker ``config.json`` ``auths`` map
keyed by that host, so Trivy offers it only when pulling from there; a
credential for one registry is never sent to another.

``password_encrypted`` is Fernet ciphertext from ``core.crypto.encrypt_secret``,
the same mechanism ``github_app_credentials.private_key_encrypted`` and
``projects.git_credential_encrypted`` use. The column name is registered in
``core.audit._SENSITIVE_COLUMNS`` so no add / rotate / delete copies ciphertext
into ``audit_logs.diff``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class RegistryCredential(Base):
    """One organization's login for one container registry."""

    __tablename__ = "registry_credentials"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "registry_host",
            name="uq_registry_credentials_org_host",
        ),
        Index("ix_registry_credentials_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Registry host exactly as it appears in an image reference: ``ghcr.io``,
    #: ``registry.example.com``, ``registry:5000``. Lower-cased on write so a
    #: lookup by the parsed host of an image reference matches.
    registry_host: Mapped[str] = mapped_column(String(255), nullable=False)

    username: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Fernet ciphertext. NEVER the plaintext, and masked in the audit diff.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )


__all__ = ["RegistryCredential"]
