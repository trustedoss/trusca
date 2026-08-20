# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
What the metrics endpoint publishes, and nothing else (N10).

The list of series lives in ``tests/contracts/metrics-series.json`` and this
module is held to it. That is the wrong way round for most code and the right
way round here: an operational endpoint grows one series at a time, each
obviously fine on its own, and nobody re-reads the whole output to notice that
it now says how many people work at the company and what their projects are
called. Adding a series means editing the list, which is a moment somebody
decides it is safe to publish.

Two rules hold for every series, and the contract test checks both. The value
is an aggregate and never a row, so a count of scans is fine and a scan id is
not. The labels are a closed vocabulary this module owns, never a string that
came from a user, so no project, package, person or repository name can reach
the output.

The exposition format is written by hand rather than pulled in as a
dependency. It is four lines of string formatting for gauges, and the
alternative is a package on the request path of an endpoint that exists to be
scraped by something outside the deployment.
"""

from __future__ import annotations

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    ComponentApproval,
    Project,
    Scan,
    User,
    Vulnerability,
    VulnerabilityFinding,
)
from models.scan import SCAN_STATUS_VALUES, VULN_SEVERITY_VALUES
from services.policy_gate import _CLOSED_FINDING_STATUSES

log = structlog.get_logger("services.metrics")


def _line(name: str, value: float, **labels: str) -> str:
    if labels:
        rendered = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        return f"{name}{{{rendered}}} {value}\n"
    return f"{name} {value}\n"


def _block(
    name: str,
    help_text: str,
    kind: str,
    samples: list[tuple[dict[str, str], float]],
) -> str:
    out = [f"# HELP {name} {help_text}\n", f"# TYPE {name} {kind}\n"]
    for labels, value in samples:
        out.append(_line(name, value, **labels))
    return "".join(out)


async def _count(session: AsyncSession, stmt: Select[tuple[int]]) -> int:
    return int((await session.execute(stmt)).scalar_one())


async def render_metrics(session: AsyncSession) -> str:
    """The whole document, in the order the contract file lists it.

    One query per series rather than one clever query for all of them: this
    runs on a scrape interval, the counts are indexed, and a single readable
    statement per series is what lets somebody check that a metric measures
    what its name says.
    """
    projects = await _count(session, select(func.count()).select_from(Project))

    scans_by_status = {
        status: 0 for status in SCAN_STATUS_VALUES
    }
    for status, count in (
        await session.execute(
            select(Scan.status, func.count()).group_by(Scan.status)
        )
    ).all():
        # A status the enum does not know cannot appear, but if the enum grows
        # and this dict does not, the row is dropped rather than emitted under
        # a label nobody declared.
        if status in scans_by_status:
            scans_by_status[status] = int(count)

    open_by_severity = {severity: 0 for severity in VULN_SEVERITY_VALUES}
    for severity, count in (
        await session.execute(
            # Severity lives on the CVE rather than on the finding, so this
            # joins rather than reading a column off the finding row. Counting
            # findings and not CVEs is deliberate: one CVE in forty projects
            # is forty pieces of work.
            select(Vulnerability.severity, func.count())
            .join(
                VulnerabilityFinding,
                VulnerabilityFinding.vulnerability_id == Vulnerability.id,
            )
            .where(VulnerabilityFinding.status.notin_(tuple(_CLOSED_FINDING_STATUSES)))
            .group_by(Vulnerability.severity)
        )
    ).all():
        if severity in open_by_severity:
            open_by_severity[severity] = int(count)

    approvals_pending = await _count(
        session,
        select(func.count())
        .select_from(ComponentApproval)
        .where(ComponentApproval.status == "pending"),
    )
    active_users = await _count(
        session,
        select(func.count())
        .select_from(User)
        .where(User.is_active.is_(True), User.is_service_account.is_(False)),
    )
    active_service_accounts = await _count(
        session,
        select(func.count())
        .select_from(User)
        .where(User.is_active.is_(True), User.is_service_account.is_(True)),
    )

    document = [
        _block(
            "trusca_projects_total",
            "Projects that exist, archived ones included.",
            "gauge",
            [({}, projects)],
        ),
        _block(
            "trusca_scans_total",
            "Scans by status. The queued and running counts are the ones an operator watches.",
            "gauge",
            [({"status": status}, count) for status, count in scans_by_status.items()],
        ),
        _block(
            "trusca_vulnerability_findings_open",
            "Open vulnerability findings by severity, across every project.",
            "gauge",
            [
                ({"severity": severity}, count)
                for severity, count in open_by_severity.items()
            ],
        ),
        _block(
            "trusca_component_approvals_pending",
            "Component approvals waiting on somebody.",
            "gauge",
            [({}, approvals_pending)],
        ),
        _block(
            "trusca_users_active",
            "Accounts that can sign in. Service accounts are counted separately.",
            "gauge",
            [({}, active_users)],
        ),
        _block(
            "trusca_service_accounts_active",
            "Automation identities that can still authenticate.",
            "gauge",
            [({}, active_service_accounts)],
        ),
        _block(
            "trusca_workspace_disk_used_ratio",
            "Workspace volume in use, 0 to 1. The scan guard refuses above its own threshold.",
            "gauge",
            [({}, await _workspace_used_ratio(session))],
        ),
    ]
    return "".join(document)


async def _workspace_used_ratio(session: AsyncSession) -> float:
    """The workspace mount, as a fraction rather than a percentage.

    Fractions are the convention a scraper expects for a ratio, and the health
    panel's percentage is a display choice rather than the underlying number.
    A mount that cannot be read reports 0 rather than failing the scrape: a
    monitoring endpoint that returns 500 because one of its seven numbers is
    unavailable takes the other six down with it.
    """
    try:
        from services.admin_disk_service import get_disk_telemetry

        telemetry = await get_disk_telemetry(session)
        for item in telemetry.items:
            if item.name == "workspace" and item.used_pct is not None:
                return round(float(item.used_pct) / 100.0, 4)
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics_workspace_disk_unavailable", error=str(exc)[:200])
    return 0.0
