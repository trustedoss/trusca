"""
Scan domain models — Phase 2 PR #7.

Tables: projects, scans, scan_artifacts, components, component_versions,
scan_components, vulnerabilities, vulnerability_findings, licenses,
license_findings, obligations.

Conventions (CLAUDE.md core rules + db-designer agent guide):
  - PostgreSQL only. UUID PKs default to gen_random_uuid() (pgcrypto extension
    enabled in 0002_auth_schema).
  - TIMESTAMPTZ for every timestamp; created_at/updated_at on every mutable row.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - Closed enums use native Postgres ENUM types created in the migration; the
    model binds with `create_type=False` so SQLAlchemy never re-creates them.
  - JSONB filter / containment columns get a GIN index.
  - No environment access at import time (CLAUDE.md core rule #11).

Cross-domain relationships:
  - This module has FK columns referencing `teams.id` and `users.id` (auth
    domain), but does NOT add ORM `relationship()` edges back into auth. We
    keep the dependency one-way (scan → auth) to avoid having to mutate
    `apps/backend/models/auth.py` (which would ripple through mypy + the auth
    integration test contract). Project / Scan therefore expose `team_id` /
    `requested_by_user_id` etc. as plain `Mapped[uuid.UUID]` columns; callers
    that need the Team/User row issue an explicit query.

Latest-scan denormalization:
  - `Project.latest_scan_id` is a deliberate denormalization so listing pages
    can render risk badges without joining scans + ordering by created_at on
    every request. The FK is created in the migration AFTER `scans` exists
    (via op.create_foreign_key) because of the circular FK between projects
    and scans.

Concurrency gate (PR #7 contract):
  - The partial unique index `ix_scans_project_active` (UNIQUE on project_id
    WHERE status IN ('queued','running')) enforces "at most one in-flight
    scan per project" at the DB layer. Service code can rely on a unique-
    violation as the canonical "another scan is running" signal.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")
EMPTY_JSONB_OBJ = text("'{}'::jsonb")
EMPTY_JSONB_ARR = text("'[]'::jsonb")

# Closed enums — encoded as native Postgres ENUM types so invalid values are
# rejected at the DB layer. The migration creates each type; here we bind via
# name= with create_type=False so SQLAlchemy never tries to (re)create them.

PROJECT_VISIBILITY_VALUES = ("team", "organization")
SCAN_KIND_VALUES = ("source", "container", "sbom")
SCAN_STATUS_VALUES = ("queued", "running", "succeeded", "failed", "cancelled")
VULN_SEVERITY_VALUES = ("critical", "high", "medium", "low", "info", "unknown")
# Vulnerability finding status — Phase 2 ships the full 7-state set up front
# (see Notes section in the 0003 migration). Phase 3.4 wires the workflow UI;
# we'd rather declare the full ENUM now than ALTER TYPE ADD VALUE later.
VULN_FINDING_STATUS_VALUES = (
    "new",
    "analyzing",
    "exploitable",
    "not_affected",
    "false_positive",
    "suppressed",
    "fixed",
)
LICENSE_CATEGORY_VALUES = ("allowed", "conditional", "forbidden", "unknown")
LICENSE_FINDING_KIND_VALUES = ("declared", "concluded", "detected")


def _project_visibility_enum() -> PG_ENUM:
    return PG_ENUM(
        *PROJECT_VISIBILITY_VALUES,
        name="project_visibility",
        create_type=False,
    )


def _scan_kind_enum() -> PG_ENUM:
    return PG_ENUM(*SCAN_KIND_VALUES, name="scan_kind", create_type=False)


def _scan_status_enum() -> PG_ENUM:
    return PG_ENUM(*SCAN_STATUS_VALUES, name="scan_status", create_type=False)


def _vuln_severity_enum() -> PG_ENUM:
    return PG_ENUM(*VULN_SEVERITY_VALUES, name="vuln_severity", create_type=False)


def _vuln_finding_status_enum() -> PG_ENUM:
    return PG_ENUM(
        *VULN_FINDING_STATUS_VALUES,
        name="vuln_finding_status",
        create_type=False,
    )


def _license_category_enum() -> PG_ENUM:
    return PG_ENUM(
        *LICENSE_CATEGORY_VALUES,
        name="license_category",
        create_type=False,
    )


def _license_finding_kind_enum() -> PG_ENUM:
    return PG_ENUM(
        *LICENSE_FINDING_KIND_VALUES,
        name="license_finding_kind",
        create_type=False,
    )


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project(Base):
    """
    A scan target owned by a team.

    visibility='team' (default) limits reads to team members; 'organization'
    is reserved for Phase 3+ org-wide projects (the API only writes 'team' in
    Phase 2). archived_at is soft-delete: the row stays for audit/history but
    list pages filter it out.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(
        _project_visibility_enum(),
        nullable=False,
        server_default=text("'team'"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Denormalized pointer to the most recent scan (regardless of status).
    # use_alter=True breaks the projects ↔ scans circular FK so SQLAlchemy
    # emits the constraint via ALTER TABLE after both tables exist (and so
    # `alembic check` recognizes the FK as part of the schema). The migration
    # creates the same constraint via op.create_foreign_key after `scans` is
    # built — see module docstring "Latest-scan denormalization".
    latest_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey(
            "scans.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_latest_scan_id",
        ),
        nullable=True,
    )
    # Phase 5 PR #16 — webhook reception. ``webhook_secret`` stores the
    # plaintext shared secret negotiated with the SCM (GitHub: HMAC key for
    # X-Hub-Signature-256; GitLab: token compared to X-Gitlab-Token). It is
    # 64 chars (cryptographic random urlsafe) and is masked in audit_logs via
    # ``core.audit._SENSITIVE_COLUMNS`` (the ``secret`` token catches it).
    # ``webhook_provider`` is the closed set 'github' | 'gitlab' so the
    # gateway knows which header schema to apply.
    webhook_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    webhook_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Feature #18 Part B (schema half) — private-repo scanning. Stores the
    # Fernet ciphertext of the user-supplied git credential (a PAT / deploy
    # token today; an SSH private key later) used to clone a private repo.
    # This is a REVERSIBLE secret (we must recover the plaintext to inject it
    # into the clone) — mirrors ``github_app_credentials.private_key_encrypted``:
    # the plaintext is NEVER persisted, encryption / decryption lives in
    # ``core.crypto``. Typed ``Text`` because Fernet tokens are urlsafe-base64
    # strings of unbounded length. Nullable — most projects are public / have
    # no credential. Masked in ``audit_logs.diff`` via
    # ``core.audit._SENSITIVE_COLUMNS`` (defence-in-depth: a credential
    # add / rotate / clear UPDATE must never copy ciphertext into the diff).
    # The backend service + clone injection + UI land in later #18-B steps.
    git_credential_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    scans: Mapped[list[Scan]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # Disambiguate against the latest_scan_id FK below.
        foreign_keys="Scan.project_id",
    )

    @property
    def has_git_credential(self) -> bool:
        """True iff a private-repo git credential is configured (#18 Part B).

        Read-only derived flag exposed on ``ProjectPublic.has_git_credential`` so
        the UI can show "credential configured" WITHOUT the schema ever reading
        the ciphertext column. Never exposes the plaintext or the ciphertext —
        only whether one is set.
        """
        return self.git_credential_encrypted is not None

    __table_args__ = (
        UniqueConstraint("team_id", "slug", name="uq_projects_team_slug"),
        Index("ix_projects_team_id", "team_id"),
        # Active-projects list page: WHERE team_id = ? AND archived_at IS NULL
        # ORDER BY updated_at DESC.
        Index("ix_projects_team_archived", "team_id", "archived_at"),
        # Webhook lookup (Phase 5): "find project by clone URL".
        Index("ix_projects_git_url", "git_url"),
        Index("ix_projects_created_by_user_id", "created_by_user_id"),
        Index("ix_projects_latest_scan_id", "latest_scan_id"),
    )


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class Scan(Base):
    """
    One execution of the scan pipeline for a project.

    progress_percent + current_step are updated by the Celery task and pushed
    to the WebSocket gateway in PR #9. metadata holds inputs (git_ref,
    image_ref, scan options) so we can replay a scan from history.
    """

    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(_scan_kind_enum(), nullable=False)
    status: Mapped[str] = mapped_column(
        _scan_status_enum(),
        nullable=False,
        server_default=text("'queued'"),
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    progress_percent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # W6-#42 — watermark for the vulnerability rematch beat. NULL means "never
    # rematched yet"; the beat's due-scan query treats NULL as "due now" (NULLS
    # FIRST in the partial index). Set after each successful Trivy re-run by
    # ``tasks.vulnerability_rematch.rematch_scan_findings``.
    last_rematched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_metadata: Mapped[dict[str, Any]] = mapped_column(
        # Column name in DB is `metadata` — but that attribute clashes with
        # SQLAlchemy's DeclarativeBase.metadata, so we rename the Python
        # attribute and pin the underlying column via name=.
        "metadata",
        JSONB,
        nullable=False,
        server_default=EMPTY_JSONB_OBJ,
    )
    # DT-style ref-keyed retention (scan-retention). Normalized git ref this
    # scan targets — ``refs/heads/main`` → ``main``, ``refs/pull/12/merge`` →
    # ``pr-12`` (see ``services.scan_service.normalize_ref``). NULL when the
    # trigger carried no ref (e.g. manual ad-hoc scans). Stamped at scan-create
    # time from ``metadata.ref`` so the retire query is index-driven instead of
    # re-normalizing a JSONB blob per row. Webhook and CI triggers must both
    # feed the same normalizer so a branch's scans converge on one ref value.
    ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # When a newer succeeded scan for the same (project_id, ref) supersedes this
    # one, ``superseded_at`` is stamped and ``superseded_by_scan_id`` points at
    # the winner. Superseded scans are hidden from releases / list enrichment /
    # rematch and reclaimed by the ``scan_retention`` beat after a grace period.
    # Scans carrying an explicit ``metadata.release`` label are never superseded.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_by_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    project: Mapped[Project] = relationship(
        back_populates="scans",
        foreign_keys=[project_id],
    )
    artifacts: Mapped[list[ScanArtifact]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )
    scan_components: Mapped[list[ScanComponent]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )
    vulnerability_findings: Mapped[list[VulnerabilityFinding]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )
    license_findings: Mapped[list[LicenseFinding]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_scans_project_id", "project_id"),
        # Project-history view (project detail page).
        Index("ix_scans_project_created_at", "project_id", "created_at"),
        # Admin queue dashboard ("show me everything queued/running").
        Index("ix_scans_status", "status"),
        Index("ix_scans_celery_task_id", "celery_task_id"),
        # JSONB GIN — supports `metadata @> '{...}'` (e.g. "find scans of branch X").
        Index("ix_scans_metadata_gin", "metadata", postgresql_using="gin"),
        # Concurrency gate: at most one scan per project may be queued or
        # running at any time. Mirrored in the migration via op.execute()
        # because partial unique indexes need explicit DDL.
        Index(
            "ix_scans_project_active",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        # W6-#42 — rematch beat's "due succeeded scans" hot path. Partial on
        # status='succeeded' keeps the index proportional to the eligible
        # cohort; NULLS FIRST (default for ASC) surfaces never-rematched scans
        # at the front so the first beat after deploy fans them out first.
        # scan-retention: also exclude superseded scans — a superseded snapshot
        # has a newer winner for the same ref, so re-matching it would waste work
        # and could fire stale notifications.
        Index(
            "ix_scans_rematch_due",
            "last_rematched_at",
            postgresql_where=text("status = 'succeeded' AND superseded_at IS NULL"),
        ),
        # scan-retention: ref-keyed retire lookup — "the prior succeeded scan(s)
        # for this (project, ref)". Partial on status='succeeded' keeps it small.
        Index(
            "ix_scans_project_ref",
            "project_id",
            "ref",
            postgresql_where=text("status = 'succeeded'"),
        ),
        # scan-retention: the beat's "superseded past grace" sweep. Partial on
        # the stamped column keeps the index proportional to the reclaim cohort.
        Index(
            "ix_scans_superseded",
            "superseded_at",
            postgresql_where=text("superseded_at IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# ScanArtifact
# ---------------------------------------------------------------------------


class ScanArtifact(Base):
    """
    Pointer to a file produced by the scan pipeline (SBOM, ORT report, Trivy
    JSON, ...). The bytes live on disk under WORKSPACE_HOST_PATH; the row
    only carries path + integrity metadata.
    """

    __tablename__ = "scan_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    scan: Mapped[Scan] = relationship(back_populates="artifacts")

    __table_args__ = (
        Index("ix_scan_artifacts_scan_id", "scan_id"),
        # Hot path: "give me the cyclonedx_json for this scan".
        Index("ix_scan_artifacts_scan_kind", "scan_id", "kind"),
    )


# ---------------------------------------------------------------------------
# Component / ComponentVersion (cross-project package catalog)
# ---------------------------------------------------------------------------


class Component(Base):
    """A package identity (PURL without version), shared across projects."""

    __tablename__ = "components"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    purl: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    package_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    namespace: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    versions: Mapped[list[ComponentVersion]] = relationship(
        back_populates="component", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_components_package_type", "package_type"),
        # Search: "find all npm packages whose name starts with foo".
        Index("ix_components_type_name", "package_type", "name"),
    )


class ComponentVersion(Base):
    """A specific version of a component. Vulnerabilities/licenses bind here."""

    __tablename__ = "component_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("components.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(255), nullable=False)
    purl_with_version: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    component: Mapped[Component] = relationship(back_populates="versions")

    __table_args__ = (
        # W8-#46 (2026-05-27): dropped redundant UNIQUE on (component_id, version).
        # purl_with_version is the natural key and is already UNIQUE on the
        # column itself — the dropped constraint collapsed Maven artefacts that
        # share (group, artifact, version) but differ in classifier qualifier
        # (e.g. com.github.jnr/jffi@1.3.1 vs same?classifier=native). See
        # alembic/versions/0027_drop_redundant_component_version_unique.py.
        Index("ix_component_versions_component_id", "component_id"),
    )


# ---------------------------------------------------------------------------
# ScanComponent (Scan ↔ ComponentVersion)
# ---------------------------------------------------------------------------


class ScanComponent(Base):
    """
    A component version observed in a particular scan, with cdxgen-derived
    metadata (scope, dependency path, direct vs transitive).
    """

    __tablename__ = "scan_components"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("component_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    dependency_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dependency_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    direct: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # v2.2 2.2-a2 — shortest-path distance from a graph root (the scanned
    # project / a top-level direct dependency). Direct deps are depth ``1``,
    # transitive ``2+``. NULL when the cdxgen SBOM carried no ``dependencies``
    # graph (older scans, ecosystems where only a flat component list was
    # produced) — i.e. "depth not computed", distinct from a real depth value.
    # SmallInteger (max 32767) is ample: integrations.dependency_graph clamps
    # depth at MAX_DEPTH (64) so a hostile deep chain can never overflow it.
    # Read by 2.2-a3 (upgrade recommendation) to prioritise direct/shallow deps.
    depth: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_OBJ
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    scan: Mapped[Scan] = relationship(back_populates="scan_components")
    component_version: Mapped[ComponentVersion] = relationship()

    __table_args__ = (
        # Same (component, version) can legitimately appear at multiple
        # dependency paths in a single scan (diamond dependencies, monorepos).
        UniqueConstraint(
            "scan_id",
            "component_version_id",
            "dependency_path",
            name="uq_scan_components_scan_version_path",
        ),
        Index("ix_scan_components_scan_id", "scan_id"),
        Index("ix_scan_components_component_version_id", "component_version_id"),
        # "Show direct dependencies for this scan" — a default UI tab.
        Index("ix_scan_components_scan_direct", "scan_id", "direct"),
        # v2.2 2.2-a2 — "shallowest dependencies first" for the upgrade
        # recommender (2.2-a3): order this scan's components by graph depth.
        Index("ix_scan_components_scan_depth", "scan_id", "depth"),
        Index(
            "ix_scan_components_raw_data_gin",
            "raw_data",
            postgresql_using="gin",
        ),
    )


# ---------------------------------------------------------------------------
# ComponentDependencyEdge (cdxgen dependency graph, v2.2 2.2-a2)
# ---------------------------------------------------------------------------


class ComponentDependencyEdge(Base):
    """
    One ``parent dependsOn child`` edge of a scan's resolved dependency graph,
    extracted from the cdxgen CycloneDX ``dependencies`` array (v2.2 2.2-a2).

    Why a dedicated edge table (vs. only ``raw_data``):
      * The graph is the input to 2.2-a3 (upgrade recommendation): "if I bump
        component X, which dependents are impacted?" is a parent/child query
        that a normalized, indexed edge table answers cheaply, whereas a
        per-component ``raw_data.dependsOn`` blob would force a full JSONB scan.
      * Both endpoints are real ``component_versions`` rows (the cdxgen ref →
        component_version mapping is resolved at ingest), so reverse lookups
        ("who depends on this cv?") and forward lookups both ride FK indexes.

    Edges whose parent OR child ref does not resolve to a component_version in
    this scan (dangling refs, the scanned project's own metadata component) are
    NOT stored — only edges between two persisted components survive. ``depth``
    is recorded on ``ScanComponent`` instead; this table is the raw adjacency.

    Scoped by ``scan_id`` (ON DELETE CASCADE) so a re-run / scan delete reclaims
    every edge with the scan, matching ``ScanComponent``'s lifecycle.
    """

    __tablename__ = "component_dependency_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_component_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("component_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    child_component_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("component_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        # An edge is unique per (scan, parent, child); a re-run that re-inserts
        # the same graph collapses onto the same rows (idempotency).
        UniqueConstraint(
            "scan_id",
            "parent_component_version_id",
            "child_component_version_id",
            name="uq_dep_edges_scan_parent_child",
        ),
        Index("ix_dep_edges_scan_id", "scan_id"),
        # Forward ("children of X in this scan") and reverse ("dependents of Y
        # in this scan") traversals for 2.2-a3 each ride a composite index.
        Index("ix_dep_edges_scan_parent", "scan_id", "parent_component_version_id"),
        Index("ix_dep_edges_scan_child", "scan_id", "child_component_version_id"),
    )


# ---------------------------------------------------------------------------
# Vulnerability / VulnerabilityFinding
# ---------------------------------------------------------------------------


class Vulnerability(Base):
    """A CVE/GHSA/OSV record synced from DT or NVD. Cross-scan, cross-project."""

    __tablename__ = "vulnerabilities"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(_vuln_severity_enum(), nullable=False)
    cvss_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    # EPSS (Exploit Prediction Scoring System) — a CVE-level attribute, NOT
    # per-finding. Both probability and percentile live on (0, 1]. Collected
    # from DT findings (v2.1 "EPSS UI first-class"). Nullable because older DT
    # versions / CVEs without an EPSS publication leave these unset; they are
    # backfilled by re-scan / DT resync (a separate data migration), so this
    # schema revision lands them as NULL.
    #   Numeric(6, 5): scale 5 preserves EPSS's published precision
    #   (e.g. 0.00042, 0.97123); precision 6 admits the closed-set max 1.00000.
    epss_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    epss_percentile: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    # CISA KEV (Known Exploited Vulnerabilities) — a CVE-level attribute, NOT
    # per-finding (same shape as EPSS above). ``kev`` flags membership in the
    # CISA KEV catalog and is periodically refreshed by
    # tasks/kev_catalog_refresh; it defaults to false so existing / unmatched
    # CVEs need no backfill. ``kev_date_added`` (catalog listing date) and
    # ``kev_due_date`` (CISA remediation due date, kept for the SLA follow-up
    # feature) are nullable because they only exist for listed CVEs.
    kev: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    kev_date_added: Mapped[date | None] = mapped_column(Date, nullable=True)
    kev_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cvss_vector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Python attr renamed to `references_` because `references` is a Python
    # soft-keyword via the column referencing pattern; SQLAlchemy is fine but
    # we keep the column name `references` in DB.
    references: Mapped[list[Any]] = mapped_column(
        "references",
        JSONB,
        nullable=False,
        server_default=EMPTY_JSONB_ARR,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        Index("ix_vulnerabilities_severity", "severity"),
        Index("ix_vulnerabilities_source", "source"),
        # "Newly modified critical CVEs" → dashboard widget.
        Index("ix_vulnerabilities_severity_modified", "severity", "modified_at"),
        # Sort the vulnerability list by EPSS (v2.1 "EPSS UI first-class").
        # A plain b-tree is enough: NULL ordering (NULLS LAST) is handled in
        # the API query, and Postgres b-tree indexes scan in either direction.
        Index("ix_vulnerabilities_epss_score", "epss_score"),
        # KEV rows are a tiny minority (the CISA catalog is ~1600 CVEs) of a
        # cross-project, cross-scan table, so a PARTIAL index on the true rows
        # serves the "KEV only" filter / count without indexing every false row.
        Index("ix_vulnerabilities_kev", "kev", postgresql_where=text("kev")),
        Index(
            "ix_vulnerabilities_references_gin",
            "references",
            postgresql_using="gin",
        ),
    )


class VulnerabilityFinding(Base):
    """
    A specific component version in a specific scan was found vulnerable to
    a specific CVE. Carries the analysis state machine that Phase 3.4
    (vulnerability triage UI) drives.
    """

    __tablename__ = "vulnerability_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("component_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    vulnerability_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        _vuln_finding_status_enum(),
        nullable=False,
        server_default=text("'new'"),
    )
    # Fix version for THIS (component_version × vulnerability) pairing — v2.2
    # 2.2-a1. Per-finding (not CVE-level) because the same CVE is patched at
    # different versions across packages, and the same package is patched at
    # different versions for different CVEs. Collected from DT findings by
    # ``tasks.scan_source._extract_fixed_version`` (untrusted string →
    # validated/normalized before persistence). NULL means "no fix version is
    # known" (legacy rows pre-0017, or DT reported none). VARCHAR(255) mirrors
    # ``component_versions.version`` so any observed version fits; the collector
    # caps the value well below this width.
    fixed_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Reachability signal (v2.3 r1, migration 0022). Best-effort enrichment
    # written by ``tasks.scan_reachability`` via a static call-graph analyser
    # (Go ``govulncheck`` today). Tri-state on purpose — the three states are
    # semantically distinct and downstream (r2 sort / gate / UI badge) MUST keep
    # them apart:
    #   NULL  → not analysed (no reachability run, or this finding's package was
    #           out of the analyser's language/scope). Default for every row.
    #   True  → the vulnerable symbol is reachable on the project's call graph.
    #   False → the analyser ran and concluded the symbol is NOT reachable.
    # A NOT-NULL DEFAULT FALSE would wrongly merge "unknown" with "unreachable",
    # so the column is NULLable (expand step; no backfill — see 0022 docstring).
    reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Which analyser produced ``reachable`` ('govulncheck' today). Free TEXT-ish
    # VARCHAR(64) — adding a future analyser needs no ALTER TYPE. NULL when
    # ``reachable`` is NULL.
    reachability_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the reachability signal was last written. NULL until a run touches the
    # row; lets r2 distinguish a stale signal from a never-run one.
    reachability_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    analysis_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_OBJ
    )
    # Provenance of the last status mutation (v2.1 A2 — VEX import). NULL on
    # legacy rows and treated as 'manual' by the API; 'vex_import' marks a
    # status that was auto-transitioned by an uploaded VEX document. Free TEXT
    # (not an ENUM) so adding a future source needs no ALTER TYPE migration.
    analysis_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provenance of the *document* that drove a VEX import: doc @id /
    # serialNumber, author, document timestamp, and the VEX status the
    # statement carried. JSONB so the two VEX dialects' differing shapes live
    # in one nullable field; NULL for manual transitions.
    vex_origin: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    analyst_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    scan: Mapped[Scan] = relationship(back_populates="vulnerability_findings")
    component_version: Mapped[ComponentVersion] = relationship()
    vulnerability: Mapped[Vulnerability] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "scan_id",
            "component_version_id",
            "vulnerability_id",
            name="uq_vuln_findings_scan_version_vuln",
        ),
        Index("ix_vuln_findings_scan_id", "scan_id"),
        Index("ix_vuln_findings_component_version_id", "component_version_id"),
        Index("ix_vuln_findings_vulnerability_id", "vulnerability_id"),
        Index("ix_vuln_findings_analyst_user_id", "analyst_user_id"),
        # "Show open findings for this scan" — list view default filter.
        Index("ix_vuln_findings_scan_status", "scan_id", "status"),
        # Reachable-only hot path (v2.3 r2, migration 0023). PARTIAL index keyed
        # on ``reachable IS TRUE`` so the "show reachable-only" list filter, the
        # ``sort=reachable`` ranking, and the gate's reachable-critical count
        # range-scan a single scan's (small) set of reachable findings instead of
        # seq-scanning every finding. Most rows are NULL ("not analysed"), so a
        # partial index keeps the structure tiny. See the migration docstring for
        # why FALSE is deliberately NOT indexed.
        Index(
            "ix_vuln_findings_reachable",
            "scan_id",
            postgresql_where=text("reachable IS TRUE"),
        ),
    )


# ---------------------------------------------------------------------------
# License / LicenseFinding / Obligation
# ---------------------------------------------------------------------------


class License(Base):
    """
    SPDX license catalog + ORT classification result.

    spdx_id is unique-but-nullable: ORT may emit custom licenses (LicenseRef-*)
    that have no SPDX identifier. category is the ORT ruleset's verdict
    (allowed / conditional / forbidden / unknown).
    """

    __tablename__ = "licenses"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    spdx_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(_license_category_enum(), nullable=False)
    is_osi_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_fsf_libre: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_deprecated_license_id: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    reference_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    obligations: Mapped[list[Obligation]] = relationship(
        back_populates="license", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_licenses_category", "category"),)


class LicenseFinding(Base):
    """
    A scan observed a (component_version, license) pairing. ORT classifies
    licenses as declared (from package metadata) vs concluded (its final
    verdict) vs detected (raw scanner output) — we keep all three.
    """

    __tablename__ = "license_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("component_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("licenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(_license_finding_kind_enum(), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_OBJ
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    scan: Mapped[Scan] = relationship(back_populates="license_findings")
    component_version: Mapped[ComponentVersion] = relationship()
    license: Mapped[License] = relationship()

    __table_args__ = (
        # The same (component, license) can be reported from multiple files
        # (LICENSE, README, package.json) — kind + source_path disambiguate.
        UniqueConstraint(
            "scan_id",
            "component_version_id",
            "license_id",
            "kind",
            "source_path",
            name="uq_license_findings_scan_cv_lic_kind_path",
        ),
        Index("ix_license_findings_scan_id", "scan_id"),
        Index("ix_license_findings_component_version_id", "component_version_id"),
        Index("ix_license_findings_license_id", "license_id"),
        Index("ix_license_findings_scan_kind", "scan_id", "kind"),
        Index(
            "ix_license_findings_raw_data_gin",
            "raw_data",
            postgresql_using="gin",
        ),
    )


class Obligation(Base):
    """
    A duty arising from a license (e.g. attribution, source disclosure).
    Phase 3.6 will use this catalog to render NOTICE files automatically.
    """

    __tablename__ = "obligations"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("licenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    license: Mapped[License] = relationship(back_populates="obligations")

    __table_args__ = (
        UniqueConstraint("license_id", "kind", name="uq_obligations_license_kind"),
        Index("ix_obligations_license_id", "license_id"),
        Index("ix_obligations_kind", "kind"),
    )
