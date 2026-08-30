#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""scan-bench warehouse, persists bench runs across invocations.

``run_bench.py`` used to drop CSV/markdown/jsonl into ``out/`` and stop there,
so nothing compared one run to the next and nothing survived a directory
clean-out. This module is the durable side: every run and every per-target
result it produced gets a row here, in a SQLite file separate from the
portal's own Postgres database.

SQLite rather than the portal's Postgres on purpose. This is tooling data,
not product data, and self-resource-validation-plan-2026-08-30.md's item 1
called out that the portal's own retention policy
(``SCAN_RETENTION_SUPERSEDED_GRACE_DAYS``) deletes findings after a week, so
leaning on the product database would just move the same loss somewhere
else. A single file that any host running ``run_bench.py`` can point at (via
``SCAN_BENCH_WAREHOUSE_DB``) needs no server to provision on a cohort runner
that otherwise has nothing else needing Postgres.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suite TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trusca_commit TEXT,
    host TEXT,
    portal_url TEXT,
    target_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    suite TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_path TEXT,
    ecosystem TEXT,
    project_id TEXT,
    scan_id TEXT,
    archive_bytes INTEGER,
    scan_status TEXT,
    scan_started_at TEXT,
    scan_finished_at TEXT,
    scan_duration_sec REAL,
    component_count INTEGER,
    direct_count INTEGER,
    license_allowed INTEGER,
    license_conditional INTEGER,
    license_forbidden INTEGER,
    license_unknown INTEGER,
    cve_total INTEGER,
    cve_critical INTEGER,
    cve_high INTEGER,
    cve_medium INTEGER,
    cve_low INTEGER,
    cve_info INTEGER,
    cve_unknown INTEGER,
    risk_score REAL,
    security_score REAL,
    license_score REAL,
    error TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_results_run ON scan_results(run_id);
CREATE INDEX IF NOT EXISTS idx_scan_results_slug ON scan_results(suite, slug);

-- Run-level numbers that don't belong to any one target: load-test SLOs
-- (S1), precision/recall (S2), cohort completion rate (S3), soak uptime
-- (S4). A key-value table rather than a column per future indicator, since
-- the plan's list of what counts as evidence (self-resource-validation-plan
-- §4) already spans four different stages that don't share a shape.
CREATE TABLE IF NOT EXISTS run_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    metric_key TEXT NOT NULL,
    metric_value REAL,
    metric_text TEXT,
    UNIQUE(run_id, metric_key)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the warehouse and ensure its schema exists.

    WAL mode so a concurrent ``warehouse_report.py`` read doesn't block a
    ``run_bench.py`` write in progress, since the two are meant to run side by
    side on a cohort host.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def record_run(
    conn: sqlite3.Connection,
    *,
    suite: str,
    started_at: str,
    finished_at: str,
    trusca_commit: str | None,
    host: str | None,
    portal_url: str | None,
    target_count: int,
    notes: str = "",
) -> int:
    with closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO runs
                (suite, started_at, finished_at, trusca_commit, host,
                 portal_url, target_count, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (suite, started_at, finished_at, trusca_commit, host, portal_url,
             target_count, notes),
        )
        conn.commit()
        return int(cur.lastrowid)


# The BenchResult dataclass fields that map straight onto scan_results
# columns. ``suite``/``name``/``slug`` are handled separately below because
# every row also needs the caller's ``run_id``.
_RESULT_COLUMNS = (
    "suite", "name", "slug", "source_path", "ecosystem", "project_id",
    "scan_id", "archive_bytes", "scan_status", "scan_started_at",
    "scan_finished_at", "scan_duration_sec", "component_count",
    "direct_count", "license_allowed", "license_conditional",
    "license_forbidden", "license_unknown", "cve_total", "cve_critical",
    "cve_high", "cve_medium", "cve_low", "cve_info", "cve_unknown",
    "risk_score", "security_score", "license_score", "error",
)


def record_results(conn: sqlite3.Connection, run_id: int, results: Iterable[Any]) -> None:
    """Persist one run's ``BenchResult`` rows (or any object/dict with the
    same fields, kept structural rather than importing the dataclass so
    this module has no dependency on ``run_bench.py``).
    """
    rows = []
    for r in results:
        d = asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r)
        notes = d.get("notes", "")
        if isinstance(notes, list):
            notes = "; ".join(notes)
        values = [d.get(c) for c in _RESULT_COLUMNS] + [notes]
        rows.append(values)

    placeholders = ", ".join(["?"] * (len(_RESULT_COLUMNS) + 1))
    columns = ", ".join((*_RESULT_COLUMNS, "notes"))
    with closing(conn.cursor()) as cur:
        cur.executemany(
            f"INSERT INTO scan_results (run_id, {columns}) "
            f"VALUES ({'?'}, {placeholders})",
            [[run_id, *row] for row in rows],
        )
        conn.commit()


def record_metric(
    conn: sqlite3.Connection,
    run_id: int,
    key: str,
    *,
    value: float | None = None,
    text: str | None = None,
) -> None:
    """Upsert one run-level metric (S1 load numbers, S2 precision/recall, ...)."""
    conn.execute(
        """
        INSERT INTO run_metrics (run_id, metric_key, metric_value, metric_text)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(run_id, metric_key)
        DO UPDATE SET metric_value = excluded.metric_value,
                      metric_text = excluded.metric_text
        """,
        (run_id, key, value, text),
    )
    conn.commit()


def previous_run_id(conn: sqlite3.Connection, suite: str, before_run_id: int) -> int | None:
    """The most recent run of ``suite`` strictly before ``before_run_id``."""
    row = conn.execute(
        "SELECT id FROM runs WHERE suite = ? AND id < ? ORDER BY id DESC LIMIT 1",
        (suite, before_run_id),
    ).fetchone()
    return int(row["id"]) if row else None


def latest_run_id(conn: sqlite3.Connection, suite: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM runs WHERE suite = ? ORDER BY id DESC LIMIT 1",
        (suite,),
    ).fetchone()
    return int(row["id"]) if row else None


def run_summary(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(f"no run {run_id}")
    agg = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN scan_status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
            SUM(CASE WHEN scan_status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN scan_status NOT IN ('succeeded', 'failed') OR scan_status IS NULL
                     THEN 1 ELSE 0 END) AS other,
            SUM(component_count) AS component_count_total,
            SUM(cve_total) AS cve_total_sum,
            SUM(license_unknown) AS license_unknown_sum
        FROM scan_results WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return {
        "run": dict(run),
        "total": agg["total"] or 0,
        "succeeded": agg["succeeded"] or 0,
        "failed": agg["failed"] or 0,
        "other": agg["other"] or 0,
        "component_count_total": agg["component_count_total"] or 0,
        "cve_total_sum": agg["cve_total_sum"] or 0,
        "license_unknown_sum": agg["license_unknown_sum"] or 0,
    }


def diff_runs(conn: sqlite3.Connection, run_a: int, run_b: int) -> dict[str, Any]:
    """Per-target deltas between two runs of the same suite, ``run_a`` -> ``run_b``.

    No pass/fail verdict on purpose. The plan document's principle (§4) is
    that the first observed run is the baseline and nothing gets a hardcoded
    threshold invented ahead of an actual measurement, this returns deltas
    for a human to read, not a gate.
    """
    a = {
        row["slug"]: dict(row)
        for row in conn.execute("SELECT * FROM scan_results WHERE run_id = ?", (run_a,))
    }
    b = {
        row["slug"]: dict(row)
        for row in conn.execute("SELECT * FROM scan_results WHERE run_id = ?", (run_b,))
    }

    status_changes = []
    numeric_deltas = []
    only_in_a = sorted(set(a) - set(b))
    only_in_b = sorted(set(b) - set(a))

    for slug in sorted(set(a) & set(b)):
        ra, rb = a[slug], b[slug]
        if ra["scan_status"] != rb["scan_status"]:
            status_changes.append(
                {"slug": slug, "name": rb["name"], "from": ra["scan_status"], "to": rb["scan_status"]}
            )
        for field in ("component_count", "cve_total", "license_unknown", "scan_duration_sec"):
            va, vb = ra.get(field), rb.get(field)
            if va is None or vb is None or va == vb:
                continue
            numeric_deltas.append(
                {"slug": slug, "name": rb["name"], "field": field, "from": va, "to": vb, "delta": vb - va}
            )

    return {
        "run_a": run_a,
        "run_b": run_b,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "status_changes": status_changes,
        "numeric_deltas": numeric_deltas,
    }
