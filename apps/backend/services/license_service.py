"""
License catalog services — Phase 3 PR #12 (Licenses tab + drawer).

Two top-level entry points, each invoked from the matching router endpoint:

- :func:`list_project_licenses`
- :func:`get_license_finding_detail`

Why a new module?
-----------------
``services/project_detail_service.py`` owns project-level aggregation reads
that span Overview and Components, where licenses appear as a column on a
per-component row. The Licenses tab is a different read shape — *one row per
distinct license in the latest scan*, with a per-license drawer — and the
detail endpoint is keyed by ``license_findings.id``, not the project. Keeping
that read in its own module mirrors PR #11's split between
``project_detail_service`` and ``vulnerability_service``.

Read-only by design
-------------------
License findings carry no analyst workflow: ORT's ruleset is the authoritative
classifier, so categories (allowed / conditional / forbidden / unknown) and
kinds (declared / concluded / detected) are immutable once persisted. There is
no PATCH endpoint, no transition matrix, and no ``if_match`` token — the
drawer is a pure read.

Authorization
-------------
- List: ``ProjectForbidden`` (403) on cross-team. Existence of a project is
  not a secret across teams (PR #10 pattern).
- Detail: ``LicenseFindingNotFound`` (404) on cross-team — a license_finding
  row is keyed by an opaque UUID, so we existence-hide cross-team reads in
  the same way the vulnerability and component drawers do (PR #10 / PR #11).

Both code paths emit a ``log.warning("authz.cross_team_attempt", ...)``
*before* raising so SOC tooling sees the rejection regardless of which HTTP
status the caller observes.

Search safety
-------------
User-supplied ``search`` is run through :func:`core.sql_safety.escape_like`
and compared with an explicit ESCAPE clause so attackers cannot collapse
the filter to "match everything" with bare ``%`` / ``_`` characters.

Aggregation only — no denormalization
-------------------------------------
Distribution counts and ``affected_count`` are computed at query time. We do
not add a new ``ort_rule_id`` column or a precomputed ``license_summary``
table; the existing indexes
(``ix_license_findings_scan_id`` + ``ix_licenses_category``) are sufficient
for the latest-scan working set.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from core.sql_safety import escape_like
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Project,
    Scan,
)
from models import (
    License as LicenseModel,
)
from services.license_flags import REVIEW_FLAG_VALUES
from services.project_detail_service import _license_rank_case
from services.project_service import ProjectError, ProjectForbidden, ProjectNotFound
from services.scan_resolution import resolve_snapshot_scan_id

log = structlog.get_logger("license.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class LicenseError(ProjectError):
    """Base class for license-domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "License Error"


class LicenseFindingNotFound(LicenseError):
    status_code = 404
    title = "License Finding Not Found"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All license_category ENUM values for filter validation.
_ALL_CATEGORY_VALUES: frozenset[str] = frozenset({"allowed", "conditional", "forbidden", "unknown"})
_ALL_KIND_VALUES: frozenset[str] = frozenset({"declared", "concluded", "detected"})

# Valid ``review_flag`` filter values (Phase D). Sourced from the classifier's
# single source of truth so the filter can never accept a token the persistence
# layer would never store.
_REVIEW_FLAG_VALUES: frozenset[str] = frozenset(REVIEW_FLAG_VALUES)

# Distribution buckets — always emitted (zero if absent) so the bar chart
# renders a stable axis. Order is the UI's "worst first" presentation.
_DISTRIBUTION_KEYS = ("forbidden", "conditional", "allowed", "unknown")

# Pagination + sort caps.
_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 500
_VALID_SORT_KEYS = frozenset({"category", "name", "spdx_id", "affected_count"})

# Defense-in-depth cap on the ``affected_components`` array embedded in the
# license drawer payload. Without this, a permissive license against a large
# monorepo could materialize a many-thousand-row JSON list — security-reviewer
# Info #1 (PR #12) and Low #1 (PR #13). Clients fall back to the Components
# tab for the full list when ``affected_components_truncated`` is true.
_AFFECTED_COMPONENTS_CAP = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from core.authz import assert_team_access  # noqa: E402

# All cross-team guards in this module flow through `assert_team_access`
# (chore PR #3) so the `authz.cross_team_attempt` log shape is centralized.


def _normalize_category_filter(raw: list[str] | None) -> list[str] | None:
    """Drop unknown values; ``[]`` signals "no rows match", ``None`` means no filter."""
    if raw is None:
        return None
    cleaned = [c for c in raw if c in _ALL_CATEGORY_VALUES]
    if not cleaned:
        return []
    return cleaned


def _normalize_kind_filter(raw: list[str] | None) -> list[str] | None:
    if raw is None:
        return None
    cleaned = [k for k in raw if k in _ALL_KIND_VALUES]
    if not cleaned:
        return []
    return cleaned


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


async def list_project_licenses(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    limit: int = _LIST_LIMIT_DEFAULT,
    offset: int = 0,
    categories: list[str] | None = None,
    kinds: list[str] | None = None,
    search: str | None = None,
    sort: str = "category",
    order: str = "desc",
    review_flag: str | None = None,
    snapshot_scan_id: uuid.UUID | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """
    Page of licenses for the project's latest scan + the global distribution.

    ``snapshot_scan_id`` (feature #28) optionally pins the listing to a SPECIFIC
    succeeded snapshot instead of the latest succeeded scan; validated via
    :func:`services.scan_resolution.resolve_snapshot_scan_id` (cross-project /
    non-succeeded / nonexistent → :class:`SnapshotScanNotFound` → 404 at router).

    Returns ``(items, distribution, total)``:

    - ``items``: list of plain dicts shaped to
      :class:`schemas.license_detail.LicenseListItem`.
    - ``distribution``: dict keyed by category with counts of distinct
      component_versions in the latest scan that carry that category. Always
      contains all four keys (zero if absent). Computed in the same trip as
      the items so the Overview tab's ``license_distribution`` and the
      Licenses tab share a single source of truth.
    - ``total``: total number of distinct licenses (post-filter) for paging.

    Authorization
    -------------
    - ``ProjectNotFound`` (404) if the project id doesn't exist.
    - ``ProjectForbidden`` (403) if the actor is not a team member. We log
      ``authz.cross_team_attempt`` before raising.

    If the project has no SUCCEEDED scan, returns
    ``([], <all-zero distribution>, 0)`` with success — empty result, not 404.
    """
    if sort not in _VALID_SORT_KEYS:
        raise LicenseError(f"unsupported sort key: {sort!r}")
    if order not in {"asc", "desc"}:
        raise LicenseError(f"unsupported order: {order!r}")

    limit = max(min(int(limit), _LIST_LIMIT_MAX), 1)
    offset = max(int(offset), 0)

    project_result = await session.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_licenses",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}"
        ),
    )

    empty_distribution: dict[str, int] = dict.fromkeys(_DISTRIBUTION_KEYS, 0)

    # Anchor on the resolved snapshot scan — the pinned ``snapshot_scan_id`` when
    # given (feature #28), else the latest SUCCEEDED scan; never
    # ``project.latest_scan_id`` (the last *attempted* scan). The Licenses tab
    # must reflect the same scan the build gate / overview use. See
    # ``services.scan_resolution``. An invalid pinned id raises
    # SnapshotScanNotFound → 404 at the router.
    scan_id = await resolve_snapshot_scan_id(session, project_id, snapshot_scan_id)
    if scan_id is None:
        return [], empty_distribution, 0

    category_filter = _normalize_category_filter(categories)
    if category_filter == []:
        # Caller passed only invalid categories — match nothing without 422.
        # Distribution still reflects the underlying scan so the chart isn't
        # zeroed out behind a stale filter.
        distribution = await _compute_distribution(session, scan_id)
        return [], distribution, 0
    kind_filter = _normalize_kind_filter(kinds)
    if kind_filter == []:
        distribution = await _compute_distribution(session, scan_id)
        return [], distribution, 0

    # Aggregate per-license inside the latest scan. We pick a representative
    # finding per (license, kind) by MIN(license_findings.id) so every list
    # item has a stable handle for the drawer URL — UUIDv7-ish ordering is
    # deterministic enough for "first finding" semantics, and avoids needing
    # a window function.
    rank = _license_rank_case()

    base = (
        select(
            LicenseModel.id.label("license_id"),
            LicenseModel.spdx_id.label("spdx_id"),
            LicenseModel.name.label("name"),
            LicenseModel.category.label("category"),
            LicenseModel.is_osi_approved.label("is_osi_approved"),
            LicenseModel.is_fsf_libre.label("is_fsf_libre"),
            LicenseModel.review_flag.label("review_flag"),
            func.min(cast(LicenseFinding.kind, String)).label("kind"),
            # Postgres has no built-in `min(uuid)` operator class. Cast to text
            # for the aggregate (UUID strings sort lexicographically, which is
            # deterministic enough for "first finding" semantics) and let
            # Pydantic v2 coerce the result back to UUID at the schema layer.
            func.min(cast(LicenseFinding.id, String)).label("sample_finding_id"),
            func.count(func.distinct(LicenseFinding.component_version_id)).label("affected_count"),
            rank.label("rank"),
        )
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id == scan_id)
        .group_by(
            LicenseModel.id,
            LicenseModel.spdx_id,
            LicenseModel.name,
            LicenseModel.category,
            LicenseModel.is_osi_approved,
            LicenseModel.is_fsf_libre,
            LicenseModel.review_flag,
        )
    )

    if review_flag is not None:
        # Filter to a single AI review class (Phase D). Unknown values collapse
        # the result to empty without a 422 — same tolerant posture as the
        # category / kind filters above. The partial index ``ix_licenses_review_flag``
        # (migration 0036) serves the non-NULL predicate.
        if review_flag not in _REVIEW_FLAG_VALUES:
            distribution = await _compute_distribution(session, scan_id)
            return [], distribution, 0
        base = base.where(LicenseModel.review_flag == review_flag)

    if category_filter:
        # Compare against text since `category` is a Postgres ENUM and bind
        # parameters arrive as Python strings — see PR #10 CI fix 69d9f1c.
        base = base.where(cast(LicenseModel.category, String).in_(category_filter))

    if kind_filter:
        # `kind` is the per-finding ENUM; the HAVING-equivalent filter needs
        # to apply to the kinds present in the group. We push it into the
        # WHERE clause: include only findings of the requested kinds, then
        # group. This means a license whose findings are ALL of an excluded
        # kind disappears from the list, which is the desired UX.
        base = base.where(cast(LicenseFinding.kind, String).in_(kind_filter))

    if search:
        # Escape LIKE metacharacters and pass the escape character explicitly
        # so Postgres uses '\' not the default ESCAPE behaviour.
        safe = escape_like(search.strip())
        like = f"%{safe}%"
        base = base.where(
            or_(
                LicenseModel.spdx_id.ilike(like, escape="\\"),
                LicenseModel.name.ilike(like, escape="\\"),
            )
        )

    # Sorting.
    primary: Any
    if sort == "category":
        # rank ranges 0..3; "worse first" matches the Licenses tab UX where
        # forbidden bubbles up.
        primary = rank.desc() if order == "desc" else rank.asc()
    elif sort == "name":
        primary = LicenseModel.name.desc() if order == "desc" else LicenseModel.name.asc()
    elif sort == "spdx_id":
        # NULLs last so unmatched LicenseRef-* rows trail SPDX rows.
        spdx_col = LicenseModel.spdx_id
        primary = spdx_col.desc().nullslast() if order == "desc" else spdx_col.asc().nullslast()
    else:  # affected_count
        count_col = func.count(func.distinct(LicenseFinding.component_version_id))
        primary = count_col.desc() if order == "desc" else count_col.asc()

    # Stable tiebreak so pagination is deterministic across pages.
    order_clauses = [primary, LicenseModel.name.asc(), LicenseModel.id.asc()]

    items_stmt = base.order_by(*order_clauses).limit(limit).offset(offset)
    count_stmt = select(func.count()).select_from(base.subquery())

    items_result = await session.execute(items_stmt)
    rows = list(items_result.all())
    count_result = await session.execute(count_stmt)
    total = int(count_result.scalar_one())

    # Distribution is computed unfiltered — it represents the project's
    # license posture, not the active page filter. Single source of truth
    # with the Overview tab's license_distribution.
    distribution = await _compute_distribution(session, scan_id)

    items: list[dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "id": r.sample_finding_id,
                "license_id": r.license_id,
                "spdx_id": r.spdx_id,
                "name": r.name,
                "category": r.category,
                "kind": r.kind,
                "affected_count": int(r.affected_count),
                "is_osi_approved": bool(r.is_osi_approved),
                "is_fsf_libre": bool(r.is_fsf_libre),
                "review_flag": r.review_flag,
                "sample_finding_id": r.sample_finding_id,
            }
        )

    return items, distribution, total


async def _compute_distribution(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> dict[str, int]:
    """
    Distinct component_versions per license category in ``scan_id``.

    A component_version that carries multiple licenses contributes once per
    *category bucket* (DISTINCT cv per category). This matches the Overview
    tab's bucketing where each cv is counted in the worst category it owns.
    Unlike the Overview, here we count each category independently — a cv
    can appear in both ``forbidden`` and ``allowed`` buckets if it carries
    licenses from both. That's the right shape for the Licenses bar chart
    where each bar represents "components touched by ANY license in this
    category", not "components classified as their worst category".

    Returns a dict with all four bucket keys present (zero if absent).
    """
    stmt = (
        select(
            cast(LicenseModel.category, String).label("category"),
            func.count(func.distinct(LicenseFinding.component_version_id)).label("n"),
        )
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id == scan_id)
        .group_by(cast(LicenseModel.category, String))
    )
    result = await session.execute(stmt)
    counts: dict[str, int] = dict.fromkeys(_DISTRIBUTION_KEYS, 0)
    for row in result.all():
        if row.category in counts:
            counts[row.category] = int(row.n)
    return counts


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


async def get_license_finding_detail(
    session: AsyncSession,
    *,
    finding_id: uuid.UUID,
    actor: CurrentUser,
) -> dict[str, Any]:
    """
    Drawer payload for a single ``license_findings`` row.

    Resolves the project + team via ``finding → scan → project``. Existence-
    hides cross-team rows (404 instead of 403) so an unauthorized caller
    cannot discover that a finding id is in use elsewhere — same policy as
    the component and vulnerability drawers.

    Returns a plain dict shaped to
    :class:`schemas.license_detail.LicenseDetailResponse`.
    """
    stmt = (
        select(LicenseFinding, LicenseModel, Project)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .join(Scan, Scan.id == LicenseFinding.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .where(LicenseFinding.id == finding_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise LicenseFindingNotFound(f"license finding {finding_id} not found")
    finding, lic, project = row[0], row[1], row[2]

    # Hide existence: 404 not 403 — we don't leak that the row exists in
    # another team.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="license_finding",
        resource_id=str(finding_id),
        deny=lambda: LicenseFindingNotFound(
            f"license finding {finding_id} not found"
        ),
    )

    affected_components, total, truncated = await _load_affected_components(
        session,
        scan_id=finding.scan_id,
        license_id=finding.license_id,
    )

    # raw_data is best-effort. The shape is not contractual — frontends
    # render defensively. We expose the whole JSONB blob so the UI can show
    # ORT match excerpts, copyrights, detector scores, etc., as they appear,
    # without requiring a backend change for every new field ORT emits.
    raw_match: dict[str, Any] | None
    if finding.raw_data:
        raw_match = dict(finding.raw_data)
    else:
        raw_match = None

    return {
        "id": finding.id,
        "license_id": lic.id,
        "spdx_id": lic.spdx_id,
        "name": lic.name,
        "category": lic.category,
        "is_osi_approved": bool(lic.is_osi_approved),
        "is_fsf_libre": bool(lic.is_fsf_libre),
        "is_deprecated_license_id": bool(lic.is_deprecated_license_id),
        "review_flag": lic.review_flag,
        "reference_url": lic.reference_url,
        "finding_kind": finding.kind,
        "ort_match": raw_match,
        "affected_components": affected_components,
        "affected_components_truncated": truncated,
        "affected_components_total": total,
        "created_at": lic.created_at,
        "updated_at": lic.updated_at,
    }


async def _load_affected_components(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    license_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], int, bool]:
    """All component_versions in the same scan that carry the same license,
    capped at :data:`_AFFECTED_COMPONENTS_CAP` rows.

    Returns ``(items, total, truncated)`` where ``items`` is the response
    payload (≤ cap), ``total`` is the un-capped row count, and ``truncated``
    is true iff ``total > cap``. The cap is applied with ``LIMIT cap+1`` so
    we detect truncation in the same trip as the items query and avoid a
    separate ``COUNT(*)`` round-trip in the common (under-cap) case — when
    we *do* see the sentinel row, we issue a follow-up exact count for the
    response envelope.

    The same (cv, license) pair can appear under multiple kinds (declared
    *and* concluded *and* detected) and across different ``source_path``
    files. We collapse duplicates per (cv, kind) by picking the
    lexicographically smallest source_path; the drawer is a summary, not an
    audit log of every file ORT looked at.
    """
    cap = _AFFECTED_COMPONENTS_CAP
    stmt = (
        select(
            ComponentVersion.id.label("component_version_id"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("version"),
            cast(LicenseFinding.kind, String).label("kind"),
            func.min(LicenseFinding.source_path).label("source_path"),
        )
        .select_from(LicenseFinding)
        .join(ComponentVersion, ComponentVersion.id == LicenseFinding.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(LicenseFinding.scan_id == scan_id)
        .where(LicenseFinding.license_id == license_id)
        .group_by(
            ComponentVersion.id,
            Component.name,
            ComponentVersion.version,
            cast(LicenseFinding.kind, String),
        )
        .order_by(Component.name.asc(), ComponentVersion.version.asc())
        .limit(cap + 1)
    )
    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > cap
    items = [
        {
            "component_version_id": r.component_version_id,
            "component_name": r.component_name,
            "version": r.version,
            "kind": r.kind,
            "source_path": r.source_path,
        }
        for r in rows[:cap]
    ]
    if not truncated:
        total = len(items)
    else:
        # Only pay for the count when the cap actually fired.
        count_stmt = (
            select(func.count())
            .select_from(
                select(
                    ComponentVersion.id,
                    cast(LicenseFinding.kind, String).label("kind"),
                )
                .select_from(LicenseFinding)
                .join(
                    ComponentVersion,
                    ComponentVersion.id == LicenseFinding.component_version_id,
                )
                .where(LicenseFinding.scan_id == scan_id)
                .where(LicenseFinding.license_id == license_id)
                .group_by(
                    ComponentVersion.id,
                    cast(LicenseFinding.kind, String),
                )
                .subquery()
            )
        )
        total = int((await session.execute(count_stmt)).scalar_one())
    return items, total, truncated


__all__ = [
    "LicenseError",
    "LicenseFindingNotFound",
    "get_license_finding_detail",
    "list_project_licenses",
]
