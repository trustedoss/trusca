"""
API Key + Webhook delivery models — Phase 5 PR #16.

Tables:
  - ``api_keys``           — long-lived bearer credentials issued to CI / users
  - ``webhook_deliveries`` — per-delivery idempotency table (provider + delivery_id
                             unique) so a re-sent GitHub / GitLab webhook is a no-op

Conventions (CLAUDE.md core rules + existing model files):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for every timestamp; ``created_at`` on every row.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - No environment access at import time (CLAUDE.md core rule #11).
  - Cross-domain relationships are one-way (api_key → auth, webhook_delivery → scan).
    The upstream User / Project / Scan rows do not gain back-refs into this module.

Security contract for ``api_keys``:
  - Plaintext key NEVER persists. Format is ``tos_<8-char prefix>_<32-char secret>``;
    the row stores the 12-char ``key_prefix`` (``tos_<prefix>``) and a bcrypt hash
    of the full plaintext. ``core.audit._SENSITIVE_COLUMNS`` masks ``key_hash``
    out of the audit diff (it already includes ``key_hash`` via ``token_hash``-
    family wildcards — we additionally extend the set in core.audit when this
    model lands).
  - Soft-delete: revocation flips ``revoked_at`` / ``revoked_by_user_id`` instead
    of DELETE so that audit trails referencing the key by id remain intact and
    so that a re-issued plaintext cannot be conflated with the original.
  - Scope encoding: ``scope`` is a small closed set ('org', 'team', 'project'),
    encoded as a CHECK-constrained VARCHAR rather than a Postgres ENUM. The
    decision to skip a native ENUM here mirrors how the existing schema treats
    coarse-grained discriminators (e.g. ``scan_artifacts.kind``) where the value
    space is not part of the persistent data contract.

Security contract for ``webhook_deliveries``:
  - The unique index on ``(provider, delivery_id)`` is the canonical "is this
    a duplicate retry?" gate. Service code attempts an INSERT and treats a
    unique-violation as the idempotent ``200 OK`` path. There is no SELECT-then-
    INSERT — it would be a TOCTOU race.
  - ``payload_hash`` (sha256 of the request body) lets investigators correlate
    the delivery row with whatever logged blob landed elsewhere. The body
    itself is NOT stored; webhooks may carry sensitive code identifiers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

API_KEY_SCOPE_VALUES = ("org", "team", "project")
WEBHOOK_PROVIDER_VALUES = ("github", "gitlab")


# ---------------------------------------------------------------------------
# APIKey
# ---------------------------------------------------------------------------


class APIKey(Base):
    """
    Long-lived bearer credential for CI / programmatic clients.

    Plaintext format: ``tos_<8-char prefix>_<32-char url-safe secret>``.

    The 12-char ``key_prefix`` ("tos_<prefix>") is unique and human-displayable
    in the management UI; the full plaintext is stored only as a bcrypt hash
    in ``key_hash``. Verification splits the inbound bearer header into prefix
    + secret, looks up by prefix, then runs ``bcrypt.checkpw`` (constant time)
    against ``key_hash``.

    Scope semantics:
      - ``scope='org'``     → ``team_id`` and ``project_id`` are NULL (issuer
                              must be super_admin).
      - ``scope='team'``    → ``team_id`` is NOT NULL, ``project_id`` is NULL.
      - ``scope='project'`` → ``project_id`` is NOT NULL; ``team_id`` mirrors
                              the project's team.

    The CHECK constraint ``ck_api_keys_scope_consistency`` enforces this at
    the DB layer so a malformed INSERT cannot smuggle a project-scoped key
    that secretly grants org-wide access.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    # Human-displayable prefix (e.g. "tos_abc12345"). Unique so we can index
    # the bearer-header lookup (LEFT(header, 12) → key_prefix) in O(log n).
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)

    # bcrypt hash of the full plaintext. Same cost as user passwords (12).
    # Audit listener masks this column — see ``core.audit._SENSITIVE_COLUMNS``.
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Caller-supplied label, e.g. "ci-prod-deploy".
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Closed set encoded as VARCHAR + CHECK to mirror existing kind columns.
    scope: Mapped[str] = mapped_column(String(16), nullable=False)

    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Issuer — never SET NULL'd by user deletion in practice (admins do not
    # vanish often) but ondelete=SET NULL keeps the row queryable for audit.
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    # Updated best-effort by the auth dependency; useful for "stale key
    # cleanup" sweeps and the management UI.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optional expiry. NULL = never expires (legacy / long-lived keys). When set,
    # authentication rejects the key once now() passes it — so a leaked CI key
    # (pipeline log, forked-PR runner) stops working without manual revocation.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Revocation (soft-delete). A revoked key is invisible to authentication
    # but the row stays for audit / forensic queries.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # Closed scope set.
        CheckConstraint(
            "scope IN ('org', 'team', 'project')",
            name="ck_api_keys_scope_values",
        ),
        # Scope ↔ id consistency.
        # org     : team_id IS NULL AND project_id IS NULL
        # team    : team_id IS NOT NULL AND project_id IS NULL
        # project : project_id IS NOT NULL  (team_id may be set to mirror parent)
        CheckConstraint(
            "("
            "  (scope = 'org'     AND team_id IS NULL AND project_id IS NULL)"
            "  OR (scope = 'team'    AND team_id IS NOT NULL AND project_id IS NULL)"
            "  OR (scope = 'project' AND project_id IS NOT NULL)"
            ")",
            name="ck_api_keys_scope_consistency",
        ),
        Index("ix_api_keys_team_id", "team_id"),
        Index("ix_api_keys_project_id", "project_id"),
        Index("ix_api_keys_created_by_user_id", "created_by_user_id"),
        # Hot path: "list all live keys" — partial index dodges the soft-deleted
        # rows so the auth lookup is bounded by the live set.
        Index(
            "ix_api_keys_active",
            "key_prefix",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# WebhookDelivery
# ---------------------------------------------------------------------------


class WebhookDelivery(Base):
    """
    One inbound webhook delivery from GitHub / GitLab.

    The unique index on ``(provider, delivery_id)`` is the canonical
    idempotency gate: a duplicate retry collides with the existing row and
    the service treats the unique-violation as ``200 OK`` (idempotent no-op).

    ``payload_hash`` is sha256(request body) — investigators can correlate
    the row with whatever the body landed in (forwarded blob storage, audit
    log, etc.). The body itself is NOT stored.

    ``api_key_id`` / ``project_id`` / ``enqueued_scan_id`` are best-effort
    pointers populated when the service can resolve them at receipt time.
    They survive ``ON DELETE SET NULL`` so historical delivery rows do not
    block deletion of the referent.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    # 'github' | 'gitlab'. Closed set, CHECK-constrained.
    provider: Mapped[str] = mapped_column(String(16), nullable=False)

    # GitHub: X-GitHub-Delivery (UUID). GitLab: X-Gitlab-Webhook-UUID.
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # 'push', 'pull_request', 'merge_request', etc. Stored as raw header value
    # for audit/observability — the service applies a whitelist before acting.
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # sha256 hex digest of the request body. 64 chars exactly.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    enqueued_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('github', 'gitlab')",
            name="ck_webhook_deliveries_provider_values",
        ),
        # Idempotency gate: a duplicate retry from the SCM provider must not
        # double-enqueue a scan. The service catches the IntegrityError and
        # returns a 200 OK with ``{"status": "duplicate"}``.
        Index(
            "ix_webhook_deliveries_provider_id",
            "provider",
            "delivery_id",
            unique=True,
        ),
        Index("ix_webhook_deliveries_received_at", "received_at"),
        Index("ix_webhook_deliveries_api_key_id", "api_key_id"),
        Index("ix_webhook_deliveries_project_id", "project_id"),
        Index("ix_webhook_deliveries_enqueued_scan_id", "enqueued_scan_id"),
    )


__all__ = [
    "API_KEY_SCOPE_VALUES",
    "APIKey",
    "WEBHOOK_PROVIDER_VALUES",
    "WebhookDelivery",
]
