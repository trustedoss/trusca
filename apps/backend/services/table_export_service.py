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
cannot drift because it is the same call. The cost is a repeated COUNT and
an OFFSET walk, which the row cap bounds.

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
    "VULNERABILITIES_CSV_COLUMNS",
    "ComponentsExportTooLarge",
    "InventoryExportTooLarge",
    "VulnerabilitiesExportTooLarge",
    "stream_components_csv",
    "stream_inventory_csv",
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
) -> AsyncIterator[str]:
    """
    Walk a list service one page at a time, yielding CSV.

    ``fetch_page(limit, offset)`` returns ``(items, total)``. The first page
    is fetched before anything is yielded so the row cap can be answered with
    a 413 rather than a file that stops halfway with nothing to say it did.
    """
    items, total = await fetch_page(CSV_STREAM_CHUNK_ROWS, 0)
    if total > EXPORT_HARD_LIMIT:
        raise too_large(
            f"{label} export would return {total} rows (limit {EXPORT_HARD_LIMIT}); "
            "narrow the filters and retry",
        )

    yield CSV_BOM + csv_line(columns)
    for item in items:
        yield csv_line(_row(columns, item, remap=remap))

    offset = len(items)
    latest_total = total
    while offset < total and items:
        items, latest_total = await fetch_page(CSV_STREAM_CHUNK_ROWS, offset)
        if not items:
            # The result set shrank under us (a rescan replaced the snapshot,
            # a finding was resolved). Stop rather than spin: what has been
            # written is a consistent prefix of a list that no longer exists.
            break
        for item in items:
            yield csv_line(_row(columns, item, remap=remap))
        offset += len(items)

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
    yield f"# rows: {offset}\n"

    # Both directions count as short. The walk stops at the first page's
    # `total`, so a result set that GREW mid-export ends with `offset ==
    # total` and looks complete while leaving rows behind; comparing against
    # the last page's count is what catches that one.
    if offset < max(total, latest_total):
        log.warning(
            "export.csv_truncated",
            label=label,
            written=offset,
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

    async def fetch_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        items, total, _distribution = await list_project_vulnerabilities(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            **filters,
        )
        return items, total

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

    async def fetch_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        items, total = await list_components_for_project(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            **filters,
        )
        return items, total

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

    async def fetch_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        # This one answers with a response model rather than a tuple; the
        # rows are Pydantic, so dump them to reach them by column name.
        page = await list_inventory_components(
            session,
            actor=actor,
            limit=limit,
            offset=offset,
            **filters,
        )
        rows: list[dict[str, Any]] = []
        for row in page.items:
            item = row.model_dump()
            item["versions"] = " ".join(item.get("versions") or [])
            rows.append(item)
        return rows, page.total

    log.info("export.inventory.csv_started", actor_user_id=str(actor.id))
    async for chunk in _stream(
        columns=INVENTORY_CSV_COLUMNS,
        remap={},
        fetch_page=fetch_page,
        too_large=InventoryExportTooLarge,
        label="inventory",
    ):
        yield chunk
