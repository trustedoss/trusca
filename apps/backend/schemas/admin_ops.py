"""
Admin operational dashboards — Phase 4 PR #14 schemas.

Pydantic v2 schemas for the operations-facing admin endpoints:

  - Scan Queue    (GET /v1/admin/scans; POST /v1/admin/scans/{id}/cancel)
  - Disk          (GET /v1/admin/disk)
  - Audit Log     (GET /v1/admin/audit; GET /v1/admin/audit/export.csv)
  - System Health (GET /v1/admin/health)

W6-#43a (ADR-0001): the DT Connector sub-router was removed when DT was
replaced by Trivy (W6-#41). All DT-shaped schemas (DTStatusOut, DTOrphanItem,
OrphanCleanupRequest, HealthProbeOut, BreakerResetOut, BreakerState) and the
``dt_volume`` AdminDiskItem name are deleted.

These schemas are deliberately split out of ``schemas/admin.py`` (PR #13's
Users / Teams shapes) so the operational surface evolves independently from
identity management. Both modules are re-exported from ``schemas`` for
ergonomic imports.

Adversarial input notes (memory ``feedback_adversarial_input_parametrize``):
  - ``AdminScanListQuery``: ``status`` is constrained to the closed
    SCAN_STATUS_VALUES enum so an attacker cannot smuggle SQL fragments
    via the filter; Literal[...] becomes a Pydantic enum validator.
  - ``AdminAuditSearchQuery``: ``target_table`` is constrained to a
    closed whitelist of audited domain tables; ``q`` is bounded at 255
    characters. Free-text is parameterized at the SQL layer (the service
    uses bound parameters), but the boundary still rejects oversized /
    obviously-malicious input early.

Closed enums (must stay in sync with ``models.scan.SCAN_STATUS_VALUES``):
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Mirrors models.scan.SCAN_STATUS_VALUES. Keeping this Literal narrows
# Pydantic's accepted values to the closed enum so the URL ``?status=`` filter
# can only ever be one of these five strings — no escape hatches for SQL
# injection / path traversal / typos.
ScanStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

# Closed whitelist of tables we audit. Any other ``target_table`` filter is
# rejected at the boundary. The list mirrors the domain-table set actually
# touched by the audit listener (``core.audit._NON_AUDITED_TABLES`` is the
# negation; this is the positive side).
AuditTargetTable = Literal[
    "users",
    "teams",
    "memberships",
    "organizations",
    "projects",
    "scans",
    "scan_artifacts",
    "components",
    "component_versions",
    "scan_components",
    "vulnerabilities",
    "vulnerability_findings",
    "licenses",
    "license_findings",
    "obligations",
    "refresh_tokens",
    "password_reset_tokens",
    "license_fetch_cache",
]

# System Health component status values.
HealthStatus = Literal["ok", "degraded", "down"]


# ---------------------------------------------------------------------------
# Scan Queue (4.5)
# ---------------------------------------------------------------------------


class AdminScanListItem(BaseModel):
    """One row in the admin scan queue listing — joins scan + project + team."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    team_id: uuid.UUID
    team_name: str
    status: ScanStatus
    kind: str
    progress_percent: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = Field(
        default=None,
        description=(
            "Mapped from ``scans.completed_at``. Renamed for clarity in the "
            "admin queue UI."
        ),
    )
    duration_seconds: float | None = None
    error_message: str | None = None
    requested_by_user_id: uuid.UUID | None = None
    created_at: datetime


class AdminScanListPage(BaseModel):
    """Paginated response of ``GET /v1/admin/scans``."""

    items: list[AdminScanListItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Disk (4.6)
# ---------------------------------------------------------------------------


class AdminDiskItem(BaseModel):
    """
    One mount / volume entry returned by ``GET /v1/admin/disk``.

    ``used_pct`` is computed server-side as ``used_bytes / total_bytes * 100``
    rounded to one decimal so the UI does not need to repeat the math.
    Threshold cells are static configuration (80% warn, 90% critical) for
    Phase 4; future PRs may make them per-mount tunable.
    """

    name: Literal["workspace", "postgres", "redis"]
    path: str | None = Field(
        default=None,
        description=(
            "Filesystem path the bytes were read from (only set for "
            "``workspace`` — DB-backed entries have no single canonical "
            "path)."
        ),
    )
    total_bytes: int | None = Field(default=None, ge=0)
    used_bytes: int = Field(ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    used_pct: float | None = Field(default=None, ge=0, le=100)
    threshold_warning: float = 80.0
    threshold_critical: float = 90.0
    status: HealthStatus = Field(
        description=(
            "ok / degraded / down derived from ``used_pct`` against the "
            "thresholds. ``degraded`` = warning band, ``down`` = critical."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Set when telemetry could not be collected (e.g. mount missing).",
    )


class AdminDiskOut(BaseModel):
    """Response of ``GET /v1/admin/disk``."""

    items: list[AdminDiskItem]
    collected_at: datetime


# ---------------------------------------------------------------------------
# Audit Log (4.7)
# ---------------------------------------------------------------------------


class AuditLogItem(BaseModel):
    """One row in the admin audit log search response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    actor_user_id: uuid.UUID | None = None
    actor_email: str | None = Field(
        default=None,
        description=(
            "Joined from ``users.email`` for display. ``null`` when the actor "
            "was deleted (FK is ``ondelete='SET NULL'``) or the row was "
            "system-initiated."
        ),
    )
    team_id: uuid.UUID | None = None
    target_table: str
    target_id: str | None = None
    action: str
    request_id: str | None = None
    # diff is intentionally returned to the UI but excluded from the CSV
    # export — see service.stream_audit_csv for the export column shape.
    diff: dict[str, Any] | None = None


class AuditLogListPage(BaseModel):
    """Paginated response of ``GET /v1/admin/audit``."""

    items: list[AuditLogItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    has_more: bool


class AuditSearchQuery(BaseModel):
    """
    Validated query parameters for the audit search.

    The router constructs this from FastAPI ``Query(...)`` params; we put the
    field-validators on the model so service-layer unit tests can drive it
    directly with kwargs and adversarial parametrize cases stay one-step
    away from the field they target.

    ``populate_by_name=True`` lets unit tests pass either ``from_`` (the
    Python attr) or ``from`` (the alias) — Pydantic v2 defaults to alias-only
    on ``model_validate``, so the keyword form would otherwise be rejected.
    """

    model_config = ConfigDict(populate_by_name=True)

    actor_user_id: uuid.UUID | None = None
    target_table: AuditTargetTable | None = None
    action: str | None = Field(default=None, max_length=64)
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    q: str | None = Field(default=None, max_length=255)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @field_validator("action")
    @classmethod
    def _strip_action(cls, value: str | None) -> str | None:
        """Strip whitespace and reject empty / control-char inputs.

        Action strings are short ascii-ish identifiers (``create`` /
        ``update`` / ``delete`` / ``revoke`` …). Reject anything that
        contains a NUL or CR/LF — log-injection vectors that have no
        legitimate use here.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if any(c in stripped for c in ("\x00", "\r", "\n")):
            raise ValueError("action must not contain control characters")
        return stripped

    @field_validator("q")
    @classmethod
    def _strip_q(cls, value: str | None) -> str | None:
        """Free-text JSONB search fragment.

        Bound at 255 chars by ``Field(max_length=...)``. We strip + reject
        empty so ``q=`` (zero-length) does not degrade to a full-table
        sequential scan, and we reject NUL / CR / LF for the same reason
        as ``action`` above. SQL escaping happens at the query layer via
        bound parameters; this validator is the boundary, not the only
        defense.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if any(c in stripped for c in ("\x00", "\r", "\n")):
            raise ValueError("q must not contain control characters")
        return stripped


# ---------------------------------------------------------------------------
# System Health (4.8)
# ---------------------------------------------------------------------------


class HealthComponent(BaseModel):
    """One probe in the system-health summary."""

    # W6-#43a: the ``dt`` component was removed alongside the DT integration
    # (ADR-0001). Keeping the Literal in sync with the runtime is the explicit
    # DoD gate of #43a — any future re-add must go through schema review.
    name: Literal[
        "postgres",
        "redis",
        "celery",
        "disk",
        "active_scans",
        "last_24h_errors",
    ]
    status: HealthStatus
    detail: str | None = Field(
        default=None,
        description="Human-readable explanation of the status (1-line).",
    )
    value: float | int | None = Field(
        default=None,
        description=(
            "Numeric reading (e.g. ``celery`` worker count, ``active_scans`` "
            "row count, ``last_24h_errors`` row count). ``null`` for boolean "
            "probes."
        ),
    )


class SystemHealthOut(BaseModel):
    """Response of ``GET /v1/admin/health`` — aggregated probe set."""

    components: list[HealthComponent]
    updated_at: datetime


# ---------------------------------------------------------------------------
# Trivy vulnerability DB status (W6-#43e)
# ---------------------------------------------------------------------------

# Pair the FE badge colour with this closed set so the renderer can ``switch``
# without a default arm. ``unknown`` is the "metadata.json not present yet"
# case — the FE renders the EmptyState instead of a badge.
TrivyDbFreshness = Literal["fresh", "stale", "very_stale", "unknown"]


class TrivyDbStatusOut(BaseModel):
    """
    Response of ``GET /v1/admin/trivy/health`` — W6-#43e.

    Every numeric / temporal field is optional so the "not yet downloaded"
    case can serialise cleanly. The FE keys empty state off ``last_update is
    None`` or ``freshness == "unknown"``.

    Configuration fields (``refresh_interval_hours``, ``cache_dir``,
    ``repository``) are always present — they reflect runtime env, not
    on-disk state, so they survive the no-DB case.
    """

    last_update: datetime | None = Field(
        default=None,
        description=(
            "``UpdatedAt`` field of the on-disk ``$TRIVY_CACHE_DIR/db/"
            "metadata.json``. ``null`` when the DB has not been downloaded "
            "yet (fresh worker boot)."
        ),
    )
    next_refresh_at: datetime | None = Field(
        default=None,
        description=(
            "``last_update + refresh_interval_hours``. ``null`` when "
            "``last_update`` is unknown."
        ),
    )
    vuln_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Total advisories tracked. Best-effort: read from "
            "``metadata.json`` if Trivy populates a count key, otherwise "
            "``null`` — the panel falls back to '—'."
        ),
    )
    db_version: str | None = Field(
        default=None,
        description=(
            "Trivy DB schema descriptor (e.g. ``trivy-db schema v2``). "
            "``null`` before the first download."
        ),
    )
    db_size_bytes: int | None = Field(
        default=None,
        ge=0,
        description="Sum of file sizes inside ``cache_dir/db/`` in bytes.",
    )
    refresh_interval_hours: int = Field(
        ge=1,
        description=(
            "Configured cadence between Trivy DB refreshes. Mirrors "
            "``TRIVY_DB_REFRESH_HOURS`` (default 168h / weekly)."
        ),
    )
    freshness: TrivyDbFreshness = Field(
        description=(
            "``fresh`` (< 7d), ``stale`` (7-14d), ``very_stale`` (> 14d), "
            "or ``unknown`` (DB not yet downloaded)."
        ),
    )
    cache_dir: str = Field(
        description=(
            "Resolved Trivy cache directory the worker reads / writes "
            "against. Useful for operators verifying air-gapped mounts."
        ),
    )
    repository: str = Field(
        description=(
            "OCI repository the worker pulls the DB from. Mirrors "
            "``TRIVY_DB_REPOSITORY`` (default ``ghcr.io/aquasecurity/"
            "trivy-db``)."
        ),
    )


__all__ = [
    "AdminDiskItem",
    "AdminDiskOut",
    "AdminScanListItem",
    "AdminScanListPage",
    "AuditLogItem",
    "AuditLogListPage",
    "AuditSearchQuery",
    "AuditTargetTable",
    "HealthComponent",
    "HealthStatus",
    "ScanStatus",
    "SystemHealthOut",
    "TrivyDbFreshness",
    "TrivyDbStatusOut",
]
