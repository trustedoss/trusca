# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The vulnerability-database freshness series, and what it refuses to decide.

A database that stopped updating does not make scans fail. They keep running,
keep finding the vulnerabilities it knew about when it stopped, and report
success. Nothing in the system objects. That is the whole reason for a series
here rather than a check somewhere.

Two things are asserted beyond the happy path. The series stays present when
no database exists at all, because a missing series draws nothing and reads as
nothing being wrong. And no staleness verdict is published, because the
deployment's own refresh cadence is the only correct basis for one and that
lives in the collector.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from services import metrics_service


@dataclass
class _Status:
    """Only the two fields the metric reads."""

    last_update: datetime | None
    refresh_interval_hours: int


@pytest.fixture
def status(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(snapshot: _Status) -> None:
        monkeypatch.setattr(
            metrics_service, "get_trivy_db_status_cached", lambda: snapshot
        )

    return _install


def test_the_last_update_is_published_as_unix_time(status: Any) -> None:
    moment = datetime(2026, 7, 18, 12, 57, 54, tzinfo=UTC)
    status(_Status(last_update=moment, refresh_interval_hours=168))

    last_update, _ = metrics_service._vuln_db_freshness()

    assert last_update == moment.timestamp()


def test_the_configured_interval_is_published_beside_it(status: Any) -> None:
    """The collector needs both to decide anything.

    The timestamp says when the data stopped changing; the interval says how
    long that is supposed to be able to go on. An air-gapped install mirroring
    on a slower cadence is not broken, and only the interval distinguishes it
    from one that is.
    """
    status(_Status(last_update=datetime.now(UTC), refresh_interval_hours=168))

    _, interval = metrics_service._vuln_db_freshness()

    assert interval == 168.0


def test_no_database_still_publishes_the_series(status: Any) -> None:
    """Zero, not an absent series.

    "No database has ever been downloaded" is the worst state this can be in,
    and it is exactly the state where a series that disappeared would leave a
    dashboard looking clean.
    """
    status(_Status(last_update=None, refresh_interval_hours=168))

    last_update, interval = metrics_service._vuln_db_freshness()

    assert last_update == 0.0
    assert interval == 168.0


def test_a_long_stale_database_is_reported_as_a_plain_timestamp(
    status: Any,
) -> None:
    """No verdict, no clamping, no special value for "too old".

    A real deployment was found 46 days behind while every scan succeeded. The
    series reports the timestamp it has; whether 46 days is a problem depends
    on the refresh cadence, which is why that is published alongside rather
    than folded into a judgement here.
    """
    stale = datetime(2026, 7, 18, 12, 57, 54, tzinfo=UTC)
    status(_Status(last_update=stale, refresh_interval_hours=168))

    last_update, _ = metrics_service._vuln_db_freshness()

    assert last_update == stale.timestamp()


def test_the_accessor_never_reads_the_panel_s_verdict() -> None:
    """The fresh / stale bucket the snapshot carries is not published.

    That classification exists for a screen. A series carrying it would freeze
    one judgement into the metric and put it beyond the collector's reach: a
    deployment cannot override a label it is handed, and the right threshold
    depends on a refresh cadence only the deployment knows.

    Asserted on the attribute access rather than on words in the source. The
    first two versions of this test matched the strings "freshness" and
    "fresh", and both failed on names that legitimately contain them: the
    accessor is named for what it measures, and "refresh" contains "fresh".
    A test of spelling is not a test of behaviour.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(metrics_service._vuln_db_freshness))
    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }

    assert "freshness" not in read, (
        "the metric reads the snapshot's freshness bucket; publish the "
        "timestamp and the interval and let the collector decide"
    )
    assert read >= {"last_update", "refresh_interval_hours"}
