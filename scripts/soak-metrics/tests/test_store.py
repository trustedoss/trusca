# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Unit tests for scripts/soak-metrics/store.py.

Pure SQLite + text-parsing logic, no network. Run with:
    python3 -m pytest scripts/soak-metrics/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import store

SAMPLE_BODY = (
    "# HELP trusca_projects_total Projects that exist, archived ones included.\n"
    "# TYPE trusca_projects_total gauge\n"
    "trusca_projects_total 12\n"
    "# HELP trusca_scans_total Scans by status.\n"
    "# TYPE trusca_scans_total gauge\n"
    'trusca_scans_total{status="succeeded"} 40\n'
    'trusca_scans_total{status="queued"} 2\n'
    "# HELP trusca_workspace_disk_used_ratio Workspace volume in use, 0 to 1.\n"
    "# TYPE trusca_workspace_disk_used_ratio gauge\n"
    "trusca_workspace_disk_used_ratio 0.4123\n"
)


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "soak.db")
    yield c
    c.close()


def test_parse_samples_reads_bare_and_labelled_gauges():
    samples = store.parse_samples(SAMPLE_BODY)
    by_name = {}
    for s in samples:
        by_name.setdefault(s.name, []).append(s)

    assert by_name["trusca_projects_total"][0].value == 12
    assert by_name["trusca_projects_total"][0].labels == {}

    scans = {s.labels["status"]: s.value for s in by_name["trusca_scans_total"]}
    assert scans == {"succeeded": 40, "queued": 2}

    assert by_name["trusca_workspace_disk_used_ratio"][0].value == pytest.approx(0.4123)


def test_parse_samples_skips_comments_and_blank_lines():
    body = "# just a comment\n\n\ntrusca_projects_total 3\n"
    samples = store.parse_samples(body)
    assert len(samples) == 1
    assert samples[0].name == "trusca_projects_total"
    assert samples[0].value == 3


def test_parse_samples_ignores_malformed_lines():
    body = "this is not a metric line\ntrusca_projects_total 5\n"
    samples = store.parse_samples(body)
    assert len(samples) == 1


def test_connect_creates_schema(conn):
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "scrapes" in tables


def test_record_scrape_success_and_failure(conn):
    ok_id = store.record_scrape(conn, status_code=200, body=SAMPLE_BODY, error=None)
    fail_id = store.record_scrape(conn, status_code=404, body=None, error="HTTP 404")
    assert ok_id != fail_id

    rows = list(store.iter_scrapes(conn))
    assert len(rows) == 2
    assert rows[0]["body"] == SAMPLE_BODY
    assert rows[1]["error"] == "HTTP 404"
    assert rows[1]["body"] is None


def test_iter_scrapes_filters_by_time_range(conn):
    store.record_scrape(conn, status_code=200, body=SAMPLE_BODY, error=None)
    all_rows = list(store.iter_scrapes(conn))
    scraped_at = all_rows[0]["scraped_at"]

    # since strictly after the only row's timestamp -> nothing.
    assert list(store.iter_scrapes(conn, since="9999-01-01T00:00:00+00:00")) == []
    # since at-or-before it -> the row comes back.
    assert len(list(store.iter_scrapes(conn, since=scraped_at))) == 1
    assert len(list(store.iter_scrapes(conn, until="0001-01-01T00:00:00+00:00"))) == 0
    assert len(list(store.iter_scrapes(conn, until=scraped_at))) == 1


def test_known_metric_names_covers_every_series_seen(conn):
    store.record_scrape(conn, status_code=200, body=SAMPLE_BODY, error=None)
    store.record_scrape(conn, status_code=404, body=None, error="boom")  # ignored, no body

    names = store.known_metric_names(conn)
    assert names == sorted(
        {"trusca_projects_total", "trusca_scans_total", "trusca_workspace_disk_used_ratio"}
    )
