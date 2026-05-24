"""
License policy model — v2.2 (Track C — c1 "per-team/org dynamic license policy").

Table:
  - ``license_policies`` — one row per (organization, team) scope describing how
    SPDX license identifiers map to the ``allowed | conditional | forbidden``
    risk categories, plus per-policy gate posture for uncatalogued / compound
    licenses and an explicit exception allow-list.

Why this exists:
  Today the policy gate (``services.policy_gate``) classifies licenses against a
  STATIC catalog baked into ``tasks/scan_source.py`` (``_LICENSE_CATEGORY_DEFAULTS``
  + ``_classify_license_category``). That catalog is global and immutable at
  runtime, so a team that wants (e.g.) to treat ``MPL-2.0`` as ``forbidden`` —
  or to grant a time-boxed exception for a single ``GPL-3.0`` dependency under
  legal review — cannot. This table makes the classification *data*, scoped per
  team and per org, so c2 can override the static path without a redeploy.

  c1 (THIS migration / PR) ships the DATA MODEL + CRUD API only. The gate wiring
  (consulting this table instead of the static catalog) and the SPDX
  adversarial-compound hardening are c2 — ``services.policy_gate`` is NOT touched
  here.

Scope semantics:
  - ``team_id IS NULL``      → the ORGANIZATION-LEVEL DEFAULT policy. At most one
                               such row per organization (partial unique index
                               ``uq_license_policies_org_default``). Applies to
                               every team in the org that has no team-level policy.
  - ``team_id IS NOT NULL``  → that specific team's policy. When present + enabled
                               it takes precedence over the org default for that
                               team. ``UniqueConstraint(organization_id, team_id)``
                               keeps it one-per-team.

  c2 effective-policy resolution (documented for the consumer):
      team policy (if present AND enabled)
        else org default policy (if present AND enabled)
          else None  → caller falls back to the static catalog.

Category string values:
  The category space is EXACTLY the ``License.category`` enum values used
  everywhere else: ``allowed | conditional | forbidden | unknown``. We reuse the
  same strings rather than minting a new vocabulary so c2's resolver can compare
  policy verdicts against the persisted ``licenses.category`` without translation.

  ``unknown`` is intentionally NOT a valid *override* target nor a valid
  ``unknown_license_category`` value: a policy says how an uncatalogued license
  should be TREATED (allowed/conditional/forbidden), it never re-labels it back
  to ``unknown``. The CHECK on ``unknown_license_category`` enforces the 3-value
  posture set; ``category_overrides`` value validation lives in the Pydantic
  layer (``schemas.license_policy``) since JSONB cannot CHECK individual values.

JSONB column shapes (the contract c2 / c3 will consume):

  ``category_overrides`` — object, SPDX id → category::
      {
        "MPL-2.0": "forbidden",
        "EPL-2.0": "conditional",
        "MIT": "allowed"
      }
    An override REPLACES the static catalog verdict for that exact SPDX id.
    Keys are case-sensitive SPDX short identifiers; values are one of
    ``allowed | conditional | forbidden``.

  ``license_exceptions`` — array of objects, each an explicit allow regardless
    of category (e.g. a legal-approved, time-boxed waiver)::
      [
        {
          "spdx_id": "GPL-3.0-only",
          "reason": "legal-approved waiver TICKET-123",
          "expires_at": "2026-12-31T00:00:00Z",
          "component_purl": "pkg:pypi/somepkg@1.2.3"
        }
      ]
    ``spdx_id`` is required. ``reason`` is required free text. ``expires_at`` is
    an optional RFC 3339 timestamp (``null`` = no expiry); c2 treats an expired
    exception as absent. ``component_purl`` is optional — when present the
    exception is scoped to that single component (purl), otherwise it applies to
    any component carrying ``spdx_id``. An exception forces the effective
    category to ``allowed`` for the matched license.

  ``compound_operator_strategy`` — object describing how c2 resolves a COMPOUND
    SPDX expression (``A AND B``, ``A OR B``, ``A WITH exc``)::
      {
        "AND": "most_restrictive",
        "OR":  "least_restrictive",
        "WITH":"most_restrictive"
      }
    Values are ``most_restrictive`` | ``least_restrictive``. The default mirrors
    the static classifier's current behaviour (``_classify_license_category``
    keeps the most restrictive sub-license across AND/OR/WITH). Surfacing it as
    data lets a team relax ``OR`` to least-restrictive (the common, legally
    sound reading of a dual-licensed dependency). c1 PERSISTS this; c2 USES it.

Conventions (CLAUDE.md core rules + existing model files — scan.py / api_key.py):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for ``created_at`` / ``updated_at`` with ``now()`` server default.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - JSONB server defaults via ``text("'{}'::jsonb")`` / ``text("'[]'::jsonb")``.
  - Closed posture set encoded as CHECK-constrained VARCHAR (mirrors api_keys.scope)
    rather than a native ENUM — the value space is not part of the persistent data
    contract and CHECK is cheaper to evolve than ALTER TYPE.
  - No environment access at import time (CLAUDE.md core rule #11).
  - Cross-domain relationships are one-way (license_policy → auth). We expose
    ``organization_id`` / ``team_id`` / ``created_by_user_id`` as plain
    ``Mapped[uuid.UUID]`` columns; no back-refs into the auth module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")
EMPTY_JSONB_OBJ = text("'{}'::jsonb")
EMPTY_JSONB_ARR = text("'[]'::jsonb")

# The 3-value gate posture set (a strict subset of License.category — ``unknown``
# is deliberately excluded: a policy assigns a concrete posture, it never relabels
# a license back to "unknown").
POLICY_CATEGORY_VALUES = ("allowed", "conditional", "forbidden")

# Default compound-operator resolution. Mirrors the static classifier's current
# "keep the most restrictive" behaviour across every operator. c2 reads this.
DEFAULT_COMPOUND_OPERATOR_STRATEGY = (
    """'{"AND": "most_restrictive", """
    """"OR": "least_restrictive", """
    """"WITH": "most_restrictive"}'::jsonb"""
)


# ---------------------------------------------------------------------------
# LicensePolicy
# ---------------------------------------------------------------------------


class LicensePolicy(Base):
    """
    Per-organization / per-team dynamic license policy.

    Exactly one of two scopes per row:
      - ``team_id IS NULL``     → org-level DEFAULT (≤ 1 per org).
      - ``team_id IS NOT NULL`` → that team's policy (≤ 1 per team).

    See the module docstring for the full JSONB shape contract and the c2
    effective-policy resolution order.
    """

    __tablename__ = "license_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    # Owning org — every policy belongs to exactly one organization. CASCADE so
    # deleting an org reclaims its policies.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL → org default; non-NULL → that team's policy. CASCADE so deleting a
    # team reclaims its policy.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Caller-supplied label, e.g. "Engineering default". Optional — the scope
    # (org/team) is the identity; ``name`` is purely for the UI.
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Object: SPDX id → category ("allowed"|"conditional"|"forbidden"). An entry
    # REPLACES the static catalog verdict for that exact id. Value validation
    # lives in the Pydantic layer (JSONB cannot CHECK individual values).
    category_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_OBJ
    )

    # Array of {"spdx_id","reason","expires_at"?,"component_purl"?}. An exception
    # forces the matched license to ``allowed`` regardless of category. See the
    # module docstring for the full shape.
    license_exceptions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_ARR
    )

    # Gate posture for licenses absent from both the override map and the static
    # catalog. One of allowed|conditional|forbidden. Default ``conditional`` —
    # an uncatalogued license should be reviewed, not silently allowed/blocked.
    unknown_license_category: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'conditional'")
    )

    # Object: operator ("AND"|"OR"|"WITH") → "most_restrictive"|"least_restrictive".
    # c2 reads this to resolve compound SPDX expressions. c1 only persists it.
    compound_operator_strategy: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(DEFAULT_COMPOUND_OPERATOR_STRATEGY),
    )

    # Master toggle. ``False`` → c2 treats the policy as absent for resolution
    # (falls back to the next scope / static catalog) without deleting the row,
    # so a team can disable dynamic policy and re-enable it without re-authoring.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Author — SET NULL on user delete keeps the policy row (and its audit
    # trail) intact when the creating user is removed.
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

    __table_args__ = (
        # One policy per (org, team) pair. For the org-default row team_id is
        # NULL — Postgres treats NULLs as distinct in a UniqueConstraint, so this
        # does NOT enforce single-org-default by itself; the partial unique index
        # below does. Both are intentional and complementary.
        UniqueConstraint(
            "organization_id",
            "team_id",
            name="uq_license_policies_org_team",
        ),
        # Single org-default per org: enforce uniqueness of organization_id over
        # the subset of rows where team_id IS NULL (the org-default rows).
        Index(
            "uq_license_policies_org_default",
            "organization_id",
            unique=True,
            postgresql_where=text("team_id IS NULL"),
        ),
        # FK lookup indexes (Postgres does not auto-create them).
        Index("ix_license_policies_team_id", "team_id"),
        Index("ix_license_policies_created_by_user_id", "created_by_user_id"),
        # Closed posture set backstop at the DB layer.
        CheckConstraint(
            "unknown_license_category IN ('allowed', 'conditional', 'forbidden')",
            name="ck_license_policies_unknown_category_values",
        ),
    )


__all__ = [
    "DEFAULT_COMPOUND_OPERATOR_STRATEGY",
    "LicensePolicy",
    "POLICY_CATEGORY_VALUES",
]
