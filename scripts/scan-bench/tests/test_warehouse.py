# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Unit tests for scripts/scan-bench/warehouse.py.

Pure SQLite logic, no portal, no network. Run with:
    python3 -m pytest scripts/scan-bench/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import warehouse
from run_bench import BenchResult


@pytest.fixture
def conn(tmp_path):
    c = warehouse.connect(tmp_path / "warehouse.db")
    yield c
    c.close()


def _result(**overrides) -> BenchResult:
    defaults = dict(
        suite="fixtures",
        name="fixture: node",
        slug="fx-node",
        source_path="/tmp/fx-node",
        ecosystem="npm",
        scan_status="succeeded",
        component_count=42,
        cve_total=3,
        cve_critical=1,
        license_unknown=2,
        scan_duration_sec=12.5,
    )
    defaults.update(overrides)
    return BenchResult(**defaults)


def test_connect_creates_schema(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"runs", "scan_results", "run_metrics"} <= tables


def test_record_run_and_results_roundtrip(conn):
    run_id = warehouse.record_run(
        conn,
        suite="fixtures",
        started_at="2026-08-30T00:00:00+00:00",
        finished_at="2026-08-30T00:10:00+00:00",
        trusca_commit="abc123",
        host="test-host",
        portal_url="http://localhost:8000",
        target_count=1,
    )
    warehouse.record_results(conn, run_id, [_result()])

    summary = warehouse.run_summary(conn, run_id)
    assert summary["total"] == 1
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
    assert summary["component_count_total"] == 42
    assert summary["cve_total_sum"] == 3
    assert summary["run"]["trusca_commit"] == "abc123"


def test_record_results_flattens_notes_list(conn):
    run_id = warehouse.record_run(
        conn, suite="fixtures", started_at="t0", finished_at="t1",
        trusca_commit=None, host=None, portal_url=None, target_count=1,
    )
    r = _result()
    r.notes = ["empty zip", "overview 500"]
    warehouse.record_results(conn, run_id, [r])

    row = conn.execute("SELECT notes FROM scan_results WHERE run_id = ?", (run_id,)).fetchone()
    assert row["notes"] == "empty zip; overview 500"


def test_latest_and_previous_run_id(conn):
    ids = [
        warehouse.record_run(
            conn, suite="fixtures", started_at=f"t{i}", finished_at=f"t{i}",
            trusca_commit=None, host=None, portal_url=None, target_count=0,
        )
        for i in range(3)
    ]
    assert warehouse.latest_run_id(conn, "fixtures") == ids[-1]
    assert warehouse.previous_run_id(conn, "fixtures", ids[-1]) == ids[-2]
    assert warehouse.previous_run_id(conn, "fixtures", ids[0]) is None
    # A different suite doesn't leak into this one's history.
    assert warehouse.latest_run_id(conn, "realworld") is None


def test_diff_runs_detects_status_change_and_numeric_delta(conn):
    run_a = warehouse.record_run(
        conn, suite="fixtures", started_at="t0", finished_at="t0",
        trusca_commit=None, host=None, portal_url=None, target_count=2,
    )
    warehouse.record_results(conn, run_a, [
        _result(slug="fx-node", scan_status="succeeded", component_count=10, cve_total=1),
        _result(slug="fx-go", name="fixture: go", scan_status="succeeded", component_count=5),
    ])

    run_b = warehouse.record_run(
        conn, suite="fixtures", started_at="t1", finished_at="t1",
        trusca_commit=None, host=None, portal_url=None, target_count=2,
    )
    warehouse.record_results(conn, run_b, [
        _result(slug="fx-node", scan_status="failed", component_count=10, cve_total=1),
        _result(slug="fx-rust", name="fixture: rust", scan_status="succeeded", component_count=7),
    ])

    diff = warehouse.diff_runs(conn, run_a, run_b)

    assert diff["only_in_a"] == ["fx-go"]
    assert diff["only_in_b"] == ["fx-rust"]
    assert diff["status_changes"] == [
        {"slug": "fx-node", "name": "fixture: node", "from": "succeeded", "to": "failed"}
    ]
    # component_count is unchanged for fx-node (10 -> 10); only fields that
    # actually moved should be reported.
    assert diff["numeric_deltas"] == []


def test_diff_runs_reports_numeric_delta_when_value_changes(conn):
    run_a = warehouse.record_run(
        conn, suite="fixtures", started_at="t0", finished_at="t0",
        trusca_commit=None, host=None, portal_url=None, target_count=1,
    )
    warehouse.record_results(conn, run_a, [_result(slug="fx-node", cve_total=1)])

    run_b = warehouse.record_run(
        conn, suite="fixtures", started_at="t1", finished_at="t1",
        trusca_commit=None, host=None, portal_url=None, target_count=1,
    )
    warehouse.record_results(conn, run_b, [_result(slug="fx-node", cve_total=4)])

    diff = warehouse.diff_runs(conn, run_a, run_b)
    assert diff["numeric_deltas"] == [
        {"slug": "fx-node", "name": "fixture: node", "field": "cve_total", "from": 1, "to": 4, "delta": 3}
    ]


def test_record_metric_upserts(conn):
    run_id = warehouse.record_run(
        conn, suite="load", started_at="t0", finished_at="t0",
        trusca_commit=None, host=None, portal_url=None, target_count=0,
    )
    warehouse.record_metric(conn, run_id, "p95_ms", value=1420.0)
    warehouse.record_metric(conn, run_id, "p95_ms", value=1380.0)  # re-measured, should replace, not duplicate

    rows = conn.execute(
        "SELECT metric_value FROM run_metrics WHERE run_id = ? AND metric_key = 'p95_ms'",
        (run_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["metric_value"] == 1380.0
