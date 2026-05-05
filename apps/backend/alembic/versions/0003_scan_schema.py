"""scan schema — projects, scans, components, vulnerabilities, licenses, obligations

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-06

Phase: 2
PR: #7
Kind: schema
Forward-only: yes

What:
  - Create 7 ENUM types: project_visibility, scan_kind, scan_status,
    vuln_severity, vuln_finding_status, license_category, license_finding_kind.
  - Create 11 tables (FK dependency order):
      components, component_versions,
      licenses, obligations,
      vulnerabilities,
      projects, scans (with circular FK projects.latest_scan_id → scans.id
                       added at the end via op.create_foreign_key),
      scan_artifacts, scan_components,
      vulnerability_findings, license_findings.
  - Create the explicit FK indexes, compound indexes, and JSONB GIN indexes
    declared in apps/backend/models/scan.py.
  - Create the partial unique index `ix_scans_project_active` (one in-flight
    scan per project) via raw DDL — partial unique indexes need explicit SQL.

Why:
  - Phase 2 task 2.1 (`docs/v2-execution-plan.md` §3.3): the scan domain
    schema is the foundation for PRs #7 (Project / Scan API), #8 (Celery
    pipeline + DT stabilization), and #9 (WebSocket + UI). Anything we miss
    here we have to expand-migrate-contract later.
  - Closed enums (status / severity / kind) become Postgres ENUM types so
    invalid writes are rejected at the DB layer, not just by Pydantic.
  - The denormalized `projects.latest_scan_id` column avoids a per-row
    "max(scans.created_at) WHERE project_id = ?" subquery on the project
    list page — the most-hit screen in the portal.
  - The partial unique index on `scans` enforces the PR #7 contract that a
    project may only have one queued/running scan at a time. Service code can
    rely on the unique-violation as the canonical "scan already in progress"
    signal.

Notes:
  - Forward-only per CLAUDE.md §6: downgrade() raises NotImplementedError.
  - Status ENUM sizing decisions (db-designer agent guide "결정해야 할 사항"):
      * vuln_finding_status: ship the full 7-state set up front
        (new/analyzing/exploitable/not_affected/false_positive/suppressed/fixed)
        rather than 4. ALTER TYPE ADD VALUE is non-transactional in older
        Postgres and we'd rather avoid the dance for one extra mig later.
      * scan_status: keep the canonical 5 (queued/running/succeeded/failed/
        cancelled). `paused` and `timed_out` are not requirements yet — Celery
        timeout simply lands the row as `failed` with error_message set —
        and adding values is cheap if/when we need them.
      * project_visibility: ship both 'team' and 'organization' even though
        the Phase 2 API only writes 'team'. The Phase 3 UI flips the toggle
        with no schema change.
  - Cross-domain FKs (team_id, *_user_id) are columns only — no ORM
    relationship from scan models back into auth — to keep the dependency
    one-way (scan → auth) and avoid touching auth.py.
  - The `metadata` column on `scans` is mapped to the Python attribute
    `scan_metadata` because `metadata` is reserved by DeclarativeBase. The
    underlying DB column name remains `metadata`.
  - The `references` column on `vulnerabilities` keeps the natural name in
    DB; SQLAlchemy maps it to the Python attribute `references` (Python
    accepts it as an identifier).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
EMPTY_JSONB_OBJ = sa.text("'{}'::jsonb")
EMPTY_JSONB_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ENUM types
    # ------------------------------------------------------------------
    op.execute("CREATE TYPE project_visibility AS ENUM ('team', 'organization')")
    op.execute("CREATE TYPE scan_kind AS ENUM ('source', 'container')")
    op.execute(
        "CREATE TYPE scan_status AS ENUM "
        "('queued', 'running', 'succeeded', 'failed', 'cancelled')"
    )
    op.execute(
        "CREATE TYPE vuln_severity AS ENUM "
        "('critical', 'high', 'medium', 'low', 'info', 'unknown')"
    )
    op.execute(
        "CREATE TYPE vuln_finding_status AS ENUM "
        "('new', 'analyzing', 'exploitable', 'not_affected', "
        "'false_positive', 'suppressed', 'fixed')"
    )
    op.execute(
        "CREATE TYPE license_category AS ENUM ('allowed', 'conditional', 'forbidden', 'unknown')"
    )
    op.execute("CREATE TYPE license_finding_kind AS ENUM ('declared', 'concluded', 'detected')")

    project_visibility = postgresql.ENUM(
        "team", "organization", name="project_visibility", create_type=False
    )
    scan_kind = postgresql.ENUM("source", "container", name="scan_kind", create_type=False)
    scan_status = postgresql.ENUM(
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        name="scan_status",
        create_type=False,
    )
    vuln_severity = postgresql.ENUM(
        "critical",
        "high",
        "medium",
        "low",
        "info",
        "unknown",
        name="vuln_severity",
        create_type=False,
    )
    vuln_finding_status = postgresql.ENUM(
        "new",
        "analyzing",
        "exploitable",
        "not_affected",
        "false_positive",
        "suppressed",
        "fixed",
        name="vuln_finding_status",
        create_type=False,
    )
    license_category = postgresql.ENUM(
        "allowed",
        "conditional",
        "forbidden",
        "unknown",
        name="license_category",
        create_type=False,
    )
    license_finding_kind = postgresql.ENUM(
        "declared",
        "concluded",
        "detected",
        name="license_finding_kind",
        create_type=False,
    )

    # ------------------------------------------------------------------
    # components  (no FKs — leaf table)
    # ------------------------------------------------------------------
    op.create_table(
        "components",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("purl", sa.Text(), nullable=False),
        sa.Column("package_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("namespace", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("purl", name="uq_components_purl"),
    )
    op.create_index("ix_components_package_type", "components", ["package_type"])
    op.create_index("ix_components_type_name", "components", ["package_type", "name"])

    # ------------------------------------------------------------------
    # component_versions  (FK → components)
    # ------------------------------------------------------------------
    op.create_table(
        "component_versions",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("component_id", UUID_PK, nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("purl_with_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["component_id"],
            ["components.id"],
            name="fk_component_versions_component_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("purl_with_version", name="uq_component_versions_purl_with_version"),
        sa.UniqueConstraint(
            "component_id", "version", name="uq_component_versions_component_version"
        ),
    )
    op.create_index("ix_component_versions_component_id", "component_versions", ["component_id"])

    # ------------------------------------------------------------------
    # licenses  (no FKs — leaf table)
    # ------------------------------------------------------------------
    op.create_table(
        "licenses",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("spdx_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", license_category, nullable=False),
        sa.Column("is_osi_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_fsf_libre", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "is_deprecated_license_id",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("reference_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("spdx_id", name="uq_licenses_spdx_id"),
    )
    op.create_index("ix_licenses_category", "licenses", ["category"])

    # ------------------------------------------------------------------
    # obligations  (FK → licenses)
    # ------------------------------------------------------------------
    op.create_table(
        "obligations",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("license_id", UUID_PK, nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["license_id"],
            ["licenses.id"],
            name="fk_obligations_license_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("license_id", "kind", name="uq_obligations_license_kind"),
    )
    op.create_index("ix_obligations_license_id", "obligations", ["license_id"])
    op.create_index("ix_obligations_kind", "obligations", ["kind"])

    # ------------------------------------------------------------------
    # vulnerabilities  (no FKs — leaf table)
    # ------------------------------------------------------------------
    op.create_table(
        "vulnerabilities",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("severity", vuln_severity, nullable=False),
        sa.Column("cvss_score", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("cvss_vector", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "references",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_ARR,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("external_id", name="uq_vulnerabilities_external_id"),
    )
    op.create_index("ix_vulnerabilities_severity", "vulnerabilities", ["severity"])
    op.create_index("ix_vulnerabilities_source", "vulnerabilities", ["source"])
    op.create_index(
        "ix_vulnerabilities_severity_modified",
        "vulnerabilities",
        ["severity", "modified_at"],
    )
    op.create_index(
        "ix_vulnerabilities_references_gin",
        "vulnerabilities",
        ["references"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # projects  (FK → teams, users; FK → scans added later for circular ref)
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("team_id", UUID_PK, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("git_url", sa.Text(), nullable=True),
        sa.Column("default_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "visibility",
            project_visibility,
            nullable=False,
            server_default=sa.text("'team'"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", UUID_PK, nullable=True),
        sa.Column("latest_scan_id", UUID_PK, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_projects_team_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_projects_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("team_id", "slug", name="uq_projects_team_slug"),
    )
    op.create_index("ix_projects_team_id", "projects", ["team_id"])
    op.create_index("ix_projects_team_archived", "projects", ["team_id", "archived_at"])
    op.create_index("ix_projects_git_url", "projects", ["git_url"])
    op.create_index("ix_projects_created_by_user_id", "projects", ["created_by_user_id"])
    op.create_index("ix_projects_latest_scan_id", "projects", ["latest_scan_id"])

    # ------------------------------------------------------------------
    # scans  (FK → projects, users)
    # ------------------------------------------------------------------
    op.create_table(
        "scans",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("project_id", UUID_PK, nullable=False),
        sa.Column("kind", scan_kind, nullable=False),
        sa.Column(
            "status",
            scan_status,
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_user_id", UUID_PK, nullable=True),
        sa.Column(
            "progress_percent",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_OBJ,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_scans_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_scans_requested_by_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_scans_project_id", "scans", ["project_id"])
    op.create_index("ix_scans_project_created_at", "scans", ["project_id", "created_at"])
    op.create_index("ix_scans_status", "scans", ["status"])
    op.create_index("ix_scans_celery_task_id", "scans", ["celery_task_id"])
    op.create_index("ix_scans_metadata_gin", "scans", ["metadata"], postgresql_using="gin")
    # Partial unique index: at most one queued/running scan per project. Raw
    # DDL is needed because the SQLAlchemy `Index(..., postgresql_where=...)`
    # form is supported, but emitting it here keeps the migration explicit
    # and matches the docstring contract for PR #7.
    op.execute(
        "CREATE UNIQUE INDEX ix_scans_project_active "
        "ON scans (project_id) "
        "WHERE status IN ('queued', 'running')"
    )

    # ------------------------------------------------------------------
    # projects.latest_scan_id → scans.id  (deferred FK for circular ref)
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_projects_latest_scan_id",
        source_table="projects",
        referent_table="scans",
        local_cols=["latest_scan_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # scan_artifacts  (FK → scans)
    # ------------------------------------------------------------------
    op.create_table(
        "scan_artifacts",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("scan_id", UUID_PK, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_scan_artifacts_scan_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_scan_artifacts_scan_id", "scan_artifacts", ["scan_id"])
    op.create_index("ix_scan_artifacts_scan_kind", "scan_artifacts", ["scan_id", "kind"])

    # ------------------------------------------------------------------
    # scan_components  (FK → scans, component_versions)
    # ------------------------------------------------------------------
    op.create_table(
        "scan_components",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("scan_id", UUID_PK, nullable=False),
        sa.Column("component_version_id", UUID_PK, nullable=False),
        sa.Column("dependency_scope", sa.String(length=32), nullable=True),
        sa.Column("dependency_path", sa.Text(), nullable=True),
        sa.Column("direct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_OBJ,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_scan_components_scan_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["component_version_id"],
            ["component_versions.id"],
            name="fk_scan_components_component_version_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "scan_id",
            "component_version_id",
            "dependency_path",
            name="uq_scan_components_scan_version_path",
        ),
    )
    op.create_index("ix_scan_components_scan_id", "scan_components", ["scan_id"])
    op.create_index(
        "ix_scan_components_component_version_id",
        "scan_components",
        ["component_version_id"],
    )
    op.create_index("ix_scan_components_scan_direct", "scan_components", ["scan_id", "direct"])
    op.create_index(
        "ix_scan_components_raw_data_gin",
        "scan_components",
        ["raw_data"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # vulnerability_findings  (FK → scans, component_versions, vulnerabilities, users)
    # ------------------------------------------------------------------
    op.create_table(
        "vulnerability_findings",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("scan_id", UUID_PK, nullable=False),
        sa.Column("component_version_id", UUID_PK, nullable=False),
        sa.Column("vulnerability_id", UUID_PK, nullable=False),
        sa.Column(
            "status",
            vuln_finding_status,
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column("analysis_state", sa.String(length=32), nullable=True),
        sa.Column("analysis_justification", sa.Text(), nullable=True),
        sa.Column(
            "analysis_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_OBJ,
        ),
        sa.Column("analyst_user_id", UUID_PK, nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_vuln_findings_scan_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["component_version_id"],
            ["component_versions.id"],
            name="fk_vuln_findings_component_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vulnerability_id"],
            ["vulnerabilities.id"],
            name="fk_vuln_findings_vulnerability_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["analyst_user_id"],
            ["users.id"],
            name="fk_vuln_findings_analyst_user_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "scan_id",
            "component_version_id",
            "vulnerability_id",
            name="uq_vuln_findings_scan_version_vuln",
        ),
    )
    op.create_index("ix_vuln_findings_scan_id", "vulnerability_findings", ["scan_id"])
    op.create_index(
        "ix_vuln_findings_component_version_id",
        "vulnerability_findings",
        ["component_version_id"],
    )
    op.create_index(
        "ix_vuln_findings_vulnerability_id",
        "vulnerability_findings",
        ["vulnerability_id"],
    )
    op.create_index(
        "ix_vuln_findings_analyst_user_id",
        "vulnerability_findings",
        ["analyst_user_id"],
    )
    op.create_index(
        "ix_vuln_findings_scan_status",
        "vulnerability_findings",
        ["scan_id", "status"],
    )

    # ------------------------------------------------------------------
    # license_findings  (FK → scans, component_versions, licenses)
    # ------------------------------------------------------------------
    op.create_table(
        "license_findings",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("scan_id", UUID_PK, nullable=False),
        sa.Column("component_version_id", UUID_PK, nullable=False),
        sa.Column("license_id", UUID_PK, nullable=False),
        sa.Column("kind", license_finding_kind, nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_OBJ,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_license_findings_scan_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["component_version_id"],
            ["component_versions.id"],
            name="fk_license_findings_component_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["license_id"],
            ["licenses.id"],
            name="fk_license_findings_license_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "scan_id",
            "component_version_id",
            "license_id",
            "kind",
            "source_path",
            name="uq_license_findings_scan_cv_lic_kind_path",
        ),
    )
    op.create_index("ix_license_findings_scan_id", "license_findings", ["scan_id"])
    op.create_index(
        "ix_license_findings_component_version_id",
        "license_findings",
        ["component_version_id"],
    )
    op.create_index("ix_license_findings_license_id", "license_findings", ["license_id"])
    op.create_index("ix_license_findings_scan_kind", "license_findings", ["scan_id", "kind"])
    op.create_index(
        "ix_license_findings_raw_data_gin",
        "license_findings",
        ["raw_data"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
