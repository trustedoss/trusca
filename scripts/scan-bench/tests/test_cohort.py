# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Unit tests for scripts/scan-bench/cohort.py.

Pure SQLite logic, no portal, no network. Run with:
    python3 -m pytest scripts/scan-bench/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import cohort


@pytest.fixture
def conn(tmp_path):
    c = cohort.connect(tmp_path / "cohort.db")
    yield c
    c.close()


SPEC = {
    "teams": [
        {
            "name": "example-org",
            "slug": "example-org",
            "repos": [
                {"name": "repo-a", "slug": "repo-a", "git_url": "https://github.com/example/repo-a.git"},
                {"name": "repo-b", "slug": "repo-b", "git_url": "https://github.com/example/repo-b.git"},
            ],
        },
        {
            "name": "other-org",
            "slug": "other-org",
            "repos": [
                {"name": "repo-c", "slug": "repo-c", "git_url": "https://github.com/other/repo-c.git"},
            ],
        },
    ]
}


def test_load_spec_flattens_teams_and_repos():
    rows = cohort.load_spec(SPEC)
    assert len(rows) == 3
    assert rows[0] == {
        "team_name": "example-org", "team_slug": "example-org",
        "project_name": "repo-a", "project_slug": "repo-a",
        "git_url": "https://github.com/example/repo-a.git",
    }


def test_seed_targets_is_idempotent(conn):
    rows = cohort.load_spec(SPEC)
    inserted_first = cohort.seed_targets(conn, "cohort-1", rows)
    inserted_second = cohort.seed_targets(conn, "cohort-1", rows)
    assert inserted_first == 3
    assert inserted_second == 0

    total = conn.execute(
        "SELECT COUNT(*) FROM cohort_targets WHERE cohort = ?", ("cohort-1",)
    ).fetchone()[0]
    assert total == 3


def test_seed_targets_scopes_by_cohort(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    cohort.seed_targets(conn, "cohort-2", rows)
    total = conn.execute("SELECT COUNT(*) FROM cohort_targets").fetchone()[0]
    assert total == 6


def test_targets_to_register_excludes_registered(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    pending = cohort.targets_to_register(conn, "cohort-1")
    assert len(pending) == 3

    cohort.mark_progress(conn, pending[0]["id"], team_id="t1", project_id="p1", scan_id="s1")
    still_pending = cohort.targets_to_register(conn, "cohort-1")
    assert len(still_pending) == 2
    assert pending[0]["id"] not in {r["id"] for r in still_pending}


def test_targets_to_register_include_failed(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    pending = cohort.targets_to_register(conn, "cohort-1")
    cohort.mark_failed(conn, pending[0]["id"], "RuntimeError: boom")

    without_failed = cohort.targets_to_register(conn, "cohort-1")
    with_failed = cohort.targets_to_register(conn, "cohort-1", include_failed=True)
    assert len(without_failed) == 2
    assert len(with_failed) == 3


def test_mark_progress_only_sets_registered_once_scan_id_present(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    row = cohort.targets_to_register(conn, "cohort-1")[0]

    cohort.mark_progress(conn, row["id"], team_id="t1")
    mid = conn.execute("SELECT * FROM cohort_targets WHERE id = ?", (row["id"],)).fetchone()
    assert mid["team_id"] == "t1"
    assert mid["register_status"] == "pending"

    cohort.mark_progress(conn, row["id"], project_id="p1")
    cohort.mark_progress(conn, row["id"], scan_id="s1")
    done = conn.execute("SELECT * FROM cohort_targets WHERE id = ?", (row["id"],)).fetchone()
    assert done["scan_id"] == "s1"
    assert done["register_status"] == "registered"


def test_mark_failed_increments_attempts_and_clears_on_success(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    row = cohort.targets_to_register(conn, "cohort-1")[0]

    cohort.mark_failed(conn, row["id"], "boom 1")
    cohort.mark_failed(conn, row["id"], "boom 2")
    twice_failed = conn.execute("SELECT * FROM cohort_targets WHERE id = ?", (row["id"],)).fetchone()
    assert twice_failed["attempts"] == 2
    assert twice_failed["register_status"] == "failed"
    assert twice_failed["error"] == "boom 2"

    cohort.mark_progress(conn, row["id"], team_id="t1", project_id="p1", scan_id="s1")
    recovered = conn.execute("SELECT * FROM cohort_targets WHERE id = ?", (row["id"],)).fetchone()
    assert recovered["register_status"] == "registered"
    assert recovered["error"] is None


def test_targets_awaiting_scan_excludes_terminal_statuses(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    for i, row in enumerate(cohort.targets_to_register(conn, "cohort-1")):
        cohort.mark_progress(conn, row["id"], team_id=f"t{i}", project_id=f"p{i}", scan_id=f"s{i}")

    awaiting = cohort.targets_awaiting_scan(conn, "cohort-1")
    assert len(awaiting) == 3

    cohort.update_scan_status(conn, awaiting[0]["id"], scan_status="succeeded")
    cohort.update_scan_status(conn, awaiting[1]["id"], scan_status="failed", error="boom")
    still_awaiting = cohort.targets_awaiting_scan(conn, "cohort-1")
    assert len(still_awaiting) == 1
    assert still_awaiting[0]["id"] == awaiting[2]["id"]


def test_summary_counts_and_lists_failures(conn):
    rows = cohort.load_spec(SPEC)
    cohort.seed_targets(conn, "cohort-1", rows)
    targets = cohort.targets_to_register(conn, "cohort-1")

    cohort.mark_progress(conn, targets[0]["id"], team_id="t0", project_id="p0", scan_id="s0")
    cohort.update_scan_status(conn, targets[0]["id"], scan_status="succeeded")

    cohort.mark_progress(conn, targets[1]["id"], team_id="t1", project_id="p1", scan_id="s1")
    cohort.update_scan_status(conn, targets[1]["id"], scan_status="failed", error="clone failed")

    cohort.mark_failed(conn, targets[2]["id"], "team create 409 unexpected")

    s = cohort.summary(conn, "cohort-1")
    assert s["total"] == 3
    assert s["by_register_status"] == {"registered": 2, "failed": 1}
    assert s["by_scan_status"] == {"succeeded": 1, "failed": 1}
    assert len(s["failed"]) == 2  # one scan failure + one registration failure
