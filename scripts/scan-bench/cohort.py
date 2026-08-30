#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""cohort registry, tracks bulk team/project registration for S3.

self-resource-validation-plan-2026-08-30.md §6-2: there was no way to
register many repositories as projects at once (the only bulk-registration
path is self-signup, and it makes one project at a time), and no record of
which of a large batch succeeded, which failed, or why. A cohort run (S3:
~120 teams x 10-30 projects each) has to survive being interrupted and
resumed without re-creating everything or losing track of what already
exists, so this is a small SQLite state machine rather than a plain script
loop: every target's registration and scan status persists across process
restarts.

Three stages per target, tracked by which id columns are populated rather
than a separate stage enum:
    team_id NULL              -> team creation not done yet (or failed)
    team_id set, project_id NULL -> project creation not done yet (or failed)
    project_id set, scan_id NULL -> scan trigger not done yet (or failed)
    scan_id set                -> registered; scan_status tracks the scan itself
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL_SCAN_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cohort_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort TEXT NOT NULL,
    team_slug TEXT NOT NULL,
    team_name TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    project_name TEXT NOT NULL,
    git_url TEXT NOT NULL,
    team_id TEXT,
    project_id TEXT,
    scan_id TEXT,
    register_status TEXT NOT NULL DEFAULT 'pending',
    scan_status TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(cohort, team_slug, project_slug)
);

CREATE INDEX IF NOT EXISTS idx_cohort_targets_cohort ON cohort_targets(cohort);
CREATE INDEX IF NOT EXISTS idx_cohort_targets_register_status
    ON cohort_targets(cohort, register_status);
CREATE INDEX IF NOT EXISTS idx_cohort_targets_scan_status
    ON cohort_targets(cohort, scan_status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_spec(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten the input spec's ``teams: [{repos: [...]}]`` shape into rows.

    Kept as a pure function (no I/O) so a malformed spec fails before any
    target has been touched, rather than partway through a 1,800-target run.
    """
    rows: list[dict[str, str]] = []
    for team in spec.get("teams", []):
        team_name = team["name"]
        team_slug = team["slug"]
        for repo in team.get("repos", []):
            rows.append({
                "team_name": team_name,
                "team_slug": team_slug,
                "project_name": repo["name"],
                "project_slug": repo["slug"],
                "git_url": repo["git_url"],
            })
    return rows


def seed_targets(conn: sqlite3.Connection, cohort: str, rows: list[dict[str, str]]) -> int:
    """Insert any row not already tracked for this cohort. Idempotent."""
    now = _now()
    with closing(conn.cursor()) as cur:
        cur.executemany(
            """
            INSERT INTO cohort_targets
                (cohort, team_slug, team_name, project_slug, project_name,
                 git_url, register_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(cohort, team_slug, project_slug) DO NOTHING
            """,
            [
                (cohort, r["team_slug"], r["team_name"], r["project_slug"],
                 r["project_name"], r["git_url"], now, now)
                for r in rows
            ],
        )
        conn.commit()
        return cur.rowcount if cur.rowcount is not None else 0


def targets_to_register(
    conn: sqlite3.Connection, cohort: str, *, include_failed: bool = False
) -> list[sqlite3.Row]:
    statuses = ("pending", "failed") if include_failed else ("pending",)
    placeholders = ", ".join(["?"] * len(statuses))
    return conn.execute(
        f"SELECT * FROM cohort_targets WHERE cohort = ? AND register_status IN ({placeholders}) "
        "ORDER BY id",
        (cohort, *statuses),
    ).fetchall()


def mark_progress(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    team_id: str | None = None,
    project_id: str | None = None,
    scan_id: str | None = None,
) -> None:
    """Record whatever got created so a retry resumes past it instead of
    re-creating (the team/project creation paths are themselves idempotent
    on slug conflict, but skipping the call entirely is one less request).

    A single static statement with ``COALESCE`` rather than building the SET
    clause from the arguments that were actually passed: a dynamic column
    list built with string formatting is exactly the shape semgrep's
    sqlalchemy-execute-raw-query rule flags, even though every value here
    still travels as a bound parameter and nothing here is user input.
    """
    conn.execute(
        """
        UPDATE cohort_targets
        SET team_id = COALESCE(?, team_id),
            project_id = COALESCE(?, project_id),
            scan_id = COALESCE(?, scan_id),
            register_status = CASE WHEN ? IS NOT NULL THEN 'registered' ELSE register_status END,
            error = CASE WHEN ? IS NOT NULL THEN NULL ELSE error END,
            updated_at = ?
        WHERE id = ?
        """,
        (team_id, project_id, scan_id, scan_id, scan_id, _now(), row_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, row_id: int, error: str) -> None:
    conn.execute(
        """
        UPDATE cohort_targets
        SET register_status = 'failed', error = ?, attempts = attempts + 1, updated_at = ?
        WHERE id = ?
        """,
        (error[:2000], _now(), row_id),
    )
    conn.commit()


def targets_awaiting_scan(conn: sqlite3.Connection, cohort: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM cohort_targets
        WHERE cohort = ? AND register_status = 'registered'
          AND (scan_status IS NULL OR scan_status NOT IN ('succeeded', 'failed', 'cancelled', 'timeout'))
        ORDER BY id
        """,
        (cohort,),
    ).fetchall()


def update_scan_status(
    conn: sqlite3.Connection, row_id: int, *, scan_status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE cohort_targets SET scan_status = ?, error = COALESCE(?, error), updated_at = ? WHERE id = ?",
        (scan_status, error[:2000] if error else None, _now(), row_id),
    )
    conn.commit()


def summary(conn: sqlite3.Connection, cohort: str) -> dict[str, Any]:
    total = conn.execute(
        "SELECT COUNT(*) FROM cohort_targets WHERE cohort = ?", (cohort,)
    ).fetchone()[0]
    by_register = {
        row["register_status"]: row["n"]
        for row in conn.execute(
            "SELECT register_status, COUNT(*) AS n FROM cohort_targets "
            "WHERE cohort = ? GROUP BY register_status",
            (cohort,),
        )
    }
    by_scan = {
        (row["scan_status"] or "(not triggered)"): row["n"]
        for row in conn.execute(
            "SELECT scan_status, COUNT(*) AS n FROM cohort_targets "
            "WHERE cohort = ? AND register_status = 'registered' GROUP BY scan_status",
            (cohort,),
        )
    }
    failed = conn.execute(
        "SELECT team_slug, project_slug, error FROM cohort_targets "
        "WHERE cohort = ? AND (register_status = 'failed' OR scan_status = 'failed') "
        "ORDER BY id",
        (cohort,),
    ).fetchall()
    return {
        "total": total,
        "by_register_status": by_register,
        "by_scan_status": by_scan,
        "failed": [dict(row) for row in failed],
    }
