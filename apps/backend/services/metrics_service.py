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

Two series (``trusca_broker_queue_backlog``, ``trusca_scan_queue_wait_seconds``)
carry their own switch, ``queue_backlog_metrics_enabled()`` (core.config),
off by default and separate from the endpoint's own on/off. The other series
here cost one Postgres query each, which a request to this endpoint already
pays for six times over; these two open a second connection, to the broker,
which is a different cost and a different failure mode. A deployment that
already scrapes this endpoint does not get that trade unless it asks for it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import redis as _redis
import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import queue_backlog_metrics_enabled, redis_url
from models import (
    ComponentApproval,
    Project,
    Scan,
    User,
    Vulnerability,
    VulnerabilityFinding,
)
from models.scan import SCAN_STATUS_VALUES, VULN_SEVERITY_VALUES
from models.task_run import TaskRun
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



#: Window the task-run aggregates cover. A constant rather than a setting:
#: changing it changes what the series means, and a time series whose
#: definition moved cannot be compared with its own past. Ninety days of rows
#: are retained, but a ninety-day average would hide a regression that started
#: yesterday, which is the case these metrics exist for.
_TASK_RUN_WINDOW = timedelta(hours=24)

#: Quantiles reported for task duration, and the suffix each is published
#: under. They go in the metric name rather than a ``quantile`` label: that
#: label belongs to Prometheus' summary type, which also promises ``_sum`` and
#: ``_count`` and a counter underneath. These are gauges over a sliding
#: window, so borrowing the label would describe them as something they are
#: not.
#:
#: p99 is left out on purpose. Beat schedules here range from every five
#: minutes to weekly, so a day's window holds one or two runs of the sparse
#: ones and a 99th percentile of two samples is just the maximum wearing a
#: statistic's name.
_DURATION_QUANTILES = ((0.5, "p50"), (0.95, "p95"))


async def _task_run_outcomes(session: AsyncSession) -> dict[tuple[str, str], int]:
    """Runs per (task, outcome) inside the window.

    Unfinished runs carry a NULL outcome and are counted under ``running``.
    They are not dropped: a task that starts and never reports back is the
    shape a killed worker leaves, and a series that omitted it would show the
    failure as an absence of data rather than as a number.
    """
    cutoff = datetime.now(UTC) - _TASK_RUN_WINDOW
    rows = (
        await session.execute(
            select(TaskRun.task_name, TaskRun.outcome, func.count())
            .where(TaskRun.started_at >= cutoff)
            .group_by(TaskRun.task_name, TaskRun.outcome)
        )
    ).all()
    return {
        (str(name), str(outcome) if outcome else "running"): int(count)
        for name, outcome, count in rows
    }


async def _task_run_durations(
    session: AsyncSession,
) -> dict[tuple[str, str], float]:
    """Duration quantiles per task, over finished runs inside the window.

    Computed in the database with ``percentile_cont`` rather than by pulling
    rows into Python: this runs on every scrape and the row count grows with
    scheduler traffic.
    """
    cutoff = datetime.now(UTC) - _TASK_RUN_WINDOW
    seconds = func.extract("epoch", TaskRun.finished_at - TaskRun.started_at)
    columns = [
        func.percentile_cont(q).within_group(seconds.asc()).label(suffix)
        for q, suffix in _DURATION_QUANTILES
    ]
    rows = (
        await session.execute(
            select(TaskRun.task_name, *columns)
            .where(TaskRun.started_at >= cutoff, TaskRun.finished_at.is_not(None))
            .group_by(TaskRun.task_name)
        )
    ).all()

    out: dict[tuple[str, str], float] = {}
    for row in rows:
        name = str(row[0])
        for i, (_, suffix) in enumerate(_DURATION_QUANTILES):
            value = row[i + 1]
            if value is not None:
                out[(name, suffix)] = float(value)
    return out


async def _task_run_last_recorded(session: AsyncSession) -> float:
    """Unix time of the most recent task-run row, 0 when there are none.

    This one watches the watcher. The recorder swallows its own errors so that
    recording history can never fail the work being recorded, which means a
    missing grant, an unrun migration or a revoked permission produces no
    symptom: tasks keep succeeding and the table quietly stays empty. A
    deployment whose scheduler runs every few minutes and whose newest row is
    hours old has lost its history without anything else noticing.

    No threshold is applied here. Beat schedules in this repo range from five
    minutes to weekly, and which of them a given deployment enables is a local
    fact, so the judgement belongs to whatever scrapes this.
    """
    newest = (
        await session.execute(select(func.max(TaskRun.started_at)))
    ).scalar_one_or_none()
    return newest.timestamp() if newest else 0.0


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

    # O5: what the background scheduler has been doing. Gauges over a fixed
    # window rather than counters, because task_runs has a retention sweep and
    # a counter that goes down reads to a collector as a process restart.
    outcomes = await _task_run_outcomes(session)
    document.append(
        _block(
            "trusca_task_runs_24h",
            "Background task runs in the last 24h by task and outcome. "
            "outcome=running counts runs that started and never reported an end.",
            "gauge",
            [
                ({"task": task, "outcome": outcome}, float(count))
                for (task, outcome), count in sorted(outcomes.items())
            ],
        )
    )

    durations = await _task_run_durations(session)
    for _, suffix in _DURATION_QUANTILES:
        document.append(
            _block(
                f"trusca_task_run_duration_seconds_{suffix}_24h",
                f"Task duration in seconds, {suffix} over runs that finished "
                f"in the last 24h.",
                "gauge",
                [
                    ({"task": task}, value)
                    for (task, this_suffix), value in sorted(durations.items())
                    if this_suffix == suffix
                ],
            )
        )

    document.append(
        _block(
            "trusca_task_runs_last_recorded_timestamp_seconds",
            "Unix time of the newest task-run row; 0 when the table is empty. "
            "Watches the recorder itself, which fails silently by design.",
            "gauge",
            [({}, await _task_run_last_recorded(session))],
        )
    )

    # M2 (concurrency plan 2026-08-22 §3.1): off by default and independent
    # of the endpoint's own switch, so a deployment that already scrapes the
    # six series above does not get a broker round trip on every poll unless
    # it asks for one. See queue_backlog_metrics_enabled().
    if queue_backlog_metrics_enabled():
        backlogs = await asyncio.to_thread(_broker_queue_backlogs)
        document.append(
            _block(
                "trusca_broker_queue_backlog",
                "Messages waiting in the Celery broker queue, not yet delivered to a worker.",
                "gauge",
                [
                    ({"queue": queue_name}, float(backlog))
                    for queue_name, backlog in backlogs.items()
                ],
            )
        )
        document.append(
            _block(
                "trusca_scan_queue_wait_seconds",
                "Age in seconds of the oldest scan still queued; 0 when nothing is queued.",
                "gauge",
                [({}, await _oldest_queued_scan_wait_seconds(session))],
            )
        )

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


def _broker_queue_backlogs() -> dict[str, int]:
    """Every Celery queue's length, read straight from the broker with ``LLEN``.

    Concurrency plan 2026-08-22 §1.1: the DB-derived ``active_scans`` count
    (admin_health_service) is queued+running together, so it cannot tell "the
    worker died and ten scans piled up" from "ten scans are running fine".
    This series answers a narrower question that count cannot: how many
    messages are sitting in the broker, unclaimed by any worker, right now.
    ``LLEN`` is O(1) and this deployment does not assign Celery message
    priorities (grep confirms no ``priority=`` call site), so the bare queue
    name is the one key that can hold anything; a deployment that starts
    using priorities would need to sum the priority-suffixed keys too.

    S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4) split the single
    queue this function used to read into ``trustedoss.scan`` and
    ``trustedoss.default``. Reading only ``task_default_queue`` after that
    split would silently stop measuring the scan queue - the half §1.1's
    slot-capacity math is actually about - so this reads both by name and
    returns one entry per queue, scan first (declaration order, so the
    document's sample order is stable scrape to scrape). S6 (``tasks.
    queue_backlog_alert``) and S7 (``services.scan_service.
    _estimate_scan_queue_wait_seconds``) both reuse this function directly
    rather than re-reading the broker a second time, so it is the one place
    this repository reads a Celery queue's length.

    Synchronous client, the same shape as admin_health_service's Redis probe
    (``_probe_redis``) and for the same reason: the caller offloads this to a
    worker thread so a slow broker cannot hold the event loop for the rest of
    the process. A broker that cannot be reached reports 0 for every queue
    rather than failing the scrape (module docstring N10: one series going
    quiet does not take the other six down with it).
    """
    from tasks.celery_app import _SCAN_QUEUE, celery_app

    default_queue = str(celery_app.conf.task_default_queue)
    queues = [_SCAN_QUEUE, default_queue]
    try:
        client = _redis.Redis.from_url(redis_url(), decode_responses=True)
        try:
            return {queue: int(client.llen(queue)) for queue in queues}  # type: ignore[arg-type]
        finally:
            client.close()  # type: ignore[no-untyped-call]
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics_broker_backlog_unavailable", error=str(exc)[:200])
        return dict.fromkeys(queues, 0)


async def _oldest_queued_scan_wait_seconds(session: AsyncSession) -> float:
    """How long the longest-waiting queued scan has been sitting there.

    Concurrency plan 2026-08-22 §1.1: the broker itself does not stamp a
    message with the time it was enqueued (Celery does not add one, and this
    deployment's tasks are dispatched from ``tasks/scan_*.py``, outside this
    unit's scope, so no header can be added here to carry one). ``scans.
    created_at`` is the measurable stand-in the docstring's "or at least
    something measurable" allows for: it is stamped at the same moment the
    scan is queued, by a column every scan already has.

    ``func.now()`` rather than the process clock, so this is one Postgres
    round trip comparing a column Postgres wrote against a timestamp Postgres
    produces, immune to any drift between this process's clock and the
    database's.
    """
    stmt = select(
        func.coalesce(
            func.max(func.extract("epoch", func.now() - Scan.created_at)),
            0,
        )
    ).where(Scan.status == "queued")
    value = (await session.execute(stmt)).scalar_one()
    return round(float(value or 0.0), 3)
