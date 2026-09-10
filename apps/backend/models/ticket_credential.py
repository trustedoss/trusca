# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Per-organization login for reading an external ticket's status back (#385).

Same shape as :class:`RegistryCredential` and for the same reason: a ticket
tracker is shared infrastructure (one Jira site per company, not one per
team), so a per-team row would mean the same token pasted once per team and
rotating it would mean finding all of them.

``host`` binds a credential to one tracker instance (``mycompany.atlassian
.net``), parsed from a finding's ``ticket_url`` at read time; a credential
for one host is never sent to another.

``auth_scheme`` is a discriminator because trackers do not share one auth
shape: Jira Cloud is HTTP Basic with the account email as username and an
API token as password; a future GitHub Issues / GitLab adapter would be a
bearer token with no username at all. ``username`` is therefore nullable
(NULL for a bearer-token scheme), and ``api_token_encrypted`` always holds
the secret half regardless of scheme.

``api_token_encrypted`` is Fernet ciphertext from ``core.crypto.
encrypt_secret``, the same mechanism ``registry_credentials.
password_encrypted`` uses. The column name is registered in
``core.audit._SENSITIVE_COLUMNS`` so no add / rotate / delete copies
ciphertext into ``audit_logs.diff``.
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

#: The only scheme an adapter exists for today. Kept as a plain string
#: column (not a native enum) rather than a Python-level closed set here,
#: matching ``kev_sync_state.last_result``'s convention elsewhere in this
#: codebase: the adapter registry in ``integrations.ticket_status`` is
#: the enforcement point, this column just records what it chose.
DEFAULT_AUTH_SCHEME = "jira_basic"


class TicketCredential(Base):
    """One organization's login for one external ticket tracker."""

    __tablename__ = "ticket_credentials"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "host",
            name="uq_ticket_credentials_org_host",
        ),
        Index("ix_ticket_credentials_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Tracker host exactly as it appears in a finding's `ticket_url`:
    #: `mycompany.atlassian.net`. Lower-cased on write so the read-time
    #: lookup (by the parsed host of that URL) matches.
    host: Mapped[str] = mapped_column(String(255), nullable=False)

    auth_scheme: Mapped[str] = mapped_column(String(32), nullable=False)

    #: NULL for an auth scheme that has no username half (a bearer token).
    #: For `jira_basic`, the Jira Cloud account's login email.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Fernet ciphertext. NEVER the plaintext, and masked in the audit diff.
    api_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

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


__all__ = ["DEFAULT_AUTH_SCHEME", "TicketCredential"]
