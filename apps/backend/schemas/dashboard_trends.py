# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Response models for ``GET /v1/dashboard/trends``.

Two kinds of number share one series, and the difference matters when reading
the chart:

* ``new_findings`` / ``resolved_findings`` are **flows** — what changed on that
  day, attributed to the day a scan finished. A day with no scan has zero flow
  because nothing was observed, not because nothing changed.
* ``critical_open`` / ``kev_open`` are **levels** — the portfolio's standing
  exposure on that day, carried forward from each project's most recent
  succeeded scan on or before it. A day with no scan repeats the previous
  level rather than dropping to zero.

``scan_count`` is what lets the UI tell those two apart honestly: it says
whether the level on that day was measured or inherited.
"""

from __future__ import annotations

import datetime
from enum import IntEnum

from pydantic import BaseModel, Field


class TrendWindow(IntEnum):
    """The windows the UI offers, and the only ones the route accepts.

    An ``IntEnum`` rather than ``Literal[7, 30, 90]``: a query string arrives
    as ``"7"``, and a literal of ints refuses to coerce it, so the literal
    version rejected every window a caller actually asked for while quietly
    accepting the default. This coerces the string and still refuses anything
    outside the set.
    """

    WEEK = 7
    MONTH = 30
    QUARTER = 90


class TrendPoint(BaseModel):
    """One day of the portfolio series."""

    date: datetime.date = Field(
        description=(
            "The UTC day, by scan start time. The last point of a series is "
            "today and therefore covers only the elapsed part of it — its "
            "flows keep rising until midnight."
        )
    )
    new_findings: int = Field(
        ge=0,
        description=(
            "Exposures open in a scan that finished this day and not open in "
            "the previous scan of the same lineage — same project, same scan "
            "kind, same git ref. A scan with no predecessor contributes "
            "nothing: scan retention deletes superseded scans, so 'no earlier "
            "scan on record' does not mean 'nothing came before', and "
            "counting its whole open set would fabricate a spike for exposure "
            "that had been there for months."
        ),
    )
    resolved_findings: int = Field(
        ge=0,
        description=(
            "The converse: open in the previous scan of the lineage and no "
            "longer open in this day's. Includes findings closed by triage or "
            "VEX, not only ones an upgrade removed."
        ),
    )
    critical_open: int = Field(
        ge=0,
        description=(
            "Open critical findings across every accessible project, counted "
            "from each project's latest succeeded scan on or before this day."
        ),
    )
    kev_open: int = Field(
        ge=0,
        description="Open findings on CISA known-exploited CVEs, same anchoring.",
    )
    scan_count: int = Field(
        ge=0,
        description=(
            "Succeeded scans that finished this day. Zero means the levels "
            "above were carried forward, not re-measured."
        ),
    )


class TrendTotals(BaseModel):
    """Flow sums over the whole window — the levels are not summable."""

    new_findings: int = Field(ge=0)
    resolved_findings: int = Field(ge=0)


class DashboardTrends(BaseModel):
    """Portfolio risk over time, scoped to the caller's accessible projects.

    The series is recomputed from current data on every request, not stored as
    it was observed. Two consequences are worth knowing before reading a point
    as history: a finding triaged today drops out of the days it was open on,
    and a project archived today is subtracted from every day of the window,
    including the days it was live. Both keep the chart consistent with what
    the rest of the product reports *now*, at the cost of yesterday's chart
    and today's disagreeing about the same past day.
    """

    period_days: int = Field(
        description="The requested window, in days. One of 7, 30, or 90."
    )
    start_date: datetime.date
    end_date: datetime.date = Field(
        description="Today, in UTC. The series is inclusive of both ends."
    )
    points: list[TrendPoint] = Field(
        default_factory=list,
        description="One entry per day, oldest first, with no gaps.",
    )
    totals: TrendTotals
    project_count: int = Field(
        ge=0,
        description=(
            "Accessible projects the series was computed over. Zero means the "
            "caller belongs to no team with projects — the series is all "
            "zeros rather than absent, so the widget renders an empty state "
            "instead of an error."
        ),
    )
