#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Read the scan-bench warehouse: run history and run-over-run diffs.

    python3 warehouse_report.py history --suite fixtures
    python3 warehouse_report.py compare --suite fixtures
    python3 warehouse_report.py compare --suite fixtures --run-a 3 --run-b 7
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import warehouse

DEFAULT_WAREHOUSE_DB = Path(__file__).resolve().parent / "warehouse.db"


def cmd_history(conn, args: argparse.Namespace) -> int:
    rows = conn.execute(
        "SELECT id, started_at, target_count, trusca_commit, host FROM runs "
        "WHERE suite = ? ORDER BY id DESC LIMIT ?",
        (args.suite, args.limit),
    ).fetchall()
    if not rows:
        print(f"no runs recorded for suite={args.suite!r}")
        return 1
    print(f"{'run':>4}  {'started_at':<26}  {'targets':>7}  {'succeeded':>9}  {'commit':<10}  host")
    for row in rows:
        summary = warehouse.run_summary(conn, row["id"])
        commit = (row["trusca_commit"] or "")[:10]
        print(
            f"{row['id']:>4}  {row['started_at']:<26}  {summary['total']:>7}  "
            f"{summary['succeeded']:>9}  {commit:<10}  {row['host'] or ''}"
        )
    return 0


def cmd_compare(conn, args: argparse.Namespace) -> int:
    run_b = args.run_b or warehouse.latest_run_id(conn, args.suite)
    if run_b is None:
        print(f"no runs recorded for suite={args.suite!r}")
        return 1
    run_a = args.run_a or warehouse.previous_run_id(conn, args.suite, run_b)
    if run_a is None:
        print(f"run {run_b} has no earlier run of suite={args.suite!r} to compare against "
              "(it is the first recorded run, treat it as the baseline)")
        return 0

    diff = warehouse.diff_runs(conn, run_a, run_b)
    print(f"comparing run {run_a} -> run {run_b} (suite={args.suite})\n")

    if diff["only_in_a"]:
        print(f"dropped from run {run_a} ({len(diff['only_in_a'])}):")
        for slug in diff["only_in_a"]:
            print(f"  - {slug}")
        print()
    if diff["only_in_b"]:
        print(f"added in run {run_b} ({len(diff['only_in_b'])}):")
        for slug in diff["only_in_b"]:
            print(f"  + {slug}")
        print()

    if diff["status_changes"]:
        print(f"status changes ({len(diff['status_changes'])}):")
        for c in diff["status_changes"]:
            print(f"  {c['slug']} ({c['name']}): {c['from']} -> {c['to']}")
        print()
    else:
        print("status changes: none\n")

    if diff["numeric_deltas"]:
        print(f"numeric deltas ({len(diff['numeric_deltas'])}):")
        for d in diff["numeric_deltas"]:
            sign = "+" if d["delta"] > 0 else ""
            print(f"  {d['slug']} {d['field']}: {d['from']} -> {d['to']} ({sign}{d['delta']:g})")
    else:
        print("numeric deltas: none")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--warehouse-db",
        default=os.getenv("SCAN_BENCH_WAREHOUSE_DB", str(DEFAULT_WAREHOUSE_DB)),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_history = sub.add_parser("history", help="list recorded runs for a suite")
    p_history.add_argument("--suite", required=True)
    p_history.add_argument("--limit", type=int, default=20)

    p_compare = sub.add_parser("compare", help="diff two runs (default: latest vs previous)")
    p_compare.add_argument("--suite", required=True)
    p_compare.add_argument("--run-a", type=int, default=None)
    p_compare.add_argument("--run-b", type=int, default=None)

    args = parser.parse_args()
    db_path = Path(args.warehouse_db)
    if not db_path.exists():
        print(f"no warehouse at {db_path} yet, run run_bench.py at least once first", file=sys.stderr)
        return 1

    conn = warehouse.connect(db_path)
    try:
        if args.command == "history":
            return cmd_history(conn, args)
        return cmd_compare(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
