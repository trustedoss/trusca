"""
Compliance unified-grid service — W9-#58 (Compliance unified grid).

One top-level entry point invoked from the matching router endpoint:

- :func:`list_project_compliance`

Why a new module
----------------
W4-C #20 introduced the Compliance tab as a sub-tab wrapper that switched
between :mod:`services.license_service` and :mod:`services.obligation_service`.
W9-#58 rebuilds that surface as a single unified grid: one row per license
in the latest scan, with the obligations attached to that license embedded
inline. Composing the two services from the router would force the router
to own the join + the affected-components preview, neither of which belongs
above the service layer. We keep the join here so the router stays a thin
adapter.

Read-only by design
-------------------
Both upstream surfaces are read-only (no analyst workflow); the unified
grid inherits that contract. There is no PATCH counterpart.

Authorization
-------------
- ``ProjectForbidden`` (403) on cross-team. Existence of a project is not a
  secret across teams — mirrors the Licenses tab list contract.
- ``ProjectNotFound`` (404) when the project does not exist.

We log ``authz.cross_team_attempt`` before raising so SOC tooling sees the
rejection regardless of which HTTP status the caller observes.

Performance
-----------
The endpoint emits at most 4 round-trips:

1. Project lookup (RBAC anchor).
2. Latest / pinned succeeded scan resolution (single SELECT via
   :func:`services.scan_resolution.resolve_snapshot_scan_id`).
3. Per-license aggregation (GROUP BY licenses ⨝ license_findings ⨝
   component_versions ⨝ components). One trip yields ``(license_finding_id,
   spdx_id, name, category, kind, affected_count, distribution_count)``.
4. Per-license obligation rows + the affected-components preview window
   (single trip each), joined to the page of license ids in step 3.

We deliberately do not denormalise affected-components into the per-license
GROUP BY because Postgres ``array_agg`` over a 10 k-row join can spill;
the preview is a separate windowed query keyed on the page's license_ids.

Search safety
-------------
User-supplied ``search`` is run through :func:`core.sql_safety.escape_like`
and compared with an explicit ESCAPE clause so attackers cannot collapse
the filter to "match everything" with bare ``%`` / ``_`` characters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from core.sql_safety import escape_like
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Obligation,
    Project,
)
from models import License as LicenseModel
from services.obligation_service import sync_catalog_obligations
from services.project_detail_service import _license_rank_case
from services.project_service import ProjectError, ProjectForbidden, ProjectNotFound
from services.scan_resolution import resolve_snapshot_scan_id

log = structlog.get_logger("compliance.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ComplianceError(ProjectError):
    """Base class for compliance-grid errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Compliance Error"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_ALL_CATEGORY_VALUES: frozenset[str] = frozenset(
    {"allowed", "conditional", "forbidden", "unknown"}
)

# Distribution buckets — always emitted (zero if absent) so the chart axis
# stays stable. Order is the UI's "worst first" presentation.
_DISTRIBUTION_KEYS = ("forbidden", "conditional", "allowed", "unknown")

# Pagination + sort caps.
_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 500
_VALID_SORT_KEYS = frozenset({"category", "license_name", "spdx_id", "affected_count"})

# Defense-in-depth cap on the affected-components preview embedded in a
# grid row. The full list ships via the License drawer; the preview is
# capped so the row stays compact even when a permissive license touches
# every component in a monorepo.
_AFFECTED_PREVIEW_CAP = 5

# Truncate the inline obligation summary so a runaway catalog row cannot
# inflate the grid response. The full text is reachable via the Obligation
# drawer (``GET /v1/projects/{id}/obligations/{id}``).
_OBLIGATION_SUMMARY_CHARS = 240

# Obligation kinds that surface a NOTICE requirement on the row. Kept in
# lock-step with the user-visible NOTICE generator semantics.
_NOTICE_REQUIRING_KINDS: frozenset[str] = frozenset({"attribution", "notice"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_category_filter(raw: list[str] | None) -> list[str] | None:
    """Drop unknown values; ``[]`` signals "no rows match", ``None`` means no filter."""
    if raw is None:
        return None
    cleaned = [c for c in raw if c in _ALL_CATEGORY_VALUES]
    if not cleaned:
        return []
    return cleaned


def _normalize_kind_filter(raw: list[str] | None) -> list[str] | None:
    """Trim + dedupe + length-cap obligation kinds; the column is open so we
    accept any well-formed string."""
    if raw is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for k in raw:
        candidate = k.strip()
        if not candidate or len(candidate) > 64 or candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)
    if not cleaned:
        return []
    return cleaned


def _summarize_obligation(text: str | None) -> str:
    """Trim an obligation's text to a one-line summary suitable for the grid.

    The catalog stores multi-paragraph obligation prose. The grid row only
    has space for a one-line summary — we collapse whitespace and truncate
    on a whole codepoint boundary so the response stays compact.
    """
    if not text:
        return ""
    # Collapse any whitespace run (incl. newlines) to a single space so the
    # grid row renders on one visual line.
    collapsed = " ".join(text.split())
    if len(collapsed) <= _OBLIGATION_SUMMARY_CHARS:
        return collapsed
    # Slice on a unicode codepoint boundary then append an ellipsis. Python
    # ``str`` is codepoint-indexed so a slice never lands mid-surrogate.
    return collapsed[: _OBLIGATION_SUMMARY_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


async def list_project_compliance(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    limit: int = _LIST_LIMIT_DEFAULT,
    offset: int = 0,
    categories: list[str] | None = None,
    kinds: list[str] | None = None,
    search: str | None = None,
    has_obligations: bool | None = None,
    sort: str = "category",
    order: str = "desc",
    snapshot_scan_id: uuid.UUID | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], int, datetime]:
    """
    Page of unified compliance rows for the project's latest scan.

    Returns ``(items, distribution, total, generated_at)``.

    - ``items``: list of plain dicts shaped to
      :class:`schemas.compliance.ComplianceRow`.
    - ``distribution``: license-category counts across the underlying scan
      (unfiltered). Always emits all four buckets so the chart axis is
      stable.
    - ``total``: total rows matching the active filter, pre-pagination.
    - ``generated_at``: server clock the response was assembled at.

    Snapshot anchoring (feature #28): ``snapshot_scan_id`` pins the read to
    a specific succeeded scan; cross-project / non-succeeded / nonexistent
    ids raise ``SnapshotScanNotFound`` (→ 404 at the router).

    Authorization
    -------------
    - ``ProjectNotFound`` (404) when the project id does not exist.
    - ``ProjectForbidden`` (403) when the actor is not a team member.
    - super_admin bypasses team membership exactly as elsewhere.

    Empty scan
    ----------
    If the project has no succeeded scan yet, returns ``([], <zeros>, 0)``
    with success — empty result, not 404.
    """
    if sort not in _VALID_SORT_KEYS:
        raise ComplianceError(f"unsupported sort key: {sort!r}")
    if order not in {"asc", "desc"}:
        raise ComplianceError(f"unsupported order: {order!r}")

    limit = max(min(int(limit), _LIST_LIMIT_MAX), 1)
    offset = max(int(offset), 0)
    generated_at = datetime.now(tz=UTC)

    project_result = await session.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_compliance",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}"
        ),
    )

    empty_distribution: dict[str, int] = dict.fromkeys(_DISTRIBUTION_KEYS, 0)

    # Anchor on the resolved snapshot scan (latest succeeded by default).
    scan_id = await resolve_snapshot_scan_id(session, project_id, snapshot_scan_id)
    if scan_id is None:
        return [], empty_distribution, 0, generated_at

    # v2.2 c4: materialise structured obligation catalog before the join so
    # licenses observed in this scan carry their concrete obligation rows.
    # Idempotent + additive (never overwrites operator-/seed-authored rows).
    await sync_catalog_obligations(session, scan_id=scan_id)

    category_filter = _normalize_category_filter(categories)
    if category_filter == []:
        # Caller passed only invalid categories — match nothing without 422.
        # Distribution still reflects the underlying scan so the chart isn't
        # zeroed out behind a stale filter.
        distribution = await _compute_distribution(session, scan_id)
        return [], distribution, 0, generated_at

    kind_filter = _normalize_kind_filter(kinds)
    if kind_filter == []:
        distribution = await _compute_distribution(session, scan_id)
        return [], distribution, 0, generated_at

    rank = _license_rank_case()

    # Aggregate per-license inside the scan. ``MIN(license_findings.id::text)``
    # gives every grid row a stable handle for the License drawer URL without
    # requiring a window function — UUIDs sort lexicographically when cast to
    # text, which is deterministic enough for "first finding" semantics.
    base = (
        select(
            LicenseModel.id.label("license_id"),
            LicenseModel.spdx_id.label("spdx_id"),
            LicenseModel.name.label("license_name"),
            LicenseModel.category.label("category"),
            func.min(cast(LicenseFinding.kind, String)).label("kind"),
            func.min(cast(LicenseFinding.id, String)).label("license_finding_id"),
            func.count(func.distinct(LicenseFinding.component_version_id)).label(
                "affected_count"
            ),
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
        )
    )

    if category_filter:
        base = base.where(cast(LicenseModel.category, String).in_(category_filter))

    if search:
        safe = escape_like(search.strip())
        like = f"%{safe}%"
        base = base.where(
            or_(
                LicenseModel.spdx_id.ilike(like, escape="\\"),
                LicenseModel.name.ilike(like, escape="\\"),
            )
        )

    # ``kind`` and ``has_obligations`` filter on obligations attached to the
    # license — apply via an EXISTS subquery so we do not skew the
    # ``affected_count`` aggregate.
    if kind_filter:
        ob_exists = (
            select(Obligation.id)
            .where(Obligation.license_id == LicenseModel.id)
            .where(Obligation.kind.in_(kind_filter))
            .limit(1)
        )
        base = base.where(ob_exists.exists())
    elif has_obligations is True:
        ob_any = (
            select(Obligation.id)
            .where(Obligation.license_id == LicenseModel.id)
            .limit(1)
        )
        base = base.where(ob_any.exists())
    elif has_obligations is False:
        ob_any = (
            select(Obligation.id)
            .where(Obligation.license_id == LicenseModel.id)
            .limit(1)
        )
        base = base.where(~ob_any.exists())

    # Sorting — primary axis chosen by ``sort``, then a deterministic tiebreak
    # so paging doesn't shuffle rows under the user.
    primary: Any
    if sort == "category":
        primary = rank.desc() if order == "desc" else rank.asc()
    elif sort == "license_name":
        primary = (
            LicenseModel.name.desc() if order == "desc" else LicenseModel.name.asc()
        )
    elif sort == "spdx_id":
        spdx_col = LicenseModel.spdx_id
        primary = (
            spdx_col.desc().nullslast() if order == "desc" else spdx_col.asc().nullslast()
        )
    else:  # affected_count
        count_col = func.count(func.distinct(LicenseFinding.component_version_id))
        primary = count_col.desc() if order == "desc" else count_col.asc()

    order_clauses = [primary, LicenseModel.name.asc(), LicenseModel.id.asc()]

    items_stmt = base.order_by(*order_clauses).limit(limit).offset(offset)
    count_stmt = select(func.count()).select_from(base.subquery())

    items_result = await session.execute(items_stmt)
    rows = list(items_result.all())
    count_result = await session.execute(count_stmt)
    total = int(count_result.scalar_one())

    distribution = await _compute_distribution(session, scan_id)

    if not rows:
        return [], distribution, total, generated_at

    license_ids = [uuid.UUID(str(r.license_id)) for r in rows]

    # Per-license obligations — single trip, then bucket in-memory.
    obligations_by_license = await _load_obligations_for_licenses(session, license_ids)

    # Per-license affected-components preview — single trip via a windowed
    # subquery so each license gets at most ``_AFFECTED_PREVIEW_CAP`` rows.
    preview_by_license = await _load_affected_preview(
        session, scan_id=scan_id, license_ids=license_ids
    )

    items: list[dict[str, Any]] = []
    for r in rows:
        lic_id = uuid.UUID(str(r.license_id))
        obligations = obligations_by_license.get(lic_id, [])
        items.append(
            {
                "license_finding_id": r.license_finding_id,
                "license_id": r.license_id,
                "spdx_id": r.spdx_id,
                "license_name": r.license_name,
                "category": r.category,
                "category_source": "static",
                "kind": r.kind,
                "affected_component_count": int(r.affected_count),
                "affected_components": preview_by_license.get(lic_id, []),
                "obligations": obligations,
                "notice_required": any(
                    o["kind"] in _NOTICE_REQUIRING_KINDS for o in obligations
                ),
                "category_override_source": None,
            }
        )

    return items, distribution, total, generated_at


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------


async def _compute_distribution(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> dict[str, int]:
    """
    Distinct component_versions per license category in ``scan_id``.

    Mirrors :func:`services.license_service._compute_distribution` so the
    unified grid's chart and the legacy Licenses tab chart agree. Always
    returns all four buckets (zero if absent).
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
# Per-license obligation buckets
# ---------------------------------------------------------------------------


async def _load_obligations_for_licenses(
    session: AsyncSession,
    license_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """One trip → ``{license_id: [obligation summaries]}``.

    Each summary is shaped to :class:`schemas.compliance.ComplianceObligation`.
    The list is ordered by obligation kind so the grid renders chips in a
    stable order.
    """
    if not license_ids:
        return {}

    stmt = (
        select(
            Obligation.id.label("obligation_id"),
            Obligation.license_id.label("license_id"),
            Obligation.kind.label("kind"),
            Obligation.text.label("text"),
        )
        .where(Obligation.license_id.in_(license_ids))
        .order_by(Obligation.license_id.asc(), Obligation.kind.asc(), Obligation.id.asc())
    )
    rows = (await session.execute(stmt)).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {lid: [] for lid in license_ids}
    for r in rows:
        out.setdefault(r.license_id, []).append(
            {
                "obligation_id": r.obligation_id,
                "kind": r.kind,
                "summary": _summarize_obligation(r.text),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Per-license affected-components preview
# ---------------------------------------------------------------------------


async def _load_affected_preview(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    license_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """One trip → ``{license_id: [<=5 components]}``.

    A naive ``GROUP BY license_id`` would materialise the entire affected
    list per license. We use a ``ROW_NUMBER()`` window so Postgres only
    emits the first ``_AFFECTED_PREVIEW_CAP`` rows per license — the same
    trip stays linear in the page size regardless of the per-license
    component fan-out.
    """
    if not license_ids:
        return {}

    rn = (
        func.row_number()
        .over(
            partition_by=LicenseFinding.license_id,
            order_by=(Component.name.asc(), ComponentVersion.version.asc()),
        )
        .label("rn")
    )
    inner = (
        select(
            LicenseFinding.license_id.label("license_id"),
            ComponentVersion.id.label("component_version_id"),
            Component.name.label("name"),
            ComponentVersion.version.label("version"),
            ComponentVersion.purl_with_version.label("purl"),
            rn,
        )
        .select_from(LicenseFinding)
        .join(
            ComponentVersion,
            ComponentVersion.id == LicenseFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(
            and_(
                LicenseFinding.scan_id == scan_id,
                LicenseFinding.license_id.in_(license_ids),
            )
        )
    ).subquery()

    stmt = (
        select(
            inner.c.license_id,
            inner.c.component_version_id,
            inner.c.name,
            inner.c.version,
            inner.c.purl,
        )
        .where(inner.c.rn <= _AFFECTED_PREVIEW_CAP)
        .order_by(inner.c.license_id.asc(), inner.c.rn.asc())
    )
    rows = (await session.execute(stmt)).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {lid: [] for lid in license_ids}
    for r in rows:
        out.setdefault(r.license_id, []).append(
            {
                "component_version_id": r.component_version_id,
                "name": r.name,
                "version": r.version,
                "purl": r.purl,
            }
        )
    return out


__all__ = [
    "ComplianceError",
    "list_project_compliance",
]
