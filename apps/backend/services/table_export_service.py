# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
CSV export for the three tables a reader spends their day in (B5).

Vulnerabilities, components and the cross-project inventory. Each one is
already filterable in the UI, and a filtered view someone has spent five
minutes assembling is exactly what they want to hand to a colleague, attach
to a ticket, or pivot in a spreadsheet.

WHY THIS PAGES THE LIST SERVICE RATHER THAN BUILDING ITS OWN QUERY

The audit export could share a ``_apply_filters`` helper with its list
because that query is a dozen WHERE clauses. These three are not: the
vulnerability listing alone is five hundred lines of joins, SLA expressions,
snapshot resolution and severity ranking, and its access check
(``assert_team_access`` against the project's team) lives inside that same
function. A second query would be a second place for the access rule to
live, and the failure mode of getting that wrong is one team reading
another's findings.

So each export calls the list function it exports, one page at a time. The
filters cannot drift because they are the same arguments; the access check
cannot drift because it is the same call. The cost was a repeated COUNT and
an OFFSET walk, bounded by the row cap but still growing with depth (#386
measured 820ms -> 1.6s across a 50k-row walk, 49s wall-clock end to end).

#463 closed the OFFSET half of that cost for the three exports actually
measured reaching that depth (vulnerabilities, components, inventory): each
list function grew a ``keyset``/``after_id`` mode that walks its primary key
via ``WHERE id > after_id`` instead of ``OFFSET``, called from here with
``sort``/``order`` no longer honoured for THIS caller (the interactive list
endpoints are untouched, same 8 sort modes, same OFFSET, because a
100-row page a person is reading does not hit the depth an unbounded export
walk does, and preserving the on-screen order is what that endpoint is
for). The licenses and projects exports stay on OFFSET: one is bounded by
the SPDX catalog size, the other by how many projects an organization
manually sets up, neither reaches a scale where OFFSET's cost matters, so
adding a second pagination mode for them would be complexity without a
measured problem behind it. See ``_stream``'s docstring for the mechanics
both shapes share, and each ``list_*`` function's "Keyset pagination" note
for why its export walks the way it does. The COUNT stays as-is either way
(both shapes still recompute the filtered total once per page): #463 traced
this to the walk's shrink/grow detection (``_stream``'s trailing "# rows:"
line and the ``export.csv_truncated`` log), which the fallback for a
SHRUNK result set (``if not items: break``) does not cover for a GROWN one;
dropping the per-page COUNT would silently lose that half of the check.

WHAT IS AND IS NOT IN A ROW

Each export carries the columns the table shows plus the identifiers a
reader needs to act on a row elsewhere. Free-text description fields are
left out: they are long, they carry newlines, and a spreadsheet is not where
anyone reads them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from services.csv_export import (
    CSV_BOM,
    CSV_STREAM_CHUNK_ROWS,
    ExportTooLarge,
    csv_line,
)

log = structlog.get_logger(__name__)

__all__ = [
    "COMPONENTS_CSV_COLUMNS",
    "INVENTORY_CSV_COLUMNS",
    "LICENSES_CSV_COLUMNS",
    "PROJECTS_CSV_COLUMNS",
    "VULNERABILITIES_CSV_COLUMNS",
    "ComponentsExportTooLarge",
    "InventoryExportTooLarge",
    "LicensesExportTooLarge",
    "ProjectsExportTooLarge",
    "VulnerabilitiesExportTooLarge",
    "stream_components_csv",
    "stream_inventory_csv",
    "stream_licenses_csv",
    "stream_projects_csv",
    "stream_vulnerabilities_csv",
]

#: Rows one export will build. Chosen to match the audit export rather than
#: derived: the number is a browser-download ceiling, not a database limit,
#: and two different ceilings would be two different things to explain.
EXPORT_HARD_LIMIT = 100_000

_ERRORS_BASE = "https://docs.trustedoss.io/errors"


class VulnerabilitiesExportTooLarge(ExportTooLarge):
    title = "Vulnerability Export Too Large"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            type_uri=f"{_ERRORS_BASE}/vulnerabilities-export-too-large",
            extension="vulnerabilities_export_too_large",
        )


class ComponentsExportTooLarge(ExportTooLarge):
    title = "Component Export Too Large"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            type_uri=f"{_ERRORS_BASE}/components-export-too-large",
            extension="components_export_too_large",
        )


class InventoryExportTooLarge(ExportTooLarge):
    title = "Inventory Export Too Large"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            type_uri=f"{_ERRORS_BASE}/inventory-export-too-large",
            extension="inventory_export_too_large",
        )


class LicensesExportTooLarge(ExportTooLarge):
    title = "License Export Too Large"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            type_uri=f"{_ERRORS_BASE}/licenses-export-too-large",
            extension="licenses_export_too_large",
        )


class ProjectsExportTooLarge(ExportTooLarge):
    title = "Project Export Too Large"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            type_uri=f"{_ERRORS_BASE}/projects-export-too-large",
            extension="projects_export_too_large",
        )


# Column order is the contract. A reader who has built a pivot table on last
# month's export should not find the columns shuffled in this month's.
VULNERABILITIES_CSV_COLUMNS = (
    "cve_id",
    "severity",
    "cvss_score",
    "epss_score",
    "kev",
    "kev_due_date",
    "status",
    "reachable",
    "component_name",
    "component_version",
    "component_license",
    "first_detected_at",
    "sla_due_date",
    "sla_status",
    "finding_id",
)

COMPONENTS_CSV_COLUMNS = (
    "name",
    "version",
    "purl",
    "direct",
    "depth",
    "dependency_scope",
    "license",
    "license_category",
    "severity_max",
    "vulnerability_count",
    "eol_state",
    "eol_date",
    "currency_state",
    "currency_latest",
    "malicious_state",
    "component_id",
)

LICENSES_CSV_COLUMNS = (
    "name",
    "spdx_id",
    "category",
    "kind",
    "affected_count",
    "is_osi_approved",
    "is_fsf_libre",
    "review_flag",
    "conflict_verdict",
    "license_finding_id",
)

# The project-list JSON response's own field set (ProjectPublic + the
# enrichment maps GET /v1/projects already overlays onto it), flattened. An
# export that invented its own shape would answer a different question than
# the screen it is supposed to let a reader take away.
PROJECTS_CSV_COLUMNS = (
    "name",
    "slug",
    "team_id",
    "visibility",
    "archived",
    "latest_scan_status",
    "severity_critical",
    "severity_high",
    "severity_medium",
    "severity_low",
    "license_forbidden",
    "license_conditional",
    "license_allowed",
    "license_unknown",
    "scan_count",
    "release_count",
    "last_scan_at",
    "created_by",
    "project_id",
)

INVENTORY_CSV_COLUMNS = (
    "name",
    "package_type",
    "purl",
    # This table aggregates one component across every project that uses it,
    # so a row names several versions rather than one. They are joined with a
    # space, which keeps the cell one field without inventing a delimiter a
    # version string could contain.
    #
    # A sample, not the set: the list service caps it at VERSION_SAMPLE_LIMIT
    # (five). `version_count` beside it carries the real number, which is why
    # the pair is exported together and neither alone.
    "versions",
    "version_count",
    "project_count",
    "license_category_max",
    "severity_max",
    "vulnerability_count",
    "eol",
    "outdated",
    "component_id",
)


def _row(
    columns: tuple[str, ...],
    item: dict[str, Any],
    *,
    remap: dict[str, str],
) -> tuple[Any, ...]:
    """
    Project one list item onto the column tuple.

    ``remap`` names the columns whose CSV name differs from the dict key.
    Missing keys render empty rather than raising: a list payload that grows
    a field should not take the export down with it.
    """
    return tuple(item.get(remap.get(column, column)) for column in columns)


async def _stream(
    *,
    columns: tuple[str, ...],
    remap: dict[str, str],
    fetch_page: Any,
    # A factory rather than the class, because each subclass supplies its own
    # type URI and extension and takes only the message. `type[ExportTooLarge]`
    # would promise the base constructor's three arguments, which is not what
    # any caller passes.
    too_large: Callable[[str], ExportTooLarge],
    label: str,
    start_position: Any = 0,
) -> AsyncIterator[str]:
    """
    Walk a list service one page at a time, yielding CSV.

    ``fetch_page(limit, position)`` returns ``(items, total, next_position)``.
    ``position`` is an opaque pagination token this function only ever
    threads from one ``fetch_page`` call to the next; it never inspects or
    computes it. Two shapes exist (#463):

    - An integer ``OFFSET`` (the ``licenses``/``projects`` exports, whose
      result sets are architecturally bounded small, the SPDX license
      catalog, the project portfolio, so ``OFFSET``'s cost, which grows with
      depth, never reaches a scale that matters).
    - The last row's primary key (the ``vulnerabilities``/``components``/
      ``inventory`` exports, the three actually measured reaching a depth
      where ``OFFSET`` degrades: 820ms → 1.6s across a 50k-row walk, 49s
      wall-clock end to end). ``start_position=None`` for these.

    The first page is fetched before anything is yielded so the row cap can
    be answered with a 413 rather than a file that stops halfway with
    nothing to say it did.
    """
    items, total, position = await fetch_page(CSV_STREAM_CHUNK_ROWS, start_position)
    if total > EXPORT_HARD_LIMIT:
        raise too_large(
            f"{label} export would return {total} rows (limit {EXPORT_HARD_LIMIT}); "
            "narrow the filters and retry",
        )

    yield CSV_BOM + csv_line(columns)
    for item in items:
        yield csv_line(_row(columns, item, remap=remap))

    written = len(items)
    latest_total = total
    while written < total and items:
        items, latest_total, position = await fetch_page(CSV_STREAM_CHUNK_ROWS, position)
        if not items:
            # The result set shrank under us (a rescan replaced the snapshot,
            # a finding was resolved). Stop rather than spin: what has been
            # written is a consistent prefix of a list that no longer exists.
            break
        for item in items:
            yield csv_line(_row(columns, item, remap=remap))
        written += len(items)

    # A trailer, so a short file can be told from a complete one.
    #
    # The status and headers are committed before the second page is fetched,
    # so anything that fails after that point (a reset connection, a statement
    # timeout on the growing OFFSET, the shrink above) truncates the body
    # inside a 200. Nothing in a CSV says it stopped early, and a partial
    # export attached to a customer deliverable understates risk, which is the
    # worst direction for this product to be wrong in. A reader or a script
    # can compare this count against the rows it parsed.
    #
    # A naive CSV reader will take this line as a one-field row. That is the
    # cost of putting the count where the file itself carries it rather than
    # in a header a saved file forgets; the alternative is a file that cannot
    # say anything about its own completeness once it leaves the browser.
    yield f"# rows: {written}\n"

    # Both directions count as short. The walk stops at the first page's
    # `total`, so a result set that GREW mid-export ends with `written ==
    # total` and looks complete while leaving rows behind; comparing against
    # the last page's count is what catches that one.
    if written < max(total, latest_total):
        log.warning(
            "export.csv_truncated",
            label=label,
            written=written,
            expected_at_start=total,
            expected_at_end=latest_total,
        )


async def stream_vulnerabilities_csv(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    filters: dict[str, Any],
) -> AsyncIterator[str]:
    """CVE findings for a project, honouring the caller's active filters."""
    from services.vulnerability_service import list_project_vulnerabilities

    # #463: keyset on VulnerabilityFinding.id (the "id" key on every item),
    # not the interactive list's OFFSET + 8 sort modes, see list_project_
    # vulnerabilities's "Keyset pagination" docstring note.
    async def fetch_page(
        limit: int, after_id: uuid.UUID | None
    ) -> tuple[list[dict[str, Any]], int, uuid.UUID | None]:
        items, total, _distribution = await list_project_vulnerabilities(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            keyset=True,
            after_id=after_id,
            **filters,
        )
        next_position = items[-1]["id"] if items else after_id
        return items, total, next_position

    log.info(
        "export.vulnerabilities.csv_started",
        project_id=str(project_id),
        actor_user_id=str(actor.id),
    )
    async for chunk in _stream(
        columns=VULNERABILITIES_CSV_COLUMNS,
        remap={
            "component_name": "affected_component_name",
            "component_version": "affected_component_version",
            "component_license": "affected_component_license",
            "finding_id": "id",
        },
        fetch_page=fetch_page,
        too_large=VulnerabilitiesExportTooLarge,
        label="vulnerability",
        start_position=None,
    ):
        yield chunk


async def stream_components_csv(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    filters: dict[str, Any],
) -> AsyncIterator[str]:
    """The project's bill of materials, honouring the caller's active filters."""
    from services.project_detail_service import list_components_for_project

    # #463: keyset on ComponentVersion.id (the "id" key on every item), not
    # the interactive list's OFFSET, see list_components_for_project's
    # docstring note.
    async def fetch_page(
        limit: int, after_id: uuid.UUID | None
    ) -> tuple[list[dict[str, Any]], int, uuid.UUID | None]:
        items, total = await list_components_for_project(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            keyset=True,
            after_id=after_id,
            **filters,
        )
        next_position = items[-1]["id"] if items else after_id
        return items, total, next_position

    log.info(
        "export.components.csv_started",
        project_id=str(project_id),
        actor_user_id=str(actor.id),
    )
    async for chunk in _stream(
        columns=COMPONENTS_CSV_COLUMNS,
        remap={},
        fetch_page=fetch_page,
        too_large=ComponentsExportTooLarge,
        label="component",
        start_position=None,
    ):
        yield chunk


async def stream_inventory_csv(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    filters: dict[str, Any],
) -> AsyncIterator[str]:
    """Every component the caller's teams use, honouring the active filters."""
    from services.inventory_service import list_inventory_components

    # #463: keyset on Component.id (the "component_id" key on every item),
    # not the interactive list's OFFSET, see list_inventory_components's
    # docstring note. This is the org-wide rollup, the export most likely to
    # reach the row depth OFFSET degrades at.
    async def fetch_page(
        limit: int, after_id: uuid.UUID | None
    ) -> tuple[list[dict[str, Any]], int, uuid.UUID | None]:
        # This one answers with a response model rather than a tuple; the
        # rows are Pydantic, so dump them to reach them by column name.
        page = await list_inventory_components(
            session,
            actor=actor,
            limit=limit,
            keyset=True,
            after_id=after_id,
            **filters,
        )
        rows: list[dict[str, Any]] = []
        for row in page.items:
            item = row.model_dump()
            item["versions"] = " ".join(item.get("versions") or [])
            rows.append(item)
        next_position = rows[-1]["component_id"] if rows else after_id
        return rows, page.total, next_position

    log.info("export.inventory.csv_started", actor_user_id=str(actor.id))
    async for chunk in _stream(
        columns=INVENTORY_CSV_COLUMNS,
        remap={},
        fetch_page=fetch_page,
        too_large=InventoryExportTooLarge,
        label="inventory",
        start_position=None,
    ):
        yield chunk


async def stream_licenses_csv(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    filters: dict[str, Any],
) -> AsyncIterator[str]:
    """The project's Licenses-tab rows, honouring the caller's active filters."""
    from services.license_service import list_project_licenses

    # #463 looked at this one too: the row here is one per DISTINCT LICENSE
    # in the scan (bounded by the SPDX catalog size), never near the depth
    # OFFSET degrades at, so it stays on OFFSET, see table_export_service's
    # module docstring / _stream's docstring for the two-shapes rationale.
    async def fetch_page(
        limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int, int]:
        items, _distribution, total, _declared, _conflict_summary = await list_project_licenses(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            **filters,
        )
        # `conflict` is a nested {verdict, why, dependency_class} object (or
        # None); a spreadsheet cell wants the verdict alone, not a stringified
        # dict.
        for item in items:
            conflict = item.get("conflict")
            item["conflict_verdict"] = conflict.get("verdict") if conflict else None
        return items, total, offset + len(items)

    log.info(
        "export.licenses.csv_started",
        project_id=str(project_id),
        actor_user_id=str(actor.id),
    )
    async for chunk in _stream(
        columns=LICENSES_CSV_COLUMNS,
        remap={"license_finding_id": "id"},
        fetch_page=fetch_page,
        too_large=LicensesExportTooLarge,
        label="license",
    ):
        yield chunk


async def stream_projects_csv(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    filters: dict[str, Any],
) -> AsyncIterator[str]:
    """The project portfolio list, honouring the caller's active filters.

    ``list_projects`` clamps ``size`` to 100 internally regardless of what is
    asked for, so this pins the same 100 here rather than passing through
    ``CSV_STREAM_CHUNK_ROWS`` (1000): page numbers only stay exact across
    calls when every call derives them from the SAME per-page size the
    service actually uses.

    #463 looked at this one too: the portfolio is organically small (every
    project is manually set up), never near the depth OFFSET degrades at,
    so it stays on OFFSET, see table_export_service's module docstring /
    _stream's docstring for the two-shapes rationale.
    """
    from services.project_list_enrichment import enrich_project_rows
    from services.project_service import list_projects

    page_size = 100

    async def fetch_page(
        limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int, int]:
        page_number = offset // page_size + 1
        rows, total = await list_projects(
            session,
            actor=actor,
            page=page_number,
            size=page_size,
            **filters,
        )
        (
            status_by_project,
            severity_by_project,
            counts_by_project,
            license_by_project,
            created_by_name,
            _team_name_by_team,
            _group_path_by_project,
        ) = await enrich_project_rows(session, projects=rows)

        items: list[dict[str, Any]] = []
        for p in rows:
            severity = severity_by_project.get(p.id) or {}
            license_summary = license_by_project.get(p.id) or {}
            counts = counts_by_project.get(p.id) or {}
            items.append(
                {
                    "name": p.name,
                    "slug": p.slug,
                    "team_id": str(p.team_id),
                    "visibility": p.visibility,
                    "archived": p.archived_at is not None,
                    "latest_scan_status": status_by_project.get(p.id),
                    "severity_critical": severity.get("critical"),
                    "severity_high": severity.get("high"),
                    "severity_medium": severity.get("medium"),
                    "severity_low": severity.get("low"),
                    "license_forbidden": license_summary.get("forbidden"),
                    "license_conditional": license_summary.get("conditional"),
                    "license_allowed": license_summary.get("allowed"),
                    "license_unknown": license_summary.get("unknown"),
                    "scan_count": counts.get("scan_count"),
                    "release_count": counts.get("release_count"),
                    "last_scan_at": counts.get("last_scan_at"),
                    "created_by": created_by_name.get(p.id),
                    "project_id": str(p.id),
                }
            )
        return items, total, offset + len(items)

    log.info("export.projects.csv_started", actor_user_id=str(actor.id))
    async for chunk in _stream(
        columns=PROJECTS_CSV_COLUMNS,
        remap={},
        fetch_page=fetch_page,
        too_large=ProjectsExportTooLarge,
        label="project",
    ):
        yield chunk
