"""Split a test run across CI jobs, one whole file per job.

CI runs ``tests/integration`` as several jobs (``--shard K/N``), each with its
own Postgres and Redis. This module decides which files each job runs and,
afterwards, checks that the jobs between them ran every file exactly once.

Why whole files, rather than individual tests:

- A file's tests keep running together and in their usual order, so module
  scoped fixtures and any ordering inside a file behave as they did in one
  job. The only new situation a split creates is a file that used to run
  after some other file now running without it.
- The assignment depends only on file paths, so it agrees across jobs even
  where a parametrised test's ids are not stable from one process to the
  next. A split by test id would assign such tests differently in each job
  and run some twice and others never.

Files are weighted by how long they took (``integration/shard_durations.json``)
and handed out longest first to the least loaded job. A file missing from the
durations file gets the median weight, so new files are placed sensibly until
the next refresh. Within a job, files run in normal collection order.

Refreshing the weights: every CI run uploads ``backend-integration-durations``
from the ``test (backend-integration)`` job. Download it and copy it over
``tests/integration/shard_durations.json``. Stale weights only make the jobs
less even; they cannot make a file run twice or not at all.

``python -m tests._shard verify REPORT...`` is what the aggregate job runs. It
is standard library only so that job needs no dependencies installed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DURATIONS_PATH = Path(__file__).resolve().parent / "integration" / "shard_durations.json"


def parse_shard(value: str) -> tuple[int, int]:
    """``"2/3"`` -> ``(2, 3)``. Shards count from 1."""
    head, sep, tail = value.partition("/")
    try:
        shard, total = int(head), int(tail)
    except ValueError:
        shard, total = 0, 0
    if not sep or total < 1 or not 1 <= shard <= total:
        raise ValueError(f"--shard expects K/N with 1 <= K <= N, got {value!r}")
    return shard, total


def load_durations(path: Path = DURATIONS_PATH) -> dict[str, float]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in data.items()}


def partition(files: Sequence[str], durations: Mapping[str, float], total: int) -> list[list[str]]:
    """Assign each file to exactly one of ``total`` buckets.

    Deterministic for a given set of files and weights, whatever order the
    files arrive in. Each bucket lists its files in the order given.
    """
    unique = list(dict.fromkeys(files))
    known = [durations[f] for f in unique if f in durations]
    default = statistics.median(known) if known else 1.0
    weight = {f: durations.get(f, default) for f in unique}

    loads = [0.0] * total
    buckets: list[list[str]] = [[] for _ in range(total)]
    for f in sorted(unique, key=lambda f: (-weight[f], f)):
        i = min(range(total), key=lambda i: (loads[i], i))
        buckets[i].append(f)
        loads[i] += weight[f]

    position = {f: n for n, f in enumerate(unique)}
    return [sorted(b, key=position.__getitem__) for b in buckets]


def file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


class ShardPlugin:
    """Deselects other shards' files and records what this shard ran."""

    def __init__(self, shard: int, total: int, report_path: str | None) -> None:
        self.shard = shard
        self.total = total
        self.report_path = report_path
        self.files_total = 0
        self.tests_total = 0
        self.selected_files: list[str] = []
        self.tests_selected = 0
        self.durations: dict[str, float] = {}
        self.slowest: list[tuple[float, str]] = []
        self._test_seconds: dict[str, float] = {}

    def select(self, config: Any, items: list[Any]) -> None:
        files = [file_of(item.nodeid) for item in items]
        buckets = partition(files, load_durations(), self.total)
        mine = set(buckets[self.shard - 1])
        kept = [item for item in items if file_of(item.nodeid) in mine]
        dropped = [item for item in items if file_of(item.nodeid) not in mine]

        self.files_total = len(set(files))
        self.tests_total = len(items)
        # From what is kept rather than from the bucket, so the report says what
        # this shard will run even if the selection above is wrong.
        self.selected_files = list(dict.fromkeys(file_of(item.nodeid) for item in kept))
        self.tests_selected = len(kept)
        if dropped:
            config.hook.pytest_deselected(items=dropped)
        items[:] = kept

    def record(self, report: Any) -> None:
        seconds = float(getattr(report, "duration", 0.0) or 0.0)
        key = file_of(report.nodeid)
        self.durations[key] = self.durations.get(key, 0.0) + seconds
        self._test_seconds[report.nodeid] = self._test_seconds.get(report.nodeid, 0.0) + seconds

    def report(self) -> dict[str, Any]:
        slowest = sorted(((s, n) for n, s in self._test_seconds.items()), reverse=True)[:10]
        return {
            "shard": self.shard,
            "total": self.total,
            "files_total": self.files_total,
            "tests_total": self.tests_total,
            "files": self.selected_files,
            "tests_selected": self.tests_selected,
            "durations": {k: round(v, 2) for k, v in sorted(self.durations.items())},
            "slowest_tests": [[n, round(s, 2)] for s, n in slowest],
        }

    def write_report(self) -> None:
        if self.report_path:
            Path(self.report_path).write_text(
                json.dumps(self.report(), indent=2) + "\n", encoding="utf-8"
            )

    def header(self) -> str:
        return (
            f"shard {self.shard}/{self.total}: {len(self.selected_files)} of "
            f"{self.files_total} files, {self.tests_selected} of {self.tests_total} tests"
        )


def verify(reports: Sequence[Mapping[str, Any]]) -> list[str]:
    """Problems with a set of shard reports; empty when every file ran once."""
    if not reports:
        return ["no shard reports were found"]
    problems: list[str] = []
    totals = {(r["total"], r["files_total"], r["tests_total"]) for r in reports}
    if len(totals) != 1:
        problems.append(f"shards disagree on what was collected: {sorted(totals)}")
        return problems
    total, files_total, tests_total = totals.pop()

    shards = sorted(r["shard"] for r in reports)
    if shards != list(range(1, total + 1)):
        problems.append(f"expected reports from shards 1..{total}, got {shards}")

    seen: dict[str, int] = {}
    for r in reports:
        for f in r["files"]:
            if f in seen:
                problems.append(f"{f} ran in shard {seen[f]} and shard {r['shard']}")
            seen[f] = r["shard"]
    if len(seen) != files_total:
        problems.append(f"shards ran {len(seen)} distinct files, {files_total} were collected")
    ran = sum(r["tests_selected"] for r in reports)
    if ran != tests_total:
        problems.append(f"shards ran {ran} tests, {tests_total} were collected")
    return problems


def merged_durations(reports: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for r in reports:
        for f, s in r["durations"].items():
            merged[f] = round(merged.get(f, 0.0) + float(s), 1)
    return dict(sorted(merged.items()))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests._shard")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify", help="check that the shards ran every file once")
    check.add_argument("reports", nargs="*", type=Path)
    check.add_argument("--durations-out", type=Path, default=None)
    args = parser.parse_args(argv)

    reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports]
    problems = verify(reports)
    for r in sorted(reports, key=lambda r: r["shard"]):
        busy = sum(r["durations"].values())
        print(
            f"shard {r['shard']}/{r['total']}: {len(r['files'])} files, "
            f"{r['tests_selected']} tests, {busy / 60:.1f} min in tests"
        )
        for nodeid, seconds in r["slowest_tests"][:3]:
            print(f"  {seconds:7.1f}s  {nodeid}")
    for p in problems:
        print(f"::error::{p}")
    if args.durations_out is not None and not problems:
        args.durations_out.write_text(
            json.dumps(merged_durations(reports), indent=2) + "\n", encoding="utf-8"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
