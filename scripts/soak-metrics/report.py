#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""report.py, reads back what collector.py has logged.

    python3 report.py summary
    python3 report.py history --metric trusca_scans_total
    python3 report.py failures
    python3 report.py csv --metric trusca_workspace_disk_used_ratio --out disk.csv
"""

from __future__ import annotations

import argparse
import csv as csv_module
import os
import sys
from pathlib import Path

import store

DEFAULT_DB = Path(__file__).resolve().parent / "soak-metrics.db"


def _label_str(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


def cmd_summary(conn) -> int:
    rows = list(store.iter_scrapes(conn))
    if not rows:
        print("no scrapes recorded yet")
        return 1
    ok = [r for r in rows if r["body"] is not None]
    failed = [r for r in rows if r["body"] is None]
    print(f"scrapes: {len(rows)} total, {len(ok)} ok, {len(failed)} failed")
    print(f"window: {rows[0]['scraped_at']} .. {rows[-1]['scraped_at']}")
    if not ok:
        return 0

    print("\nlast successful scrape's samples:")
    for sample in store.parse_samples(ok[-1]["body"]):
        print(f"  {sample.name}{_label_str(sample.labels)} = {sample.value:g}")

    print("\nfirst vs last, per metric (bare/no-label series only):")
    first_samples = {s.name: s.value for s in store.parse_samples(ok[0]["body"]) if not s.labels}
    last_samples = {s.name: s.value for s in store.parse_samples(ok[-1]["body"]) if not s.labels}
    for name in sorted(set(first_samples) & set(last_samples)):
        first_v, last_v = first_samples[name], last_samples[name]
        delta = last_v - first_v
        sign = "+" if delta > 0 else ""
        print(f"  {name}: {first_v:g} -> {last_v:g} ({sign}{delta:g})")
    return 0


def cmd_history(conn, metric: str) -> int:
    printed = 0
    for row in store.iter_scrapes(conn):
        if row["body"] is None:
            continue
        for sample in store.parse_samples(row["body"]):
            if sample.name == metric:
                print(f"{row['scraped_at']}  {_label_str(sample.labels)}  {sample.value:g}")
                printed += 1
    if not printed:
        known = store.known_metric_names(conn)
        print(f"no samples for {metric!r}. known metrics: {', '.join(known) or '(none scraped yet)'}")
        return 1
    return 0


def cmd_failures(conn) -> int:
    rows = [r for r in store.iter_scrapes(conn) if r["body"] is None]
    if not rows:
        print("no failed scrapes")
        return 0
    for row in rows:
        print(f"{row['scraped_at']}  status={row['status_code']}  {row['error']}")
    return 0


def cmd_csv(conn, metric: str, out_path: str) -> int:
    rows_out = []
    for row in store.iter_scrapes(conn):
        if row["body"] is None:
            continue
        for sample in store.parse_samples(row["body"]):
            if sample.name == metric:
                rows_out.append((row["scraped_at"], _label_str(sample.labels), sample.value))
    if not rows_out:
        print(f"no samples for {metric!r}", file=sys.stderr)
        return 1
    with open(out_path, "w", newline="") as fh:
        writer = csv_module.writer(fh)
        writer.writerow(["scraped_at", "labels", "value"])
        writer.writerows(rows_out)
    print(f"wrote {len(rows_out)} row(s) to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.getenv("SOAK_METRICS_DB", str(DEFAULT_DB)))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="scrape counts + first/last sample of every bare-label metric")

    p_history = sub.add_parser("history", help="every recorded sample for one metric")
    p_history.add_argument("--metric", required=True)

    sub.add_parser("failures", help="list scrapes that did not return a body")

    p_csv = sub.add_parser("csv", help="dump one metric's history to a CSV file")
    p_csv.add_argument("--metric", required=True)
    p_csv.add_argument("--out", required=True)

    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"no store at {db_path} yet, run collector.py at least once first", file=sys.stderr)
        return 1

    conn = store.connect(db_path)
    try:
        if args.command == "summary":
            return cmd_summary(conn)
        if args.command == "history":
            return cmd_history(conn, args.metric)
        if args.command == "failures":
            return cmd_failures(conn)
        return cmd_csv(conn, args.metric, args.out)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
